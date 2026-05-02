from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.database import Database
from utils.job_paths import get_job_results_dir
from utils.repo_paths import resolve_repo_path, to_repo_relative_path

# Shared job-history summary helpers keep jobs/reports views aligned.


DEFAULT_JOB_SUMMARY_LIMIT = 100
_SELECTOR_DIAGNOSTICS_ARTIFACT = "selector_diagnostics.json"
_WORKING_MEMORY_ARTIFACT = "working_memory.json"
_DISTILLED_MEMORY_ARTIFACT = "distilled_memory_summary.md"
_REPORT_AUDIT_ARTIFACT = "report_audit.json"


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_diagnostic_snapshot(artifact_paths: dict[str, Path]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    selector_path = artifact_paths["selector_diagnostics"]
    if selector_path.exists():
        selector_payload = _load_json_file(selector_path)
        bundle = selector_payload.get("selection_memory_bundle")
        if isinstance(bundle, dict):
            snapshot["selector_candidate_count"] = int(
                selector_payload.get("candidate_count", 0) or 0
            )
            snapshot["selector_ranked_count"] = int(
                selector_payload.get("ranked_count", 0) or 0
            )
            snapshot["selector_memory_chars"] = len(
                str(selector_payload.get("selection_memory", "") or "")
            )
            snapshot["selector_digest_count"] = len(
                bundle.get("high_level_digest", []) or []
            )
            snapshot["selector_claim_count"] = len(
                bundle.get("priority_claims", []) or []
            )
            snapshot["selector_related_count"] = len(
                bundle.get("related_papers", []) or []
            )

    working_memory_path = artifact_paths["working_memory"]
    if working_memory_path.exists():
        working_memory_payload = _load_json_file(working_memory_path)
        metrics = working_memory_payload.get("metrics")
        if isinstance(metrics, dict):
            for key in (
                "memory_extraction_prompt_chars",
                "memory_extraction_candidate_count",
                "memory_extraction_original_candidate_count",
                "retrieved_claim_count",
                "retrieved_evidence_count",
                "translation_hint_count",
            ):
                value = metrics.get(key)
                if isinstance(value, (int, float)):
                    snapshot[key] = int(value)
        promotion_candidates = working_memory_payload.get("promotion_candidates")
        if isinstance(promotion_candidates, list):
            counts = {"accepted": 0, "review_required": 0, "rejected": 0}
            for item in promotion_candidates:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "")).strip()
                if status in counts:
                    counts[status] += 1
            snapshot["promotion_counts"] = counts

    report_audit_path = artifact_paths["report_audit"]
    if report_audit_path.exists():
        report_audit_payload = _load_json_file(report_audit_path)
        issues = report_audit_payload.get("issues")
        if isinstance(issues, list):
            severity_counts = {"high": 0, "medium": 0, "low": 0}
            for item in issues:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("severity") or "").strip().lower()
                if severity in severity_counts:
                    severity_counts[severity] += 1
            snapshot["report_audit_issue_count"] = len(issues)
            snapshot["report_audit_severity_counts"] = severity_counts
            snapshot["report_audit_warning"] = bool(
                report_audit_payload.get("warning")
            )

    return snapshot


