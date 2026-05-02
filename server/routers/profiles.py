"""Memory profile CRUD and overview API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from server.deps import get_db
from server.routers.jobs import _get_manager
from server.routers.memory_workspace import _ensure_profile
from server.schemas import (
    LivingSurveyResponse,
    ProfileBriefResponse,
    ProfileCreate,
    ProfileDeleteResponse,
    ProfileDetailResponse,
    ProfileJobMemoryDeleteResponse,
    ProfileMemoryRebuildResponse,
    ProfilePaperMoveRequest,
    ProfilePaperMoveResponse,
    ProfilePaperMemoryDeleteResponse,
    ProfileResponse,
)
from utils.memory import MemoryManager

router = APIRouter(tags=["profiles"])


def _get_mm() -> MemoryManager:
    return MemoryManager()


@router.get("/profiles", response_model=list[ProfileResponse])
async def list_profiles():
    mm = _get_mm()
    try:
        return mm.list_profiles()
    finally:
        mm.close()


@router.post("/profiles", response_model=ProfileResponse)
async def create_profile(body: ProfileCreate):
    mm = _get_mm()
    try:
        return mm.create_profile(body.name, body.description)
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}", response_model=ProfileDeleteResponse)
async def delete_profile(profile_id: int):
    mm = _get_mm()
    db = get_db()
    manager = _get_manager()
    try:
        profile = _ensure_profile(mm, profile_id)
        if str(profile.get("name", "")).strip().lower() == "default":
            raise HTTPException(
                status_code=409, detail="The default profile cannot be deleted"
            )

        active_jobs = db.list_active_jobs_for_profile(profile_id)
        if active_jobs:
            blocked_job_ids = [str(item.get("id") or "") for item in active_jobs]
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete profile while jobs are still active: "
                    + ", ".join(blocked_job_ids)
                ),
            )

        purge_results: list[dict[str, Any]] = []
        for job in db.list_jobs_for_profile(profile_id):
            job_id = str(job.get("id") or "").strip()
            if not job_id:
                continue
            purged = manager.purge_inactive_job(job_id)
            if purged is not None:
                purge_results.append(purged)

        delete_result = mm.delete_profile(profile_id)
        return {
            "profile_id": profile_id,
            "deleted_profile": bool(delete_result.get("deleted_profile")),
            "purged_job_count": len(purge_results),
            "deleted_report_count": sum(
                1 for job in purge_results if job.get("results_dir_removed")
            ),
            "deleted_paper_record_count": sum(
                1 for job in purge_results if job.get("paper_record_deleted")
            ),
            "deleted_writeback_count": int(
                delete_result.get("deleted_writeback_count", 0) or 0
            ),
            "results_dirs_removed": sum(
                1 for job in purge_results if job.get("results_dir_removed")
            ),
            "fetch_dirs_removed": sum(
                1 for job in purge_results if job.get("fetch_dir_removed")
            ),
            "cache_dirs_removed": sum(
                1 for job in purge_results if job.get("cache_dir_removed")
            ),
            "blocked_active_job_ids": [],
        }
    finally:
        mm.close()


@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: int):
    mm = _get_mm()
    try:
        profile = mm.get_profile_by_id(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/brief", response_model=ProfileBriefResponse)
async def get_profile_brief(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_brief(profile_id)
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/detail", response_model=ProfileDetailResponse)
async def get_profile_detail(
    profile_id: int,
    activity_limit: int = 30,
    memory_limit: int = 20,
    links_limit: int = 20,
):
    mm = _get_mm()
    db = get_db()
    try:
        profile = _ensure_profile(mm, profile_id)
        brief = mm.get_or_build_brief(profile_id)
        overview = mm.get_workspace_overview(profile_id)
        curated = mm.get_workspace_curated(profile_id)
        theme_preview = mm.get_or_build_theme_snapshot(profile_id).get("items", [])[:4]
        gap_preview = mm.get_or_build_gap_snapshot(profile_id).get("items", [])[:4]
        opportunity_preview = mm.get_or_build_opportunity_snapshot(profile_id).get(
            "items", []
        )[:4]
        health = mm.get_or_build_memory_health(profile_id)
        field_map_preview = mm.get_or_build_field_map(profile_id).get("clusters", [])[:3]
        return {
            "profile": profile,
            "overview": overview,
            "brief": brief,
            "theme_preview": theme_preview,
            "gap_preview": gap_preview,
            "opportunity_preview": opportunity_preview,
            "health": health,
            "field_map_preview": field_map_preview,
            "survey_meta": mm.get_artifact_meta(profile_id, "living_survey"),
            "knowledge": mm.query_domain_knowledge(profile_id, top_k=memory_limit),
            "curated_digest": curated.get("domain_digest", []),
            "style": mm.get_style_preferences(profile_id),
            "links": mm.query_paper_links(profile_id, limit=links_limit),
            "activity": db.list_profile_activity(profile_id, limit=activity_limit),
        }
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/rebuild", response_model=ProfileMemoryRebuildResponse
)
async def rebuild_profile_memory(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        result = mm.rebuild_profile_cognition(profile_id)
        return {"profile_id": profile_id, **result}
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/move-papers", response_model=ProfilePaperMoveResponse
)
async def move_profile_papers(profile_id: int, body: ProfilePaperMoveRequest):
    mm = _get_mm()
    db = get_db()
    try:
        source_profile = _ensure_profile(mm, profile_id)
        target_profile = _ensure_profile(mm, body.target_profile_id)
        if int(profile_id) == int(body.target_profile_id):
            raise HTTPException(
                status_code=400, detail="Source and target profiles must be different"
            )

        resolved_job_ids = [
            str(item).strip() for item in body.job_ids if str(item).strip()
        ]
        if not resolved_job_ids:
            raise HTTPException(status_code=400, detail="No jobs were selected")

        blocked_job_ids: list[str] = []
        for job_id in resolved_job_ids:
            job = db.get_job(job_id)
            if job is None:
                continue
            if str(job.get("status") or "").strip() not in {"completed", "failed"}:
                blocked_job_ids.append(job_id)
        if blocked_job_ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot move jobs that are still active: "
                    + ", ".join(blocked_job_ids)
                ),
            )

        try:
            result = mm.move_job_memories(
                profile_id,
                body.target_profile_id,
                resolved_job_ids,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

        move_note = (
            f"Manually moved from profile '{source_profile['name']}' "
            f"to '{target_profile['name']}'."
        )
        for job_id in resolved_job_ids:
            db.update_job(
                job_id,
                profile_id=body.target_profile_id,
                profile_mode="explicit",
                profile_assignment_status="manual",
                profile_assignment_note=move_note,
            )
        return result
    finally:
        mm.close()


@router.delete(
    "/profiles/{profile_id}/jobs/{job_id}/memory",
    response_model=ProfileJobMemoryDeleteResponse,
)
async def delete_profile_job_memory(profile_id: int, job_id: str):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        deleted = mm.delete_job_memories(profile_id, job_id)
        if deleted is None:
            raise HTTPException(
                status_code=404,
                detail="Memory for this job was not found in the selected profile",
            )
        return deleted
    finally:
        mm.close()


@router.delete(
    "/profiles/{profile_id}/papers/{paper_id:path}/memory",
    response_model=ProfilePaperMemoryDeleteResponse,
)
async def delete_profile_paper_memory(profile_id: int, paper_id: str):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        deleted = mm.delete_paper_memories(profile_id, paper_id)
        if deleted is None:
            raise HTTPException(
                status_code=404,
                detail="Memory for this paper was not found in the selected profile",
            )
        return deleted
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/survey", response_model=LivingSurveyResponse)
async def get_profile_survey(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_living_survey(profile_id)
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/knowledge")
async def get_knowledge(profile_id: int, keywords: str = "", top_k: int = 20):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        kw_list = (
            [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
        )
        return mm.query_domain_knowledge(profile_id, keywords=kw_list, top_k=top_k)
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/links")
async def get_links(profile_id: int, paper_id: str = "", limit: int = 20):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        links = list(mm.query_paper_links(profile_id, paper_id=paper_id, limit=limit))
        if paper_id:
            lowered = paper_id.lower()
            links = [
                item
                for item in links
                if lowered in str(item.get("source_paper_id", "")).lower()
                or lowered in str(item.get("target_paper_id", "")).lower()
            ]
        return links[:limit]
    finally:
        mm.close()


@router.get("/profiles/{profile_id}/style")
async def get_style(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_style_preferences(profile_id)
    finally:
        mm.close()
