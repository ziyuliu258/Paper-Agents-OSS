"""Paper Interpreter Agent: shared notes + memory-aware interpretation pipeline."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modules.paper_interpreter.assembler import assemble
from modules.paper_interpreter.distillation import build_distilled_memory_summary
from modules.paper_interpreter.report_auditor import audit_and_repair_report
from modules.paper_interpreter.task_runner import build_paper_notes, run_all_tasks
from modules.paper_interpreter.working_memory import WorkingMemory
from modules.paper_selector.topic_fit import judge_paper_notes_topic_fit
from server.database import Database
from utils.job_paths import get_job_results_dir
from utils.llm import call_llm_fallback
from utils.logger import get_logger
from utils.memory import (
    MemoryManager,
    build_memory_extraction_prompt,
    build_memory_keywords,
    parse_memory_extraction,
    validate_memory_extraction_for_writeback,
    write_memories_with_fresh_manager,
)
from utils.repo_paths import resolve_repo_path
from utils.profile_assignment import assign_profile_for_paper
from utils.working_memory_localization import (
    ensure_localized_distilled_summary_artifact,
    ensure_localized_working_memory_artifact,
)

log = get_logger(__name__)

_MEMORY_EXTRACTION_MAX_PROMPT_CHARS = 18000
_MEMORY_EXTRACTION_MAX_SUMMARY_CHARS = 7000
_MEMORY_EXTRACTION_MAX_REVIEW_CONTEXT_CHARS = 1800
_MEMORY_EXTRACTION_MAX_PROMOTION_CANDIDATES = 8
_DISTILLED_SUMMARY_ONE_LINE_CHAR_BUDGET = 160
_DISTILLED_SUMMARY_BULLET_CHAR_BUDGET = 220
_DISTILLED_SUMMARY_OPEN_QUESTION_CHAR_BUDGET = 160
_DISTILLED_SUMMARY_ASSESSMENT_CHAR_BUDGET = 260


def _trim_text_block(text: str, *, max_chars: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _build_distilled_summary_compression_prompt(
    *,
    paper_notes: dict[str, Any],
    distilled_summary: str,
) -> str:
    metadata = paper_notes.get("metadata") if isinstance(paper_notes, dict) else {}
    title = str(metadata.get("title_en", "")).strip()
    paper_summary = str(paper_notes.get("paper_summary", "")).strip()
    context_lines = []
    if title:
        context_lines.append(f"Title: {title}")
    if paper_summary:
        context_lines.append(f"Paper summary: {paper_summary}")
    context_block = "\n".join(context_lines) or "Title: Unknown"
    return (
        "Rewrite the following working-memory distilled summary into a compact markdown summary for artifact storage.\n\n"
        f"{context_block}\n\n"
        "Rules:\n"
        "1. Preserve the same factual content, including concrete improvements, caveats, and evidence anchors.\n"
        "2. Compress by rewriting, not by truncating. Do not use ellipses unless the source literally contains them.\n"
        "3. Keep the same high-level sections when they are present.\n"
        f"4. The `One-line summary` line must stay within {_DISTILLED_SUMMARY_ONE_LINE_CHAR_BUDGET} characters.\n"
        f"5. Each distilled claim bullet must stay within {_DISTILLED_SUMMARY_BULLET_CHAR_BUDGET} characters, including evidence brackets.\n"
        f"6. Each open-question bullet must stay within {_DISTILLED_SUMMARY_OPEN_QUESTION_CHAR_BUDGET} characters.\n"
        f"7. The `Overall assessment` paragraph must stay within {_DISTILLED_SUMMARY_ASSESSMENT_CHAR_BUDGET} characters.\n"
        "8. If a bullet is too long, shorten wording while preserving the key entities, metrics, comparisons, and evidence labels.\n"
        "9. Output markdown only. Do not wrap in code fences.\n\n"
        f"Source distilled summary:\n\n{distilled_summary}"
    )


async def _compress_distilled_summary(
    *,
    paper_notes: dict[str, Any],
    distilled_summary: str,
) -> str:
    source_text = str(distilled_summary or "").strip()
    if not source_text:
        return ""

    messages = [
        {
            "role": "system",
            "content": (
                "You compress research-memory distilled summaries into concise markdown artifacts. "
                "You preserve facts and evidence anchors while enforcing strict per-line length budgets."
            ),
        },
        {
            "role": "user",
            "content": _build_distilled_summary_compression_prompt(
                paper_notes=paper_notes,
                distilled_summary=source_text,
            ),
        },
    ]
    try:
        compressed = await call_llm_fallback(
            ["gem_pro", "gpt_pro"],
            messages,
            step_label="working memory distilled summary compression",
            temperature=0.1,
            max_tokens=2048,
            step_timeout=90.0,
        )
    except Exception as err:
        log.warning("Distilled summary compression skipped: %s", err)
        return source_text

    cleaned = str(compressed or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return cleaned or source_text


def _serialize_promotion_candidates(
    working_memory: WorkingMemory,
) -> list[dict[str, Any]]:
    status_rank = {
        "accepted": 0,
        "review_required": 1,
        "candidate": 2,
        "rejected": 3,
    }
    ordered = sorted(
        working_memory.promotion_candidates,
        key=lambda item: (
            status_rank.get(item.status, 9),
            -float(item.confidence),
            -len(item.evidence_refs),
            str(item.source_section),
        ),
    )
    serialized: list[dict[str, Any]] = []
    for item in ordered:
        payload = dict(item.payload or {})
        for key in ("body", "summary", "default_resolution"):
            if key in payload:
                payload[key] = _trim_text_block(
                    str(payload.get(key, "")), max_chars=420
                )
        serialized.append(
            {
                "candidate_type": item.candidate_type,
                "payload": payload,
                "source_section": item.source_section,
                "evidence_refs": item.evidence_refs[:3],
                "confidence": item.confidence,
                "status": item.status,
            }
        )
    return serialized


def _build_memory_extraction_request(
    *,
    paper_notes: dict[str, Any],
    summary: str,
    working_memory: WorkingMemory,
    review_context: str,
) -> tuple[str, list[str], dict[str, int]]:
    trimmed_summary = _trim_text_block(
        summary, max_chars=_MEMORY_EXTRACTION_MAX_SUMMARY_CHARS
    )
    trimmed_review_context = _trim_text_block(
        review_context,
        max_chars=_MEMORY_EXTRACTION_MAX_REVIEW_CONTEXT_CHARS,
    )
    serialized_candidates = _serialize_promotion_candidates(working_memory)
    candidate_limit = min(
        len(serialized_candidates), _MEMORY_EXTRACTION_MAX_PROMOTION_CANDIDATES
    )
    selected_candidates = serialized_candidates[:candidate_limit]
    prompt = build_memory_extraction_prompt(
        paper_notes,
        trimmed_summary,
        promotion_candidates=selected_candidates,
        review_context=trimmed_review_context,
    )

    while len(prompt) > _MEMORY_EXTRACTION_MAX_PROMPT_CHARS and candidate_limit > 4:
        candidate_limit -= 1
        selected_candidates = serialized_candidates[:candidate_limit]
        prompt = build_memory_extraction_prompt(
            paper_notes,
            trimmed_summary,
            promotion_candidates=selected_candidates,
            review_context=trimmed_review_context,
        )

    if (
        len(prompt) > _MEMORY_EXTRACTION_MAX_PROMPT_CHARS
        and len(trimmed_review_context) > 900
    ):
        trimmed_review_context = _trim_text_block(trimmed_review_context, max_chars=900)
        prompt = build_memory_extraction_prompt(
            paper_notes,
            trimmed_summary,
            promotion_candidates=selected_candidates,
            review_context=trimmed_review_context,
        )

    if (
        len(prompt) > _MEMORY_EXTRACTION_MAX_PROMPT_CHARS
        and len(trimmed_summary) > 5200
    ):
        trimmed_summary = _trim_text_block(trimmed_summary, max_chars=5200)
        prompt = build_memory_extraction_prompt(
            paper_notes,
            trimmed_summary,
            promotion_candidates=selected_candidates,
            review_context=trimmed_review_context,
        )

    preferred_models = ["gpt_pro", "gem_pro"]
    if len(prompt) > 14000:
        preferred_models = ["gem_pro", "gpt_pro"]

    metrics = {
        "memory_extraction_prompt_chars": len(prompt),
        "memory_extraction_summary_chars": len(trimmed_summary),
        "memory_extraction_review_context_chars": len(trimmed_review_context),
        "memory_extraction_candidate_count": len(selected_candidates),
        "memory_extraction_original_candidate_count": len(serialized_candidates),
    }
    return prompt, preferred_models, metrics


class PaperInterpreterAgent:
    def __init__(self) -> None:
        pass

    async def _persist_working_memory_artifacts(
        self,
        *,
        parsed_paper: dict[str, Any],
        working_memory: WorkingMemory,
        distilled_summary: str,
        artifact_stage: str,
    ) -> None:
        job_id = str(parsed_paper.get("job_id", "")).strip()
        if not job_id:
            return

        results_dir = get_job_results_dir(job_id)
        results_dir.mkdir(parents=True, exist_ok=True)

        working_memory_path = results_dir / "working_memory.json"
        summary_path = results_dir / "distilled_memory_summary.md"

        snapshot = working_memory.build_distillation_input()
        snapshot["retrieved_context"] = working_memory.retrieved_context
        snapshot["paper_title"] = working_memory.paper_title
        snapshot["paper_id"] = working_memory.paper_id
        snapshot["job_id"] = working_memory.job_id
        snapshot["profile_id"] = working_memory.profile_id
        snapshot["artifact_stage"] = artifact_stage

        working_memory_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if distilled_summary or not summary_path.exists():
            summary_path.write_text(
                distilled_summary or "",
                encoding="utf-8",
            )

    async def _persist_report_audit_artifact(
        self, *, parsed_paper: dict[str, Any], report_audit: dict[str, Any]
    ) -> None:
        job_id = str(parsed_paper.get("job_id", "")).strip()
        if not job_id:
            return
        results_dir = get_job_results_dir(job_id)
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "report_audit.json").write_text(
            json.dumps(report_audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _persist_selected_paper_topic_audit(
        self, *, parsed_paper: dict[str, Any], topic_audit: dict[str, Any]
    ) -> None:
        job_id = str(parsed_paper.get("job_id", "")).strip()
        if not job_id:
            return
        results_dir = get_job_results_dir(job_id)
        results_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_path = results_dir / "selector_diagnostics.json"
        diagnostics: dict[str, Any] = {}
        if diagnostics_path.exists():
            try:
                loaded = json.loads(diagnostics_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    diagnostics = loaded
            except Exception:
                diagnostics = {}
        diagnostics["selected_paper_topic_audit"] = topic_audit
        diagnostics_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sync_working_memory_after_audit(
        self,
        *,
        working_memory: WorkingMemory,
        task_results: dict[str, Any],
        report_audit: dict[str, Any],
    ) -> None:
        for key in (
            "t2_background",
            "t2_background_structured",
            "t3_method",
            "t3_method_structured",
            "t4_experiments",
            "t4_experiments_structured",
        ):
            if key in task_results:
                working_memory.remember_task_output(key, task_results[key])

        removed_claims_raw = report_audit.get("removed_claims_by_section")
        if not isinstance(removed_claims_raw, dict):
            return
        removed_claims_by_section = {
            str(section_key): {
                str(claim).strip()
                for claim in claims
                if str(claim).strip()
            }
            for section_key, claims in removed_claims_raw.items()
            if isinstance(claims, list)
        }
        if not removed_claims_by_section:
            return

        working_memory.draft_claims = [
            item
            for item in working_memory.draft_claims
            if item.claim not in removed_claims_by_section.get(item.section_key, set())
        ]
        working_memory.observations = [
            item
            for item in working_memory.observations
            if item.section_key not in removed_claims_by_section
            or not any(
                item.summary == claim
                or item.summary in claim
                or claim in item.summary
                for claim in removed_claims_by_section[item.section_key]
            )
        ]

    async def _write_back_memory(
        self,
        *,
        parsed_paper: dict[str, Any],
        task_results: dict[str, Any],
        working_memory: WorkingMemory,
        profile_id: int,
        memory_manager: MemoryManager,
        report_audit: dict[str, Any] | None = None,
    ) -> None:
        paper_id = str(parsed_paper.get("paper_id", "unknown")).strip() or "unknown"
        paper_notes = (
            task_results.get("paper_notes") or parsed_paper.get("paper_notes") or {}
        )
        distilled_summary, distill_metrics = build_distilled_memory_summary(
            working_memory,
            task_results,
        )
        distilled_summary = await _compress_distilled_summary(
            paper_notes=paper_notes,
            distilled_summary=distilled_summary,
        )
        review_bundle: dict[str, Any] = {}
        review_context = ""
        if working_memory.profile_id is not None:
            review_bundle = memory_manager.retrieve_for_review_conflict(
                working_memory.profile_id,
                keywords=working_memory.retrieved_context.get(
                    "interpreter_bundle", {}
                ).get("keywords", []),
                target_text=distilled_summary,
            )
            review_context = memory_manager.render_review_conflict_context(
                review_bundle
            )
            working_memory.set_retrieved_context("review_bundle", review_bundle)
            working_memory.set_retrieved_context("review_context", review_context)
            working_memory.set_metric(
                "review_bundle_claim_count",
                len(review_bundle.get("priority_claims", [])),
            )
            working_memory.set_metric(
                "review_bundle_conflict_count",
                len(review_bundle.get("active_conflicts", [])),
            )
        summary = distilled_summary or (
            f"One-line summary: {task_results.get('t1_summary', '')}\n\n"
            f"Research Background and Motivation:\n{task_results.get('t2_background', '')}\n\n"
            f"Core Method:\n{task_results.get('t3_method', '')}\n\n"
            f"Experiments and Results:\n{task_results.get('t4_experiments', '')}\n\n"
            f"Ablation Studies:\n{task_results.get('t5_ablation', '')}\n\n"
            f"Limitations and Future Directions:\n{task_results.get('t6_limitations', '')}\n\n"
            f"Overall Assessment:\n{task_results.get('t7_conclusion', '')}"
        )
        for key, value in distill_metrics.items():
            working_memory.set_metric(key, value)
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary=summary,
            artifact_stage="writeback_ready",
        )
        prompt, preferred_models, extraction_metrics = _build_memory_extraction_request(
            paper_notes=paper_notes,
            summary=summary,
            working_memory=working_memory,
            review_context=review_context,
        )
        for key, value in extraction_metrics.items():
            working_memory.set_metric(key, value)
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract reusable information from a single paper interpretation for long-term memory. "
                    "You must return JSON only, with no explanation."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await call_llm_fallback(
            preferred_models,
            messages,
            step_label="memory writeback extraction",
            temperature=0.1,
            max_tokens=4096,
            step_timeout=180.0,
        )
        extraction = parse_memory_extraction(response)
        extraction, validation_report = validate_memory_extraction_for_writeback(
            extraction,
            report_audit=report_audit,
        )
        working_memory.set_retrieved_context(
            "memory_writeback_validation", validation_report
        )
        working_memory.set_metric(
            "memory_writeback_kept_claim_count",
            validation_report.get("kept_claim_count", 0),
        )
        working_memory.set_metric(
            "memory_writeback_dropped_claim_count",
            validation_report.get("dropped_claim_count", 0),
        )
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary=summary,
            artifact_stage="writeback_validated",
        )
        title = str((paper_notes.get("metadata") or {}).get("title_en", "")).strip()
        await asyncio.to_thread(
            write_memories_with_fresh_manager,
            memory_manager.db_path,
            profile_id,
            paper_id,
            extraction,
            job_id=str(parsed_paper.get("job_id", "")).strip() or None,
            paper_title=title,
        )
        log.info(
            "Memory writeback distilled %d accepted / %d review-required candidates before promotion; nearby review conflicts=%d; validation kept=%d dropped=%d; extraction prompt=%d chars; models=%s",
            distill_metrics.get("accepted_count", 0),
            distill_metrics.get("review_required_count", 0),
            len(review_bundle.get("active_conflicts", [])),
            validation_report.get("kept_claim_count", 0),
            validation_report.get("dropped_claim_count", 0),
            extraction_metrics.get("memory_extraction_prompt_chars", 0),
            " -> ".join(preferred_models),
        )

    async def run(
        self,
        parsed_paper: dict[str, Any],
        *,
        profile_id: int | None = None,
        profile_mode: str = "explicit",
        memory_manager: MemoryManager | None = None,
        db: Database | None = None,
        on_profile_assignment: Callable[[dict[str, Any]], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> Path:
        paper_name = str(parsed_paper.get("paper_id", "unknown"))
        source_path = resolve_repo_path(
            str(parsed_paper.get("source_path") or parsed_paper.get("pdf_path") or "")
        )
        job_id = str(parsed_paper.get("job_id", "")).strip()
        working_memory = WorkingMemory(
            job_id=job_id,
            profile_id=profile_id,
            paper_id=paper_name,
        )
        resolved_profile_id = profile_id
        normalized_profile_mode = (
            str(profile_mode or "explicit").strip().lower() or "explicit"
        )
        normalized_job_mode = str(parsed_paper.get("job_mode") or "").strip().lower()
        selection_config = (
            parsed_paper.get("selection")
            if isinstance(parsed_paper.get("selection"), dict)
            else {}
        )
        try:
            post_download_topic_fit_threshold = float(
                selection_config.get("post_download_topic_fit_threshold", 0.55)
            )
        except (TypeError, ValueError):
            post_download_topic_fit_threshold = 0.55
        log.info("Interpreting paper: %s", paper_name)
        log.info(
            "WorkingMemory initialized for job=%s paper=%s",
            job_id or "unknown",
            paper_name,
        )

        log.info("Building shared paper_notes before memory injection...")
        paper_notes = await build_paper_notes(
            source_path,
            parsed_paper,
            working_memory=working_memory,
        )
        log.info("paper_notes ready")
        working_memory.set_metric("artifact_stage_code", 1)
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary="",
            artifact_stage="paper_notes_ready",
        )
        if on_progress:
            on_progress("paper_notes_ready")

        topics = parsed_paper.get("topics", [])
        if isinstance(topics, list) and topics:
            skip_post_download_topic_audit = (
                normalized_job_mode == "manual"
                and normalized_profile_mode == "explicit"
                and isinstance(resolved_profile_id, int)
            )
            if skip_post_download_topic_audit:
                topic_audit = {
                    "fit_label": "skipped_manual_explicit_profile",
                    "topic_fit_score": 1.0,
                    "matched_aspects": [],
                    "mismatch_reasons": [],
                    "skipped": True,
                    "reason": "manual_upload_with_explicit_profile",
                    "threshold": post_download_topic_fit_threshold,
                }
                working_memory.set_metric("selected_paper_topic_fit_skipped", 1.0)
                log.info(
                    "Skipping post-download topic audit for manual explicit-profile run"
                )
            else:
                topic_audit = judge_paper_notes_topic_fit(paper_notes, topics)
                topic_audit["threshold"] = post_download_topic_fit_threshold
            await self._persist_selected_paper_topic_audit(
                parsed_paper=parsed_paper,
                topic_audit=topic_audit,
            )
            if not skip_post_download_topic_audit:
                working_memory.set_metric(
                    "selected_paper_topic_fit_score",
                    float(topic_audit.get("topic_fit_score", 0.0) or 0.0),
                )
                if (
                    str(topic_audit.get("fit_label") or "") == "mismatch"
                    or float(topic_audit.get("topic_fit_score", 0.0) or 0.0)
                    < post_download_topic_fit_threshold
                ):
                    raise RuntimeError(
                        "Selected paper failed the post-download topic audit and was rejected as off-topic."
                    )

        if (
            normalized_profile_mode == "auto"
            and memory_manager is not None
            and db is not None
        ):
            assignment = assign_profile_for_paper(
                memory_manager,
                db,
                paper_notes,
                topics=topics if isinstance(topics, list) else None,
            )
            resolved_profile_id = int(assignment["profile_id"])
            working_memory.profile_id = resolved_profile_id
            if on_profile_assignment is not None:
                on_profile_assignment(
                    {
                        "profile_id": resolved_profile_id,
                        "profile_assignment_status": str(
                            assignment.get("status") or "matched"
                        ),
                        "profile_assignment_note": str(
                            assignment.get("note") or ""
                        ),
                    }
                )
            log.info(
                "Auto profile assignment resolved to %s (%s)",
                assignment.get("profile_name"),
                assignment.get("status"),
            )

        memory_context = ""
        translation_style_context = ""
        if resolved_profile_id is not None and memory_manager is not None:
            keywords = build_memory_keywords(paper_notes)
            retrieval_bundle = memory_manager.retrieve_for_interpreter(
                resolved_profile_id,
                keywords=keywords,
                current_paper_id=paper_name,
            )
            memory_context = memory_manager.render_interpreter_context(retrieval_bundle)
            if not memory_context:
                memory_context = memory_manager.build_memory_context(
                    resolved_profile_id,
                    keywords=keywords,
                    current_paper_id=paper_name,
                )
            working_memory.set_retrieved_context("interpreter_bundle", retrieval_bundle)
            working_memory.set_retrieved_context(
                "long_term_memory_summary", memory_context
            )
            working_memory.set_metric("memory_context_chars", len(memory_context))
            working_memory.set_metric(
                "retrieved_digest_count",
                len(retrieval_bundle.get("high_level_digest", [])),
            )
            working_memory.set_metric(
                "retrieved_claim_count",
                len(retrieval_bundle.get("priority_claims", [])),
            )
            working_memory.set_metric(
                "retrieved_evidence_count",
                len(retrieval_bundle.get("relevant_evidence", [])),
            )
            translation_bundle = memory_manager.retrieve_for_translation_style(
                resolved_profile_id,
                keywords=keywords,
            )
            translation_style_context = memory_manager.render_translation_style_context(
                translation_bundle
            )
            working_memory.set_retrieved_context(
                "translation_bundle", translation_bundle
            )
            working_memory.set_retrieved_context(
                "translation_style_context", translation_style_context
            )
            working_memory.set_metric(
                "translation_hint_count",
                len(translation_bundle.get("terminology_hints", [])),
            )
            log.info("Memory context prepared (%d chars)", len(memory_context))
        else:
            log.info("Memory manager/profile not provided; skipping memory injection")
        working_memory.set_metric("artifact_stage_code", 2)
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary="",
            artifact_stage="memory_context_ready",
        )
        if on_progress:
            on_progress("memory_context_ready")

        log.info("Running T1-T7 interpretation tasks...")
        task_results = await run_all_tasks(
            parsed_paper,
            paper_notes=paper_notes,
            memory_context=memory_context,
            working_memory=working_memory,
        )
        task_results["working_memory"] = working_memory.build_distillation_input()
        task_results["translation_style_context"] = translation_style_context

        log.info(
            "WorkingMemory snapshot: %d observations, %d open questions, %d draft claims",
            len(working_memory.observations),
            len(
                [
                    item
                    for item in working_memory.open_questions
                    if item.status == "open"
                ]
            ),
            len(working_memory.draft_claims),
        )
        log.info(
            "WorkingMemory metrics: retrieval claims=%s evidence=%s translation hints=%s",
            working_memory.metrics.get("retrieved_claim_count", 0),
            working_memory.metrics.get("retrieved_evidence_count", 0),
            working_memory.metrics.get("translation_hint_count", 0),
        )
        working_memory.set_metric("artifact_stage_code", 3)
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary="",
            artifact_stage="tasks_complete",
        )
        if on_progress:
            on_progress("tasks_complete")

        task_results, report_audit = audit_and_repair_report(parsed_paper, task_results)
        self._sync_working_memory_after_audit(
            working_memory=working_memory,
            task_results=task_results,
            report_audit=report_audit,
        )
        await self._persist_report_audit_artifact(
            parsed_paper=parsed_paper,
            report_audit=report_audit,
        )

        if resolved_profile_id is not None and memory_manager is not None:
            await self._write_back_memory(
                parsed_paper=parsed_paper,
                task_results=task_results,
                working_memory=working_memory,
                profile_id=resolved_profile_id,
                memory_manager=memory_manager,
                report_audit=report_audit,
            )

        log.info("Assembling final Markdown...")
        output_path = await assemble(parsed_paper, task_results, paper_name)
        working_memory.set_metric("artifact_stage_code", 4)
        await self._persist_working_memory_artifacts(
            parsed_paper=parsed_paper,
            working_memory=working_memory,
            distilled_summary="",
            artifact_stage="report_assembled",
        )
        if on_progress:
            on_progress("report_assembled")
        should_precompute_localized_memory = bool(
            parsed_paper.get("precompute_localized_working_memory", True)
        )
        if job_id and should_precompute_localized_memory:
            try:
                await ensure_localized_working_memory_artifact(job_id, language="zh")
                await ensure_localized_distilled_summary_artifact(job_id, language="zh")
                log.info("Localized memory artifacts prepared for job=%s", job_id)
            except Exception as err:
                log.warning(
                    "Localized memory artifact preparation skipped for job=%s: %s",
                    job_id,
                    err,
                )
        log.info("Interpretation complete: %s", output_path)
        return output_path
