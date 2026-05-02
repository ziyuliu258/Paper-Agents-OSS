"""Rule-based report audit and conservative auto-repair."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pymupdf

from modules.paper_interpreter.task_runner import _render_section_markdown
from utils.repo_paths import resolve_repo_path

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")


def _load_pdf_page_texts(pdf_path: Path) -> list[str]:
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:
        return []
    try:
        return [" ".join(doc[idx].get_text("text").split()) for idx in range(len(doc))]
    finally:
        doc.close()


def _load_html_section_texts(parsed_paper: dict[str, Any]) -> list[str]:
    html_bundle = parsed_paper.get("html_bundle")
    if not isinstance(html_bundle, dict):
        return []
    sections = html_bundle.get("sections", [])
    if not isinstance(sections, list):
        return []
    chunks: list[str] = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading", "")).strip()
        content = str(item.get("content", "")).strip()
        joined = " ".join(part for part in (heading, content) if part).strip()
        if joined:
            chunks.append(joined)
    plain_text = str(html_bundle.get("plain_text", "")).strip()
    if not chunks and plain_text:
        chunks.append(plain_text)
    return chunks


def _issue(
    *,
    issue_type: str,
    severity: str,
    status: str,
    section_key: str,
    claim: str,
    evidence_refs: list[str],
    reason: str,
    repair_action: str,
) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "status": status,
        "section_key": section_key,
        "claim": claim,
        "evidence_refs": evidence_refs,
        "reason": reason,
        "repair_action": repair_action,
    }


def _format_evidence_ref(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    page = item.get("page")
    detail = str(item.get("detail", "")).strip()
    parts = [label] if label else []
    if isinstance(page, int) and page > 0:
        parts.append(f"p.{page}")
    if detail:
        parts.append(detail)
    return " | ".join(parts)


def _groundedness_issues(
    sections: list[tuple[str, dict[str, Any]]],
    page_texts: list[str],
    *,
    enforce_page_bounds: bool,
) -> tuple[list[dict[str, Any]], bool, dict[str, list[str]]]:
    issues: list[dict[str, Any]] = []
    repaired = False
    removed_claims_by_section: dict[str, list[str]] = {}
    for section_key, payload in sections:
        claims = payload.get("claims", []) if isinstance(payload, dict) else []
        kept_claims: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_text = str(claim.get("claim", "")).strip()
            evidence = claim.get("evidence", []) if isinstance(claim.get("evidence"), list) else []
            evidence_refs = [
                ref for ref in (_format_evidence_ref(item) for item in evidence if isinstance(item, dict)) if ref
            ]
            if not evidence:
                issues.append(
                    _issue(
                        issue_type="groundedness",
                        severity="high",
                        status="repaired",
                        section_key=section_key,
                        claim=claim_text,
                        evidence_refs=[],
                        reason="Structured claim has no evidence items.",
                        repair_action="Dropped the unsupported claim from the section.",
                    )
                )
                repaired = True
                removed_claims_by_section.setdefault(section_key, []).append(claim_text)
                continue
            invalid_pages = []
            if enforce_page_bounds:
                invalid_pages = [
                    item
                    for item in evidence
                    if isinstance(item, dict)
                    and isinstance(item.get("page"), int)
                    and int(item["page"]) > len(page_texts)
                    and len(page_texts) > 0
                ]
            if invalid_pages:
                issues.append(
                    _issue(
                        issue_type="groundedness",
                        severity="high",
                        status="repaired",
                        section_key=section_key,
                        claim=claim_text,
                        evidence_refs=evidence_refs,
                        reason="Evidence points to PDF pages that do not exist in the parsed document.",
                        repair_action="Dropped the claim because its evidence anchor was invalid.",
                    )
                )
                repaired = True
                removed_claims_by_section.setdefault(section_key, []).append(claim_text)
                continue
            if not page_texts:
                issues.append(
                    _issue(
                        issue_type="groundedness",
                        severity="low",
                        status="warning",
                        section_key=section_key,
                        claim=claim_text,
                        evidence_refs=evidence_refs,
                        reason="PDF text was unavailable, so evidence pages could not be independently re-checked.",
                        repair_action="Kept the claim but marked the audit as conservative.",
                    )
                )
            kept_claims.append(claim)
        payload["claims"] = kept_claims
    return issues, repaired, removed_claims_by_section


def _consistency_issues(task_results: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    supported_numbers = set()
    for key in ("t2_background", "t3_method", "t4_experiments"):
        supported_numbers.update(_NUMBER_RE.findall(str(task_results.get(key) or "")))

    for key, section_key in (
        ("t1_summary", "summary"),
        ("t5_ablation", "ablation"),
        ("t6_limitations", "limitations"),
        ("t7_conclusion", "conclusion"),
    ):
        text = str(task_results.get(key) or "")
        numbers = set(_NUMBER_RE.findall(text))
        unsupported = sorted(number for number in numbers if number not in supported_numbers)
        if unsupported:
            issues.append(
                _issue(
                    issue_type="consistency",
                    severity="medium",
                    status="warning",
                    section_key=section_key,
                    claim=text[:200],
                    evidence_refs=unsupported,
                    reason="This plain-text section contains numeric claims that were not found in the structured evidence-backed sections.",
                    repair_action="Kept the section unchanged but flagged it for manual review.",
                )
            )
    return issues


def _rebuild_rendered_sections(task_results: dict[str, Any]) -> None:
    mapping = (
        ("t2_background", "Research Background and Motivation", "t2_background_structured"),
        ("t3_method", "Core Method", "t3_method_structured"),
        ("t4_experiments", "Experiments and Results", "t4_experiments_structured"),
    )
    for rendered_key, label, structured_key in mapping:
        payload = task_results.get(structured_key)
        if isinstance(payload, dict):
            task_results[rendered_key] = _render_section_markdown(label, payload)


def _collect_severity_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for item in issues:
        severity = str(item.get("severity") or "").strip().lower()
        if severity in severity_counts:
            severity_counts[severity] += 1
    return severity_counts


def _run_single_audit_pass(
    *,
    parsed_paper: dict[str, Any],
    task_results: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, dict[str, list[str]]]:
    sections = [
        ("background", task_results.get("t2_background_structured") or {}),
        ("method", task_results.get("t3_method_structured") or {}),
        ("experiments", task_results.get("t4_experiments_structured") or {}),
    ]
    source_type = str(parsed_paper.get("source_type") or "pdf").strip().lower() or "pdf"
    if source_type == "html":
        page_texts = _load_html_section_texts(parsed_paper)
        enforce_page_bounds = False
    else:
        page_texts = _load_pdf_page_texts(
            resolve_repo_path(str(parsed_paper.get("pdf_path") or ""))
        )
        enforce_page_bounds = True
    groundedness, repaired, removed_claims_by_section = _groundedness_issues(
        sections,
        page_texts,
        enforce_page_bounds=enforce_page_bounds,
    )
    if repaired:
        _rebuild_rendered_sections(task_results)
    consistency = _consistency_issues(task_results)
    return groundedness + consistency, repaired, removed_claims_by_section


def audit_and_repair_report(
    parsed_paper: dict[str, Any],
    task_results: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    initial_issues, repaired, removed_claims_by_section = _run_single_audit_pass(
        parsed_paper=parsed_paper,
        task_results=task_results,
    )
    initial_severity_counts = _collect_severity_counts(initial_issues)
    repair_attempted = repaired or initial_severity_counts["high"] > 0

    final_issues: list[dict[str, Any]]
    if repair_attempted:
        final_issues, _, _ = _run_single_audit_pass(
            parsed_paper=parsed_paper,
            task_results=task_results,
        )
        issues = initial_issues + final_issues
    else:
        final_issues = initial_issues
        issues = initial_issues

    severity_counts = _collect_severity_counts(final_issues)
    warning = bool(severity_counts["high"] or severity_counts["medium"])
    audit_payload = {
        "generated_at": time.time(),
        "status": "warning" if warning else "pass",
        "warning": warning,
        "repaired": repaired,
        "repair_attempted": repair_attempted,
        "repair_passes": 2 if repair_attempted else 1,
        "initial_severity_counts": initial_severity_counts,
        "issues": issues,
        "severity_counts": severity_counts,
        "removed_claims_by_section": removed_claims_by_section,
    }
    return task_results, audit_payload
