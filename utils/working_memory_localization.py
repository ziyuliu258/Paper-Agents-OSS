"""Helpers for localized working-memory artifacts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from modules.paper_interpreter.translator import (
    translate_brief_markdown_to_chinese,
    translate_memory_batch,
)
from utils.job_paths import get_job_results_dir
from utils.logger import get_logger

log = get_logger(__name__)

_WORKING_MEMORY_FILENAME = "working_memory.json"
_DISTILLED_SUMMARY_FILENAME = "distilled_memory_summary.md"


def get_working_memory_path(job_id: str) -> Path:
    return get_job_results_dir(job_id) / _WORKING_MEMORY_FILENAME


def get_working_memory_localized_cache_path(job_id: str, language: str) -> Path:
    return get_working_memory_path(job_id).with_name(f"working_memory.{language}.json")


def get_distilled_summary_path(job_id: str) -> Path:
    return get_job_results_dir(job_id) / _DISTILLED_SUMMARY_FILENAME


def get_distilled_summary_localized_cache_path(job_id: str, language: str) -> Path:
    return get_distilled_summary_path(job_id).with_name(f"distilled_memory_summary.{language}.md")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


async def localize_working_memory_payload(payload: dict[str, Any], *, language: str) -> dict[str, Any]:
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


async def ensure_localized_working_memory_artifact(job_id: str, *, language: str = "zh") -> dict[str, Any]:
    source_path = get_working_memory_path(job_id)
    if not source_path.exists() or not source_path.is_file():
        return {}

    if language == "en":
        payload = _load_json(source_path)
        payload["translation_language"] = "en"
        return payload

    cache_path = get_working_memory_localized_cache_path(job_id, language)
    if cache_path.exists() and cache_path.stat().st_mtime >= source_path.stat().st_mtime:
        return _load_json(cache_path)

    localized = await localize_working_memory_payload(_load_json(source_path), language=language)
    cache_path.write_text(json.dumps(localized, ensure_ascii=False, indent=2), encoding="utf-8")
    return localized


async def ensure_localized_distilled_summary_artifact(
    job_id: str,
    *,
    language: str = "zh",
) -> str:
    source_path = get_distilled_summary_path(job_id)
    if not source_path.exists() or not source_path.is_file():
        return ""

    source_text = source_path.read_text(encoding="utf-8")
    if language == "en":
        return source_text

    cache_path = get_distilled_summary_localized_cache_path(job_id, language)
    if cache_path.exists() and cache_path.stat().st_mtime >= source_path.stat().st_mtime:
        return cache_path.read_text(encoding="utf-8")

    paper_context = ""
    working_memory_payload = _load_json(get_working_memory_path(job_id))
    if working_memory_payload:
        paper_context = _build_working_memory_paper_context(working_memory_payload)

    translated = await translate_brief_markdown_to_chinese(
        source_text,
        paper_context=paper_context,
    )
    cache_path.write_text(translated, encoding="utf-8")
    return translated
