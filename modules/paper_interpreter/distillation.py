"""Heuristic distillation from WorkingMemory into promotion-ready summaries."""

from __future__ import annotations

import re
from typing import Any

from modules.paper_interpreter.working_memory import (
    PromotionCandidate,
    WorkingMemory,
)


def _clip_text(text: str, *, limit: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", str(text).strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _slugify(text: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower())
    return lowered.strip("-") or "candidate"


def _candidate_signature(candidate_type: str, payload: dict[str, Any]) -> str:
    title = str(payload.get("title", "")).strip().lower()
    body = str(payload.get("body", payload.get("summary", ""))).strip().lower()
    return f"{candidate_type}:{title}:{body[:120]}"


def _has_conflict_overlap(
    working_memory: WorkingMemory,
    *,
    claim_text: str,
    section_key: str,
) -> bool:
    normalized_claim = str(claim_text).strip().lower()
    if not normalized_claim:
        return False

    conflict_bundle = working_memory.retrieved_context.get("interpreter_bundle", {})
    active_conflicts = (
        conflict_bundle.get("active_conflicts", [])
        if isinstance(conflict_bundle, dict)
        else []
    )
    for item in active_conflicts:
        title = str(item.get("title", "")).strip().lower()
        description = str(item.get("description", "")).strip().lower()
        default_resolution = str(item.get("default_resolution", "")).strip().lower()
        if title and title in normalized_claim:
            return True
        if description and description[:80] and description[:80] in normalized_claim:
            return True
        if (
            default_resolution
            and default_resolution[:80]
            and default_resolution[:80] in normalized_claim
        ):
            return True

    for question in working_memory.open_questions:
        if question.status != "open" or question.section_key != section_key:
            continue
        question_text = str(question.question).strip().lower()
        if question_text and (
            question_text in normalized_claim or normalized_claim[:80] in question_text
        ):
            return True
    return False


def build_distillation_candidates(
    working_memory: WorkingMemory,
    task_results: dict[str, Any],
) -> dict[str, list[PromotionCandidate]]:
    accepted: list[PromotionCandidate] = []
    review_required: list[PromotionCandidate] = []
    rejected: list[PromotionCandidate] = []
    seen: set[str] = set()

    for draft in working_memory.draft_claims:
        payload = {
            "claim_key": f"{draft.section_key}:{_slugify(draft.claim)}",
            "title": _clip_text(draft.claim, limit=160),
            "body": draft.claim,
            "claim_type": "finding",
            "stance": "support",
            "importance": 0.85 if draft.importance == "high" else 0.7 if draft.importance == "medium" else 0.55,
            "default_resolution": draft.claim,
            "entity_names": [],
        }
        signature = _candidate_signature("claim", payload)
        if signature in seen:
            continue
        seen.add(signature)

        candidate = PromotionCandidate(
            candidate_type="claim",
            payload=payload,
            source_section=draft.section_key,
            evidence_refs=list(draft.evidence_refs),
            confidence=float(draft.confidence),
        )

        if candidate.confidence >= 0.72 and candidate.evidence_refs:
            if _has_conflict_overlap(
                working_memory,
                claim_text=draft.claim,
                section_key=draft.section_key,
            ):
                candidate.status = "review_required"
                review_required.append(candidate)
                continue
            candidate.status = "accepted"
            accepted.append(candidate)
        elif candidate.confidence >= 0.58:
            candidate.status = "review_required"
            review_required.append(candidate)
        else:
            candidate.status = "rejected"
            candidate.rejection_reason = "Low confidence draft claim."
            rejected.append(candidate)

    one_line_summary = str(task_results.get("t1_summary", "")).strip()
    conclusion = str(task_results.get("t7_conclusion", "")).strip()
    if one_line_summary:
        summary_candidate = PromotionCandidate(
            candidate_type="synthesis",
            payload={
                "item_type": "consensus",
                "title": "Paper takeaway",
                "summary": one_line_summary,
                "default_resolution": conclusion or one_line_summary,
                "confidence": 0.62,
            },
            source_section="summary",
            evidence_refs=[],
            confidence=0.62,
            status="review_required",
        )
        signature = _candidate_signature("synthesis", summary_candidate.payload)
        if signature not in seen:
            seen.add(signature)
            review_required.append(summary_candidate)

    return {
        "accepted": accepted,
        "review_required": review_required,
        "rejected": rejected,
    }


def build_distilled_memory_summary(
    working_memory: WorkingMemory,
    task_results: dict[str, Any],
) -> tuple[str, dict[str, int]]:
    buckets = build_distillation_candidates(working_memory, task_results)

    for candidate in buckets["accepted"] + buckets["review_required"] + buckets["rejected"]:
        working_memory.register_promotion_candidate(
            candidate_type=candidate.candidate_type,
            payload=candidate.payload,
            source_section=candidate.source_section,
            evidence_refs=candidate.evidence_refs,
            confidence=candidate.confidence,
            status=candidate.status,
            rejection_reason=candidate.rejection_reason,
        )

    lines: list[str] = []
    one_line_summary = str(task_results.get("t1_summary", "")).strip()
    if one_line_summary:
        lines.append(f"One-line summary: {one_line_summary}")

    accepted_by_section: dict[str, list[PromotionCandidate]] = {}
    for candidate in buckets["accepted"]:
        accepted_by_section.setdefault(candidate.source_section, []).append(candidate)

    for section_key in ["background", "method", "experiments"]:
        section_candidates = accepted_by_section.get(section_key, [])
        if not section_candidates:
            continue
        lines.append(f"{section_key.title()} distilled claims:")
        for candidate in section_candidates[:4]:
            claim_text = str(candidate.payload.get("body", "")).strip()
            suffix = (
                f" [evidence: {'; '.join(candidate.evidence_refs)}]"
                if candidate.evidence_refs
                else ""
            )
            lines.append(f"- {claim_text}{suffix}")

    open_questions = [item for item in working_memory.open_questions if item.status == "open"]
    if open_questions:
        lines.append("Open questions to keep conservative in long-term memory:")
        for item in open_questions[:4]:
            reason = f" ({item.reason})" if item.reason else ""
            lines.append(f"- {item.question}{reason}")

    conclusion = str(task_results.get("t7_conclusion", "")).strip()
    if conclusion:
        lines.append(f"Overall assessment:\n{conclusion}")

    summary = "\n\n".join(block for block in lines if block.strip())
    metrics = {
        "accepted_count": len(buckets["accepted"]),
        "review_required_count": len(buckets["review_required"]),
        "rejected_count": len(buckets["rejected"]),
        "accepted_claim_count": len(
            [item for item in buckets["accepted"] if item.candidate_type == "claim"]
        ),
        "review_required_claim_count": len(
            [
                item
                for item in buckets["review_required"]
                if item.candidate_type == "claim"
            ]
        ),
    }
    return summary, metrics
