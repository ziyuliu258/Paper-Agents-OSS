"""Reports browsing API."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from modules.paper_interpreter.report_refiner import (
    list_report_variants,
    load_report_variant,
    refine_report_variant,
)
from modules.paper_interpreter.translator import translate_memory_batch
from server.deps import get_db
from server.job_summaries import (
    DEFAULT_JOB_SUMMARY_LIMIT,
    build_job_report_summaries,
    build_profile_name_map,
    extract_title_from_markdown,
    get_job_memory_artifact_paths,
    normalize_job_profile,
)
from server.schemas import JobReportListResponse, JobReportResponse, ReportRefineRequest
from utils.job_paths import get_job_assets_root
from utils.logger import get_logger
from utils.report_styles import normalize_report_structure_mode
from utils.repo_paths import resolve_repo_path, to_repo_relative_path
from utils.runtime_access import ensure_runtime_access_allowed_for_request
from utils.working_memory_localization import ensure_localized_distilled_summary_artifact

router = APIRouter(tags=["reports"])
log = get_logger(__name__)
_SUPPORTED_MEMORY_ARTIFACTS = {
    "selector-diagnostics": ("selector_diagnostics", "application/json"),
    "working-memory": ("working_memory", "application/json"),
    "distilled-memory-summary": ("distilled_memory_summary", "text/markdown; charset=utf-8"),
    "report-audit": ("report_audit", "application/json"),
}
def _ensure_runtime_settings_available() -> None:
    ok, detail = ensure_runtime_access_allowed_for_request()
    if not ok:
        raise HTTPException(status_code=400, detail=detail)


def _extract_job_asset_base(content: str) -> str | None:
    match = re.search(r"\]\((assets/[^)]+)\)", content)
    if not match:
        return None
    asset_rel_path = match.group(1)
    first_segment = Path(asset_rel_path).parts[1] if len(Path(asset_rel_path).parts) > 1 else None
    return first_segment


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_working_memory_localized_cache_path(job_id: str, language: str) -> Path:
    artifact_paths = get_job_memory_artifact_paths(job_id)
    return artifact_paths["working_memory"].with_name(f"working_memory.{language}.json")


def _build_working_memory_paper_context(payload: dict[str, Any]) -> str:
    paper_notes = payload.get("paper_notes") if isinstance(payload.get("paper_notes"), dict) else {}
    metadata = paper_notes.get("metadata") if isinstance(paper_notes.get("metadata"), dict) else {}
    title = str(payload.get("paper_title") or metadata.get("title_en") or metadata.get("title_cn") or "").strip()
    summary = str(paper_notes.get("paper_summary") or "").strip()
    context_lines = []
    if title:
        context_lines.append(f"Title: {title}")
    if summary:
        context_lines.append(f"Summary: {summary}")
    return "\n".join(context_lines)


async def _localize_working_memory_payload(payload: dict[str, Any], *, language: str) -> dict[str, Any]:
    localized = json.loads(json.dumps(payload, ensure_ascii=False))
    if language != "zh":
        localized["translation_language"] = "en"
        localized["translation_generated_at"] = time.time()
        return localized

    requests: list[dict[str, Any]] = []
    references: list[tuple[str, int, tuple[str, ...]]] = []

    observations = localized.get("observations")
    if isinstance(observations, list):
        for index, item in enumerate(observations):
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            requests.append(
                {
                    "kind": "working_memory_observation",
                    "fields": {"summary": summary},
                    "context": {
                        "section_key": str(item.get("section_key") or ""),
                        "kind": str(item.get("kind") or ""),
                    },
                }
            )
            references.append(("observations", index, ("summary",)))

    open_questions = localized.get("open_questions")
    if isinstance(open_questions, list):
        for index, item in enumerate(open_questions):
            if not isinstance(item, dict):
                continue
            fields = {
                key: str(item.get(key) or "").strip()
                for key in ("question", "reason", "resolution_note")
                if str(item.get(key) or "").strip()
            }
            if not fields:
                continue
            requests.append(
                {
                    "kind": "working_memory_open_question",
                    "fields": fields,
                    "context": {
                        "section_key": str(item.get("section_key") or ""),
                        "status": str(item.get("status") or ""),
                    },
                }
            )
            references.append(("open_questions", index, tuple(fields.keys())))

    draft_claims = localized.get("draft_claims")
    if isinstance(draft_claims, list):
        for index, item in enumerate(draft_claims):
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue
            requests.append(
                {
                    "kind": "working_memory_draft_claim",
                    "fields": {"claim": claim},
                    "context": {
                        "section_key": str(item.get("section_key") or ""),
                        "importance": str(item.get("importance") or ""),
                    },
                }
            )
            references.append(("draft_claims", index, ("claim",)))

    promotion_candidates = localized.get("promotion_candidates")
    if isinstance(promotion_candidates, list):
        for index, item in enumerate(promotion_candidates):
            if not isinstance(item, dict):
                continue
            payload_fields = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            fields = {
                key: str(payload_fields.get(key) or "").strip()
                for key in ("title", "body", "summary")
                if str(payload_fields.get(key) or "").strip()
            }
            if not fields:
                continue
            requests.append(
                {
                    "kind": "working_memory_promotion_candidate",
                    "fields": fields,
                    "context": {
                        "status": str(item.get("status") or ""),
                        "candidate_type": str(item.get("candidate_type") or ""),
                        "source_section": str(item.get("source_section") or ""),
                    },
                }
            )
            references.append(("promotion_candidates", index, tuple(fields.keys())))

    terminology_entries = (
        list((localized.get("terminology_map") or {}).items())
        if isinstance(localized.get("terminology_map"), dict)
        else []
    )
    for index, (term, description) in enumerate(terminology_entries):
        term_text = str(term or "").strip()
        description_text = str(description or "").strip()
        if not term_text and not description_text:
            continue
        requests.append(
            {
                "kind": "working_memory_terminology",
                "fields": {
                    "term": term_text,
                    "description": description_text,
                },
                "context": {},
            }
        )
        references.append(("terminology_map", index, ("term", "description")))

    if requests:
        try:
            translations = await translate_memory_batch(
                requests,
                step_label="job working memory localization",
                paper_context=_build_working_memory_paper_context(localized),
            )
        except Exception as exc:
            log.warning(
                "Working memory localization failed for job %s: %s",
                localized.get("job_id") or "unknown",
                exc,
            )
            translations = [{} for _ in requests]

        terminology_localized_entries: list[tuple[str, str]] = []
        for reference, translated in zip(references, translations):
            bucket, index, fields = reference
            translated_fields = translated if isinstance(translated, dict) else {}

            if bucket == "terminology_map":
                original_term, original_description = terminology_entries[index]
                translated_term = str(translated_fields.get("term") or "").strip() or str(original_term)
                translated_description = str(translated_fields.get("description") or "").strip() or str(original_description)
                terminology_localized_entries.append((translated_term, translated_description))
                continue

            collection = localized.get(bucket)
            if not isinstance(collection, list) or index >= len(collection) or not isinstance(collection[index], dict):
                continue

            if bucket == "promotion_candidates":
                payload_fields = collection[index].get("payload")
                if not isinstance(payload_fields, dict):
                    continue
                for field_name in fields:
                    translated_value = str(translated_fields.get(field_name) or "").strip()
                    if translated_value:
                        payload_fields[field_name] = translated_value
                continue

            for field_name in fields:
                translated_value = str(translated_fields.get(field_name) or "").strip()
                if translated_value:
                    collection[index][field_name] = translated_value

        if isinstance(localized.get("terminology_map"), dict) and terminology_entries:
            localized["terminology_map"] = {
                term: description
                for term, description in (terminology_localized_entries or terminology_entries)
            }

    localized["translation_language"] = "zh"
    localized["translation_generated_at"] = time.time()
    return localized


async def _load_or_build_localized_working_memory(job_id: str, *, language: str) -> dict[str, Any]:
    artifact_paths = get_job_memory_artifact_paths(job_id)
    source_path = artifact_paths["working_memory"]
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    if language == "en":
        payload = _load_json(source_path)
        payload["translation_language"] = "en"
        return payload

    cache_path = _build_working_memory_localized_cache_path(job_id, language)
    if cache_path.exists() and cache_path.stat().st_mtime >= source_path.stat().st_mtime:
        return _load_json(cache_path)

    localized = await _localize_working_memory_payload(_load_json(source_path), language=language)
    cache_path.write_text(json.dumps(localized, ensure_ascii=False, indent=2), encoding="utf-8")
    return localized


def _resolve_original_structure_mode(job: dict[str, Any]) -> str:
    snapshot = job.get("config_snapshot") if isinstance(job.get("config_snapshot"), dict) else {}
    report_config = snapshot.get("report") if isinstance(snapshot, dict) and isinstance(snapshot.get("report"), dict) else {}
    return normalize_report_structure_mode(str(report_config.get("structure_mode") or "classic"))


def _build_job_report_response(
    *,
    job: dict[str, Any],
    original_report_path: Path,
    report_source: dict[str, Any],
) -> JobReportResponse:
    db = get_db()
    artifact_paths = get_job_memory_artifact_paths(str(job.get("id", "")))
    selector_diagnostics_path = artifact_paths["selector_diagnostics"]
    working_memory_path = artifact_paths["working_memory"]
    distilled_summary_path = artifact_paths["distilled_memory_summary"]
    profile_id, profile_name = normalize_job_profile(
        job,
        build_profile_name_map(db),
        default_profile_id=db.resolve_default_profile_id(),
    )
    original_structure_mode = _resolve_original_structure_mode(job)
    variants = list_report_variants(
        job_id=str(job.get("id", "")),
        original_report_path=original_report_path,
        original_structure_mode=original_structure_mode,
    )
    return JobReportResponse(
        job_id=str(job.get("id", "")),
        profile_id=profile_id,
        profile_name=profile_name,
        profile_mode=str(job.get("profile_mode") or "auto"),
        profile_assignment_status=str(
            job.get("profile_assignment_status") or "pending"
        ),
        profile_assignment_note=str(job.get("profile_assignment_note") or ""),
        title=str(report_source.get("title") or extract_title_from_markdown(original_report_path)),
        paper_title=str(job.get("paper_title", "")),
        report_path=to_repo_relative_path(report_source.get("path") or original_report_path),
        selector_diagnostics_path=to_repo_relative_path(selector_diagnostics_path) if selector_diagnostics_path.exists() else "",
        working_memory_path=to_repo_relative_path(working_memory_path) if working_memory_path.exists() else "",
        distilled_memory_summary_path=to_repo_relative_path(distilled_summary_path) if distilled_summary_path.exists() else "",
        report_audit_path=to_repo_relative_path(artifact_paths["report_audit"]) if artifact_paths["report_audit"].exists() else "",
        has_selector_diagnostics=selector_diagnostics_path.exists(),
        has_working_memory=working_memory_path.exists(),
        has_distilled_memory_summary=distilled_summary_path.exists(),
        has_report_audit=artifact_paths["report_audit"].exists(),
        content=str(report_source.get("content") or ""),
        size_bytes=int(report_source.get("size_bytes") or 0),
        modified_at=float(report_source.get("modified_at") or 0.0),
        variant_id=str(report_source.get("variant_id") or "original"),
        variant_label=str(report_source.get("label") or "Original"),
        variant_kind=str(report_source.get("kind") or "original"),
        source_variant_id=str(report_source.get("source_variant_id") or ""),
        structure_mode=str(report_source.get("structure_mode") or original_structure_mode),
        detail_level=str(report_source.get("detail_level") or "balanced"),
        instruction=str(report_source.get("instruction") or ""),
        variants=variants,
    )



@router.get("/reports/jobs", response_model=JobReportListResponse)
async def list_job_reports(limit: int = DEFAULT_JOB_SUMMARY_LIMIT, include_all_jobs: bool = False):
    db = get_db()
    jobs = db.list_jobs(limit=limit) if include_all_jobs else db.list_report_jobs(limit=limit)
    reports = build_job_report_summaries(
        jobs,
        build_profile_name_map(db),
        default_profile_id=db.resolve_default_profile_id(),
    )
    if not include_all_jobs:
        reports = [report for report in reports if report["has_report"]]

    return JobReportListResponse(reports=reports)


@router.get("/reports/jobs/{job_id}", response_model=JobReportResponse)
async def get_job_report(job_id: str, variant_id: str | None = None):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    original_report_path = resolve_repo_path(job.get("report_path", ""))
    if not original_report_path.exists() or not original_report_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        report_source = load_report_variant(
            job_id=job_id,
            original_report_path=original_report_path,
            original_structure_mode=_resolve_original_structure_mode(job),
            variant_id=variant_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _build_job_report_response(
        job=job,
        original_report_path=original_report_path,
        report_source=report_source,
    )


@router.post("/reports/jobs/{job_id}/refine", response_model=JobReportResponse)
async def refine_job_report(job_id: str, body: ReportRefineRequest):
    _ensure_runtime_settings_available()
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    original_report_path = resolve_repo_path(job.get("report_path", ""))
    if not original_report_path.exists() or not original_report_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        report_source = await refine_report_variant(
            job_id=job_id,
            original_report_path=original_report_path,
            original_structure_mode=_resolve_original_structure_mode(job),
            instruction=body.instruction,
            target_structure_mode=body.target_structure_mode,
            detail_level=body.detail_level,
            base_variant_id=body.base_variant_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_job_report_response(
        job=job,
        original_report_path=original_report_path,
        report_source=report_source,
    )


@router.get("/reports/jobs/{job_id}/artifacts/{artifact_name}")
async def get_job_memory_artifact(job_id: str, artifact_name: str):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    artifact_key, media_type = _SUPPORTED_MEMORY_ARTIFACTS.get(artifact_name, (None, None))
    if artifact_key is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_paths = get_job_memory_artifact_paths(job_id)
    artifact_path = artifact_paths[artifact_key]
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    try:
        artifact_path.resolve().relative_to(artifact_path.parent.parent.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return FileResponse(str(artifact_path), media_type=media_type)


@router.get("/reports/jobs/{job_id}/working-memory-localized")
async def get_localized_working_memory(job_id: str, language: str = "zh"):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    normalized_language = language.strip().lower()
    if normalized_language not in {"zh", "en"}:
        raise HTTPException(status_code=400, detail="Unsupported language")
    if normalized_language != "en":
        _ensure_runtime_settings_available()

    return await _load_or_build_localized_working_memory(job_id, language=normalized_language)


@router.get(
    "/reports/jobs/{job_id}/distilled-summary-localized",
    response_class=PlainTextResponse,
)
async def get_localized_distilled_summary(job_id: str, language: str = "zh"):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    normalized_language = language.strip().lower()
    if normalized_language not in {"zh", "en"}:
        raise HTTPException(status_code=400, detail="Unsupported language")
    if normalized_language != "en":
        _ensure_runtime_settings_available()

    artifact_paths = get_job_memory_artifact_paths(job_id)
    source_path = artifact_paths["distilled_memory_summary"]
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    content = await ensure_localized_distilled_summary_artifact(
        job_id,
        language=normalized_language,
    )
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


@router.get("/reports/jobs/{job_id}/assets/{file_path:path}")
async def get_job_report_asset(job_id: str, file_path: str):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    report_path = resolve_repo_path(job.get("report_path", ""))
    if not report_path.exists() or not report_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    report_content = report_path.read_text(encoding="utf-8")
    job_assets_root = get_job_assets_root(job_id)
    normalized_parts = [part for part in Path(file_path).parts if part not in {".", ""}]
    asset_rel_path = Path(*normalized_parts) if normalized_parts else Path(file_path)

    candidate_paths = [job_assets_root / asset_rel_path]
    if normalized_parts and normalized_parts[0] == "assets":
        candidate_paths.insert(0, job_assets_root.parent / asset_rel_path)

    asset_base = _extract_job_asset_base(report_content)
    if asset_base and normalized_parts and normalized_parts[0] != asset_base:
        candidate_paths.append(job_assets_root / asset_base / asset_rel_path)

    for asset_path in candidate_paths:
        if asset_path.exists() and asset_path.is_file():
            try:
                asset_path.resolve().relative_to(job_assets_root.parent.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="Access denied") from exc
            return FileResponse(str(asset_path))

    raise HTTPException(status_code=404, detail="Asset not found")
