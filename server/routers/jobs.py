"""Job management API with WebSocket log streaming."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse

from server.deps import get_db
from server.job_manager import JobManager
from server.job_summaries import (
    DEFAULT_JOB_SUMMARY_LIMIT,
    build_job_report_summaries,
    build_profile_name_map,
    enrich_job_with_artifact_readiness,
)
from server.schemas import (
    JobCreate,
    JobDeleteResponse,
    JobForceStopPurgeResponse,
    JobListResponse,
    JobReportListResponse,
    JobResponse,
    KeywordGroup,
    StatsResponse,
    TopicKeywordSuggestRequest,
    TopicKeywordSuggestResponse,
)
from server.ws import get_log_handler
from utils.config import (
    load_config,
    resolve_model,
)
from utils.llm import call_llm_fallback
from utils.logger import get_logger
from utils.memory import MemoryManager
from utils.repo_paths import resolve_repo_path
from utils.runtime_access import ensure_runtime_access_allowed_for_request

router = APIRouter(tags=["jobs"])
log = get_logger(__name__)

_manager: JobManager | None = None

_KEYWORD_GROUPS = [
    ("Core Tasks", "core_tasks"),
    ("Core Problems", "core_problems"),
    ("Representative Models", "representative_models"),
]

_JSON_JUNK_RE = re.compile(r'[{}\[\]"\\]')
_JSON_OBJECT_FORMAT: dict[str, Any] = {"type": "json_object"}

def _get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager(get_db())
    return _manager


def _ensure_runtime_settings_available() -> None:
    ok, detail = ensure_runtime_access_allowed_for_request()
    if not ok:
        raise HTTPException(status_code=400, detail=detail)


def _normalize_keyword(keyword: str) -> str:
    return " ".join(keyword.split()).strip()


def _dedupe_keywords(
    keywords: list[str],
    *,
    limit: int | None = None,
    excluded: set[str] | None = None,
) -> list[str]:
    seen = set(excluded or set())
    merged: list[str] = []

    for keyword in keywords:
        normalized = _normalize_keyword(keyword)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
        if limit is not None and len(merged) >= limit:
            break

    return merged


def _strip_code_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(raw_text)
    attempts = [cleaned]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        attempts.append(cleaned[start : end + 1])

    last_error: Exception | None = None
    for attempt in attempts:
        try:
            payload = json.loads(attempt)
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            last_error = exc

    raise ValueError("未找到合法 JSON 对象") from last_error


def _looks_like_keyword(text: str) -> bool:
    if len(text) < 2:
        return False
    if len(text.split()) > 6:
        return False
    junk_ratio = len(_JSON_JUNK_RE.findall(text)) / max(len(text), 1)
    if junk_ratio > 0.15:
        return False
    if text.startswith(("{", "[", '"')) or text.endswith(("}", "]")):
        return False
    return True


def _clean_keyword_list(
    raw: Any,
    *,
    limit: int,
    excluded: set[str],
) -> list[str]:
    if not isinstance(raw, list):
        return []
    items = [_normalize_keyword(str(item)).strip("\"'`") for item in raw]
    items = [item for item in items if _looks_like_keyword(item)]
    return _dedupe_keywords(items, limit=limit, excluded=excluded)


def _parse_grouped_response(
    raw_text: str,
    *,
    max_per_group: int,
    existing_keywords: list[str],
) -> list[KeywordGroup]:
    payload = _extract_json_object(raw_text)
    excluded = {kw.casefold() for kw in existing_keywords}

    groups: list[KeywordGroup] = []
    for label, json_key in _KEYWORD_GROUPS:
        raw_list = payload.get(json_key, [])
        keywords = _clean_keyword_list(raw_list, limit=max_per_group, excluded=excluded)
        excluded.update(kw.casefold() for kw in keywords)
        groups.append(KeywordGroup(label=label, keywords=keywords))

    return groups


def _build_keyword_suggestion_messages(
    *,
    name: str,
    query: str,
    existing_keywords: list[str],
    max_per_group: int,
) -> list[dict[str, str]]:
    existing_text = ", ".join(existing_keywords) if existing_keywords else "(none)"
    group_desc = ", ".join(f'"{key}"' for _, key in _KEYWORD_GROUPS)
    return [
        {
            "role": "system",
            "content": (
                "You are a research keyword generator for academic paper search. "
                "Given a research topic, output ONLY a JSON object with exactly three keys: "
                f"{group_desc}. "
                "Each key maps to an array of short English keyword phrases (1-5 words each). "
                "\n\n"
                "Rules for each group:\n"
                '- "core_tasks": Standard task names that describe what the research does '
                '(e.g. "long-term time series forecasting", "multivariate regression"). '
                "Focus on established, commonly searched task descriptions.\n"
                '- "core_problems": Key technical problems, mechanisms, or concepts the research addresses '
                '(e.g. "channel dependency", "cross-variable interaction", "temporal pattern extraction"). '
                "Focus on the specific technical challenges, not generic terms.\n"
                '- "representative_models": Well-known model or method names strongly associated with this topic '
                '(e.g. "PatchTST", "iTransformer", "Autoformer"). '
                "Only include real, established model names — do NOT invent names.\n"
                "\n"
                "General rules:\n"
                "- Each group should have at most {max} keywords.\n"
                "- Avoid generic terms like AI, deep learning, model, paper, neural network.\n"
                "- Avoid keywords that overlap with the existing keywords listed below.\n"
                "- Keep keywords search-friendly: use terms that commonly appear in paper titles/abstracts.\n"
                "- Do NOT include any explanation or text outside the JSON object.\n"
                "- If you are unsure about a group, return an empty array for that group."
            ).replace("{max}", str(max_per_group)),
        },
        {
            "role": "user",
            "content": (
                f"Topic name: {name or '(empty)'}\n"
                f"Topic description: {query or '(empty)'}\n"
                f"Keywords already chosen (do NOT repeat): {existing_text}\n"
                f"Return at most {max_per_group} keywords per group.\n"
                "Respond with ONLY the JSON object."
            ),
        },
    ]


async def _persist_manual_upload(file: UploadFile) -> tuple[Path, str]:
    filename = Path(file.filename or "manual-upload.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    if file.content_type and file.content_type not in {
        "application/pdf",
        "application/x-pdf",
    }:
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF")

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            temp_file.write(chunk)

    if temp_path.stat().st_size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

    return temp_path, filename


@router.post("/jobs", response_model=JobResponse)
async def create_job(body: JobCreate):
    _ensure_runtime_settings_available()
    manager = _get_manager()
    profile_mode = str(body.profile_mode or "auto").strip().lower() or "auto"
    if profile_mode not in {"auto", "explicit"}:
        raise HTTPException(status_code=400, detail="Unsupported profile_mode")
    if profile_mode == "explicit" and body.profile_id is None:
        raise HTTPException(
            status_code=400, detail="profile_id is required for explicit profile_mode"
        )
    if profile_mode == "auto" and body.profile_id is not None:
        raise HTTPException(
            status_code=400, detail="profile_id must be empty for auto profile_mode"
        )
    config_override = (
        body.config_override.model_dump() if body.config_override else None
    )
    job = await manager.create_and_run(
        profile_id=body.profile_id,
        profile_mode=profile_mode,
        config_override=config_override,
        replace_job_id=body.replace_job_id,
    )
    return enrich_job_with_artifact_readiness(job)


@router.post("/jobs/manual", response_model=JobResponse)
async def create_manual_job(
    file: UploadFile = File(...),
    profile_id: int | None = Form(default=None),
    profile_mode: str | None = Form(default="auto"),
    config_override_json: str | None = Form(default=None),
    replace_job_id: str | None = Form(default=None),
):
    _ensure_runtime_settings_available()
    temp_path, original_name = await _persist_manual_upload(file)
    manager = _get_manager()
    config_override: dict[str, Any] | None = None
    if config_override_json and config_override_json.strip():
        try:
            payload = json.loads(config_override_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail="config_override_json must be valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="config_override_json must be a JSON object"
            )
        config_override = payload
    normalized_profile_mode = str(profile_mode or "auto").strip().lower() or "auto"
    if normalized_profile_mode not in {"auto", "explicit"}:
        raise HTTPException(status_code=400, detail="Unsupported profile_mode")
    if normalized_profile_mode == "explicit" and profile_id is None:
        raise HTTPException(
            status_code=400, detail="profile_id is required for explicit profile_mode"
        )
    if normalized_profile_mode == "auto" and profile_id is not None:
        raise HTTPException(
            status_code=400, detail="profile_id must be empty for auto profile_mode"
        )
    try:
        job = await manager.create_and_run_manual_upload(
            source_path=temp_path,
            original_name=original_name,
            profile_id=profile_id,
            profile_mode=normalized_profile_mode,
            config_override=config_override,
            replace_job_id=replace_job_id,
        )
        return enrich_job_with_artifact_readiness(job)
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(limit: int = 50):
    db = get_db()
    return JobListResponse(
        jobs=[enrich_job_with_artifact_readiness(j) for j in db.list_jobs(limit=limit)]
    )


@router.get("/jobs/history", response_model=JobReportListResponse)
async def list_job_history(limit: int = DEFAULT_JOB_SUMMARY_LIMIT):
    db = get_db()
    jobs = db.list_jobs(limit=limit)
    reports = build_job_report_summaries(
        jobs,
        build_profile_name_map(db),
        default_profile_id=db.resolve_default_profile_id(),
    )
    return JobReportListResponse(reports=reports)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return enrich_job_with_artifact_readiness(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    manager = _get_manager()
    cancelled = await manager.cancel(job_id)
    return {"cancelled": cancelled}


@router.post("/jobs/{job_id}/force-stop", response_model=JobForceStopPurgeResponse)
async def force_stop_job(job_id: str):
    manager = _get_manager()
    result = await manager.force_stop_and_purge(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.delete("/jobs/{job_id}", response_model=JobDeleteResponse)
async def delete_job(job_id: str):
    manager = _get_manager()
    result = await manager.delete_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
async def retry_job(job_id: str):
    _ensure_runtime_settings_available()
    db = get_db()
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")
    manager = _get_manager()
    if manager.has_live_task(job_id):
        raise HTTPException(status_code=409, detail="Job already has a running task")
    retried = await manager.retry_failed_job(job_id)
    return enrich_job_with_artifact_readiness(retried)


@router.post("/jobs/{job_id}/rerun", response_model=JobResponse)
async def rerun_job(job_id: str):
    _ensure_runtime_settings_available()
    db = get_db()
    source_job = db.get_job(job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    paper = db.get_paper_for_job(
        job_id, report_path=str(source_job.get("report_path", ""))
    )
    if paper is None:
        raise HTTPException(
            status_code=404, detail="No paper record found for this job"
        )

    raw_source_path = str(
        paper.get("source_path") or paper.get("pdf_path", "")
    ).strip()
    if not raw_source_path:
        raise HTTPException(
            status_code=404, detail="Source path not found for this report"
        )

    resolved_source = resolve_repo_path(raw_source_path)
    if not resolved_source.exists() or not resolved_source.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")

    manager = _get_manager()
    snapshot = source_job.get("config_snapshot")
    config_snapshot = snapshot if isinstance(snapshot, dict) else None
    rerun = await manager.create_and_run_existing_source(
        source_path=resolved_source,
        original_name=resolved_source.name,
        source_type=str(paper.get("source_type") or "pdf"),
        profile_id=source_job.get("profile_id")
        if isinstance(source_job.get("profile_id"), int)
        else None,
        profile_mode=str(source_job.get("profile_mode") or "auto"),
        config_snapshot=config_snapshot,
        replace_job_id=job_id,
    )
    return enrich_job_with_artifact_readiness(rerun)


@router.post("/topics/keyword-candidates", response_model=TopicKeywordSuggestResponse)
async def suggest_topic_keywords(body: TopicKeywordSuggestRequest):
    _ensure_runtime_settings_available()
    name = _normalize_keyword(body.name)
    query = _normalize_keyword(body.query)
    if not name and not query:
        raise HTTPException(status_code=400, detail="Topic name or query is required")

    existing_keywords = _dedupe_keywords([str(kw) for kw in body.existing_keywords])
    config = load_config()
    models = config.get("models", {}) if isinstance(config, dict) else {}
    model_alias = str(models.get("fast", "gem_flash") or "gem_flash").strip()
    resolved_model = resolve_model(model_alias).strip()
    if not resolved_model:
        raise HTTPException(status_code=503, detail="GEM_FLASH is not configured")

    messages = _build_keyword_suggestion_messages(
        name=name,
        query=query or name,
        existing_keywords=existing_keywords,
        max_per_group=body.max_per_group,
    )

    last_error: Exception | None = None
    for response_format in (_JSON_OBJECT_FORMAT, None):
        try:
            raw_text = await call_llm_fallback(
                [model_alias],
                messages,
                step_label="Topic 关键词生成",
                temperature=0.3,
                max_tokens=1024,
                step_timeout=60.0,
                response_format=response_format,
            )
        except Exception as exc:
            last_error = exc
            continue

        if not raw_text or not raw_text.strip():
            last_error = ValueError("Model returned empty response")
            continue

        try:
            groups = _parse_grouped_response(
                raw_text,
                max_per_group=body.max_per_group,
                existing_keywords=existing_keywords,
            )
            total = sum(len(g.keywords) for g in groups)
            if total > 0:
                return TopicKeywordSuggestResponse(groups=groups)
        except Exception as exc:
            last_error = exc
            continue

        last_error = ValueError(
            f"Could not extract keywords from model output ({len(raw_text)} chars)"
        )

    detail = "Keyword generation failed to produce usable candidates"
    if last_error is not None:
        detail = f"Keyword generation failed: {last_error}"
    raise HTTPException(status_code=502, detail=detail)


@router.websocket("/jobs/{job_id}/ws")
async def job_websocket(websocket: WebSocket, job_id: str):
    await websocket.accept()
    handler = get_log_handler()
    queue, buffered_logs = handler.subscribe(job_id)

    try:
        db = get_db()
        manager = _get_manager()
        job = db.get_job(job_id)
        if job is None:
            await websocket.send_json(
                {"type": "done", "job": None, "reason": "not_found"}
            )
            return

        if job.get("status") in JobManager.TERMINAL_STATUSES:
            await websocket.send_json({"type": "state", "job": job})
            await websocket.send_json(
                {"type": "done", "job": job, "reason": "terminal"}
            )
            return

        if not manager.has_live_task(job_id):
            job = manager.finalize_stale_job(
                job_id,
                error="Recovered stale job after runtime loss",
                publish_done=False,
            )
            await websocket.send_json({"type": "state", "job": job})
            await websocket.send_json({"type": "done", "job": job, "reason": "stale"})
            return

        await websocket.send_json({"type": "state", "job": job})

        # Replay buffered log messages that were emitted before this
        # WebSocket subscriber connected.  This ensures the client sees
        # the full log history from the very start of the pipeline.
        for buffered_msg in buffered_logs:
            await websocket.send_json(buffered_msg)

        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(message)
                if message.get("type") == "done":
                    break
            except asyncio.TimeoutError:
                refreshed_job = db.get_job(job_id)
                if refreshed_job is None:
                    await websocket.send_json(
                        {"type": "done", "job": None, "reason": "not_found"}
                    )
                    break
                if refreshed_job.get("status") in JobManager.TERMINAL_STATUSES:
                    await websocket.send_json({"type": "state", "job": refreshed_job})
                    await websocket.send_json(
                        {"type": "done", "job": refreshed_job, "reason": "terminal"}
                    )
                    break
                if not manager.has_live_task(job_id):
                    refreshed_job = manager.finalize_stale_job(
                        job_id,
                        error="Recovered stale job after runtime loss",
                        publish_done=False,
                    )
                    await websocket.send_json({"type": "state", "job": refreshed_job})
                    await websocket.send_json(
                        {"type": "done", "job": refreshed_job, "reason": "stale"}
                    )
                    break
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.warning("Job websocket failed for %s", job_id, exc_info=True)
    finally:
        handler.unsubscribe(job_id, queue)


# --- Stats & Papers ---


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    db = get_db()
    stats = db.get_stats()
    mm = MemoryManager()
    try:
        stats["profiles_total"] = len(mm.list_profiles())
    finally:
        mm.close()
    return stats


@router.get("/papers")
async def list_papers(limit: int = 50, search: str = ""):
    db = get_db()
    if search:
        return db.search_papers(search, limit=limit)
    return db.list_papers(limit=limit)


@router.get("/papers/{paper_row_id}/pdf")
async def get_paper_pdf(paper_row_id: int):
    db = get_db()
    paper = db.get_paper_by_db_id(paper_row_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    raw_source_path = str(
        paper.get("source_path") or paper.get("pdf_path", "")
    ).strip()
    if not raw_source_path:
        raise HTTPException(status_code=404, detail="Source path not found")

    resolved_source = resolve_repo_path(raw_source_path)
    if not resolved_source.exists() or not resolved_source.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")

    return FileResponse(
        str(resolved_source),
        media_type="application/pdf"
        if str(paper.get("source_type") or "pdf") == "pdf"
        else "text/html; charset=utf-8",
        headers={"Content-Disposition": "inline"},
    )
