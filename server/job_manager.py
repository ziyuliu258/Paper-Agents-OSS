"""Async job manager: runs pipeline tasks in background, broadcasts progress."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from modules.paper_selector.agent import SelectionFailure
from server.database import Database
from server.job_summaries import enrich_job_with_artifact_readiness
from server.ws import get_log_handler, reset_log_job_context, set_log_job_context
from utils.config import load_config, load_runtime_config, runtime_config_override
from utils.llm import (
    cleanup_r2_job_artifacts,
    invalidate_r2_cache,
    prepare_r2_job_run,
    reset_r2_job_context,
    set_r2_job_context,
)
from utils.job_paths import (
    get_job_assets_dir,
    get_job_cache_dir,
    get_job_fetch_dir,
    get_job_pdf_path,
    get_job_report_path,
    get_job_results_dir,
)
from utils.logger import get_logger
from utils.memory import MemoryManager
from utils.profile_assignment import suggest_profile_for_topics
from utils.repo_paths import resolve_repo_path, to_repo_relative_path
from utils.topic_enrichment import enrich_topics_for_search

log = get_logger(__name__)


class JobManager:
    TERMINAL_STATUSES = {"completed", "failed"}
    FORCE_STOP_TIMEOUT_SECONDS = 10.0

    def __init__(self, db: Database) -> None:
        self._db = db
        self._tasks: dict[str, asyncio.Task] = {}
        self._purged_jobs: set[str] = set()

    def _resolve_profile_mode(
        self, profile_id: int | None, profile_mode: str | None
    ) -> str:
        normalized = str(profile_mode or "").strip().lower()
        if isinstance(profile_id, int):
            return "explicit"
        if normalized == "explicit":
            return "explicit"
        return "auto"

    async def _enrich_config_topics(self, config: dict[str, Any]) -> dict[str, Any]:
        topics = config.get("topics")
        if not isinstance(topics, list) or not topics:
            return config
        enriched = await enrich_topics_for_search(
            topics,
            model_alias=str(config.get("models", {}).get("fast") or "gem_flash"),
        )
        next_config = copy.deepcopy(config)
        next_config["topics"] = enriched
        return next_config

    def _initial_profile_assignment(
        self, *, profile_mode: str, profile_id: int | None
    ) -> tuple[str, str]:
        if profile_mode == "explicit" and isinstance(profile_id, int):
            return "explicit", "Using the explicitly selected profile."
        return "pending", "Auto assign is waiting for enough paper context."

    def _spawn_pipeline_task(
        self,
        *,
        job_id: str,
        config: dict[str, Any],
        mode: str,
        source_path: str | None,
        profile_id: int | None,
        profile_mode: str,
        replace_job_id: str | None,
    ) -> None:
        # Start buffering logs *before* spawning the task so that every
        # message emitted from the very first line of ``_run_pipeline`` is
        # captured even if no WebSocket subscriber has connected yet.
        get_log_handler().start_buffering(job_id)
        prepare_r2_job_run(job_id)

        runtime_config_snapshot = copy.deepcopy(load_runtime_config())

        async def _run_with_runtime_snapshot() -> None:
            # Keep runtime credentials/config stable for the full background task.
            with runtime_config_override(runtime_config_snapshot):
                await self._run_pipeline(
                    job_id,
                    config,
                    mode,
                    source_path,
                    profile_id,
                    profile_mode,
                    replace_job_id,
                )

        task = asyncio.create_task(_run_with_runtime_snapshot())
        self._tasks[job_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(job_id, None))

    def _create_manual_job(
        self,
        *,
        source_path: Path,
        original_name: str,
        source_type: str = "pdf",
        validate_pdf: bool = True,
        profile_id: int | None,
        profile_mode: str,
        config: dict[str, Any],
        replace_job_id: str | None,
    ) -> dict[str, Any]:
        resolved_profile_mode = self._resolve_profile_mode(profile_id, profile_mode)
        assignment_status, assignment_note = self._initial_profile_assignment(
            profile_mode=resolved_profile_mode, profile_id=profile_id
        )
        job = self._db.create_job(
            mode="manual",
            profile_id=profile_id,
            profile_mode=resolved_profile_mode,
            profile_assignment_status=assignment_status,
            profile_assignment_note=assignment_note,
            config_snapshot=config,
        )
        job_id = job["id"]
        persisted_source = self._prepare_job_source(
            job_id=job_id,
            source_file=source_path,
            original_name=original_name,
            source_type=source_type,
            validate_pdf=validate_pdf,
        )
        self._spawn_pipeline_task(
            job_id=job_id,
            config=config,
            mode="manual",
            source_path=str(persisted_source),
            profile_id=profile_id,
            profile_mode=resolved_profile_mode,
            replace_job_id=replace_job_id,
        )
        return job

    def _publish_job_state(self, job_id: str) -> None:
        if job_id in self._purged_jobs:
            return
        job = self._db.get_job(job_id)
        if job is None:
            return
        get_log_handler().publish_state(job_id, enrich_job_with_artifact_readiness(job))

    def _update_job_and_publish(self, job_id: str, **fields: Any) -> None:
        if job_id in self._purged_jobs:
            return
        self._db.update_job(job_id, **fields)
        self._publish_job_state(job_id)

    def _ensure_job_not_purged(self, job_id: str) -> None:
        if job_id in self._purged_jobs:
            raise asyncio.CancelledError

    async def create_and_run(
        self,
        *,
        profile_id: int | None = None,
        profile_mode: str = "auto",
        config_override: dict[str, Any] | None = None,
        replace_job_id: str | None = None,
    ) -> dict[str, Any]:
        config = load_config()
        if config_override:
            for key, value in config_override.items():
                if isinstance(value, dict) and isinstance(config.get(key), dict):
                    config[key].update(value)
                else:
                    config[key] = value
        config = await self._enrich_config_topics(config)

        resolved_profile_mode = self._resolve_profile_mode(profile_id, profile_mode)
        assignment_status, assignment_note = self._initial_profile_assignment(
            profile_mode=resolved_profile_mode, profile_id=profile_id
        )
        job = self._db.create_job(
            mode="auto",
            profile_id=profile_id,
            profile_mode=resolved_profile_mode,
            profile_assignment_status=assignment_status,
            profile_assignment_note=assignment_note,
            config_snapshot=config,
        )
        job_id = job["id"]

        self._spawn_pipeline_task(
            job_id=job_id,
            config=config,
            mode="auto",
            source_path=None,
            profile_id=profile_id,
            profile_mode=resolved_profile_mode,
            replace_job_id=replace_job_id,
        )

        return job

    async def create_and_run_manual_upload(
        self,
        *,
        source_path: Path,
        original_name: str,
        profile_id: int | None = None,
        profile_mode: str = "auto",
        config_override: dict[str, Any] | None = None,
        replace_job_id: str | None = None,
    ) -> dict[str, Any]:
        config = load_config()
        if config_override:
            for key, value in config_override.items():
                if isinstance(value, dict) and isinstance(config.get(key), dict):
                    config[key].update(value)
                else:
                    config[key] = value
        config = await self._enrich_config_topics(config)
        return self._create_manual_job(
            source_path=source_path,
            original_name=original_name,
            source_type="pdf",
            validate_pdf=True,
            profile_id=profile_id,
            profile_mode=profile_mode,
            config=config,
            replace_job_id=replace_job_id,
        )

    async def create_and_run_existing_source(
        self,
        *,
        source_path: Path,
        original_name: str,
        source_type: str = "pdf",
        profile_id: int | None = None,
        profile_mode: str = "auto",
        config_snapshot: dict[str, Any] | None = None,
        replace_job_id: str | None = None,
    ) -> dict[str, Any]:
        config = copy.deepcopy(config_snapshot) if config_snapshot else load_config()
        config = await self._enrich_config_topics(config)
        return self._create_manual_job(
            source_path=source_path,
            original_name=original_name,
            source_type=source_type,
            validate_pdf=source_type == "pdf",
            profile_id=profile_id,
            profile_mode=profile_mode,
            config=config,
            replace_job_id=replace_job_id,
        )

    async def retry_failed_job(self, job_id: str) -> dict[str, Any]:
        job = self._db.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.get("status") != "failed":
            raise ValueError(f"Job {job_id} is not in failed state")

        config = copy.deepcopy(job.get("config_snapshot")) or load_config()
        config = await self._enrich_config_topics(config)
        original_mode = job.get("mode", "auto")
        profile_id = (
            job.get("profile_id") if isinstance(job.get("profile_id"), int) else None
        )
        profile_mode = str(job.get("profile_mode") or "auto")

        report_path = str(job.get("report_path", "")).strip()
        if report_path:
            resolved = resolve_repo_path(report_path)
            if resolved.exists() and resolved.is_file():
                resolved.unlink(missing_ok=True)
        results_dir = get_job_results_dir(job_id)
        if results_dir.exists():
            shutil.rmtree(results_dir, ignore_errors=True)
        job_cache_dir = get_job_cache_dir(job_id)
        if job_cache_dir.exists():
            shutil.rmtree(job_cache_dir, ignore_errors=True)
        self._db.delete_papers_for_job(job_id)

        self._update_job_and_publish(
            job_id,
            status="pending",
            progress=0,
            current_step="",
            error=None,
            report_path="",
            paper_title="",
            started_at=None,
            completed_at=None,
        )

        fetch_dir = get_job_fetch_dir(job_id)
        existing_pdf: Path | None = None
        if fetch_dir.exists():
            source_files = [path for path in fetch_dir.iterdir() if path.is_file()]
            if source_files:
                existing_pdf = source_files[0]

        if existing_pdf:
            mode = "manual"
            source_path: str | None = str(existing_pdf)
        elif original_mode == "auto":
            mode = "auto"
            source_path = None
        else:
            raise ValueError("Manual job has no source document available for retry")

        self._spawn_pipeline_task(
            job_id=job_id,
            config=config,
            mode=mode,
            source_path=source_path,
            profile_id=profile_id,
            profile_mode=profile_mode,
            replace_job_id=None,
        )

        log.info(
            "Job %s retried in-place (mode=%s, has_source=%s)",
            job_id,
            mode,
            existing_pdf is not None,
        )
        return self._db.get_job(job_id)  # type: ignore[return-value]

    def has_live_task(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return bool(task and not task.done())

    def finalize_stale_job(
        self, job_id: str, *, error: str, publish_done: bool = False
    ) -> dict[str, Any] | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None
        if job.get("status") in self.TERMINAL_STATUSES:
            return job
        self._update_job_and_publish(
            job_id,
            status="failed",
            error=error,
            completed_at=time.time(),
        )
        final_job = self._db.get_job(job_id)
        if publish_done and final_job is not None:
            get_log_handler().publish_done(
                job_id, enrich_job_with_artifact_readiness(final_job)
            )
        return final_job

    def reconcile_orphaned_jobs(self) -> int:
        fixed = 0
        for job in self._db.list_active_jobs():
            if self.has_live_task(str(job.get("id", ""))):
                continue
            finalized = self.finalize_stale_job(
                str(job["id"]),
                error="Server restarted before job completion",
                publish_done=False,
            )
            if finalized is not None:
                fixed += 1
        return fixed

    async def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            self._update_job_and_publish(
                job_id,
                status="failed",
                error="Cancelled by user",
                completed_at=time.time(),
            )
            # Cancel may interrupt an R2 upload mid-flight.
            invalidate_r2_cache()
            return True

        job = self._db.get_job(job_id)
        if job and job.get("status") not in self.TERMINAL_STATUSES:
            self.finalize_stale_job(
                job_id,
                error="Cancelled stale job after runtime loss",
                publish_done=True,
            )
            return True
        return False

    def _delete_job_artifacts(self, job_id: str) -> dict[str, bool]:
        removed: dict[str, bool] = {}
        artifact_dirs = {
            "results_dir_removed": get_job_results_dir(job_id),
            "fetch_dir_removed": get_job_fetch_dir(job_id),
            "cache_dir_removed": get_job_cache_dir(job_id),
        }
        for key, path in artifact_dirs.items():
            if not path.exists():
                removed[key] = False
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed[key] = not path.exists()
        return removed

    def _write_selector_diagnostics(self, job_id: str, diagnostics: dict[str, Any]) -> None:
        if not diagnostics:
            return
        results_dir = get_job_results_dir(job_id)
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "selector_diagnostics.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _purge_job_state(
        self, job_id: str, *, job: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resolved_job = job or self._db.get_job(job_id)
        profile_id = (
            int(resolved_job["profile_id"])
            if resolved_job is not None
            and isinstance(resolved_job.get("profile_id"), int)
            else None
        )
        paper_record = self._db.get_paper_for_job(job_id)
        memory_deleted: dict[str, Any] | None = None
        if profile_id is not None:
            mm = MemoryManager()
            try:
                memory_deleted = mm.delete_job_memories(profile_id, job_id)
            finally:
                mm.close()

        artifact_state = self._delete_job_artifacts(job_id)
        cleanup_r2_job_artifacts(job_id)
        self._db.delete_papers_for_job(job_id)
        if resolved_job is not None:
            self._db.delete_job(job_id)

        return {
            "job_id": job_id,
            "profile_id": profile_id,
            "job_deleted": resolved_job is not None,
            "paper_record_deleted": paper_record is not None,
            "memory_deleted": memory_deleted is not None,
            "memory_delete_summary": memory_deleted,
            **artifact_state,
        }

    async def _stop_job_for_purge(
        self, job_id: str, *, job: dict[str, Any], stale_error: str
    ) -> dict[str, bool]:
        self._purged_jobs.add(job_id)
        task = self._tasks.get(job_id)
        task_cancel_requested = False
        task_cancel_timed_out = False
        running_job_stopped = False

        if task and not task.done():
            running_job_stopped = True
            task_cancel_requested = True
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=self.FORCE_STOP_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                task_cancel_timed_out = True
                log.warning(
                    "Job delete timed out while waiting for job %s to acknowledge cancellation",
                    job_id,
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                log.warning(
                    "Job delete observed task exception for job %s: %s", job_id, exc
                )
        elif job.get("status") not in self.TERMINAL_STATUSES:
            running_job_stopped = True
            self.finalize_stale_job(job_id, error=stale_error, publish_done=False)

        return {
            "running_job_stopped": running_job_stopped,
            "task_cancel_requested": task_cancel_requested,
            "task_cancel_timed_out": task_cancel_timed_out,
        }

    async def delete_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None

        stop_result = await self._stop_job_for_purge(
            job_id,
            job=job,
            stale_error="Deleted by user",
        )
        purge_result = self._purge_job_state(job_id, job=job)
        # Invalidate R2 URL cache so the next run re-uploads fresh PDFs
        # rather than reusing potentially stale CDN URLs.
        invalidate_r2_cache()
        get_log_handler().publish_done(job_id, None)
        log.info("Job %s deleted and purged", job_id)
        return {**purge_result, **stop_result}

    async def force_stop_and_purge(self, job_id: str) -> dict[str, Any] | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None

        stop_result = await self._stop_job_for_purge(
            job_id,
            job=job,
            stale_error="Force stopped by user",
        )

        purge_result = self._purge_job_state(job_id, job=job)
        # Force-stop may interrupt an in-flight R2 upload, leaving a
        # corrupted object.  Clear the cache so subsequent runs always
        # re-upload with a fresh key.
        invalidate_r2_cache()
        get_log_handler().publish_done(job_id, None)
        log.info("Job %s force-stopped and purged", job_id)
        return {
            **purge_result,
            "force_stopped": stop_result["running_job_stopped"],
            "task_cancel_requested": stop_result["task_cancel_requested"],
            "task_cancel_timed_out": stop_result["task_cancel_timed_out"],
        }

    def purge_inactive_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None
        if str(job.get("status") or "") not in self.TERMINAL_STATUSES:
            raise ValueError("Only terminal jobs can be purged without force stop")
        return self._purge_job_state(job_id, job=job)

    async def _run_pipeline(
        self,
        job_id: str,
        config: dict[str, Any],
        mode: str,
        source_path: str | None,
        profile_id: int | None,
        profile_mode: str,
        replace_job_id: str | None = None,
    ) -> None:
        handler = get_log_handler()
        log_context_token = set_log_job_context(job_id)
        r2_context_token = set_r2_job_context(job_id)

        job_results_dir = get_job_results_dir(job_id)
        job_report_path = get_job_report_path(job_id)
        job_fetch_dir = get_job_fetch_dir(job_id)
        job_cache_dir = get_job_cache_dir(job_id)
        config.setdefault("storage", {})["fetch_dir"] = to_repo_relative_path(
            job_fetch_dir
        )
        config.setdefault("storage", {})["cache_dir"] = to_repo_relative_path(
            job_cache_dir
        )

        self._update_job_and_publish(
            job_id,
            status="selecting",
            started_at=time.time(),
            progress=5,
            current_step="Initializing...",
        )

        try:
            self._ensure_job_not_purged(job_id)
            # Phase 0: Get source document
            if mode == "manual":
                if not source_path:
                    raise ValueError("Manual mode requires a source document")
                manual_source = resolve_repo_path(source_path)
                if not manual_source.exists() or not manual_source.is_file():
                    raise FileNotFoundError(
                        f"Manual source document not found: {manual_source}"
                    )
                source_type = (
                    "pdf" if manual_source.suffix.lower() == ".pdf" else "html"
                )
                paper_meta = {
                    "pdf_path": to_repo_relative_path(manual_source)
                    if source_type == "pdf"
                    else "",
                    "source_path": to_repo_relative_path(manual_source),
                    "source_type": source_type,
                    "paper_id": manual_source.stem,
                    "title": "",
                }
                self._update_job_and_publish(
                    job_id,
                    progress=15,
                    current_step="Saved manual source document into job folder",
                )
            else:
                self._update_job_and_publish(
                    job_id,
                    status="selecting",
                    progress=5,
                    current_step="Searching papers...",
                )
                from modules.paper_selector.agent import PaperSelectorAgent

                selector_profile_id = profile_id
                if profile_mode == "auto" and selector_profile_id is None:
                    mm = MemoryManager()
                    try:
                        soft_route = suggest_profile_for_topics(
                            mm,
                            self._db,
                            config.get("topics", []),
                        )
                    finally:
                        mm.close()
                    matched_profile = soft_route.get("matched_profile")
                    if isinstance(matched_profile, dict):
                        selector_profile_id = int(matched_profile["profile_id"])
                        self._update_job_and_publish(
                            job_id,
                            profile_assignment_note=(
                                f"Soft-routed selector memory from profile "
                                f"'{matched_profile['profile_name']}'. Final auto assignment will happen after paper_notes."
                            ),
                        )

                selector = PaperSelectorAgent(config, profile_id=selector_profile_id)
                paper_meta = await selector.run()
                self._ensure_job_not_purged(job_id)
                selector_diagnostics = paper_meta.pop("selector_diagnostics", None)
                if isinstance(selector_diagnostics, dict):
                    self._write_selector_diagnostics(job_id, selector_diagnostics)
                self._update_job_and_publish(
                    job_id,
                    progress=25,
                    current_step=f"Selected: {paper_meta.get('title', '')[:50]}",
                )

            self._ensure_job_not_purged(job_id)
            raw_source_path = str(
                paper_meta.get("source_path") or paper_meta.get("pdf_path") or ""
            ).strip()
            if not raw_source_path:
                raise FileNotFoundError("Selected paper source path is missing")
            resolved_source = resolve_repo_path(raw_source_path)
            paper_name = str(paper_meta.get("paper_id") or resolved_source.stem)
            paper_meta["job_id"] = job_id
            paper_meta["job_results_dir"] = to_repo_relative_path(job_results_dir)
            paper_meta["job_report_path"] = to_repo_relative_path(job_report_path)
            paper_meta["job_assets_dir"] = to_repo_relative_path(
                get_job_assets_dir(job_id, resolved_source.stem)
            )
            self._update_job_and_publish(
                job_id, paper_title=paper_meta.get("title", resolved_source.stem)
            )
            self._db.save_paper(job_id, paper_meta)

            # Phase 1: Process source
            self._update_job_and_publish(
                job_id,
                status="processing",
                progress=30,
                current_step="Processing source document...",
            )
            from modules.paper_processor.agent import PaperProcessorAgent

            processor = PaperProcessorAgent()
            parsed_paper = await processor.run(paper_meta)
            self._ensure_job_not_purged(job_id)
            paper_meta["pdf_path"] = str(parsed_paper.get("pdf_path") or "")
            paper_meta["source_path"] = str(
                parsed_paper.get("source_path") or paper_meta.get("source_path") or ""
            )
            paper_meta["source_type"] = str(
                parsed_paper.get("source_type") or paper_meta.get("source_type") or "pdf"
            )
            if paper_meta.get("paper_id"):
                self._db.save_paper(job_id, paper_meta)
            parsed_paper["job_id"] = job_id
            parsed_paper["job_results_dir"] = to_repo_relative_path(job_results_dir)
            parsed_paper["job_report_path"] = to_repo_relative_path(job_report_path)
            parsed_paper["job_assets_dir"] = to_repo_relative_path(
                get_job_assets_dir(job_id, resolved_source.stem)
            )
            parsed_paper["paper_name"] = paper_name
            parsed_paper["report_options"] = copy.deepcopy(config.get("report") or {})
            parsed_paper["topics"] = copy.deepcopy(config.get("topics") or [])
            parsed_paper["selection"] = copy.deepcopy(config.get("selection") or {})
            parsed_paper["job_mode"] = mode
            parsed_paper["profile_mode"] = profile_mode
            self._update_job_and_publish(
                job_id,
                progress=45,
                current_step=f"Processed source ({len(parsed_paper.get('figures', []))} figures)",
            )

            # Phase 2: Interpret
            self._update_job_and_publish(
                job_id,
                status="interpreting",
                progress=50,
                current_step="Building paper notes...",
            )
            from modules.paper_interpreter.agent import PaperInterpreterAgent

            mm = MemoryManager()
            try:
                interpreter = PaperInterpreterAgent()
                output_path = await interpreter.run(
                    parsed_paper,
                    profile_id=profile_id,
                    profile_mode=profile_mode,
                    memory_manager=mm,
                    db=self._db,
                    on_profile_assignment=lambda payload: self._update_job_and_publish(
                        job_id,
                        profile_id=payload.get("profile_id"),
                        profile_assignment_status=payload.get("profile_assignment_status"),
                        profile_assignment_note=payload.get("profile_assignment_note"),
                    ),
                    on_progress=lambda stage: self._publish_job_state(job_id),
                )
                self._ensure_job_not_purged(job_id)
            finally:
                mm.close()

            notes_meta = (parsed_paper.get("paper_notes") or {}).get("metadata") or {}
            if notes_meta.get("title_en"):
                paper_meta["title"] = notes_meta["title_en"]
                self._update_job_and_publish(job_id, paper_title=notes_meta["title_en"])
            if notes_meta.get("venue"):
                paper_meta["venue"] = notes_meta["venue"]
            if notes_meta.get("pub_date"):
                paper_meta["pub_date"] = notes_meta["pub_date"]
            if notes_meta.get("institution"):
                paper_meta["institution"] = notes_meta["institution"]

            report_path = to_repo_relative_path(output_path)
            self._update_job_and_publish(
                job_id,
                status="completed",
                progress=100,
                current_step="Done",
                report_path=report_path,
                completed_at=time.time(),
            )

            # Update paper record with report path and enriched metadata
            paper_id = paper_meta.get("paper_id", "")
            if paper_id:
                paper_record = self._db.get_paper(paper_id)
                if paper_record:
                    self._db.save_paper(
                        job_id, {**paper_meta, "report_path": report_path}
                    )

            final_job = self._db.get_job(job_id)
            final_profile_id = (
                int(final_job["profile_id"])
                if final_job is not None
                and isinstance(final_job.get("profile_id"), int)
                else profile_id
            )
            if final_profile_id is not None:
                safety_mm = MemoryManager()
                try:
                    safety_mm.recompute_profile_paper_count(final_profile_id)
                finally:
                    safety_mm.close()

            if replace_job_id and replace_job_id != job_id:
                self._cleanup_replaced_job(
                    source_job_id=replace_job_id, replacement_job_id=job_id
                )

            log.info("Job %s completed: %s", job_id, report_path)

        except asyncio.CancelledError:
            if job_id not in self._purged_jobs:
                self._update_job_and_publish(
                    job_id, status="failed", error="Cancelled", completed_at=time.time()
                )
            log.warning("Job %s cancelled", job_id)
        except SelectionFailure as exc:
            self._write_selector_diagnostics(
                job_id,
                {
                    **exc.diagnostics,
                    "failure_reason": str(
                        exc.diagnostics.get("failure_reason") or "selection_failure"
                    ),
                },
            )
            self._update_job_and_publish(
                job_id,
                status="failed",
                error=str(exc),
                completed_at=time.time(),
            )
            log.warning("Job %s selection failed with diagnostics: %s", job_id, exc)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            self._update_job_and_publish(
                job_id, status="failed", error=error_msg, completed_at=time.time()
            )
            log.error(
                "Job %s failed: %s\n%s", job_id, error_msg, traceback.format_exc()
            )
        finally:
            cleanup_r2_job_artifacts(job_id)
            reset_r2_job_context(r2_context_token)
            reset_log_job_context(log_context_token)

            # Send completion signal to WebSocket subscribers
            job_final = (
                None if job_id in self._purged_jobs else self._db.get_job(job_id)
            )
            if job_final is not None:
                job_final = enrich_job_with_artifact_readiness(job_final)
            handler.publish_done(job_id, job_final)

    def _prepare_job_source(
        self,
        *,
        job_id: str,
        source_file: Path,
        original_name: str = "",
        source_type: str = "pdf",
        validate_pdf: bool = True,
    ) -> Path:
        resolved_source = source_file.expanduser().resolve()
        if not resolved_source.exists() or not resolved_source.is_file():
            raise FileNotFoundError(
                f"Manual source document not found: {resolved_source}"
            )
        if validate_pdf and resolved_source.suffix.lower() != ".pdf":
            raise ValueError("Manual upload must be a PDF file")

        destination = get_job_pdf_path(job_id, original_name or resolved_source.name)
        if source_type == "html":
            destination = destination.with_suffix(".html")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if resolved_source != destination.resolve():
            shutil.copy2(resolved_source, destination)
        return destination

    def _cleanup_replaced_job(
        self, *, source_job_id: str, replacement_job_id: str
    ) -> None:
        source_job = self._db.get_job(source_job_id)
        if source_job is None:
            return

        self._purge_job_state(source_job_id, job=source_job)
        log.info(
            "Job %s replaced historical job %s and fully cleaned its state",
            replacement_job_id,
            source_job_id, 
        )