def extract_title_from_markdown(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if len(line) > 200:
                    break
    except Exception:
        pass
    return path.stem


def build_profile_name_map(db: Database) -> dict[int, str]:
    profiles_by_id: dict[int, str] = {}
    default_profile_id = db.resolve_default_profile_id()

    list_profiles = getattr(db, "list_profiles", None)
    if callable(list_profiles):
        for profile in list_profiles():
            profile_id = profile.get("id")
            if profile_id is None:
                continue
            pid = int(profile_id)
            name = str(profile.get("name", "")).strip() or f"Profile #{pid}"
            profiles_by_id[pid] = name

    if default_profile_id is not None:
        profiles_by_id.setdefault(default_profile_id, "Default")

    return profiles_by_id


def normalize_job_profile(
    job: dict[str, Any],
    profiles_by_id: dict[int, str],
    *,
    default_profile_id: int | None,
) -> tuple[int | None, str]:
    profile_id_raw = job.get("profile_id")
    if isinstance(profile_id_raw, int):
        profile_id = int(profile_id_raw)
        return profile_id, profiles_by_id.get(profile_id, f"Profile #{profile_id}")

    profile_mode = str(job.get("profile_mode") or "auto").strip().lower()
    if profile_mode == "explicit" and default_profile_id is not None:
        return default_profile_id, profiles_by_id.get(default_profile_id, "Default")

    return None, ""


def get_job_memory_artifact_paths(job_id: str) -> dict[str, Path]:
    results_dir = get_job_results_dir(job_id)
    return {
        "selector_diagnostics": results_dir / _SELECTOR_DIAGNOSTICS_ARTIFACT,
        "working_memory": results_dir / _WORKING_MEMORY_ARTIFACT,
        "distilled_memory_summary": results_dir / _DISTILLED_MEMORY_ARTIFACT,
        "report_audit": results_dir / _REPORT_AUDIT_ARTIFACT,
    }


def enrich_job_with_artifact_readiness(job: dict[str, Any]) -> dict[str, Any]:
    """Add ``has_*`` artifact-readiness flags to a raw job dict.

    This is intended for live job views (``GET /api/jobs/{id}`` and
    WebSocket state broadcasts) so the frontend can decide whether to
    request the corresponding artifact endpoints.

    The function returns a **shallow copy** with the extra keys injected;
    it never mutates the original *job* dict.
    """
    job_id = str(job.get("id", "")).strip()
    if not job_id:
        return {
            **job,
            "has_selector_diagnostics": False,
            "has_working_memory": False,
            "has_distilled_memory_summary": False,
            "has_report_audit": False,
        }
    artifact_paths = get_job_memory_artifact_paths(job_id)
    return {
        **job,
        "has_selector_diagnostics": artifact_paths["selector_diagnostics"].exists(),
        "has_working_memory": artifact_paths["working_memory"].exists(),
        "has_distilled_memory_summary": artifact_paths[
            "distilled_memory_summary"
        ].exists(),
        "has_report_audit": artifact_paths["report_audit"].exists(),
    }


def build_job_report_summary(
    job: dict[str, Any],
    profiles_by_id: dict[int, str],
    *,
    default_profile_id: int | None,
) -> dict[str, Any]:
    report_path = resolve_repo_path(job.get("report_path", ""))
    has_report = report_path.exists() and report_path.is_file()
    stat = report_path.stat() if has_report else None
    artifact_paths = get_job_memory_artifact_paths(str(job.get("id", "")))
    profile_id, profile_name = normalize_job_profile(
        job, profiles_by_id, default_profile_id=default_profile_id
    )

    error_value = job.get("error")
    return {
        "job_id": str(job.get("id", "")),
        "status": str(job.get("status", "")),
        "mode": str(job.get("mode", "")),
        "profile_id": profile_id,
        "profile_name": profile_name,
        "profile_mode": str(job.get("profile_mode") or "auto"),
        "profile_assignment_status": str(
            job.get("profile_assignment_status") or "pending"
        ),
        "profile_assignment_note": str(job.get("profile_assignment_note") or ""),
        "config_snapshot": job.get("config_snapshot") or {},
        "diagnostic_snapshot": _build_diagnostic_snapshot(artifact_paths),
        "progress": float(job.get("progress", 0) or 0),
        "current_step": str(job.get("current_step", "")),
        "paper_title": str(job.get("paper_title", "")),
        "report_path": to_repo_relative_path(report_path) if has_report else "",
        "error": str(error_value).strip() if error_value not in (None, "") else None,
        "created_at": float(job.get("created_at", 0) or 0),
        "started_at": float(job.get("started_at", 0))
        if job.get("started_at") is not None
        else None,
        "completed_at": float(job.get("completed_at", 0))
        if job.get("completed_at") is not None
        else None,
        "has_report": has_report,
        "has_selector_diagnostics": artifact_paths["selector_diagnostics"].exists(),
        "has_working_memory": artifact_paths["working_memory"].exists(),
        "has_distilled_memory_summary": artifact_paths[
            "distilled_memory_summary"
        ].exists(),
        "has_report_audit": artifact_paths["report_audit"].exists(),
        "title": extract_title_from_markdown(report_path) if has_report else "",
        "size_bytes": stat.st_size if stat else 0,
        "modified_at": stat.st_mtime if stat else 0.0,
    }


def build_job_report_summaries(
    jobs: list[dict[str, Any]],
    profiles_by_id: dict[int, str],
    *,
    default_profile_id: int | None,
) -> list[dict[str, Any]]:
    return [
        build_job_report_summary(
            job, profiles_by_id, default_profile_id=default_profile_id
        )
        for job in jobs
    ]
