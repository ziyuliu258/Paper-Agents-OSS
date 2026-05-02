"""Cross-paper long-term memory system backed by SQLite (Memory V2)."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from modules.paper_interpreter.translator import (
    translate_memory_batch_sync,
    translate_memory_item_sync,
)
from utils.config import DATA_DIR
from utils.memory_claim_relations import build_claim_relations
from utils.memory_opportunities import build_opportunity_snapshot
from utils.logger import get_logger

log = get_logger(__name__)

_DB_PATH = DATA_DIR / "memory.db"
_MEMORY_SCHEMA_VERSION = "4"
_MAX_DOMAIN_KNOWLEDGE_PER_PROFILE = 240
_MAX_MEMORY_CONTEXT_TOKENS = 700
_MAX_RELATED_LINKS = 6
_SIMILARITY_THRESHOLD = 0.8
_INTERPRETER_DIGEST_BUDGET = 6
_INTERPRETER_CLAIM_BUDGET = 8
_INTERPRETER_EVIDENCE_BUDGET = 6
_INTERPRETER_CONFLICT_BUDGET = 4
_INTERPRETER_LINK_BUDGET = 5
_SELECTOR_DIGEST_BUDGET = 4
_SELECTOR_CLAIM_BUDGET = 4
_SELECTOR_LINK_BUDGET = 6
_REVIEW_CLAIM_BUDGET = 6
_REVIEW_EVIDENCE_BUDGET = 8
_REVIEW_CONFLICT_BUDGET = 6
_TRANSLATION_TERM_BUDGET = 12
_THEME_ARTIFACT_KEY = "theme_snapshot"
_GAP_ARTIFACT_KEY = "gap_snapshot"
_SURVEY_ARTIFACT_KEY = "living_survey"
_OPPORTUNITY_ARTIFACT_KEY = "opportunity_snapshot"
_MEMORY_HEALTH_ARTIFACT_KEY = "memory_health_snapshot"
_FIELD_MAP_ARTIFACT_KEY = "field_map_snapshot"
_EVIDENCE_MATRIX_ARTIFACT_KEY = "evidence_matrix_snapshot"
_THEME_ARTIFACT_VERSION = "v1"
_GAP_ARTIFACT_VERSION = "v1"
_SURVEY_ARTIFACT_VERSION = "v1"
_OPPORTUNITY_ARTIFACT_VERSION = "v1"
_MEMORY_HEALTH_ARTIFACT_VERSION = "v1"
_FIELD_MAP_ARTIFACT_VERSION = "v1"
_EVIDENCE_MATRIX_ARTIFACT_VERSION = "v1"


def write_memories_with_fresh_manager(
    db_path: Path | None,
    profile_id: int,
    paper_id: str,
    extraction: dict[str, Any],
    *,
    job_id: str | None = None,
    paper_title: str = "",
) -> None:
    manager = MemoryManager(db_path=db_path)
    try:
        manager.write_memories(
            profile_id, paper_id, extraction, job_id=job_id, paper_title=paper_title
        )
    finally:
        manager.close()


_MEMORY_TABLES = [
    "profiles",
    "domain_knowledge",
    "style_preferences",
    "paper_links",
    "memory_profile_state",
    "memory_meta",
    "memory_writebacks",
    "memory_knowledge_events",
    "memory_style_events",
    "memory_link_events",
    "memory_entities",
    "memory_entity_aliases",
    "memory_claims",
    "memory_claim_evidence",
    "memory_claim_entities",
    "memory_synthesis_items",
    "memory_synthesis_claims",
    "memory_graph_edges",
    "memory_review_items",
    "memory_revisions",
    "memory_derived_artifacts",
]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL,
    last_used_at    REAL    NOT NULL,
    paper_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domain_knowledge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    paper_id        TEXT    NOT NULL DEFAULT '',
    category        TEXT    NOT NULL DEFAULT 'general',
    content         TEXT    NOT NULL,
    content_zh      TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL,
    relevance_score REAL    NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS style_preferences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    key             TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    updated_at      REAL    NOT NULL,
    UNIQUE(profile_id, key)
);

CREATE TABLE IF NOT EXISTS paper_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    source_paper_id TEXT    NOT NULL,
    target_paper_id TEXT    NOT NULL,
    relation_type   TEXT    NOT NULL DEFAULT 'related_to',
    summary         TEXT    NOT NULL DEFAULT '',
    summary_zh      TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL,
    UNIQUE(profile_id, source_paper_id, target_paper_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_domain_knowledge_profile ON domain_knowledge(profile_id);
CREATE INDEX IF NOT EXISTS idx_style_preferences_profile ON style_preferences(profile_id);
CREATE INDEX IF NOT EXISTS idx_paper_links_profile ON paper_links(profile_id);

CREATE TABLE IF NOT EXISTS memory_profile_state (
    profile_id           INTEGER PRIMARY KEY REFERENCES profiles(id),
    legacy_backfilled_at REAL
);

CREATE TABLE IF NOT EXISTS memory_claim_relations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id         INTEGER NOT NULL REFERENCES profiles(id),
    source_claim_id    INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
    target_claim_id    INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
    relation_type      TEXT    NOT NULL,
    confidence         REAL    NOT NULL DEFAULT 0.5,
    rationale          TEXT    NOT NULL DEFAULT '',
    rationale_zh       TEXT    NOT NULL DEFAULT '',
    origin_writeback_id INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
    manual_locked      INTEGER NOT NULL DEFAULT 0,
    created_at         REAL    NOT NULL,
    updated_at         REAL    NOT NULL,
    deleted_at         REAL,
    UNIQUE(profile_id, source_claim_id, target_claim_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_profile ON memory_claim_relations(profile_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_source ON memory_claim_relations(profile_id, source_claim_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_target ON memory_claim_relations(profile_id, target_claim_id, deleted_at);

CREATE TABLE IF NOT EXISTS memory_derived_artifacts (
    profile_id       INTEGER NOT NULL REFERENCES profiles(id),
    artifact_key     TEXT    NOT NULL,
    artifact_version TEXT    NOT NULL DEFAULT '',
    payload_json     TEXT    NOT NULL DEFAULT '',
    stale            INTEGER NOT NULL DEFAULT 1,
    updated_at       REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY(profile_id, artifact_key)
);

CREATE INDEX IF NOT EXISTS idx_memory_derived_artifacts_profile ON memory_derived_artifacts(profile_id);
CREATE INDEX IF NOT EXISTS idx_memory_derived_artifacts_stale ON memory_derived_artifacts(profile_id, stale);

CREATE TABLE IF NOT EXISTS memory_writebacks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES profiles(id),
    job_id          TEXT    NOT NULL,
    paper_id        TEXT    NOT NULL DEFAULT '',
    provenance_mode TEXT    NOT NULL DEFAULT 'exact',
    created_at      REAL    NOT NULL,
    deleted_at      REAL,
    UNIQUE(profile_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_writebacks_profile ON memory_writebacks(profile_id);
CREATE INDEX IF NOT EXISTS idx_memory_writebacks_profile_active ON memory_writebacks(profile_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_writebacks_job ON memory_writebacks(job_id);

CREATE TABLE IF NOT EXISTS memory_knowledge_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    writeback_id    INTEGER NOT NULL REFERENCES memory_writebacks(id) ON DELETE CASCADE,
    category        TEXT    NOT NULL DEFAULT 'general',
    content         TEXT    NOT NULL,
    relevance_score REAL    NOT NULL DEFAULT 1.0,
    created_at      REAL    NOT NULL,
    UNIQUE(writeback_id, category, content, created_at)
);

CREATE INDEX IF NOT EXISTS idx_memory_knowledge_events_writeback ON memory_knowledge_events(writeback_id);

CREATE TABLE IF NOT EXISTS memory_style_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    writeback_id INTEGER NOT NULL REFERENCES memory_writebacks(id) ON DELETE CASCADE,
    key          TEXT    NOT NULL,
    value        TEXT    NOT NULL,
    created_at   REAL    NOT NULL,
    UNIQUE(writeback_id, key, value, created_at)
);

CREATE INDEX IF NOT EXISTS idx_memory_style_events_writeback ON memory_style_events(writeback_id);

CREATE TABLE IF NOT EXISTS memory_link_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    writeback_id    INTEGER NOT NULL REFERENCES memory_writebacks(id) ON DELETE CASCADE,
    source_paper_id TEXT    NOT NULL,
    target_paper_id TEXT    NOT NULL,
    relation_type   TEXT    NOT NULL DEFAULT 'related_to',
    summary         TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL,
    UNIQUE(writeback_id, source_paper_id, target_paper_id, relation_type, summary, created_at)
);

CREATE INDEX IF NOT EXISTS idx_memory_link_events_writeback ON memory_link_events(writeback_id);

CREATE TABLE IF NOT EXISTS memory_entities (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id         INTEGER NOT NULL REFERENCES profiles(id),
    canonical_name     TEXT    NOT NULL,
    canonical_name_zh  TEXT    NOT NULL DEFAULT '',
    normalized_name    TEXT    NOT NULL,
    entity_type        TEXT    NOT NULL DEFAULT 'concept',
    summary            TEXT    NOT NULL DEFAULT '',
    summary_zh         TEXT    NOT NULL DEFAULT '',
    manual_locked      INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'active',
    created_at         REAL    NOT NULL,
    updated_at         REAL    NOT NULL,
    deleted_at         REAL,
    UNIQUE(profile_id, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_memory_entities_profile ON memory_entities(profile_id, deleted_at);

CREATE TABLE IF NOT EXISTS memory_entity_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
    alias           TEXT    NOT NULL,
    normalized_alias TEXT   NOT NULL,
    created_at      REAL    NOT NULL,
    UNIQUE(entity_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS memory_claims (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id            INTEGER NOT NULL REFERENCES profiles(id),
    origin_writeback_id   INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
    claim_key             TEXT    NOT NULL,
    title                 TEXT    NOT NULL,
    title_zh              TEXT    NOT NULL DEFAULT '',
    body                  TEXT    NOT NULL,
    body_zh               TEXT    NOT NULL DEFAULT '',
    claim_type            TEXT    NOT NULL DEFAULT 'finding',
    stance                TEXT    NOT NULL DEFAULT 'support',
    importance            REAL    NOT NULL DEFAULT 0.5,
    status                TEXT    NOT NULL DEFAULT 'active',
    default_resolution    TEXT    NOT NULL DEFAULT '',
    default_resolution_zh TEXT    NOT NULL DEFAULT '',
    scope_json            TEXT    NOT NULL DEFAULT '{}',
    stability_score       REAL    NOT NULL DEFAULT 0.5,
    last_supported_at     REAL,
    last_challenged_at    REAL,
    lifecycle_state       TEXT    NOT NULL DEFAULT 'emerging',
    lifecycle_reason_json TEXT    NOT NULL DEFAULT '{}',
    superseded_by_claim_id INTEGER REFERENCES memory_claims(id) ON DELETE SET NULL,
    last_lifecycle_update_at REAL,
    review_status         TEXT    NOT NULL DEFAULT 'none',
    manual_locked         INTEGER NOT NULL DEFAULT 0,
    created_at            REAL    NOT NULL,
    updated_at            REAL    NOT NULL,
    deleted_at            REAL
);

CREATE INDEX IF NOT EXISTS idx_memory_claims_profile ON memory_claims(profile_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_claims_key ON memory_claims(profile_id, claim_key, deleted_at);

CREATE TABLE IF NOT EXISTS memory_claim_evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id            INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
    writeback_id        INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
    section_key         TEXT    NOT NULL DEFAULT 'other',
    section_title       TEXT    NOT NULL DEFAULT '',
    section_title_zh    TEXT    NOT NULL DEFAULT '',
    snippet             TEXT    NOT NULL,
    snippet_zh          TEXT    NOT NULL DEFAULT '',
    evidence_summary    TEXT    NOT NULL DEFAULT '',
    evidence_summary_zh TEXT    NOT NULL DEFAULT '',
    anchor_kind         TEXT    NOT NULL DEFAULT 'text',
    context_before      TEXT    NOT NULL DEFAULT '',
    context_after       TEXT    NOT NULL DEFAULT '',
    structured_signal_json TEXT NOT NULL DEFAULT '',
    page_label          TEXT    NOT NULL DEFAULT '',
    page_start          INTEGER,
    page_end            INTEGER,
    weight              REAL    NOT NULL DEFAULT 1.0,
    manual_locked       INTEGER NOT NULL DEFAULT 0,
    created_at          REAL    NOT NULL,
    updated_at          REAL    NOT NULL,
    deleted_at          REAL
);

CREATE INDEX IF NOT EXISTS idx_memory_claim_evidence_claim ON memory_claim_evidence(claim_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_claim_evidence_writeback ON memory_claim_evidence(writeback_id, deleted_at);

CREATE TABLE IF NOT EXISTS memory_claim_entities (
    claim_id    INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
    entity_id   INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL DEFAULT 'mentions',
    created_at  REAL    NOT NULL,
    PRIMARY KEY (claim_id, entity_id, role)
);

CREATE TABLE IF NOT EXISTS memory_synthesis_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id            INTEGER NOT NULL REFERENCES profiles(id),
    origin_writeback_id   INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
    synthesis_key         TEXT    NOT NULL,
    item_type             TEXT    NOT NULL DEFAULT 'consensus',
    title                 TEXT    NOT NULL,
    title_zh              TEXT    NOT NULL DEFAULT '',
    summary               TEXT    NOT NULL,
    summary_zh            TEXT    NOT NULL DEFAULT '',
    confidence            REAL    NOT NULL DEFAULT 0.5,
    status                TEXT    NOT NULL DEFAULT 'active',
    default_resolution    TEXT    NOT NULL DEFAULT '',
    default_resolution_zh TEXT    NOT NULL DEFAULT '',
    review_status         TEXT    NOT NULL DEFAULT 'none',
    manual_locked         INTEGER NOT NULL DEFAULT 0,
    created_at            REAL    NOT NULL,
    updated_at            REAL    NOT NULL,
    deleted_at            REAL
);

CREATE INDEX IF NOT EXISTS idx_memory_synthesis_profile ON memory_synthesis_items(profile_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_memory_synthesis_key ON memory_synthesis_items(profile_id, synthesis_key, deleted_at);

CREATE TABLE IF NOT EXISTS memory_synthesis_claims (
    synthesis_id INTEGER NOT NULL REFERENCES memory_synthesis_items(id) ON DELETE CASCADE,
    claim_id     INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
    role         TEXT    NOT NULL DEFAULT 'supports',
    created_at   REAL    NOT NULL,
    PRIMARY KEY (synthesis_id, claim_id, role)
);

CREATE TABLE IF NOT EXISTS memory_graph_edges (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id         INTEGER NOT NULL REFERENCES profiles(id),
    origin_writeback_id INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
    source_kind        TEXT    NOT NULL,
    source_ref         TEXT    NOT NULL,
    target_kind        TEXT    NOT NULL,
    target_ref         TEXT    NOT NULL,
    relation_type      TEXT    NOT NULL,
    summary            TEXT    NOT NULL DEFAULT '',
    summary_zh         TEXT    NOT NULL DEFAULT '',
    weight             REAL    NOT NULL DEFAULT 1.0,
    manual_locked      INTEGER NOT NULL DEFAULT 0,
    created_at         REAL    NOT NULL,
    updated_at         REAL    NOT NULL,
    deleted_at         REAL,
    UNIQUE(profile_id, source_kind, source_ref, target_kind, target_ref, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_memory_graph_edges_profile ON memory_graph_edges(profile_id, deleted_at);

CREATE TABLE IF NOT EXISTS memory_review_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id            INTEGER NOT NULL REFERENCES profiles(id),
    target_type           TEXT    NOT NULL,
    target_id             INTEGER NOT NULL,
    review_type           TEXT    NOT NULL,
    title                 TEXT    NOT NULL,
    title_zh              TEXT    NOT NULL DEFAULT '',
    description           TEXT    NOT NULL DEFAULT '',
    description_zh        TEXT    NOT NULL DEFAULT '',
    default_resolution    TEXT    NOT NULL DEFAULT '',
    default_resolution_zh TEXT    NOT NULL DEFAULT '',
    suggested_payload     TEXT    NOT NULL DEFAULT '',
    status                TEXT    NOT NULL DEFAULT 'pending',
    reminder_active       INTEGER NOT NULL DEFAULT 1,
    resolution_note       TEXT    NOT NULL DEFAULT '',
    created_at            REAL    NOT NULL,
    updated_at            REAL    NOT NULL,
    resolved_at           REAL
);

CREATE INDEX IF NOT EXISTS idx_memory_review_items_profile ON memory_review_items(profile_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES profiles(id),
    target_type   TEXT    NOT NULL,
    target_id     TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    actor_type    TEXT    NOT NULL,
    summary       TEXT    NOT NULL DEFAULT '',
    summary_zh    TEXT    NOT NULL DEFAULT '',
    before_json   TEXT    NOT NULL DEFAULT '',
    after_json    TEXT    NOT NULL DEFAULT '',
    writeback_id  INTEGER,
    created_at    REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_revisions_profile ON memory_revisions(profile_id, created_at DESC);
"""


def _timestamp() -> float:
    return float(time.time())


def _strip_code_fence(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return payload


def _normalize_whitespace(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _normalize_lookup_key(text: Any) -> str:
    normalized = _normalize_whitespace(text).lower()
    normalized = re.sub(r"[^\w\u4e00-\u9fff\- ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _slugify(text: Any) -> str:
    normalized = _normalize_lookup_key(text)
    normalized = normalized.replace(" ", "-")
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized[:120] or "item"


def _safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(str(payload), ensure_ascii=False)


def _safe_json_loads(payload: str) -> Any:
    text = _normalize_whitespace(payload)
    if not text:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return payload


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _make_localized_text(en: Any, zh: Any = "") -> dict[str, str]:
    english = _safe_text(en)
    chinese = _safe_text(zh)
    return {
        "en": english,
        "zh": chinese,
        "primary": chinese or english,
    }


def _localized_for_fields(
    item: dict[str, Any], pairs: dict[str, str]
) -> dict[str, dict[str, str]]:
    return {
        target_key: _make_localized_text(
            item.get(source_key, ""), item.get(f"{source_key}_zh", "")
        )
        for target_key, source_key in pairs.items()
    }


def _merge_suggested_payload_translation(
    payload: Any, translations: dict[str, str] | None
) -> Any:
    if not isinstance(payload, dict):
        return payload
    if not translations:
        return payload
    merged = dict(payload)
    for key, value in translations.items():
        merged[f"{key}_zh"] = _safe_text(value)
    return merged


def _normalize_scope_payload(raw_scope: Any) -> dict[str, Any]:
    scope_source = raw_scope if isinstance(raw_scope, dict) else {}
    return {
        "conditions": [
            _normalize_whitespace(item)
            for item in scope_source.get("conditions", [])
            if _normalize_whitespace(item)
        ][:6],
        "boundary": _normalize_whitespace(scope_source.get("boundary", "")),
        "population": _normalize_whitespace(scope_source.get("population", "")),
        "notes": _normalize_whitespace(scope_source.get("notes", "")),
    }


def _normalize_structured_signal_payload(raw_signal: Any) -> dict[str, str]:
    source = raw_signal if isinstance(raw_signal, dict) else {}
    return {
        key: _normalize_whitespace(source.get(key, ""))
        for key in (
            "task",
            "method",
            "dataset",
            "metric",
            "value",
            "baseline",
            "comparator",
            "setting",
            "limitation",
            "scope_note",
        )
        if _normalize_whitespace(source.get(key, ""))
    }


def _normalize_manual_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": _maybe_int(payload.get("claim_id")),
        "section_key": _normalize_whitespace(payload.get("section_key", "other")) or "other",
        "section_title": _normalize_whitespace(payload.get("section_title", "")),
        "snippet": _normalize_whitespace(payload.get("snippet", "")),
        "evidence_summary": _normalize_whitespace(
            payload.get("evidence_summary", payload.get("summary", ""))
        ),
        "page_label": _normalize_whitespace(payload.get("page_label", "")),
        "page_start": _maybe_int(payload.get("page_start")),
        "page_end": _maybe_int(payload.get("page_end")),
        "anchor_kind": _normalize_whitespace(payload.get("anchor_kind", "text"))
        or "text",
        "context_before": _normalize_whitespace(payload.get("context_before", "")),
        "context_after": _normalize_whitespace(payload.get("context_after", "")),
        "structured_signal": _normalize_structured_signal_payload(
            payload.get("structured_signal")
            if payload.get("structured_signal") is not None
            else _safe_json_loads(str(payload.get("structured_signal_json", "")))
        ),
    }


def _bool_to_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _int_to_bool(value: Any) -> bool:
    return bool(int(value or 0))


def _maybe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _sequence_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _dedupe_strings(items: list[str], *, limit: int | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _normalize_whitespace(item)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
        if limit is not None and len(merged) >= limit:
            break
    return merged


def _claim_default_key(title: str, body: str) -> str:
    return _slugify(title or body[:120])


def _claim_validation_text(claim: dict[str, Any]) -> str:
    return " ".join(
        _normalize_whitespace(claim.get(key, ""))
        for key in ("title", "body", "default_resolution")
        if _normalize_whitespace(claim.get(key, ""))
    )


def _audit_removed_claim_texts(report_audit: dict[str, Any] | None) -> set[str]:
    if not isinstance(report_audit, dict):
        return set()
    removed = report_audit.get("removed_claims_by_section", {})
    if not isinstance(removed, dict):
        return set()
    texts: set[str] = set()
    for claims in removed.values():
        if not isinstance(claims, list):
            continue
        for claim in claims:
            normalized = _normalize_lookup_key(claim)
            if normalized:
                texts.add(normalized)
    return texts


def _claim_matches_removed_audit_claim(
    claim: dict[str, Any], removed_claim_texts: set[str]
) -> bool:
    if not removed_claim_texts:
        return False
    claim_text = _normalize_lookup_key(_claim_validation_text(claim))
    if not claim_text:
        return False
    return any(
        removed == claim_text
        or (len(removed) >= 24 and removed in claim_text)
        or (len(claim_text) >= 24 and claim_text in removed)
        for removed in removed_claim_texts
    )


def validate_memory_extraction_for_writeback(
    extraction: dict[str, Any],
    *,
    report_audit: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply deterministic writeback gates before claims enter long-term memory."""
    if not isinstance(extraction, dict):
        return extraction, {
            "kept_claim_count": 0,
            "dropped_claim_count": 0,
            "dropped_claims": [],
        }

    removed_claim_texts = _audit_removed_claim_texts(report_audit)
    kept_claims: list[dict[str, Any]] = []
    dropped_claims: list[dict[str, Any]] = []
    dropped_claim_keys: set[str] = set()
    for claim in extraction.get("claims", []):
        if not isinstance(claim, dict):
            continue
        claim_key = _normalize_whitespace(claim.get("claim_key", ""))
        evidence_items = [
            item
            for item in claim.get("evidence", [])
            if isinstance(item, dict) and _normalize_whitespace(item.get("snippet", ""))
        ]
        reason = ""
        if not evidence_items:
            reason = "missing_evidence"
        elif _claim_matches_removed_audit_claim(claim, removed_claim_texts):
            reason = "removed_by_report_audit"
        if reason:
            if claim_key:
                dropped_claim_keys.add(claim_key)
            dropped_claims.append(
                {
                    "claim_key": claim_key,
                    "title": _normalize_whitespace(claim.get("title", "")),
                    "reason": reason,
                }
            )
            continue
        kept_claim = dict(claim)
        kept_claim["evidence"] = evidence_items
        kept_claims.append(kept_claim)

    kept_synthesis: list[dict[str, Any]] = []
    stripped_synthesis_links = 0
    for item in extraction.get("synthesis_items", []):
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        if isinstance(next_item.get("claim_keys"), list) and dropped_claim_keys:
            before = len(next_item.get("claim_keys", []))
            next_item["claim_keys"] = [
                str(claim_key)
                for claim_key in next_item.get("claim_keys", [])
                if _normalize_whitespace(claim_key) not in dropped_claim_keys
            ]
            stripped_synthesis_links += before - len(next_item["claim_keys"])
        kept_synthesis.append(next_item)

    validated = {
        **extraction,
        "claims": kept_claims,
        "synthesis_items": kept_synthesis,
    }
    report = {
        "kept_claim_count": len(kept_claims),
        "dropped_claim_count": len(dropped_claims),
        "dropped_claims": dropped_claims,
        "stripped_synthesis_claim_link_count": stripped_synthesis_links,
        "audit_removed_claim_candidate_count": len(removed_claim_texts),
    }
    return validated, report


def build_memory_keywords(paper_notes: dict[str, Any], *, limit: int = 12) -> list[str]:
    metadata = paper_notes.get("metadata") if isinstance(paper_notes, dict) else {}
    problem = paper_notes.get("problem") if isinstance(paper_notes, dict) else []
    glossary_seed = (
        paper_notes.get("glossary_seed") if isinstance(paper_notes, dict) else []
    )
    method_steps = (
        paper_notes.get("method_steps") if isinstance(paper_notes, dict) else []
    )
    main_results = (
        paper_notes.get("main_results") if isinstance(paper_notes, dict) else []
    )

    candidates: list[str] = []
    if isinstance(metadata, dict):
        candidates.extend(
            [
                str(metadata.get("title_en", "")),
                str(metadata.get("title_cn", "")),
                str(metadata.get("venue", "")),
            ]
        )
    if isinstance(problem, list):
        candidates.extend(str(item) for item in problem[:4])
    if isinstance(glossary_seed, list):
        candidates.extend(str(item) for item in glossary_seed[:4])
    if isinstance(method_steps, list):
        candidates.extend(str(item) for item in method_steps[:3])
    if isinstance(main_results, list):
        candidates.extend(str(item) for item in main_results[:3])

    keywords: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cleaned = _normalize_whitespace(item)
        if not cleaned:
            continue
        fragments = [
            frag.strip()
            for frag in re.split(r"[、,，;；/()（）]", cleaned)
            if frag.strip()
        ]
        for fragment in fragments:
            if len(fragment) < 2:
                continue
            lowered = fragment.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            keywords.append(fragment)
            if len(keywords) >= limit:
                return keywords
    return keywords


class _DeltaCollector:
    """Collects cognitive changes during a single write_memories() call."""

    def __init__(self) -> None:
        self.new_entities: list[dict[str, str]] = []
        self.new_claims: list[dict[str, str]] = []
        self.reinforced_claims: list[dict[str, Any]] = []
        self.challenged_claims: list[dict[str, Any]] = []
        self.new_synthesis: list[dict[str, str]] = []
        self.updated_synthesis: list[dict[str, str]] = []
        self.new_debates: list[dict[str, str]] = []

    def record_new_entity(self, name: str, entity_type: str) -> None:
        self.new_entities.append({"name": name, "type": entity_type})

    def record_new_claim(self, title: str, claim_type: str, stance: str) -> None:
        self.new_claims.append(
            {"title": title, "claim_type": claim_type, "stance": stance}
        )

    def record_claim_reinforced(self, title: str, now_supported_by: int = 0) -> None:
        self.reinforced_claims.append(
            {"title": title, "now_supported_by": now_supported_by}
        )

    def record_claim_challenged(
        self, title: str, conflict_type: str, triggered_review: bool = False
    ) -> None:
        self.challenged_claims.append(
            {
                "title": title,
                "conflict_type": conflict_type,
                "triggered_review": triggered_review,
            }
        )

    def record_new_synthesis(self, title: str, item_type: str) -> None:
        self.new_synthesis.append({"title": title, "type": item_type})

    def record_updated_synthesis(self, title: str, what_changed: str) -> None:
        self.updated_synthesis.append({"title": title, "what_changed": what_changed})

    def record_new_debate(self, title: str) -> None:
        self.new_debates.append({"title": title})

    def compute_impact_score(self) -> float:
        score = (
            len(self.new_entities) * 0.08
            + len(self.new_claims) * 0.1
            + len(self.challenged_claims) * 0.3
            + len(self.new_debates) * 0.25
            + len(self.reinforced_claims) * 0.05
            + len(self.new_synthesis) * 0.12
            + len(self.updated_synthesis) * 0.06
        )
        return round(min(score, 1.0), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_entities": self.new_entities,
            "new_claims": self.new_claims,
            "reinforced_claims": self.reinforced_claims,
            "challenged_claims": self.challenged_claims,
            "new_synthesis": self.new_synthesis,
            "updated_synthesis": self.updated_synthesis,
            "new_debates": self.new_debates,
            "impact_score": self.compute_impact_score(),
        }


def _normalize_claim_text(title: str, body: str) -> tuple[str, str]:
    """Ensure claim text conforms to display constraints."""
    title = _normalize_whitespace(title)
    if len(title) > 100:
        title = title[:97] + "..."
    body = _normalize_whitespace(body)
    sentences = re.split(r"(?<=[.!?。！？])\s+", body.strip())
    if len(sentences) > 3:
        body = " ".join(sentences[:3])
    if len(body) > 280:
        body = body[:277] + "..."
    return title, body


def _normalize_synthesis_text(title: str, summary: str) -> tuple[str, str]:
    """Ensure synthesis text conforms to display constraints."""
    title = _normalize_whitespace(title)
    if len(title) > 100:
        title = title[:97] + "..."
    summary = _normalize_whitespace(summary)
    sentences = re.split(r"(?<=[.!?。！？])\s+", summary.strip())
    if len(sentences) > 4:
        summary = " ".join(sentences[:4])
    if len(summary) > 360:
        summary = summary[:357] + "..."
    return title, summary


class _DeferredLocalizationContext:
    """Collects translation requests during write_memories() and flushes them in one batch."""

    def __init__(self, *, paper_context: str = "") -> None:
        self.pending: list[dict[str, Any]] = []
        self.backfill_ops: list[tuple[str, int, dict[str, str]]] = []
        self.paper_context = paper_context
        self._counter = 0

    def submit(
        self,
        kind: str,
        fields: dict[str, Any],
        context: dict[str, Any] | None,
        step_label: str,
    ) -> int:
        idx = self._counter
        self._counter += 1
        self.pending.append(
            {
                "kind": kind,
                "fields": fields,
                "context": context or {},
                "_idx": idx,
            }
        )
        return idx

    def register_backfill(
        self, idx: int, table: str, row_id: int, field_mapping: dict[str, str]
    ) -> None:
        """Register a SQL backfill: after flush, UPDATE *table* SET col=translated WHERE id=*row_id*.

        *field_mapping* maps translation field name -> SQL column name.
        """
        self.backfill_ops.append((table, row_id, {**field_mapping, "_idx": str(idx)}))

    def flush(self, conn: sqlite3.Connection) -> None:
        if not self.pending:
            return
        batch_items = [
            {"kind": item["kind"], "fields": item["fields"], "context": item["context"]}
            for item in self.pending
        ]
        results = translate_memory_batch_sync(
            batch_items,
            step_label="deferred memory localization",
            paper_context=self.paper_context,
        )
        result_by_idx: dict[int, dict[str, str]] = {}
        for item, translated in zip(self.pending, results):
            result_by_idx[item["_idx"]] = translated

        for table, row_id, mapping in self.backfill_ops:
            idx = int(mapping.pop("_idx"))
            translated = result_by_idx.get(idx, {})
            if not translated:
                continue
            set_parts: list[str] = []
            values: list[Any] = []
            for field_name, col_name in mapping.items():
                val = translated.get(field_name, "")
                if val:
                    set_parts.append(f"{col_name} = ?")
                    values.append(val)
            if set_parts:
                sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = ?"
                values.append(row_id)
                conn.execute(sql, values)
        conn.commit()
        log.info(
            "Deferred localization flushed: %d items translated, %d backfill ops applied",
            len(self.pending),
            len(self.backfill_ops),
        )


class MemoryManager:
    """Per-profile long-term memory CRUD with SQLite backend."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._closed = False
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()
        self._ensure_default_profile()
        self._deferred_ctx: _DeferredLocalizationContext | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        if self._closed:
            return
        self._conn.close()
        self._closed = True

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        try:
            self.close()
        except Exception:
            pass

    def _ensure_schema(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = self._conn.execute(
            "SELECT value FROM memory_meta WHERE key = 'schema_version'"
        ).fetchone()
        current_version = str(row["value"]) if row else ""
        has_existing_memory = any(
            self._table_exists(table_name)
            for table_name in (
                "profiles",
                "memory_writebacks",
                "memory_entities",
                "memory_claims",
                "memory_synthesis_items",
            )
        )
        if current_version != _MEMORY_SCHEMA_VERSION and not has_existing_memory:
            log.info(
                "Resetting memory schema to v%s (old=%s)",
                _MEMORY_SCHEMA_VERSION,
                current_version or "none",
            )
            self._conn.execute("PRAGMA foreign_keys = OFF")
            for table in _MEMORY_TABLES:
                self._conn.execute(f"DROP TABLE IF EXISTS {table}")
            self._conn.commit()
            self._conn.execute("PRAGMA foreign_keys = ON")
        elif current_version != _MEMORY_SCHEMA_VERSION and has_existing_memory:
            log.warning(
                "Memory schema version mismatch detected (db=%s, code=%s); preserving existing data and applying safe migrations only",
                current_version or "none",
                _MEMORY_SCHEMA_VERSION,
            )
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.execute(
            "INSERT INTO memory_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_MEMORY_SCHEMA_VERSION,),
        )
        self._conn.commit()
        self._apply_soft_migrations()
        self.ensure_all_profiles_memory_provenance()

    def _apply_soft_migrations(self) -> None:
        """Non-destructive column additions that preserve existing data."""
        migrations = [
            ("profiles", "brief_json", "TEXT NOT NULL DEFAULT ''"),
            ("profiles", "brief_stale", "INTEGER NOT NULL DEFAULT 1"),
            ("memory_writebacks", "delta_json", "TEXT NOT NULL DEFAULT ''"),
            ("memory_profile_state", "claim_relations_stale", "INTEGER NOT NULL DEFAULT 1"),
            ("memory_profile_state", "claim_relations_updated_at", "REAL NOT NULL DEFAULT 0"),
            ("memory_claims", "scope_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("memory_claims", "stability_score", "REAL NOT NULL DEFAULT 0.5"),
            ("memory_claims", "last_supported_at", "REAL"),
            ("memory_claims", "last_challenged_at", "REAL"),
            ("memory_claims", "lifecycle_state", "TEXT NOT NULL DEFAULT 'emerging'"),
            ("memory_claims", "lifecycle_reason_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("memory_claims", "superseded_by_claim_id", "INTEGER"),
            ("memory_claims", "last_lifecycle_update_at", "REAL"),
            ("memory_claim_evidence", "anchor_kind", "TEXT NOT NULL DEFAULT 'text'"),
            ("memory_claim_evidence", "context_before", "TEXT NOT NULL DEFAULT ''"),
            ("memory_claim_evidence", "context_after", "TEXT NOT NULL DEFAULT ''"),
            ("memory_claim_evidence", "structured_signal_json", "TEXT NOT NULL DEFAULT ''"),
        ]
        for table, column, col_def in migrations:
            try:
                self._conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                log.info("Soft migration: added %s.%s", table, column)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_derived_artifacts (
                profile_id       INTEGER NOT NULL REFERENCES profiles(id),
                artifact_key     TEXT    NOT NULL,
                artifact_version TEXT    NOT NULL DEFAULT '',
                payload_json     TEXT    NOT NULL DEFAULT '',
                stale            INTEGER NOT NULL DEFAULT 1,
                updated_at       REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY(profile_id, artifact_key)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_derived_artifacts_profile ON memory_derived_artifacts(profile_id);
            CREATE INDEX IF NOT EXISTS idx_memory_derived_artifacts_stale ON memory_derived_artifacts(profile_id, stale);

            CREATE TABLE IF NOT EXISTS memory_claim_relations (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id         INTEGER NOT NULL REFERENCES profiles(id),
                source_claim_id    INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
                target_claim_id    INTEGER NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
                relation_type      TEXT    NOT NULL,
                confidence         REAL    NOT NULL DEFAULT 0.5,
                rationale          TEXT    NOT NULL DEFAULT '',
                rationale_zh       TEXT    NOT NULL DEFAULT '',
                origin_writeback_id INTEGER REFERENCES memory_writebacks(id) ON DELETE SET NULL,
                manual_locked      INTEGER NOT NULL DEFAULT 0,
                created_at         REAL    NOT NULL,
                updated_at         REAL    NOT NULL,
                deleted_at         REAL,
                UNIQUE(profile_id, source_claim_id, target_claim_id, relation_type)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_profile ON memory_claim_relations(profile_id, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_source ON memory_claim_relations(profile_id, source_claim_id, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_memory_claim_relations_target ON memory_claim_relations(profile_id, target_claim_id, deleted_at);
            """
        )
        self._conn.commit()

    def _ensure_default_profile(self) -> None:
        if self.get_profile_by_name("default") is None:
            self.create_profile("default", "Default profile")

    def ensure_profile_memory_provenance(self, profile_id: int) -> None:
        # Memory V2 starts from a clean schema and no longer needs legacy backfill.
        self._conn.execute(
            "INSERT OR IGNORE INTO memory_profile_state (profile_id, legacy_backfilled_at, claim_relations_stale, claim_relations_updated_at) VALUES (?, ?, 1, 0)",
            (profile_id, _timestamp()),
        )
        self._conn.commit()

    def ensure_all_profiles_memory_provenance(self) -> None:
        rows = self._conn.execute("SELECT id FROM profiles").fetchall()
        for row in rows:
            self.ensure_profile_memory_provenance(int(row["id"]))

    def mark_claim_relations_stale(self, profile_id: int) -> None:
        self.ensure_profile_memory_provenance(profile_id)
        self._conn.execute(
            "UPDATE memory_profile_state SET claim_relations_stale = 1 WHERE profile_id = ?",
            (profile_id,),
        )
        self._conn.commit()

    def _claim_relations_are_stale(self, profile_id: int) -> bool:
        self.ensure_profile_memory_provenance(profile_id)
        row = self._conn.execute(
            "SELECT claim_relations_stale FROM memory_profile_state WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return True
        return bool(int(row["claim_relations_stale"] if row["claim_relations_stale"] is not None else 1))

    def _ensure_claim_relations(self, profile_id: int) -> None:
        if not self._claim_relations_are_stale(profile_id):
            return
        self._rebuild_claim_relations(profile_id)

    def list_claim_relations(
        self, profile_id: int, *, limit: int = 240, ensure_fresh: bool = True
    ) -> list[dict[str, Any]]:
        if ensure_fresh:
            self._ensure_claim_relations(profile_id)
        rows = self._conn.execute(
            "SELECT * FROM memory_claim_relations WHERE profile_id = ? AND deleted_at IS NULL ORDER BY confidence DESC, updated_at DESC, id DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
            item["rationale_localized"] = _make_localized_text(
                item.get("rationale", ""), item.get("rationale_zh", "")
            )
            payloads.append(item)
        return payloads

    def _rebuild_claim_relations(self, profile_id: int) -> dict[str, Any]:
        self.ensure_profile_memory_provenance(profile_id)
        claims = [
            item
            for item in self.list_claims(profile_id, limit=None)
            if not item.get("deleted_at")
        ]
        evidence_fragments = [
            item
            for item in self.list_evidence(profile_id, limit=None)
            if not item.get("deleted_at")
        ]
        reviews = self.list_review_items(profile_id, limit=None)
        relation_rows, claim_stats = build_claim_relations(
            claims, evidence_fragments, reviews
        )
        now = _timestamp()
        self._conn.execute(
            "UPDATE memory_claim_relations SET deleted_at = ?, updated_at = ? WHERE profile_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (now, now, profile_id),
        )
        for row in relation_rows:
            source_claim_id = int(row.get("source_claim_id", 0) or 0)
            target_claim_id = int(row.get("target_claim_id", 0) or 0)
            relation_type = _normalize_whitespace(row.get("relation_type", ""))
            if source_claim_id <= 0 or target_claim_id <= 0 or not relation_type:
                continue
            existing = self._conn.execute(
                "SELECT id, manual_locked FROM memory_claim_relations WHERE profile_id = ? AND source_claim_id = ? AND target_claim_id = ? AND relation_type = ? ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END, manual_locked DESC, updated_at DESC LIMIT 1",
                (profile_id, source_claim_id, target_claim_id, relation_type),
            ).fetchone()
            if existing is not None and bool(existing["manual_locked"]):
                self._conn.execute(
                    "UPDATE memory_claim_relations SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                    (now, int(existing["id"])),
                )
                continue
            if existing is not None:
                self._conn.execute(
                    "UPDATE memory_claim_relations SET confidence = ?, rationale = ?, rationale_zh = ?, origin_writeback_id = NULL, updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (
                        float(row.get("confidence", 0.5) or 0.5),
                        _normalize_whitespace(row.get("rationale", "")),
                        _normalize_whitespace(row.get("rationale_zh", "")),
                        now,
                        int(existing["id"]),
                    ),
                )
            else:
                self._conn.execute(
                    "INSERT INTO memory_claim_relations (profile_id, source_claim_id, target_claim_id, relation_type, confidence, rationale, rationale_zh, origin_writeback_id, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, NULL)",
                    (
                        profile_id,
                        source_claim_id,
                        target_claim_id,
                        relation_type,
                        float(row.get("confidence", 0.5) or 0.5),
                        _normalize_whitespace(row.get("rationale", "")),
                        _normalize_whitespace(row.get("rationale_zh", "")),
                        now,
                        now,
                    ),
                )
        for claim_id, stats in claim_stats.items():
            self._conn.execute(
                "UPDATE memory_claims SET stability_score = ?, last_supported_at = ?, last_challenged_at = ?, lifecycle_state = ?, lifecycle_reason_json = ?, last_lifecycle_update_at = ? WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
                (
                    float(stats.get("stability_score", 0.5) or 0.5),
                    stats.get("last_supported_at"),
                    stats.get("last_challenged_at"),
                    _normalize_whitespace(stats.get("lifecycle_state", "emerging"))
                    or "emerging",
                    _safe_json_dumps(stats.get("lifecycle_reason", {})),
                    now,
                    claim_id,
                    profile_id,
                ),
            )
        self._conn.execute(
            "UPDATE memory_profile_state SET claim_relations_stale = 0, claim_relations_updated_at = ? WHERE profile_id = ?",
            (now, profile_id),
        )
        self._conn.commit()
        return {
            "relation_count": len(relation_rows),
            "claim_stat_count": len(claim_stats),
            "updated_at": now,
        }

    def _resolve_unique_profile_name(self, name: str) -> str:
        base_name = _normalize_whitespace(name) or f"profile-{int(_timestamp())}"
        if self.get_profile_by_name(base_name) is None:
            return base_name
        suffix = 2
        while self.get_profile_by_name(f"{base_name}-{suffix}") is not None:
            suffix += 1
        return f"{base_name}-{suffix}"

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, name, description, created_at, last_used_at, paper_count FROM profiles "
            "ORDER BY last_used_at DESC, created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_profile_by_name(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, name, description, created_at, last_used_at, paper_count "
            "FROM profiles WHERE name = ? ORDER BY id ASC LIMIT 1",
            (_normalize_whitespace(name),),
        ).fetchone()
        return dict(row) if row else None

    def get_profile_by_id(self, profile_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, name, description, created_at, last_used_at, paper_count FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_profile(self, name: str, description: str = "") -> dict[str, Any]:
        unique_name = self._resolve_unique_profile_name(name)
        now = _timestamp()
        cur = self._conn.execute(
            "INSERT INTO profiles (name, description, created_at, last_used_at, paper_count) VALUES (?, ?, ?, ?, 0)",
            (unique_name, _normalize_whitespace(description), now, now),
        )
        self._conn.commit()
        return {
            "id": int(cur.lastrowid),
            "name": unique_name,
            "description": _normalize_whitespace(description),
            "created_at": now,
            "last_used_at": now,
            "paper_count": 0,
        }

    def delete_profile(self, profile_id: int) -> dict[str, Any]:
        profile = self.get_profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Profile not found")
        if str(profile.get("name", "")).strip().lower() == "default":
            raise ValueError("The default profile cannot be deleted")

        writeback_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        deleted_writeback_count = int(writeback_row["cnt"] if writeback_row else 0)

        with self._conn:
            self._conn.execute(
                "DELETE FROM memory_claim_relations WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_derived_artifacts WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_review_items WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_revisions WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_graph_edges WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_synthesis_items WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_claims WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_entities WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_writebacks WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM domain_knowledge WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM style_preferences WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM paper_links WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute(
                "DELETE FROM memory_profile_state WHERE profile_id = ?", (profile_id,)
            )
            self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

        return {
            "profile_id": profile_id,
            "profile_name": str(profile.get("name", "")).strip(),
            "deleted_profile": True,
            "deleted_writeback_count": deleted_writeback_count,
        }

    def touch_profile(self, profile_id: int) -> None:
        self._conn.execute(
            "UPDATE profiles SET last_used_at = ? WHERE id = ?",
            (_timestamp(), profile_id),
        )
        self._conn.commit()

    def increment_paper_count(self, profile_id: int) -> None:
        self._conn.execute(
            "UPDATE profiles SET paper_count = paper_count + 1 WHERE id = ?",
            (profile_id,),
        )
        self._conn.commit()

    def _ensure_writeback(
        self,
        profile_id: int,
        job_id: str,
        paper_id: str,
        *,
        provenance_mode: str,
        created_at: float | None = None,
    ) -> int | None:
        normalized_job_id = _normalize_whitespace(job_id)
        if not normalized_job_id:
            return None
        normalized_paper_id = _normalize_whitespace(paper_id)
        now = float(created_at or _timestamp())
        existing = self._conn.execute(
            "SELECT id, provenance_mode, created_at FROM memory_writebacks WHERE profile_id = ? AND job_id = ?",
            (profile_id, normalized_job_id),
        ).fetchone()
        if existing:
            next_mode = (
                "exact"
                if str(existing["provenance_mode"]) == "exact"
                or provenance_mode == "exact"
                else provenance_mode
            )
            preserved_created_at = float(existing["created_at"] or now)
            self._conn.execute(
                "UPDATE memory_writebacks SET paper_id = ?, provenance_mode = ?, created_at = ?, deleted_at = NULL WHERE id = ?",
                (
                    normalized_paper_id or "",
                    next_mode,
                    preserved_created_at,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cur = self._conn.execute(
            "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, ?, ?, NULL)",
            (profile_id, normalized_job_id, normalized_paper_id, provenance_mode, now),
        )
        return int(cur.lastrowid)

    def _delete_writeback_events(self, writeback_id: int) -> None:
        self._conn.execute(
            "DELETE FROM memory_knowledge_events WHERE writeback_id = ?",
            (writeback_id,),
        )
        self._conn.execute(
            "DELETE FROM memory_style_events WHERE writeback_id = ?", (writeback_id,)
        )
        self._conn.execute(
            "DELETE FROM memory_link_events WHERE writeback_id = ?", (writeback_id,)
        )

    def _log_revision(
        self,
        profile_id: int,
        *,
        target_type: str,
        target_id: str | int,
        action: str,
        actor_type: str,
        summary: str,
        summary_zh: str = "",
        before: Any = None,
        after: Any = None,
        writeback_id: int | None = None,
    ) -> int | None:
        cur = self._conn.execute(
            "INSERT INTO memory_revisions (profile_id, target_type, target_id, action, actor_type, summary, summary_zh, before_json, after_json, writeback_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                _normalize_whitespace(target_type),
                str(target_id),
                _normalize_whitespace(action),
                _normalize_whitespace(actor_type),
                _normalize_whitespace(summary),
                _normalize_whitespace(summary_zh),
                _safe_json_dumps(before) if before is not None else "",
                _safe_json_dumps(after) if after is not None else "",
                writeback_id,
                _timestamp(),
            ),
        )
        return int(cur.lastrowid) if cur.lastrowid else None

    def _clear_pending_reviews_for_target(
        self, profile_id: int, *, target_type: str, target_id: int, note: str
    ) -> None:
        now = _timestamp()
        self._conn.execute(
            "UPDATE memory_review_items SET status = 'resolved', reminder_active = 0, resolution_note = ?, resolved_at = ?, updated_at = ? "
            "WHERE profile_id = ? AND target_type = ? AND target_id = ? AND status = 'pending'",
            (
                _normalize_whitespace(note),
                now,
                now,
                profile_id,
                _normalize_whitespace(target_type),
                target_id,
            ),
        )

    def _upsert_review_item(
        self,
        profile_id: int,
        *,
        target_type: str,
        target_id: int,
        review_type: str,
        title: str,
        description: str,
        default_resolution: str = "",
        suggested_payload: Any = None,
        title_zh: str = "",
        description_zh: str = "",
        default_resolution_zh: str = "",
    ) -> int:
        now = _timestamp()
        existing = self._conn.execute(
            "SELECT id FROM memory_review_items WHERE profile_id = ? AND target_type = ? AND target_id = ? AND review_type = ? AND status = 'pending' "
            "ORDER BY updated_at DESC LIMIT 1",
            (
                profile_id,
                _normalize_whitespace(target_type),
                target_id,
                _normalize_whitespace(review_type),
            ),
        ).fetchone()
        payload_text = (
            _safe_json_dumps(suggested_payload) if suggested_payload is not None else ""
        )
        if existing:
            self._conn.execute(
                "UPDATE memory_review_items SET title = ?, title_zh = ?, description = ?, description_zh = ?, default_resolution = ?, default_resolution_zh = ?, suggested_payload = ?, reminder_active = 1, updated_at = ? WHERE id = ?",
                (
                    _normalize_whitespace(title),
                    _normalize_whitespace(title_zh),
                    _normalize_whitespace(description),
                    _normalize_whitespace(description_zh),
                    _normalize_whitespace(default_resolution),
                    _normalize_whitespace(default_resolution_zh),
                    payload_text,
                    now,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cur = self._conn.execute(
            "INSERT INTO memory_review_items (profile_id, target_type, target_id, review_type, title, title_zh, description, description_zh, default_resolution, default_resolution_zh, suggested_payload, status, reminder_active, resolution_note, created_at, updated_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, '', ?, ?, NULL)",
            (
                profile_id,
                _normalize_whitespace(target_type),
                target_id,
                _normalize_whitespace(review_type),
                _normalize_whitespace(title),
                _normalize_whitespace(title_zh),
                _normalize_whitespace(description),
                _normalize_whitespace(description_zh),
                _normalize_whitespace(default_resolution),
                _normalize_whitespace(default_resolution_zh),
                payload_text,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)

    def _entity_row_to_dict(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
        item["name_localized"] = _make_localized_text(
            item.get("canonical_name", ""), item.get("canonical_name_zh", "")
        )
        item["summary_localized"] = _make_localized_text(
            item.get("summary", ""), item.get("summary_zh", "")
        )
        return item

    def _claim_row_to_dict(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
        item["scope"] = _safe_json_loads(str(item.get("scope_json", "{}")))
        item["lifecycle_reason"] = _safe_json_loads(
            str(item.get("lifecycle_reason_json", "{}"))
        )
        item["title_localized"] = _make_localized_text(
            item.get("title", ""), item.get("title_zh", "")
        )
        item["body_localized"] = _make_localized_text(
            item.get("body", ""), item.get("body_zh", "")
        )
        item["default_resolution_localized"] = _make_localized_text(
            item.get("default_resolution", ""), item.get("default_resolution_zh", "")
        )
        return item

    def _synthesis_row_to_dict(
        self, row: sqlite3.Row | dict[str, Any]
    ) -> dict[str, Any]:
        item = dict(row)
        item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
        item["title_localized"] = _make_localized_text(
            item.get("title", ""), item.get("title_zh", "")
        )
        item["summary_localized"] = _make_localized_text(
            item.get("summary", ""), item.get("summary_zh", "")
        )
        item["default_resolution_localized"] = _make_localized_text(
            item.get("default_resolution", ""), item.get("default_resolution_zh", "")
        )
        return item

    def _review_row_to_dict(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["reminder_active"] = _int_to_bool(item.get("reminder_active"))
        item["suggested_payload"] = _safe_json_loads(
            str(item.get("suggested_payload", ""))
        )
        item["title_localized"] = _make_localized_text(
            item.get("title", ""), item.get("title_zh", "")
        )
        item["description_localized"] = _make_localized_text(
            item.get("description", ""), item.get("description_zh", "")
        )
        item["default_resolution_localized"] = _make_localized_text(
            item.get("default_resolution", ""), item.get("default_resolution_zh", "")
        )
        return item

    def _find_entity_by_name(self, profile_id: int, name: str) -> dict[str, Any] | None:
        normalized_name = _normalize_lookup_key(name)
        if not normalized_name:
            return None
        row = self._conn.execute(
            "SELECT * FROM memory_entities WHERE profile_id = ? AND normalized_name = ? AND deleted_at IS NULL LIMIT 1",
            (profile_id, normalized_name),
        ).fetchone()
        return self._entity_row_to_dict(row) if row else None

    def _localize_fields(
        self,
        kind: str,
        fields: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        step_label: str = "memory localization",
    ) -> dict[str, str]:
        normalized_fields = {key: _safe_text(value) for key, value in fields.items()}
        if not any(normalized_fields.values()):
            return {key: "" for key in normalized_fields.keys()}
        if self._deferred_ctx is not None:
            idx = self._deferred_ctx.submit(
                kind, normalized_fields, context, step_label
            )
            return {
                "_deferred_idx": str(idx),
                **{key: "" for key in normalized_fields.keys()},
            }
        return translate_memory_item_sync(
            kind, normalized_fields, context=context or {}, step_label=step_label
        )

    def _localize_batch(
        self, items: list[dict[str, Any]], *, step_label: str = "memory localization"
    ) -> list[dict[str, str]]:
        return translate_memory_batch_sync(items, step_label=step_label)

    def _register_backfill(
        self,
        translated: dict[str, str],
        table: str,
        row_id: int,
        field_mapping: dict[str, str],
    ) -> None:
        """If in deferred mode, register a backfill op for later batch execution."""
        if self._deferred_ctx is None:
            return
        idx_str = translated.get("_deferred_idx")
        if idx_str is None:
            return
        self._deferred_ctx.register_backfill(int(idx_str), table, row_id, field_mapping)

    def _upsert_entity(
        self,
        profile_id: int,
        *,
        name: str,
        entity_type: str = "concept",
        summary: str = "",
        actor_type: str = "ai",
        manual_locked: bool = False,
        canonical_name_zh: str = "",
        summary_zh: str = "",
    ) -> int | None:
        canonical_name = _normalize_whitespace(name)
        if not canonical_name:
            return None
        normalized_name = _normalize_lookup_key(canonical_name)
        now = _timestamp()
        existing = self._conn.execute(
            "SELECT * FROM memory_entities WHERE profile_id = ? AND normalized_name = ? AND deleted_at IS NULL LIMIT 1",
            (profile_id, normalized_name),
        ).fetchone()
        localized = {
            "canonical_name_zh": _safe_text(canonical_name_zh),
            "summary_zh": _safe_text(summary_zh),
        }
        entity_translated: dict[str, str] = {}
        if not localized["canonical_name_zh"] or (
            _normalize_whitespace(summary) and not localized["summary_zh"]
        ):
            entity_translated = self._localize_fields(
                "entity",
                {
                    "canonical_name": canonical_name,
                    "summary": _normalize_whitespace(summary),
                },
                context={"entity_type": entity_type, "actor_type": actor_type},
                step_label=f"entity localization {canonical_name}",
            )
            localized["canonical_name_zh"] = localized[
                "canonical_name_zh"
            ] or entity_translated.get("canonical_name", "")
            localized["summary_zh"] = localized["summary_zh"] or entity_translated.get(
                "summary", ""
            )
        if existing:
            before = self._entity_row_to_dict(existing)
            updated_summary = _normalize_whitespace(summary) or before.get(
                "summary", ""
            )
            updated_summary_zh = localized["summary_zh"] or before.get("summary_zh", "")
            updated_type = _normalize_whitespace(entity_type) or before.get(
                "entity_type", "concept"
            )
            updated_name_zh = localized["canonical_name_zh"] or before.get(
                "canonical_name_zh", ""
            )
            if before.get("manual_locked") and actor_type == "ai":
                changed = (
                    updated_summary
                    and _sequence_similarity(
                        str(before.get("summary", "")), updated_summary
                    )
                    < _SIMILARITY_THRESHOLD
                )
                if changed:
                    review_title = f"Entity update suggested: {canonical_name}"
                    review_description = "A newly processed paper suggested a different entity description. Review manually before replacing the current human-edited entity."
                    review_default = str(before.get("summary", ""))
                    review_translation = self._localize_fields(
                        "review_entity_candidate_update",
                        {
                            "title": review_title,
                            "description": review_description,
                            "default_resolution": review_default,
                        },
                        context={
                            "entity_type": updated_type,
                            "target_name": canonical_name,
                        },
                        step_label=f"entity review localization {canonical_name}",
                    )
                    review_id = self._upsert_review_item(
                        profile_id,
                        target_type="entity",
                        target_id=int(before["id"]),
                        review_type="candidate_update",
                        title=review_title,
                        title_zh=review_translation.get("title", ""),
                        description=review_description,
                        description_zh=review_translation.get("description", ""),
                        default_resolution=review_default,
                        default_resolution_zh=review_translation.get(
                            "default_resolution", ""
                        ),
                        suggested_payload=_merge_suggested_payload_translation(
                            {
                                "name": canonical_name,
                                "entity_type": updated_type,
                                "summary": updated_summary,
                            },
                            {"name": updated_name_zh, "summary": updated_summary_zh},
                        ),
                    )
                    self._register_backfill(
                        entity_translated,
                        "memory_entities",
                        int(before["id"]),
                        {
                            "canonical_name": "canonical_name_zh",
                            "summary": "summary_zh",
                        },
                    )
                    if review_id is not None:
                        self._register_backfill(
                            review_translation,
                            "memory_review_items",
                            review_id,
                            {
                                "title": "title_zh",
                                "description": "description_zh",
                                "default_resolution": "default_resolution_zh",
                            },
                        )
                    return int(before["id"])
            self._conn.execute(
                "UPDATE memory_entities SET canonical_name = ?, canonical_name_zh = ?, entity_type = ?, summary = ?, summary_zh = ?, manual_locked = ?, updated_at = ? WHERE id = ?",
                (
                    canonical_name,
                    updated_name_zh,
                    updated_type,
                    updated_summary,
                    updated_summary_zh,
                    _bool_to_int(
                        before.get("manual_locked")
                        or manual_locked
                        or actor_type == "user"
                    ),
                    now,
                    int(before["id"]),
                ),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_entity_aliases (entity_id, alias, normalized_alias, created_at) VALUES (?, ?, ?, ?)",
                (int(before["id"]), canonical_name, normalized_name, now),
            )
            self._register_backfill(
                entity_translated,
                "memory_entities",
                int(before["id"]),
                {"canonical_name": "canonical_name_zh", "summary": "summary_zh"},
            )
            if actor_type == "user" and (
                canonical_name != str(before.get("canonical_name", ""))
                or updated_type != str(before.get("entity_type", ""))
                or updated_summary != str(before.get("summary", ""))
            ):
                revision_translation = self._localize_fields(
                    "entity_revision_update",
                    {"summary": f"Updated entity {canonical_name}"},
                    context={"entity_type": updated_type},
                    step_label=f"entity revision localization {canonical_name}",
                )
                self._log_revision(
                    profile_id,
                    target_type="entity",
                    target_id=int(before["id"]),
                    action="update",
                    actor_type=actor_type,
                    summary=f"Updated entity {canonical_name}",
                    summary_zh=revision_translation.get("summary", ""),
                    before=before,
                    after={
                        **before,
                        "canonical_name": canonical_name,
                        "canonical_name_zh": updated_name_zh,
                        "entity_type": updated_type,
                        "summary": updated_summary,
                        "summary_zh": updated_summary_zh,
                        "manual_locked": True,
                    },
                )
            return int(before["id"])
        cur = self._conn.execute(
            "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL)",
            (
                profile_id,
                canonical_name,
                localized["canonical_name_zh"],
                normalized_name,
                _normalize_whitespace(entity_type) or "concept",
                _normalize_whitespace(summary),
                localized["summary_zh"],
                _bool_to_int(manual_locked or actor_type == "user"),
                now,
                now,
            ),
        )
        entity_id = int(cur.lastrowid)
        self._conn.execute(
            "INSERT OR IGNORE INTO memory_entity_aliases (entity_id, alias, normalized_alias, created_at) VALUES (?, ?, ?, ?)",
            (entity_id, canonical_name, normalized_name, now),
        )
        self._register_backfill(
            entity_translated,
            "memory_entities",
            entity_id,
            {"canonical_name": "canonical_name_zh", "summary": "summary_zh"},
        )
        if actor_type == "user":
            revision_translation = self._localize_fields(
                "entity_revision_create",
                {"summary": f"Created entity {canonical_name}"},
                context={"entity_type": entity_type},
                step_label=f"entity revision localization {canonical_name}",
            )
            self._log_revision(
                profile_id,
                target_type="entity",
                target_id=entity_id,
                action="create",
                actor_type=actor_type,
                summary=f"Created entity {canonical_name}",
                summary_zh=revision_translation.get("summary", ""),
                after={
                    "id": entity_id,
                    "canonical_name": canonical_name,
                    "canonical_name_zh": localized["canonical_name_zh"],
                    "entity_type": _normalize_whitespace(entity_type) or "concept",
                    "summary": _normalize_whitespace(summary),
                    "summary_zh": localized["summary_zh"],
                    "manual_locked": True,
                },
            )
        return entity_id

    def list_entities(
        self, profile_id: int, *, limit: int | None = 200
    ) -> list[dict[str, Any]]:
        resolved_limit = self._resolve_limit(limit, 200)
        query = (
            "SELECT e.*, COUNT(DISTINCT c.id) AS claim_count "
            "FROM memory_entities e "
            "LEFT JOIN memory_claim_entities ce ON ce.entity_id = e.id "
            "LEFT JOIN memory_claims c ON c.id = ce.claim_id AND c.deleted_at IS NULL "
            "WHERE e.profile_id = ? AND e.deleted_at IS NULL "
            "GROUP BY e.id ORDER BY e.manual_locked DESC, e.updated_at DESC, e.created_at DESC"
        )
        params: tuple[Any, ...]
        if resolved_limit > 0:
            query += " LIMIT ?"
            params = (profile_id, resolved_limit)
        else:
            params = (profile_id,)
        rows = self._conn.execute(query, params).fetchall()
        return [self._entity_row_to_dict(row) for row in rows]

    def save_entity(
        self, profile_id: int, payload: dict[str, Any], *, entity_id: int | None = None
    ) -> dict[str, Any]:
        name = _normalize_whitespace(payload.get("name", ""))
        if not name:
            raise ValueError("Entity name is required")
        summary = _normalize_whitespace(payload.get("summary", ""))
        entity_type = (
            _normalize_whitespace(payload.get("entity_type", "concept")) or "concept"
        )
        aliases = _dedupe_strings(
            [name, *[str(item) for item in payload.get("aliases", [])]]
        )
        now = _timestamp()
        localized = self._localize_fields(
            "entity",
            {"canonical_name": name, "summary": summary},
            context={"entity_type": entity_type, "mode": "manual_edit"},
            step_label=f"entity save localization {name}",
        )
        if entity_id is None:
            resolved_id = self._upsert_entity(
                profile_id,
                name=name,
                entity_type=entity_type,
                summary=summary,
                canonical_name_zh=localized.get("canonical_name", ""),
                summary_zh=localized.get("summary", ""),
                actor_type="user",
                manual_locked=True,
            )
            if resolved_id is None:
                raise ValueError("Unable to create entity")
            entity_id = resolved_id
        else:
            existing = self._conn.execute(
                "SELECT * FROM memory_entities WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
                (entity_id, profile_id),
            ).fetchone()
            if existing is None:
                raise ValueError("Entity not found")
            before = self._entity_row_to_dict(existing)
            revision_translation = self._localize_fields(
                "entity_revision_update",
                {"summary": f"Updated entity {name}"},
                context={"entity_type": entity_type},
                step_label=f"entity revision localization {name}",
            )
            self._conn.execute(
                "UPDATE memory_entities SET canonical_name = ?, canonical_name_zh = ?, normalized_name = ?, entity_type = ?, summary = ?, summary_zh = ?, manual_locked = 1, updated_at = ? WHERE id = ?",
                (
                    name,
                    localized.get("canonical_name", ""),
                    _normalize_lookup_key(name),
                    entity_type,
                    summary,
                    localized.get("summary", ""),
                    now,
                    entity_id,
                ),
            )
            self._log_revision(
                profile_id,
                target_type="entity",
                target_id=entity_id,
                action="update",
                actor_type="user",
                summary=f"Updated entity {name}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "canonical_name": name,
                    "canonical_name_zh": localized.get("canonical_name", ""),
                    "entity_type": entity_type,
                    "summary": summary,
                    "summary_zh": localized.get("summary", ""),
                    "manual_locked": True,
                },
            )
        for alias in aliases:
            normalized_alias = _normalize_lookup_key(alias)
            if normalized_alias:
                self._conn.execute(
                    "INSERT OR IGNORE INTO memory_entity_aliases (entity_id, alias, normalized_alias, created_at) VALUES (?, ?, ?, ?)",
                    (entity_id, alias, normalized_alias, now),
                )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="entity",
            target_id=entity_id,
            note="Resolved by manual entity edit",
        )
        self._conn.commit()
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        row = self._conn.execute(
            "SELECT * FROM memory_entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._entity_row_to_dict(row) if row else {}

    def delete_entity(self, profile_id: int, entity_id: int) -> None:
        row = self._conn.execute(
            "SELECT * FROM memory_entities WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
            (entity_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Entity not found")
        before = self._entity_row_to_dict(row)
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_entities SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (deleted_at, deleted_at, entity_id),
        )
        self._conn.execute(
            "DELETE FROM memory_claim_entities WHERE entity_id = ?", (entity_id,)
        )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="entity",
            target_id=entity_id,
            note="Entity deleted manually",
        )
        revision_translation = self._localize_fields(
            "entity_revision_delete",
            {"summary": f"Deleted entity {before.get('canonical_name', '')}"},
            context={"entity_type": before.get("entity_type", "")},
            step_label=f"entity revision localization delete-{entity_id}",
        )
        self._log_revision(
            profile_id,
            target_type="entity",
            target_id=entity_id,
            action="delete",
            actor_type="user",
            summary=f"Deleted entity {before.get('canonical_name', '')}",
            summary_zh=revision_translation.get("summary", ""),
            before=before,
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

    def _get_active_claim_by_key(
        self, profile_id: int, claim_key: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memory_claims WHERE profile_id = ? AND claim_key = ? AND deleted_at IS NULL ORDER BY manual_locked DESC, updated_at DESC LIMIT 1",
            (profile_id, claim_key),
        ).fetchone()
        return self._claim_row_to_dict(row) if row else None

    def _sync_claim_entities(self, claim_id: int, entity_ids: list[int]) -> None:
        self._conn.execute(
            "DELETE FROM memory_claim_entities WHERE claim_id = ?", (claim_id,)
        )
        now = _timestamp()
        for entity_id in entity_ids:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                (claim_id, entity_id, now),
            )

    def _append_claim_evidence(
        self,
        claim_id: int,
        writeback_id: int | None,
        evidence_items: list[dict[str, Any]],
        *,
        actor_type: str = "ai",
    ) -> None:
        now = _timestamp()
        for index, evidence in enumerate(evidence_items):
            if not isinstance(evidence, dict):
                continue
            snippet = _normalize_whitespace(evidence.get("snippet", ""))
            if not snippet:
                continue
            section_key = (
                _normalize_whitespace(evidence.get("section_key", "other")) or "other"
            )
            section_title = _normalize_whitespace(evidence.get("section_title", ""))
            evidence_summary = _normalize_whitespace(
                evidence.get("summary", evidence.get("evidence_summary", ""))
            )
            page_label = _normalize_whitespace(evidence.get("page_label", ""))
            page_start = _maybe_int(evidence.get("page_start"))
            page_end = _maybe_int(evidence.get("page_end"))
            anchor_kind = (
                _normalize_whitespace(evidence.get("anchor_kind", "text")) or "text"
            )
            context_before = _normalize_whitespace(evidence.get("context_before", ""))
            context_after = _normalize_whitespace(evidence.get("context_after", ""))
            structured_signal_json = _safe_json_dumps(
                evidence.get("structured_signal", {})
                if isinstance(evidence.get("structured_signal", {}), dict)
                else {}
            )
            section_title_zh = _safe_text(evidence.get("section_title_zh", ""))
            snippet_zh = _safe_text(evidence.get("snippet_zh", ""))
            evidence_summary_zh = _safe_text(
                evidence.get("evidence_summary_zh", evidence.get("summary_zh", ""))
            )
            evidence_translated: dict[str, str] = {}
            if (
                not section_title_zh
                or not snippet_zh
                or (evidence_summary and not evidence_summary_zh)
            ):
                evidence_translated = self._localize_fields(
                    "evidence",
                    {
                        "section_title": section_title,
                        "snippet": snippet,
                        "evidence_summary": evidence_summary,
                    },
                    context={
                        "section_key": section_key,
                        "page_label": page_label,
                        "actor_type": actor_type,
                    },
                    step_label=f"evidence localization claim-{claim_id}-{index}",
                )
                section_title_zh = section_title_zh or evidence_translated.get(
                    "section_title", ""
                )
                snippet_zh = snippet_zh or evidence_translated.get("snippet", "")
                evidence_summary_zh = evidence_summary_zh or evidence_translated.get(
                    "evidence_summary", ""
                )
            existing = self._conn.execute(
                "SELECT id FROM memory_claim_evidence WHERE claim_id = ? AND deleted_at IS NULL AND snippet = ? AND section_key = ? AND COALESCE(page_label, '') = ? LIMIT 1",
                (claim_id, snippet, section_key, page_label),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE memory_claim_evidence SET evidence_summary = ?, evidence_summary_zh = ?, section_title = ?, section_title_zh = ?, snippet_zh = ?, anchor_kind = ?, context_before = ?, context_after = ?, structured_signal_json = ?, updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (
                        evidence_summary,
                        evidence_summary_zh,
                        section_title,
                        section_title_zh,
                        snippet_zh,
                        anchor_kind,
                        context_before,
                        context_after,
                        structured_signal_json,
                        now + index * 0.0001,
                        int(existing["id"]),
                    ),
                )
                self._register_backfill(
                    evidence_translated,
                    "memory_claim_evidence",
                    int(existing["id"]),
                    {
                        "section_title": "section_title_zh",
                        "snippet": "snippet_zh",
                        "evidence_summary": "evidence_summary_zh",
                    },
                )
                continue
            cur = self._conn.execute(
                "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, anchor_kind, context_before, context_after, structured_signal_json, weight, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    claim_id,
                    writeback_id,
                    section_key,
                    section_title,
                    section_title_zh,
                    snippet,
                    snippet_zh,
                    evidence_summary,
                    evidence_summary_zh,
                    page_label,
                    page_start,
                    page_end,
                    anchor_kind,
                    context_before,
                    context_after,
                    structured_signal_json,
                    1.0,
                    _bool_to_int(actor_type == "user"),
                    now + index * 0.0001,
                    now + index * 0.0001,
                ),
            )
            self._register_backfill(
                evidence_translated,
                "memory_claim_evidence",
                int(cur.lastrowid),
                {
                    "section_title": "section_title_zh",
                    "snippet": "snippet_zh",
                    "evidence_summary": "evidence_summary_zh",
                },
            )

    def _claim_payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "claim_key": str(row.get("claim_key", "")),
            "title": str(row.get("title", "")),
            "title_zh": str(row.get("title_zh", "")),
            "body": str(row.get("body", "")),
            "body_zh": str(row.get("body_zh", "")),
            "claim_type": str(row.get("claim_type", "finding")),
            "stance": str(row.get("stance", "support")),
            "importance": float(row.get("importance", 0.5) or 0.5),
            "status": str(row.get("status", "active")),
            "default_resolution": str(row.get("default_resolution", "")),
            "default_resolution_zh": str(row.get("default_resolution_zh", "")),
            "review_status": str(row.get("review_status", "none")),
            "manual_locked": bool(row.get("manual_locked", False)),
        }

    def _upsert_claim_from_extraction(
        self,
        profile_id: int,
        writeback_id: int,
        claim: dict[str, Any],
        entity_id_cache: dict[str, int],
    ) -> int | None:
        title = _normalize_whitespace(claim.get("title", ""))
        body = _normalize_whitespace(claim.get("body", ""))
        if not title and not body:
            return None
        title, body = _normalize_claim_text(title, body)
        claim_key = _normalize_whitespace(
            claim.get("claim_key", "")
        ) or _claim_default_key(title, body)
        claim_type = (
            _normalize_whitespace(claim.get("claim_type", "finding")) or "finding"
        )
        stance = _normalize_whitespace(claim.get("stance", "support")) or "support"
        importance = min(max(_maybe_float(claim.get("importance"), 0.5), 0.0), 1.0)
        scope_payload = _normalize_scope_payload(claim.get("scope", {}))
        scope_json = _safe_json_dumps(scope_payload)
        default_resolution = (
            _normalize_whitespace(claim.get("default_resolution", "")) or body
        )
        title_zh = _safe_text(claim.get("title_zh", ""))
        body_zh = _safe_text(claim.get("body_zh", ""))
        default_resolution_zh = _safe_text(claim.get("default_resolution_zh", ""))
        claim_translated: dict[str, str] = {}
        if (
            not title_zh
            or not body_zh
            or (default_resolution and not default_resolution_zh)
        ):
            claim_translated = self._localize_fields(
                "claim",
                {
                    "title": title or body[:80],
                    "body": body or title,
                    "default_resolution": default_resolution,
                },
                context={
                    "claim_type": claim_type,
                    "stance": stance,
                    "importance": importance,
                },
                step_label=f"claim localization {claim_key}",
            )
            title_zh = title_zh or claim_translated.get("title", "")
            body_zh = body_zh or claim_translated.get("body", "")
            default_resolution_zh = default_resolution_zh or claim_translated.get(
                "default_resolution", ""
            )
        entity_names = _dedupe_strings(
            [str(item) for item in claim.get("entity_names", [])]
        )
        entity_ids: list[int] = []
        for entity_name in entity_names:
            cache_key = _normalize_lookup_key(entity_name)
            entity_id = entity_id_cache.get(cache_key)
            if entity_id is None:
                entity_id = self._upsert_entity(
                    profile_id, name=entity_name, actor_type="ai"
                )
                if entity_id is not None:
                    entity_id_cache[cache_key] = entity_id
            if entity_id is not None:
                entity_ids.append(entity_id)
        now = _timestamp()
        existing = self._get_active_claim_by_key(profile_id, claim_key)
        candidate_payload = {
            "claim_key": claim_key,
            "title": title or body[:80],
            "title_zh": title_zh,
            "body": body or title,
            "body_zh": body_zh,
            "claim_type": claim_type,
            "stance": stance,
            "importance": importance,
            "default_resolution": default_resolution,
            "default_resolution_zh": default_resolution_zh,
            "scope": scope_payload,
            "entity_names": entity_names,
        }
        if existing is None:
            cur = self._conn.execute(
                "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, scope_json, review_status, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 'none', 0, ?, ?, NULL)",
                (
                    profile_id,
                    writeback_id,
                    claim_key,
                    candidate_payload["title"],
                    candidate_payload["title_zh"],
                    candidate_payload["body"],
                    candidate_payload["body_zh"],
                    claim_type,
                    stance,
                    importance,
                    default_resolution,
                    candidate_payload["default_resolution_zh"],
                    scope_json,
                    now,
                    now,
                ),
            )
            claim_id = int(cur.lastrowid)
            self._register_backfill(
                claim_translated,
                "memory_claims",
                claim_id,
                {
                    "title": "title_zh",
                    "body": "body_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            self._sync_claim_entities(claim_id, entity_ids)
            self._append_claim_evidence(
                claim_id, writeback_id, claim.get("evidence", []), actor_type="ai"
            )
            return claim_id

        claim_id = int(existing["id"])
        before = dict(existing)
        changed_meaningfully = (
            (
                _normalize_whitespace(title)
                and _sequence_similarity(str(existing.get("title", "")), title)
                < _SIMILARITY_THRESHOLD
            )
            or (
                body
                and _sequence_similarity(str(existing.get("body", "")), body)
                < _SIMILARITY_THRESHOLD
            )
            or (stance and stance != str(existing.get("stance", "support")))
        )

        next_title = title or str(existing.get("title", ""))
        next_title_zh = title_zh or str(existing.get("title_zh", ""))
        next_body = body or str(existing.get("body", ""))
        next_body_zh = body_zh or str(existing.get("body_zh", ""))
        next_claim_type = claim_type or str(existing.get("claim_type", "finding"))
        next_importance = max(float(existing.get("importance", 0.5) or 0.5), importance)
        next_status = str(existing.get("status", "active"))
        next_review_status = str(existing.get("review_status", "none"))
        next_default_resolution = (
            str(existing.get("default_resolution", "")) or next_body
        )
        next_default_resolution_zh = default_resolution_zh or str(
            existing.get("default_resolution_zh", "")
        )
        next_stance = str(existing.get("stance", "support"))

        if bool(existing.get("manual_locked")) and changed_meaningfully:
            review_title = f"Candidate update for claim: {next_title or existing.get('title', claim_key)}"
            review_description = "A newly processed paper suggested an update to a manually edited claim. The current human-edited claim remains active until you review it."
            review_default = str(
                existing.get("default_resolution", existing.get("body", ""))
            )
            review_translation = self._localize_fields(
                "review_claim_candidate_update",
                {
                    "title": review_title,
                    "description": review_description,
                    "default_resolution": review_default,
                },
                context={
                    "claim_type": claim_type,
                    "stance": stance,
                    "claim_key": claim_key,
                },
                step_label=f"claim review localization {claim_key}",
            )
            review_id = self._upsert_review_item(
                profile_id,
                target_type="claim",
                target_id=claim_id,
                review_type="candidate_update",
                title=review_title,
                title_zh=review_translation.get("title", ""),
                description=review_description,
                description_zh=review_translation.get("description", ""),
                default_resolution=review_default,
                default_resolution_zh=review_translation.get("default_resolution", ""),
                suggested_payload=_merge_suggested_payload_translation(
                    {**candidate_payload, "evidence": claim.get("evidence", [])},
                    {
                        "title": next_title_zh,
                        "body": next_body_zh,
                        "default_resolution": default_resolution_zh,
                    },
                ),
            )
            self._register_backfill(
                claim_translated,
                "memory_claims",
                claim_id,
                {
                    "title": "title_zh",
                    "body": "body_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            self._register_backfill(
                review_translation,
                "memory_review_items",
                review_id,
                {
                    "title": "title_zh",
                    "description": "description_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            return claim_id

        if stance and stance != str(existing.get("stance", "support")):
            next_status = "conflicted"
            next_review_status = "pending"
            if importance >= float(existing.get("importance", 0.5) or 0.5):
                next_stance = stance
                next_title = title or next_title
                next_title_zh = title_zh or next_title_zh
                next_body = body or next_body
                next_body_zh = body_zh or next_body_zh
            next_default_resolution = next_body
            next_default_resolution_zh = next_body_zh
            review_title = f"Conflict needs review: {next_title}"
            review_description = "Different papers disagree on this claim. A default resolution is kept for future jobs until you manually review it."
            review_translation = self._localize_fields(
                "review_claim_conflict",
                {
                    "title": review_title,
                    "description": review_description,
                    "default_resolution": next_default_resolution,
                },
                context={
                    "claim_type": claim_type,
                    "stance": stance,
                    "claim_key": claim_key,
                },
                step_label=f"claim conflict localization {claim_key}",
            )
            conflict_review_id = self._upsert_review_item(
                profile_id,
                target_type="claim",
                target_id=claim_id,
                review_type="conflict",
                title=review_title,
                title_zh=review_translation.get("title", ""),
                description=review_description,
                description_zh=review_translation.get("description", ""),
                default_resolution=next_default_resolution,
                default_resolution_zh=review_translation.get("default_resolution", ""),
                suggested_payload=_merge_suggested_payload_translation(
                    {**candidate_payload, "evidence": claim.get("evidence", [])},
                    {
                        "title": title_zh,
                        "body": body_zh,
                        "default_resolution": default_resolution_zh,
                    },
                ),
            )
            self._register_backfill(
                review_translation,
                "memory_review_items",
                conflict_review_id,
                {
                    "title": "title_zh",
                    "description": "description_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
        else:
            if title and (
                len(title) >= len(str(existing.get("title", "")))
                or not existing.get("title")
            ):
                next_title = title
                next_title_zh = title_zh or next_title_zh
            if body and (
                importance >= float(existing.get("importance", 0.5) or 0.5)
                or len(body) >= len(str(existing.get("body", "")))
            ):
                next_body = body
                next_body_zh = body_zh or next_body_zh
                next_default_resolution = body
                next_default_resolution_zh = body_zh or next_default_resolution_zh
            if next_status != "conflicted":
                next_status = "active"
                next_review_status = "none"

            self._conn.execute(
                "UPDATE memory_claims SET title = ?, title_zh = ?, body = ?, body_zh = ?, claim_type = ?, stance = ?, importance = ?, status = ?, default_resolution = ?, default_resolution_zh = ?, scope_json = ?, review_status = ?, updated_at = ? WHERE id = ?",
                (
                    next_title,
                    next_title_zh,
                    next_body,
                next_body_zh,
                next_claim_type,
                next_stance,
                next_importance,
                    next_status,
                    next_default_resolution,
                    next_default_resolution_zh,
                    scope_json,
                    next_review_status,
                    now,
                    claim_id,
                ),
            )
        self._register_backfill(
            claim_translated,
            "memory_claims",
            claim_id,
            {
                "title": "title_zh",
                "body": "body_zh",
                "default_resolution": "default_resolution_zh",
            },
        )
        self._sync_claim_entities(
            claim_id,
            entity_ids
            or [
                int(item["entity_id"])
                for item in self._conn.execute(
                    "SELECT entity_id FROM memory_claim_entities WHERE claim_id = ?",
                    (claim_id,),
                ).fetchall()
            ],
        )
        self._append_claim_evidence(
            claim_id, writeback_id, claim.get("evidence", []), actor_type="ai"
        )
        if changed_meaningfully:
            revision_translation = self._localize_fields(
                "claim_revision_update",
                {"summary": f"Updated claim {claim_key}"},
                context={"claim_type": claim_type, "stance": next_stance},
                step_label=f"claim revision localization {claim_key}",
            )
            revision_id = self._log_revision(
                profile_id,
                target_type="claim",
                target_id=claim_id,
                action="auto_update",
                actor_type="ai",
                summary=f"Updated claim {claim_key}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "title": next_title,
                    "title_zh": next_title_zh,
                    "body": next_body,
                    "body_zh": next_body_zh,
                    "claim_type": next_claim_type,
                    "stance": next_stance,
                    "importance": next_importance,
                    "status": next_status,
                    "default_resolution": next_default_resolution,
                    "default_resolution_zh": next_default_resolution_zh,
                    "scope": scope_payload,
                    "review_status": next_review_status,
                },
                writeback_id=writeback_id,
            )
            if revision_id is not None:
                self._register_backfill(
                    revision_translation,
                    "memory_revisions",
                    revision_id,
                    {"summary": "summary_zh"},
                )
        return claim_id

    def list_claims(self, profile_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._get_active_claim_rows(profile_id, limit=limit)
        entity_names_by_claim = self._fetch_claim_entity_names_map(
            [int(row["id"]) for row in rows]
        )
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = self._claim_row_to_dict(row)
            item["entity_names"] = entity_names_by_claim.get(int(item["id"]), [])
            payloads.append(item)
        return payloads

    def save_claim(
        self, profile_id: int, payload: dict[str, Any], *, claim_id: int | None = None
    ) -> dict[str, Any]:
        title = _normalize_whitespace(payload.get("title", ""))
        body = _normalize_whitespace(payload.get("body", ""))
        if not title and not body:
            raise ValueError("Claim title or body is required")
        claim_key = _normalize_whitespace(
            payload.get("claim_key", "")
        ) or _claim_default_key(title, body)
        claim_type = (
            _normalize_whitespace(payload.get("claim_type", "finding")) or "finding"
        )
        stance = _normalize_whitespace(payload.get("stance", "support")) or "support"
        importance = min(max(_maybe_float(payload.get("importance"), 0.5), 0.0), 1.0)
        status = _normalize_whitespace(payload.get("status", "active")) or "active"
        scope_payload = _normalize_scope_payload(payload.get("scope", {}))
        scope_json = _safe_json_dumps(scope_payload)
        default_resolution = (
            _normalize_whitespace(payload.get("default_resolution", ""))
            or body
            or title
        )
        localized = self._localize_fields(
            "claim",
            {
                "title": title or body[:80],
                "body": body or title,
                "default_resolution": default_resolution,
            },
            context={
                "claim_type": claim_type,
                "stance": stance,
                "importance": importance,
                "mode": "manual_edit",
            },
            step_label=f"claim save localization {claim_key}",
        )
        entity_names = _dedupe_strings(
            [str(item) for item in payload.get("entity_names", [])]
        )
        entity_ids: list[int] = []
        for entity_name in entity_names:
            entity_id = self._upsert_entity(
                profile_id, name=entity_name, actor_type="user", manual_locked=True
            )
            if entity_id is not None:
                entity_ids.append(entity_id)
        now = _timestamp()
        if claim_id is None:
            cur = self._conn.execute(
                "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, scope_json, review_status, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'resolved', 1, ?, ?, NULL)",
                (
                    profile_id,
                    claim_key,
                    title or body[:80],
                    localized.get("title", ""),
                    body or title,
                    localized.get("body", ""),
                    claim_type,
                    stance,
                    importance,
                    status,
                    default_resolution,
                    localized.get("default_resolution", ""),
                    scope_json,
                    now,
                    now,
                ),
            )
            claim_id = int(cur.lastrowid)
            self._sync_claim_entities(claim_id, entity_ids)
            revision_translation = self._localize_fields(
                "claim_revision_create",
                {"summary": f"Created claim {claim_key}"},
                context={"claim_type": claim_type, "stance": stance},
                step_label=f"claim revision localization {claim_key}",
            )
            self._log_revision(
                profile_id,
                target_type="claim",
                target_id=claim_id,
                action="create",
                actor_type="user",
                summary=f"Created claim {claim_key}",
                summary_zh=revision_translation.get("summary", ""),
                after={
                    "claim_key": claim_key,
                    "title": title or body[:80],
                    "title_zh": localized.get("title", ""),
                    "body": body or title,
                    "body_zh": localized.get("body", ""),
                    "claim_type": claim_type,
                    "stance": stance,
                    "importance": importance,
                    "status": status,
                    "default_resolution": default_resolution,
                    "default_resolution_zh": localized.get("default_resolution", ""),
                    "scope": scope_payload,
                    "entity_names": entity_names,
                    "manual_locked": True,
                },
            )
        else:
            row = self._conn.execute(
                "SELECT * FROM memory_claims WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
                (claim_id, profile_id),
            ).fetchone()
            if row is None:
                raise ValueError("Claim not found")
            before = self._claim_row_to_dict(row)
            self._conn.execute(
                "UPDATE memory_claims SET claim_key = ?, title = ?, title_zh = ?, body = ?, body_zh = ?, claim_type = ?, stance = ?, importance = ?, status = ?, default_resolution = ?, default_resolution_zh = ?, scope_json = ?, review_status = 'resolved', manual_locked = 1, updated_at = ? WHERE id = ?",
                (
                    claim_key,
                    title or body[:80],
                    localized.get("title", ""),
                    body or title,
                    localized.get("body", ""),
                    claim_type,
                    stance,
                    importance,
                    status,
                    default_resolution,
                    localized.get("default_resolution", ""),
                    scope_json,
                    now,
                    claim_id,
                ),
            )
            self._sync_claim_entities(claim_id, entity_ids)
            revision_translation = self._localize_fields(
                "claim_revision_update",
                {"summary": f"Updated claim {claim_key}"},
                context={"claim_type": claim_type, "stance": stance},
                step_label=f"claim revision localization {claim_key}",
            )
            self._log_revision(
                profile_id,
                target_type="claim",
                target_id=claim_id,
                action="update",
                actor_type="user",
                summary=f"Updated claim {claim_key}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "claim_key": claim_key,
                    "title": title or body[:80],
                    "title_zh": localized.get("title", ""),
                    "body": body or title,
                    "body_zh": localized.get("body", ""),
                    "claim_type": claim_type,
                    "stance": stance,
                    "importance": importance,
                    "status": status,
                    "default_resolution": default_resolution,
                    "default_resolution_zh": localized.get("default_resolution", ""),
                    "scope": scope_payload,
                    "entity_names": entity_names,
                    "manual_locked": True,
                    "review_status": "resolved",
                },
            )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="claim",
            target_id=claim_id,
            note="Resolved by manual claim edit",
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        claim_row = self._conn.execute(
            "SELECT * FROM memory_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        item = self._claim_row_to_dict(claim_row) if claim_row else {}
        item["entity_names"] = entity_names
        return item

    def delete_claim(self, profile_id: int, claim_id: int) -> None:
        row = self._conn.execute(
            "SELECT * FROM memory_claims WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
            (claim_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Claim not found")
        before = self._claim_row_to_dict(row)
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_claims SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (deleted_at, deleted_at, claim_id),
        )
        self._conn.execute(
            "UPDATE memory_claim_evidence SET deleted_at = ?, updated_at = ? WHERE claim_id = ? AND deleted_at IS NULL",
            (deleted_at, deleted_at, claim_id),
        )
        self._conn.execute(
            "DELETE FROM memory_claim_entities WHERE claim_id = ?", (claim_id,)
        )
        self._conn.execute(
            "DELETE FROM memory_synthesis_claims WHERE claim_id = ?", (claim_id,)
        )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="claim",
            target_id=claim_id,
            note="Claim deleted manually",
        )
        revision_translation = self._localize_fields(
            "claim_revision_delete",
            {"summary": f"Deleted claim {before.get('claim_key', '')}"},
            context={
                "claim_type": before.get("claim_type", ""),
                "stance": before.get("stance", ""),
            },
            step_label=f"claim revision localization delete-{claim_id}",
        )
        self._log_revision(
            profile_id,
            target_type="claim",
            target_id=claim_id,
            action="delete",
            actor_type="user",
            summary=f"Deleted claim {before.get('claim_key', '')}",
            summary_zh=revision_translation.get("summary", ""),
            before=before,
        )
        self._conn.commit()
        self._prune_orphaned_synthesis(profile_id)
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

    def list_evidence(
        self, profile_id: int, *, limit: int | None = 300
    ) -> list[dict[str, Any]]:
        rows = self._get_active_evidence_rows(profile_id, limit=limit)
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
            structured_signal = _safe_json_loads(
                str(item.get("structured_signal_json", ""))
            )
            item["structured_signal"] = (
                structured_signal if isinstance(structured_signal, dict) else {}
            )
            item["section_title_localized"] = _make_localized_text(
                item.get("section_title", ""), item.get("section_title_zh", "")
            )
            item["snippet_localized"] = _make_localized_text(
                item.get("snippet", ""), item.get("snippet_zh", "")
            )
            item["evidence_summary_localized"] = _make_localized_text(
                item.get("evidence_summary", ""), item.get("evidence_summary_zh", "")
            )
            item["claim_title_localized"] = _make_localized_text(
                item.get("claim_title", ""), item.get("claim_title_zh", "")
            )
            payloads.append(item)
        return payloads

    def save_evidence(
        self,
        profile_id: int,
        payload: dict[str, Any],
        *,
        evidence_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_payload = _normalize_manual_evidence_payload(payload)
        claim_id = _maybe_int(normalized_payload.get("claim_id"))
        if claim_id is None:
            raise ValueError("claim_id is required")
        claim_row = self._conn.execute(
            "SELECT id FROM memory_claims WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
            (claim_id, profile_id),
        ).fetchone()
        if claim_row is None:
            raise ValueError("Claim not found")
        snippet = _normalize_whitespace(normalized_payload.get("snippet", ""))
        if not snippet:
            raise ValueError("Evidence snippet is required")
        section_key = str(normalized_payload.get("section_key", "other"))
        section_title = str(normalized_payload.get("section_title", ""))
        evidence_summary = str(normalized_payload.get("evidence_summary", ""))
        page_label = str(normalized_payload.get("page_label", ""))
        page_start = _maybe_int(normalized_payload.get("page_start"))
        page_end = _maybe_int(normalized_payload.get("page_end"))
        anchor_kind = str(normalized_payload.get("anchor_kind", "text")) or "text"
        context_before = str(normalized_payload.get("context_before", ""))
        context_after = str(normalized_payload.get("context_after", ""))
        structured_signal_json = _safe_json_dumps(
            normalized_payload.get("structured_signal", {})
        )
        localized = self._localize_fields(
            "evidence",
            {
                "section_title": section_title,
                "snippet": snippet,
                "evidence_summary": evidence_summary,
            },
            context={
                "section_key": section_key,
                "page_label": page_label,
                "mode": "manual_edit",
            },
            step_label=f"evidence save localization claim-{claim_id}",
        )
        now = _timestamp()
        if evidence_id is None:
            cur = self._conn.execute(
                "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, anchor_kind, context_before, context_after, structured_signal_json, weight, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 1, ?, ?, NULL)",
                (
                    claim_id,
                    section_key,
                    section_title,
                    localized.get("section_title", ""),
                    snippet,
                    localized.get("snippet", ""),
                    evidence_summary,
                    localized.get("evidence_summary", ""),
                    page_label,
                    page_start,
                    page_end,
                    anchor_kind,
                    context_before,
                    context_after,
                    structured_signal_json,
                    now,
                    now,
                ),
            )
            evidence_id = int(cur.lastrowid)
            revision_translation = self._localize_fields(
                "evidence_revision_create",
                {"summary": f"Added evidence to claim #{claim_id}"},
                context={"section_key": section_key},
                step_label=f"evidence revision localization create-{claim_id}",
            )
            self._log_revision(
                profile_id,
                target_type="evidence",
                target_id=evidence_id,
                action="create",
                actor_type="user",
                summary=f"Added evidence to claim #{claim_id}",
                summary_zh=revision_translation.get("summary", ""),
                after={
                    "claim_id": claim_id,
                    "section_key": section_key,
                    "section_title": section_title,
                    "section_title_zh": localized.get("section_title", ""),
                    "snippet": snippet,
                    "snippet_zh": localized.get("snippet", ""),
                    "evidence_summary": evidence_summary,
                    "evidence_summary_zh": localized.get("evidence_summary", ""),
                    "page_label": page_label,
                    "page_start": page_start,
                    "page_end": page_end,
                    "anchor_kind": anchor_kind,
                    "context_before": context_before,
                    "context_after": context_after,
                    "structured_signal": normalized_payload.get("structured_signal", {}),
                },
            )
        else:
            row = self._conn.execute(
                "SELECT e.* FROM memory_claim_evidence e JOIN memory_claims c ON c.id = e.claim_id WHERE e.id = ? AND c.profile_id = ? AND e.deleted_at IS NULL",
                (evidence_id, profile_id),
            ).fetchone()
            if row is None:
                raise ValueError("Evidence not found")
            before = dict(row)
            before["manual_locked"] = _int_to_bool(before.get("manual_locked"))
            self._conn.execute(
                "UPDATE memory_claim_evidence SET claim_id = ?, section_key = ?, section_title = ?, section_title_zh = ?, snippet = ?, snippet_zh = ?, evidence_summary = ?, evidence_summary_zh = ?, page_label = ?, page_start = ?, page_end = ?, anchor_kind = ?, context_before = ?, context_after = ?, structured_signal_json = ?, manual_locked = 1, updated_at = ? WHERE id = ?",
                (
                    claim_id,
                    section_key,
                    section_title,
                    localized.get("section_title", ""),
                    snippet,
                    localized.get("snippet", ""),
                    evidence_summary,
                    localized.get("evidence_summary", ""),
                    page_label,
                    page_start,
                    page_end,
                    anchor_kind,
                    context_before,
                    context_after,
                    structured_signal_json,
                    now,
                    evidence_id,
                ),
            )
            revision_translation = self._localize_fields(
                "evidence_revision_update",
                {"summary": f"Updated evidence #{evidence_id}"},
                context={"section_key": section_key},
                step_label=f"evidence revision localization update-{evidence_id}",
            )
            self._log_revision(
                profile_id,
                target_type="evidence",
                target_id=evidence_id,
                action="update",
                actor_type="user",
                summary=f"Updated evidence #{evidence_id}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "claim_id": claim_id,
                    "section_key": section_key,
                    "section_title": section_title,
                    "section_title_zh": localized.get("section_title", ""),
                    "snippet": snippet,
                    "snippet_zh": localized.get("snippet", ""),
                    "evidence_summary": evidence_summary,
                    "evidence_summary_zh": localized.get("evidence_summary", ""),
                    "page_label": page_label,
                    "page_start": page_start,
                    "page_end": page_end,
                    "anchor_kind": anchor_kind,
                    "context_before": context_before,
                    "context_after": context_after,
                    "structured_signal": normalized_payload.get("structured_signal", {}),
                    "manual_locked": True,
                },
            )
        self._conn.execute(
            "UPDATE memory_claims SET manual_locked = 1, updated_at = ?, review_status = 'resolved' WHERE id = ?",
            (now, claim_id),
        )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="claim",
            target_id=claim_id,
            note="Resolved by manual evidence edit",
        )
        self._conn.commit()
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        row = self._conn.execute(
            "SELECT * FROM memory_claim_evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        item = dict(row) if row else {}
        item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
        structured_signal = _safe_json_loads(
            str(item.get("structured_signal_json", ""))
        )
        item["structured_signal"] = (
            structured_signal if isinstance(structured_signal, dict) else {}
        )
        item["section_title_localized"] = _make_localized_text(
            item.get("section_title", ""), item.get("section_title_zh", "")
        )
        item["snippet_localized"] = _make_localized_text(
            item.get("snippet", ""), item.get("snippet_zh", "")
        )
        item["evidence_summary_localized"] = _make_localized_text(
            item.get("evidence_summary", ""), item.get("evidence_summary_zh", "")
        )
        return item

    def delete_evidence(self, profile_id: int, evidence_id: int) -> None:
        row = self._conn.execute(
            "SELECT e.* FROM memory_claim_evidence e JOIN memory_claims c ON c.id = e.claim_id WHERE e.id = ? AND c.profile_id = ? AND e.deleted_at IS NULL",
            (evidence_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Evidence not found")
        before = dict(row)
        before["manual_locked"] = _int_to_bool(before.get("manual_locked"))
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_claim_evidence SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (deleted_at, deleted_at, evidence_id),
        )
        revision_translation = self._localize_fields(
            "evidence_revision_delete",
            {"summary": f"Deleted evidence #{evidence_id}"},
            context={"section_key": before.get("section_key", "")},
            step_label=f"evidence revision localization delete-{evidence_id}",
        )
        self._log_revision(
            profile_id,
            target_type="evidence",
            target_id=evidence_id,
            action="delete",
            actor_type="user",
            summary=f"Deleted evidence #{evidence_id}",
            summary_zh=revision_translation.get("summary", ""),
            before=before,
        )
        self._conn.commit()
        self._prune_orphaned_claims(profile_id)
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

    def _get_active_synthesis_by_key(
        self, profile_id: int, synthesis_key: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memory_synthesis_items WHERE profile_id = ? AND synthesis_key = ? AND deleted_at IS NULL ORDER BY manual_locked DESC, updated_at DESC LIMIT 1",
            (profile_id, synthesis_key),
        ).fetchone()
        return self._synthesis_row_to_dict(row) if row else None

    def _sync_synthesis_claims(self, synthesis_id: int, claim_ids: list[int]) -> None:
        self._conn.execute(
            "DELETE FROM memory_synthesis_claims WHERE synthesis_id = ?",
            (synthesis_id,),
        )
        now = _timestamp()
        for claim_id in claim_ids:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_synthesis_claims (synthesis_id, claim_id, role, created_at) VALUES (?, ?, 'supports', ?)",
                (synthesis_id, claim_id, now),
            )

    def _upsert_synthesis_from_extraction(
        self,
        profile_id: int,
        writeback_id: int,
        item: dict[str, Any],
        claim_id_by_key: dict[str, int],
    ) -> int | None:
        item_type = (
            _normalize_whitespace(item.get("item_type", "consensus")) or "consensus"
        )
        title = _normalize_whitespace(item.get("title", ""))
        summary = _normalize_whitespace(item.get("summary", ""))
        if not title and not summary:
            return None
        title, summary = _normalize_synthesis_text(title, summary)
        synthesis_key = (
            _normalize_whitespace(item.get("synthesis_key", ""))
            or f"{item_type}:{_slugify(title or summary[:80])}"
        )
        confidence = min(max(_maybe_float(item.get("confidence"), 0.5), 0.0), 1.0)
        default_resolution = summary or title
        synthesis_translated = self._localize_fields(
            "synthesis",
            {
                "title": title or summary[:80],
                "summary": summary or title,
                "default_resolution": default_resolution,
            },
            context={"item_type": item_type, "confidence": confidence},
            step_label=f"synthesis localization {synthesis_key}",
        )
        claim_ids = [
            claim_id_by_key[key]
            for key in item.get("claim_keys", [])
            if key in claim_id_by_key
        ]
        now = _timestamp()
        existing = self._get_active_synthesis_by_key(profile_id, synthesis_key)
        candidate_payload = {
            "synthesis_key": synthesis_key,
            "item_type": item_type,
            "title": title or summary[:80],
            "title_zh": synthesis_translated.get("title", ""),
            "summary": summary or title,
            "summary_zh": synthesis_translated.get("summary", ""),
            "confidence": confidence,
            "default_resolution": default_resolution,
            "default_resolution_zh": synthesis_translated.get("default_resolution", ""),
            "claim_ids": claim_ids,
        }
        if existing is None:
            cur = self._conn.execute(
                "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 'none', 0, ?, ?, NULL)",
                (
                    profile_id,
                    writeback_id,
                    synthesis_key,
                    item_type,
                    candidate_payload["title"],
                    candidate_payload["title_zh"],
                    candidate_payload["summary"],
                    candidate_payload["summary_zh"],
                    confidence,
                    candidate_payload["default_resolution"],
                    candidate_payload["default_resolution_zh"],
                    now,
                    now,
                ),
            )
            synthesis_id = int(cur.lastrowid)
            self._register_backfill(
                synthesis_translated,
                "memory_synthesis_items",
                synthesis_id,
                {
                    "title": "title_zh",
                    "summary": "summary_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            self._sync_synthesis_claims(synthesis_id, claim_ids)
            return synthesis_id
        synthesis_id = int(existing["id"])
        before = dict(existing)
        changed = (
            title
            and _sequence_similarity(str(existing.get("title", "")), title)
            < _SIMILARITY_THRESHOLD
        ) or (
            summary
            and _sequence_similarity(str(existing.get("summary", "")), summary)
            < _SIMILARITY_THRESHOLD
        )
        if bool(existing.get("manual_locked")) and changed:
            review_title = f"Candidate update for {candidate_payload['title']}"
            review_description = "A newly processed paper suggested a different high-level domain cognition summary. The current human-edited synthesis remains active until you review it."
            review_default = str(
                existing.get("default_resolution", existing.get("summary", ""))
            )
            review_translation = self._localize_fields(
                "review_synthesis_candidate_update",
                {
                    "title": review_title,
                    "description": review_description,
                    "default_resolution": review_default,
                },
                context={"item_type": item_type, "synthesis_key": synthesis_key},
                step_label=f"synthesis review localization {synthesis_key}",
            )
            review_id = self._upsert_review_item(
                profile_id,
                target_type="synthesis",
                target_id=synthesis_id,
                review_type="candidate_update",
                title=review_title,
                title_zh=review_translation.get("title", ""),
                description=review_description,
                description_zh=review_translation.get("description", ""),
                default_resolution=review_default,
                default_resolution_zh=review_translation.get("default_resolution", ""),
                suggested_payload=_merge_suggested_payload_translation(
                    candidate_payload,
                    {
                        "title": candidate_payload["title_zh"],
                        "summary": candidate_payload["summary_zh"],
                        "default_resolution": candidate_payload[
                            "default_resolution_zh"
                        ],
                    },
                ),
            )
            self._register_backfill(
                synthesis_translated,
                "memory_synthesis_items",
                synthesis_id,
                {
                    "title": "title_zh",
                    "summary": "summary_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            self._register_backfill(
                review_translation,
                "memory_review_items",
                review_id,
                {
                    "title": "title_zh",
                    "description": "description_zh",
                    "default_resolution": "default_resolution_zh",
                },
            )
            return synthesis_id
        next_title = title or str(existing.get("title", ""))
        next_title_zh = synthesis_translated.get("title", "") or str(
            existing.get("title_zh", "")
        )
        next_summary = summary or str(existing.get("summary", ""))
        next_summary_zh = synthesis_translated.get("summary", "") or str(
            existing.get("summary_zh", "")
        )
        next_default_resolution = default_resolution or str(
            existing.get("default_resolution", "")
        )
        next_default_resolution_zh = synthesis_translated.get(
            "default_resolution", ""
        ) or str(existing.get("default_resolution_zh", ""))
        next_confidence = max(float(existing.get("confidence", 0.5) or 0.5), confidence)
        linked_claim_rows = self._conn.execute(
            "SELECT c.review_status, c.status FROM memory_synthesis_claims sc JOIN memory_claims c ON c.id = sc.claim_id WHERE sc.synthesis_id = ? AND c.deleted_at IS NULL",
            (synthesis_id,),
        ).fetchall()
        next_review_status = (
            "pending"
            if any(
                str(row["review_status"]) == "pending"
                or str(row["status"]) == "conflicted"
                for row in linked_claim_rows
            )
            else "none"
        )
        self._conn.execute(
            "UPDATE memory_synthesis_items SET title = ?, title_zh = ?, summary = ?, summary_zh = ?, confidence = ?, default_resolution = ?, default_resolution_zh = ?, review_status = ?, updated_at = ? WHERE id = ?",
            (
                next_title,
                next_title_zh,
                next_summary,
                next_summary_zh,
                next_confidence,
                next_default_resolution,
                next_default_resolution_zh,
                next_review_status,
                now,
                synthesis_id,
            ),
        )
        self._register_backfill(
            synthesis_translated,
            "memory_synthesis_items",
            synthesis_id,
            {
                "title": "title_zh",
                "summary": "summary_zh",
                "default_resolution": "default_resolution_zh",
            },
        )
        self._sync_synthesis_claims(
            synthesis_id,
            claim_ids
            or [
                int(item_row["claim_id"])
                for item_row in self._conn.execute(
                    "SELECT claim_id FROM memory_synthesis_claims WHERE synthesis_id = ?",
                    (synthesis_id,),
                ).fetchall()
            ],
        )
        if changed:
            revision_translation = self._localize_fields(
                "synthesis_revision_update",
                {"summary": f"Updated synthesis {synthesis_key}"},
                context={"item_type": item_type},
                step_label=f"synthesis revision localization {synthesis_key}",
            )
            revision_id = self._log_revision(
                profile_id,
                target_type="synthesis",
                target_id=synthesis_id,
                action="auto_update",
                actor_type="ai",
                summary=f"Updated synthesis {synthesis_key}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "title": next_title,
                    "title_zh": next_title_zh,
                    "summary": next_summary,
                    "summary_zh": next_summary_zh,
                    "default_resolution": next_default_resolution,
                    "default_resolution_zh": next_default_resolution_zh,
                    "confidence": next_confidence,
                    "review_status": next_review_status,
                },
                writeback_id=writeback_id,
            )
            if revision_id is not None:
                self._register_backfill(
                    revision_translation,
                    "memory_revisions",
                    revision_id,
                    {"summary": "summary_zh"},
                )
        return synthesis_id

    def _ensure_derived_synthesis_from_claim(
        self, profile_id: int, claim_row: dict[str, Any]
    ) -> None:
        claim_id = int(claim_row["id"])
        title = str(claim_row.get("title", "")).strip() or str(
            claim_row.get("claim_key", "claim")
        )
        if (
            str(claim_row.get("review_status", "none")) == "pending"
            or str(claim_row.get("status", "")) == "conflicted"
        ):
            synthesis_key = f"debate:{_slugify(title)}"
            existing = self._get_active_synthesis_by_key(profile_id, synthesis_key)
            summary = _normalize_whitespace(
                claim_row.get("default_resolution", "")
            ) or str(claim_row.get("body", ""))
            debate_translated = self._localize_fields(
                "derived_synthesis_debate",
                {
                    "title": title,
                    "summary": summary,
                    "default_resolution": summary,
                },
                context={"item_type": "debate", "claim_id": claim_id},
                step_label=f"derived synthesis localization debate-{claim_id}",
            )
            if existing is None:
                now = _timestamp()
                cur = self._conn.execute(
                    "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, NULL, ?, 'debate', ?, ?, ?, ?, ?, 'active', ?, ?, 'pending', 0, ?, ?, NULL)",
                    (
                        profile_id,
                        synthesis_key,
                        title,
                        debate_translated.get("title", ""),
                        summary,
                        debate_translated.get("summary", ""),
                        float(claim_row.get("importance", 0.5) or 0.5),
                        summary,
                        debate_translated.get("default_resolution", ""),
                        now,
                        now,
                    ),
                )
                synthesis_id = int(cur.lastrowid)
                self._register_backfill(
                    debate_translated,
                    "memory_synthesis_items",
                    synthesis_id,
                    {
                        "title": "title_zh",
                        "summary": "summary_zh",
                        "default_resolution": "default_resolution_zh",
                    },
                )
                self._sync_synthesis_claims(synthesis_id, [claim_id])
            elif not bool(existing.get("manual_locked")):
                now = _timestamp()
                self._conn.execute(
                    "UPDATE memory_synthesis_items SET summary = ?, summary_zh = ?, default_resolution = ?, default_resolution_zh = ?, review_status = 'pending', updated_at = ? WHERE id = ?",
                    (
                        summary,
                        debate_translated.get("summary", ""),
                        summary,
                        debate_translated.get("default_resolution", ""),
                        now,
                        int(existing["id"]),
                    ),
                )
                self._register_backfill(
                    debate_translated,
                    "memory_synthesis_items",
                    int(existing["id"]),
                    {
                        "summary": "summary_zh",
                        "default_resolution": "default_resolution_zh",
                    },
                )
                self._sync_synthesis_claims(int(existing["id"]), [claim_id])
        if str(claim_row.get("stance", "")) == "open" or str(
            claim_row.get("claim_type", "")
        ) in {"open_question", "hypothesis"}:
            synthesis_key = f"open_question:{_slugify(title)}"
            existing = self._get_active_synthesis_by_key(profile_id, synthesis_key)
            summary = _normalize_whitespace(claim_row.get("body", ""))
            oq_translated = self._localize_fields(
                "derived_synthesis_open_question",
                {
                    "title": title,
                    "summary": summary,
                    "default_resolution": summary,
                },
                context={"item_type": "open_question", "claim_id": claim_id},
                step_label=f"derived synthesis localization open-{claim_id}",
            )
            if existing is None:
                now = _timestamp()
                cur = self._conn.execute(
                    "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, NULL, ?, 'open_question', ?, ?, ?, ?, ?, 'active', ?, ?, 'none', 0, ?, ?, NULL)",
                    (
                        profile_id,
                        synthesis_key,
                        title,
                        oq_translated.get("title", ""),
                        summary,
                        oq_translated.get("summary", ""),
                        float(claim_row.get("importance", 0.4) or 0.4),
                        summary,
                        oq_translated.get("default_resolution", ""),
                        now,
                        now,
                    ),
                )
                synthesis_id = int(cur.lastrowid)
                self._register_backfill(
                    oq_translated,
                    "memory_synthesis_items",
                    synthesis_id,
                    {
                        "title": "title_zh",
                        "summary": "summary_zh",
                        "default_resolution": "default_resolution_zh",
                    },
                )
                self._sync_synthesis_claims(synthesis_id, [claim_id])
            elif not bool(existing.get("manual_locked")):
                self._conn.execute(
                    "UPDATE memory_synthesis_items SET title = ?, title_zh = ?, summary = ?, summary_zh = ?, default_resolution = ?, default_resolution_zh = ?, updated_at = ? WHERE id = ?",
                    (
                        title,
                        oq_translated.get("title", ""),
                        summary,
                        oq_translated.get("summary", ""),
                        summary,
                        oq_translated.get("default_resolution", ""),
                        _timestamp(),
                        int(existing["id"]),
                    ),
                )
                self._register_backfill(
                    oq_translated,
                    "memory_synthesis_items",
                    int(existing["id"]),
                    {
                        "title": "title_zh",
                        "summary": "summary_zh",
                        "default_resolution": "default_resolution_zh",
                    },
                )
                self._sync_synthesis_claims(int(existing["id"]), [claim_id])

    def list_synthesis_items(
        self, profile_id: int, *, limit: int | None = 120
    ) -> list[dict[str, Any]]:
        resolved_limit = self._resolve_limit(limit, 120)
        query = (
            "SELECT * FROM memory_synthesis_items WHERE profile_id = ? AND deleted_at IS NULL ORDER BY manual_locked DESC, updated_at DESC, created_at DESC"
        )
        params: tuple[Any, ...]
        if resolved_limit > 0:
            query += " LIMIT ?"
            params = (profile_id, resolved_limit)
        else:
            params = (profile_id,)
        rows = self._conn.execute(query, params).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = self._synthesis_row_to_dict(row)
            claim_rows = self._conn.execute(
                "SELECT claim_id FROM memory_synthesis_claims WHERE synthesis_id = ? ORDER BY claim_id ASC",
                (int(item["id"]),),
            ).fetchall()
            item["claim_ids"] = [int(claim_row["claim_id"]) for claim_row in claim_rows]
            payloads.append(item)
        return payloads

    def save_synthesis_item(
        self,
        profile_id: int,
        payload: dict[str, Any],
        *,
        synthesis_id: int | None = None,
    ) -> dict[str, Any]:
        item_type = (
            _normalize_whitespace(payload.get("item_type", "consensus")) or "consensus"
        )
        title = _normalize_whitespace(payload.get("title", ""))
        summary = _normalize_whitespace(payload.get("summary", ""))
        if not title and not summary:
            raise ValueError("Synthesis title or summary is required")
        confidence = min(max(_maybe_float(payload.get("confidence"), 0.5), 0.0), 1.0)
        status = _normalize_whitespace(payload.get("status", "active")) or "active"
        default_resolution = (
            _normalize_whitespace(payload.get("default_resolution", ""))
            or summary
            or title
        )
        synthesis_key = (
            _normalize_whitespace(payload.get("synthesis_key", ""))
            or f"{item_type}:{_slugify(title or summary[:80])}"
        )
        translated = self._localize_fields(
            "synthesis",
            {
                "title": title or summary[:80],
                "summary": summary or title,
                "default_resolution": default_resolution,
            },
            context={
                "item_type": item_type,
                "confidence": confidence,
                "mode": "manual_edit",
            },
            step_label=f"synthesis save localization {synthesis_key}",
        )
        claim_ids = [
            int(item)
            for item in payload.get("claim_ids", [])
            if _maybe_int(item) is not None
        ]
        now = _timestamp()
        if synthesis_id is None:
            cur = self._conn.execute(
                "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'resolved', 1, ?, ?, NULL)",
                (
                    profile_id,
                    synthesis_key,
                    item_type,
                    title or summary[:80],
                    translated.get("title", ""),
                    summary or title,
                    translated.get("summary", ""),
                    confidence,
                    status,
                    default_resolution,
                    translated.get("default_resolution", ""),
                    now,
                    now,
                ),
            )
            synthesis_id = int(cur.lastrowid)
            self._sync_synthesis_claims(synthesis_id, claim_ids)
            revision_translation = self._localize_fields(
                "synthesis_revision_create",
                {"summary": f"Created synthesis {synthesis_key}"},
                context={"item_type": item_type},
                step_label=f"synthesis revision localization {synthesis_key}",
            )
            self._log_revision(
                profile_id,
                target_type="synthesis",
                target_id=synthesis_id,
                action="create",
                actor_type="user",
                summary=f"Created synthesis {synthesis_key}",
                summary_zh=revision_translation.get("summary", ""),
                after={
                    "synthesis_key": synthesis_key,
                    "item_type": item_type,
                    "title": title or summary[:80],
                    "title_zh": translated.get("title", ""),
                    "summary": summary or title,
                    "summary_zh": translated.get("summary", ""),
                    "confidence": confidence,
                    "status": status,
                    "default_resolution": default_resolution,
                    "default_resolution_zh": translated.get("default_resolution", ""),
                    "claim_ids": claim_ids,
                    "manual_locked": True,
                },
            )
        else:
            row = self._conn.execute(
                "SELECT * FROM memory_synthesis_items WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
                (synthesis_id, profile_id),
            ).fetchone()
            if row is None:
                raise ValueError("Synthesis item not found")
            before = self._synthesis_row_to_dict(row)
            self._conn.execute(
                "UPDATE memory_synthesis_items SET synthesis_key = ?, item_type = ?, title = ?, title_zh = ?, summary = ?, summary_zh = ?, confidence = ?, status = ?, default_resolution = ?, default_resolution_zh = ?, review_status = 'resolved', manual_locked = 1, updated_at = ? WHERE id = ?",
                (
                    synthesis_key,
                    item_type,
                    title or summary[:80],
                    translated.get("title", ""),
                    summary or title,
                    translated.get("summary", ""),
                    confidence,
                    status,
                    default_resolution,
                    translated.get("default_resolution", ""),
                    now,
                    synthesis_id,
                ),
            )
            self._sync_synthesis_claims(synthesis_id, claim_ids)
            revision_translation = self._localize_fields(
                "synthesis_revision_update",
                {"summary": f"Updated synthesis {synthesis_key}"},
                context={"item_type": item_type},
                step_label=f"synthesis revision localization {synthesis_key}",
            )
            self._log_revision(
                profile_id,
                target_type="synthesis",
                target_id=synthesis_id,
                action="update",
                actor_type="user",
                summary=f"Updated synthesis {synthesis_key}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "synthesis_key": synthesis_key,
                    "item_type": item_type,
                    "title": title or summary[:80],
                    "title_zh": translated.get("title", ""),
                    "summary": summary or title,
                    "summary_zh": translated.get("summary", ""),
                    "confidence": confidence,
                    "status": status,
                    "default_resolution": default_resolution,
                    "default_resolution_zh": translated.get("default_resolution", ""),
                    "claim_ids": claim_ids,
                    "manual_locked": True,
                    "review_status": "resolved",
                },
            )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="synthesis",
            target_id=synthesis_id,
            note="Resolved by manual synthesis edit",
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        row = self._conn.execute(
            "SELECT * FROM memory_synthesis_items WHERE id = ?", (synthesis_id,)
        ).fetchone()
        item = self._synthesis_row_to_dict(row) if row else {}
        item["claim_ids"] = claim_ids
        return item

    def delete_synthesis_item(self, profile_id: int, synthesis_id: int) -> None:
        row = self._conn.execute(
            "SELECT * FROM memory_synthesis_items WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
            (synthesis_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Synthesis item not found")
        before = self._synthesis_row_to_dict(row)
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_synthesis_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (deleted_at, deleted_at, synthesis_id),
        )
        self._conn.execute(
            "DELETE FROM memory_synthesis_claims WHERE synthesis_id = ?",
            (synthesis_id,),
        )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="synthesis",
            target_id=synthesis_id,
            note="Synthesis deleted manually",
        )
        revision_translation = self._localize_fields(
            "synthesis_revision_delete",
            {"summary": f"Deleted synthesis {before.get('synthesis_key', '')}"},
            context={"item_type": before.get("item_type", "")},
            step_label=f"synthesis revision localization delete-{synthesis_id}",
        )
        self._log_revision(
            profile_id,
            target_type="synthesis",
            target_id=synthesis_id,
            action="delete",
            actor_type="user",
            summary=f"Deleted synthesis {before.get('synthesis_key', '')}",
            summary_zh=revision_translation.get("summary", ""),
            before=before,
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

    def list_graph_edges(
        self, profile_id: int, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memory_graph_edges WHERE profile_id = ? AND deleted_at IS NULL ORDER BY manual_locked DESC, updated_at DESC, created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
            item["summary_localized"] = _make_localized_text(
                item.get("summary", ""), item.get("summary_zh", "")
            )
            payloads.append(item)
        return payloads

    def _find_graph_edge_by_signature(
        self,
        profile_id: int,
        *,
        source_kind: str,
        source_ref: str,
        target_kind: str,
        target_ref: str,
        relation_type: str,
        exclude_edge_id: int | None = None,
    ) -> sqlite3.Row | None:
        query = (
            "SELECT * FROM memory_graph_edges WHERE profile_id = ? AND source_kind = ? AND source_ref = ? "
            "AND target_kind = ? AND target_ref = ? AND relation_type = ?"
        )
        params: list[Any] = [
            profile_id,
            source_kind,
            source_ref,
            target_kind,
            target_ref,
            relation_type,
        ]
        if exclude_edge_id is not None:
            query += " AND id != ?"
            params.append(exclude_edge_id)
        query += " ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END, manual_locked DESC, updated_at DESC, created_at DESC LIMIT 1"
        return self._conn.execute(query, params).fetchone()

    def _upsert_graph_edge_from_extraction(
        self,
        profile_id: int,
        writeback_id: int,
        edge: dict[str, Any],
        *,
        source_paper_id: str,
    ) -> int | None:
        target = _normalize_whitespace(edge.get("target", ""))
        if not target:
            return None
        relation_type = (
            _normalize_whitespace(edge.get("relation", "related_to")) or "related_to"
        )
        summary = _normalize_whitespace(edge.get("summary", ""))
        edge_translated = self._localize_fields(
            "graph_edge",
            {"summary": summary},
            context={
                "source_ref": source_paper_id,
                "target_ref": target,
                "relation_type": relation_type,
            },
            step_label=f"edge localization {source_paper_id}->{target}",
        )
        source_ref = _normalize_whitespace(source_paper_id) or "unknown-paper"
        existing = self._find_graph_edge_by_signature(
            profile_id,
            source_kind="paper",
            source_ref=source_ref,
            target_kind="paper",
            target_ref=target,
            relation_type=relation_type,
        )
        now = _timestamp()
        if existing:
            existing_id = int(existing["id"])
            if bool(existing["manual_locked"]):
                if (
                    summary
                    and _sequence_similarity(str(existing["summary"]), summary)
                    < _SIMILARITY_THRESHOLD
                ):
                    review_title = (
                        f"Candidate update for relation {source_ref} → {target}"
                    )
                    review_description = "A newly processed paper suggested an updated paper relationship summary. Review before replacing the current human-edited edge."
                    review_default = str(existing["summary"] or "")
                    review_translation = self._localize_fields(
                        "review_edge_candidate_update",
                        {
                            "title": review_title,
                            "description": review_description,
                            "default_resolution": review_default,
                        },
                        context={
                            "relation_type": relation_type,
                            "source_ref": source_ref,
                            "target_ref": target,
                        },
                        step_label=f"edge review localization {source_ref}->{target}",
                    )
                    review_id = self._upsert_review_item(
                        profile_id,
                        target_type="edge",
                        target_id=existing_id,
                        review_type="candidate_update",
                        title=review_title,
                        title_zh=review_translation.get("title", ""),
                        description=review_description,
                        description_zh=review_translation.get("description", ""),
                        default_resolution=review_default,
                        default_resolution_zh=review_translation.get(
                            "default_resolution", ""
                        ),
                        suggested_payload=_merge_suggested_payload_translation(
                            {
                                "source_kind": "paper",
                                "source_ref": source_ref,
                                "target_kind": "paper",
                                "target_ref": target,
                                "relation_type": relation_type,
                                "summary": summary,
                                "weight": 1.0,
                            },
                            {"summary": edge_translated.get("summary", "")},
                        ),
                    )
                    self._register_backfill(
                        edge_translated,
                        "memory_graph_edges",
                        existing_id,
                        {"summary": "summary_zh"},
                    )
                    self._register_backfill(
                        review_translation,
                        "memory_review_items",
                        review_id,
                        {
                            "title": "title_zh",
                            "description": "description_zh",
                            "default_resolution": "default_resolution_zh",
                        },
                    )
                return existing_id
            self._conn.execute(
                "UPDATE memory_graph_edges SET origin_writeback_id = ?, summary = ?, summary_zh = ?, updated_at = ?, deleted_at = NULL WHERE id = ?",
                (
                    writeback_id,
                    summary,
                    edge_translated.get("summary", ""),
                    now,
                    existing_id,
                ),
            )
            self._register_backfill(
                edge_translated,
                "memory_graph_edges",
                existing_id,
                {"summary": "summary_zh"},
            )
            return existing_id
        cur = self._conn.execute(
            "INSERT INTO memory_graph_edges (profile_id, origin_writeback_id, source_kind, source_ref, target_kind, target_ref, relation_type, summary, summary_zh, weight, manual_locked, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, 'paper', ?, 'paper', ?, ?, ?, ?, 1.0, 0, ?, ?, NULL)",
            (
                profile_id,
                writeback_id,
                source_ref,
                target,
                relation_type,
                summary,
                edge_translated.get("summary", ""),
                now,
                now,
            ),
        )
        edge_id = int(cur.lastrowid)
        self._register_backfill(
            edge_translated, "memory_graph_edges", edge_id, {"summary": "summary_zh"}
        )
        return edge_id

    def save_graph_edge(
        self, profile_id: int, payload: dict[str, Any], *, edge_id: int | None = None
    ) -> dict[str, Any]:
        source_kind = (
            _normalize_whitespace(payload.get("source_kind", "entity")) or "entity"
        )
        source_ref = _normalize_whitespace(payload.get("source_ref", ""))
        target_kind = (
            _normalize_whitespace(payload.get("target_kind", "entity")) or "entity"
        )
        target_ref = _normalize_whitespace(payload.get("target_ref", ""))
        relation_type = (
            _normalize_whitespace(payload.get("relation_type", "related_to"))
            or "related_to"
        )
        summary = _normalize_whitespace(payload.get("summary", ""))
        translated = self._localize_fields(
            "graph_edge",
            {"summary": summary},
            context={
                "source_kind": source_kind,
                "source_ref": source_ref,
                "target_kind": target_kind,
                "target_ref": target_ref,
                "relation_type": relation_type,
                "mode": "manual_edit",
            },
            step_label=f"edge save localization {source_ref}->{target_ref}",
        )
        weight = min(max(_maybe_float(payload.get("weight"), 1.0), 0.0), 3.0)
        if not source_ref or not target_ref:
            raise ValueError("source_ref and target_ref are required")
        now = _timestamp()
        if edge_id is None:
            existing = self._find_graph_edge_by_signature(
                profile_id,
                source_kind=source_kind,
                source_ref=source_ref,
                target_kind=target_kind,
                target_ref=target_ref,
                relation_type=relation_type,
            )
            if existing is not None:
                edge_id = int(existing["id"])
                before = dict(existing)
                before["manual_locked"] = _int_to_bool(before.get("manual_locked"))
                self._conn.execute(
                    "UPDATE memory_graph_edges SET origin_writeback_id = NULL, summary = ?, summary_zh = ?, weight = ?, manual_locked = 1, updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (summary, translated.get("summary", ""), weight, now, edge_id),
                )
                revision_translation = self._localize_fields(
                    "edge_revision_update",
                    {"summary": f"Updated graph edge #{edge_id}"},
                    context={"relation_type": relation_type},
                    step_label=f"edge revision localization update-{edge_id}",
                )
                self._log_revision(
                    profile_id,
                    target_type="edge",
                    target_id=edge_id,
                    action="update",
                    actor_type="user",
                    summary=f"Updated graph edge #{edge_id}",
                    summary_zh=revision_translation.get("summary", ""),
                    before=before,
                    after={
                        **before,
                        "source_kind": source_kind,
                        "source_ref": source_ref,
                        "target_kind": target_kind,
                        "target_ref": target_ref,
                        "relation_type": relation_type,
                        "summary": summary,
                        "summary_zh": translated.get("summary", ""),
                        "weight": weight,
                        "manual_locked": True,
                        "deleted_at": None,
                    },
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO memory_graph_edges (profile_id, origin_writeback_id, source_kind, source_ref, target_kind, target_ref, relation_type, summary, summary_zh, weight, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL)",
                    (
                        profile_id,
                        source_kind,
                        source_ref,
                        target_kind,
                        target_ref,
                        relation_type,
                        summary,
                        translated.get("summary", ""),
                        weight,
                        now,
                        now,
                    ),
                )
                edge_id = int(cur.lastrowid)
                revision_translation = self._localize_fields(
                    "edge_revision_create",
                    {
                        "summary": f"Created graph edge {source_kind}:{source_ref} -> {target_kind}:{target_ref}"
                    },
                    context={"relation_type": relation_type},
                    step_label=f"edge revision localization create-{source_ref}-{target_ref}",
                )
                self._log_revision(
                    profile_id,
                    target_type="edge",
                    target_id=edge_id,
                    action="create",
                    actor_type="user",
                    summary=f"Created graph edge {source_kind}:{source_ref} -> {target_kind}:{target_ref}",
                    summary_zh=revision_translation.get("summary", ""),
                    after={
                        "source_kind": source_kind,
                        "source_ref": source_ref,
                        "target_kind": target_kind,
                        "target_ref": target_ref,
                        "relation_type": relation_type,
                        "summary": summary,
                        "summary_zh": translated.get("summary", ""),
                        "weight": weight,
                        "manual_locked": True,
                    },
                )
        else:
            row = self._conn.execute(
                "SELECT * FROM memory_graph_edges WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
                (edge_id, profile_id),
            ).fetchone()
            if row is None:
                raise ValueError("Graph edge not found")
            before = dict(row)
            before["manual_locked"] = _int_to_bool(before.get("manual_locked"))
            duplicate = self._find_graph_edge_by_signature(
                profile_id,
                source_kind=source_kind,
                source_ref=source_ref,
                target_kind=target_kind,
                target_ref=target_ref,
                relation_type=relation_type,
                exclude_edge_id=edge_id,
            )
            if duplicate is not None:
                duplicate_id = int(duplicate["id"])
                self._conn.execute(
                    "UPDATE memory_graph_edges SET origin_writeback_id = NULL, summary = ?, summary_zh = ?, weight = ?, manual_locked = 1, updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (summary, translated.get("summary", ""), weight, now, duplicate_id),
                )
                self._conn.execute(
                    "UPDATE memory_graph_edges SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, edge_id),
                )
                edge_id = duplicate_id
            else:
                self._conn.execute(
                    "UPDATE memory_graph_edges SET source_kind = ?, source_ref = ?, target_kind = ?, target_ref = ?, relation_type = ?, summary = ?, summary_zh = ?, weight = ?, manual_locked = 1, updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (
                        source_kind,
                        source_ref,
                        target_kind,
                        target_ref,
                        relation_type,
                        summary,
                        translated.get("summary", ""),
                        weight,
                        now,
                        edge_id,
                    ),
                )
            revision_translation = self._localize_fields(
                "edge_revision_update",
                {"summary": f"Updated graph edge #{edge_id}"},
                context={"relation_type": relation_type},
                step_label=f"edge revision localization update-{edge_id}",
            )
            self._log_revision(
                profile_id,
                target_type="edge",
                target_id=edge_id,
                action="update",
                actor_type="user",
                summary=f"Updated graph edge #{edge_id}",
                summary_zh=revision_translation.get("summary", ""),
                before=before,
                after={
                    **before,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "target_kind": target_kind,
                    "target_ref": target_ref,
                    "relation_type": relation_type,
                    "summary": summary,
                    "summary_zh": translated.get("summary", ""),
                    "weight": weight,
                    "manual_locked": True,
                    "deleted_at": None,
                },
            )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="edge",
            target_id=edge_id,
            note="Resolved by manual edge edit",
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        row = self._conn.execute(
            "SELECT * FROM memory_graph_edges WHERE id = ?", (edge_id,)
        ).fetchone()
        item = dict(row) if row else {}
        item["manual_locked"] = _int_to_bool(item.get("manual_locked"))
        item["summary_localized"] = _make_localized_text(
            item.get("summary", ""), item.get("summary_zh", "")
        )
        return item

    def delete_graph_edge(self, profile_id: int, edge_id: int) -> None:
        row = self._conn.execute(
            "SELECT * FROM memory_graph_edges WHERE id = ? AND profile_id = ? AND deleted_at IS NULL",
            (edge_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Graph edge not found")
        before = dict(row)
        before["manual_locked"] = _int_to_bool(before.get("manual_locked"))
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_graph_edges SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (deleted_at, deleted_at, edge_id),
        )
        self._clear_pending_reviews_for_target(
            profile_id,
            target_type="edge",
            target_id=edge_id,
            note="Edge deleted manually",
        )
        revision_translation = self._localize_fields(
            "edge_revision_delete",
            {"summary": f"Deleted graph edge #{edge_id}"},
            context={"relation_type": before.get("relation_type", "")},
            step_label=f"edge revision localization delete-{edge_id}",
        )
        self._log_revision(
            profile_id,
            target_type="edge",
            target_id=edge_id,
            action="delete",
            actor_type="user",
            summary=f"Deleted graph edge #{edge_id}",
            summary_zh=revision_translation.get("summary", ""),
            before=before,
        )
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

    def _prune_orphaned_claims(self, profile_id: int) -> int:
        pruned = 0
        rows = self._conn.execute(
            "SELECT c.id, c.manual_locked FROM memory_claims c WHERE c.profile_id = ? AND c.deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in rows:
            claim_id = int(row["id"])
            if _int_to_bool(row["manual_locked"]):
                continue
            evidence_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claim_evidence WHERE claim_id = ? AND deleted_at IS NULL",
                (claim_id,),
            ).fetchone()
            if int(evidence_row["cnt"] if evidence_row else 0) == 0:
                deleted_at = _timestamp()
                self._conn.execute(
                    "UPDATE memory_claims SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (deleted_at, deleted_at, claim_id),
                )
                self._conn.execute(
                    "DELETE FROM memory_claim_entities WHERE claim_id = ?", (claim_id,)
                )
                self._conn.execute(
                    "DELETE FROM memory_synthesis_claims WHERE claim_id = ?",
                    (claim_id,),
                )
                self._conn.execute(
                    "UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? WHERE profile_id = ? AND target_type = 'claim' AND target_id = ? AND status = 'pending'",
                    (deleted_at, deleted_at, profile_id, claim_id),
                )
                pruned += 1
        return pruned

    def _prune_orphaned_synthesis(self, profile_id: int) -> int:
        pruned = 0
        rows = self._conn.execute(
            "SELECT s.id, s.manual_locked FROM memory_synthesis_items s WHERE s.profile_id = ? AND s.deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in rows:
            synthesis_id = int(row["id"])
            if _int_to_bool(row["manual_locked"]):
                continue
            link_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_claims sc JOIN memory_claims c ON c.id = sc.claim_id WHERE sc.synthesis_id = ? AND c.deleted_at IS NULL",
                (synthesis_id,),
            ).fetchone()
            if int(link_row["cnt"] if link_row else 0) == 0:
                deleted_at = _timestamp()
                self._conn.execute(
                    "UPDATE memory_synthesis_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (deleted_at, deleted_at, synthesis_id),
                )
                self._conn.execute(
                    "UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? WHERE profile_id = ? AND target_type = 'synthesis' AND target_id = ? AND status = 'pending'",
                    (deleted_at, deleted_at, profile_id, synthesis_id),
                )
                pruned += 1
        return pruned

    def _prune_orphaned_entities(self, profile_id: int) -> int:
        pruned = 0
        rows = self._conn.execute(
            "SELECT e.id, e.manual_locked FROM memory_entities e WHERE e.profile_id = ? AND e.deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in rows:
            entity_id = int(row["id"])
            if _int_to_bool(row["manual_locked"]):
                continue
            claim_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claim_entities ce "
                "JOIN memory_claims c ON c.id = ce.claim_id "
                "WHERE ce.entity_id = ? AND c.deleted_at IS NULL",
                (entity_id,),
            ).fetchone()
            if int(claim_row["cnt"] if claim_row else 0) == 0:
                deleted_at = _timestamp()
                self._conn.execute(
                    "UPDATE memory_entities SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (deleted_at, deleted_at, entity_id),
                )
                self._conn.execute(
                    "DELETE FROM memory_claim_entities WHERE entity_id = ?",
                    (entity_id,),
                )
                self._conn.execute(
                    "UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? "
                    "WHERE profile_id = ? AND target_type = 'entity' AND target_id = ? AND status = 'pending'",
                    (deleted_at, deleted_at, profile_id, entity_id),
                )
                pruned += 1
        return pruned

    def rebuild_profile_cognition(self, profile_id: int) -> dict[str, int]:
        self.ensure_profile_memory_provenance(profile_id)
        auto_items = self._conn.execute(
            "SELECT id FROM memory_synthesis_items WHERE profile_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (profile_id,),
        ).fetchall()
        now = _timestamp()
        for row in auto_items:
            synthesis_id = int(row["id"])
            self._conn.execute(
                "DELETE FROM memory_synthesis_claims WHERE synthesis_id = ?",
                (synthesis_id,),
            )
            self._conn.execute(
                "UPDATE memory_synthesis_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, synthesis_id),
            )
        claim_rows = self.list_claims(profile_id, limit=400)
        created = 0
        for claim_row in claim_rows:
            before = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_items WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone()
            self._ensure_derived_synthesis_from_claim(profile_id, claim_row)
            after = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_items WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone()
            created += max(
                0,
                int(after["cnt"] if after else 0) - int(before["cnt"] if before else 0),
            )
        edge_rows = self.list_graph_edges(profile_id, limit=200)
        for edge in edge_rows:
            if (
                str(edge.get("source_kind")) == "paper"
                and str(edge.get("target_kind")) == "paper"
                and str(edge.get("relation_type"))
                in {"extends", "competes", "compares_with"}
            ):
                title = f"{edge.get('source_ref')} {edge.get('relation_type')} {edge.get('target_ref')}"
                synthesis_key = f"evolution:{_slugify(title)}"
                if self._get_active_synthesis_by_key(profile_id, synthesis_key) is None:
                    summary_text = (
                        _normalize_whitespace(edge.get("summary", "")) or title
                    )
                    translated = self._localize_fields(
                        "derived_synthesis_evolution",
                        {
                            "title": title,
                            "summary": summary_text,
                            "default_resolution": summary_text,
                        },
                        context={
                            "item_type": "evolution",
                            "relation_type": edge.get("relation_type", ""),
                        },
                        step_label=f"derived synthesis localization evolution-{synthesis_key}",
                    )
                    cur = self._conn.execute(
                        "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                        "VALUES (?, NULL, ?, 'evolution', ?, ?, ?, ?, ?, 'active', ?, ?, 'none', 0, ?, ?, NULL)",
                        (
                            profile_id,
                            synthesis_key,
                            title,
                            translated.get("title", ""),
                            summary_text,
                            translated.get("summary", ""),
                            min(
                                max(_maybe_float(edge.get("weight"), 1.0) / 2.0, 0.1),
                                1.0,
                            ),
                            summary_text,
                            translated.get("default_resolution", ""),
                            now,
                            now,
                        ),
                    )
                    created += 1 if cur.lastrowid else 0
        self._conn.commit()
        self._prune_orphaned_synthesis(profile_id)
        self.rebuild_profile_memory(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        return {"rebuilt_items": created, "active_claims": len(claim_rows)}

    def _rank_text_items(
        self,
        items: list[dict[str, Any]],
        *,
        keywords: list[str] | None,
        text_fields: list[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not keywords:
            return items[:top_k]
        lowered_keywords = [kw.lower() for kw in keywords if kw.strip()]
        scored: list[tuple[tuple[float, float, float], dict[str, Any]]] = []
        for item in items:
            haystack = " ".join(
                str(item.get(field, "")) for field in text_fields
            ).lower()
            hit_count = sum(1 for kw in lowered_keywords if kw in haystack)
            lexical = max(
                (
                    SequenceMatcher(
                        None, kw, haystack[: min(len(haystack), 300)]
                    ).ratio()
                    for kw in lowered_keywords
                ),
                default=0.0,
            )
            priority = float(
                item.get(
                    "relevance_score",
                    item.get("confidence", item.get("importance", 0.0)),
                )
                or 0.0
            )
            scored.append(((float(hit_count), float(lexical), priority), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def _build_rebuilt_knowledge_rows(self, profile_id: int) -> list[dict[str, Any]]:
        synthesis_rows = self.list_synthesis_items(profile_id, limit=200)
        claim_rows = self.list_claims(profile_id, limit=200)
        rebuilt: list[dict[str, Any]] = []
        for item in synthesis_rows:
            content = _normalize_whitespace(
                f"{item.get('title', '')}: {item.get('default_resolution', '') or item.get('summary', '')}"
            )
            content_zh = _normalize_whitespace(
                f"{item.get('title_zh', '') or item.get('title', '')}: {item.get('default_resolution_zh', '') or item.get('summary_zh', '') or item.get('default_resolution', '') or item.get('summary', '')}"
            )
            if not content:
                continue
            rebuilt.append(
                {
                    "paper_id": "",
                    "category": _normalize_whitespace(
                        item.get("item_type", "consensus")
                    )
                    or "consensus",
                    "content": content,
                    "content_zh": content_zh,
                    "created_at": float(
                        item.get("updated_at", item.get("created_at", _timestamp()))
                        or _timestamp()
                    ),
                    "relevance_score": float(item.get("confidence", 0.5) or 0.5)
                    + (0.2 if item.get("manual_locked") else 0.0),
                }
            )
        for claim in claim_rows:
            content = _normalize_whitespace(
                claim.get("default_resolution", "") or claim.get("body", "")
            )
            content_zh = _normalize_whitespace(
                claim.get("default_resolution_zh", "")
                or claim.get("body_zh", "")
                or content
            )
            if not content:
                continue
            rebuilt.append(
                {
                    "paper_id": _normalize_whitespace(claim.get("paper_id", "")),
                    "category": f"claim/{_normalize_whitespace(claim.get('claim_type', 'finding')) or 'finding'}",
                    "content": f"{_normalize_whitespace(claim.get('title', ''))}: {content}",
                    "content_zh": f"{_normalize_whitespace(claim.get('title_zh', '') or claim.get('title', ''))}: {content_zh}",
                    "created_at": float(
                        claim.get("updated_at", claim.get("created_at", _timestamp()))
                        or _timestamp()
                    ),
                    "relevance_score": float(claim.get("importance", 0.5) or 0.5)
                    + (0.15 if claim.get("manual_locked") else 0.0),
                }
            )
        deduped: list[dict[str, Any]] = []
        for row in rebuilt:
            normalized_content = _normalize_whitespace(row["content"])
            duplicate: dict[str, Any] | None = None
            for existing in reversed(deduped[-120:]):
                if (
                    _sequence_similarity(str(existing["content"]), normalized_content)
                    >= _SIMILARITY_THRESHOLD
                ):
                    duplicate = existing
                    break
            if duplicate is not None:
                duplicate["relevance_score"] = max(
                    float(duplicate["relevance_score"]), float(row["relevance_score"])
                )
                continue
            deduped.append({**row, "content": normalized_content})
        if len(deduped) > _MAX_DOMAIN_KNOWLEDGE_PER_PROFILE:
            deduped.sort(
                key=lambda item: (
                    float(item["relevance_score"]),
                    float(item["created_at"]),
                )
            )
            deduped = deduped[-_MAX_DOMAIN_KNOWLEDGE_PER_PROFILE:]
            deduped.sort(key=lambda item: float(item["created_at"]), reverse=True)
        return deduped

    def _build_rebuilt_style_rows(self, profile_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT mse.key AS key, mse.value AS value, mse.created_at AS created_at "
            "FROM memory_writebacks mw JOIN memory_style_events mse ON mse.writeback_id = mw.id "
            "WHERE mw.profile_id = ? AND mw.deleted_at IS NULL "
            "ORDER BY mse.created_at ASC, mse.id ASC",
            (profile_id,),
        ).fetchall()
        latest_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            normalized_key = _normalize_whitespace(row["key"])
            normalized_value = _normalize_whitespace(row["value"])
            if not normalized_key or not normalized_value:
                continue
            latest_by_key[normalized_key] = {
                "key": normalized_key,
                "value": normalized_value,
                "updated_at": float(row["created_at"] or _timestamp()),
            }
        return list(latest_by_key.values())

    def _build_rebuilt_link_rows(self, profile_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT source_ref, target_ref, relation_type, summary, summary_zh, created_at FROM memory_graph_edges "
            "WHERE profile_id = ? AND deleted_at IS NULL AND source_kind = 'paper' AND target_kind = 'paper' "
            "ORDER BY updated_at DESC, created_at DESC",
            (profile_id,),
        ).fetchall()
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            source_paper_id = _normalize_whitespace(row["source_ref"])
            target_paper_id = _normalize_whitespace(row["target_ref"])
            relation_type = _normalize_whitespace(row["relation_type"]) or "related_to"
            if (
                not source_paper_id
                or not target_paper_id
                or source_paper_id == target_paper_id
            ):
                continue
            dedupe_key = (source_paper_id, target_paper_id, relation_type)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(
                {
                    "source_paper_id": source_paper_id,
                    "target_paper_id": target_paper_id,
                    "relation_type": relation_type,
                    "summary": _normalize_whitespace(row["summary"]),
                    "summary_zh": _normalize_whitespace(row["summary_zh"]),
                    "summary_localized": _make_localized_text(
                        row["summary"], row["summary_zh"]
                    ),
                    "created_at": float(row["created_at"] or _timestamp()),
                }
            )
        return deduped

    def rebuild_profile_memory(self, profile_id: int) -> None:
        knowledge_rows = self._build_rebuilt_knowledge_rows(profile_id)
        style_rows = self._build_rebuilt_style_rows(profile_id)
        link_rows = self._build_rebuilt_link_rows(profile_id)
        self._conn.execute(
            "DELETE FROM domain_knowledge WHERE profile_id = ?", (profile_id,)
        )
        self._conn.execute(
            "DELETE FROM style_preferences WHERE profile_id = ?", (profile_id,)
        )
        self._conn.execute(
            "DELETE FROM paper_links WHERE profile_id = ?", (profile_id,)
        )
        if knowledge_rows:
            self._conn.executemany(
                "INSERT INTO domain_knowledge (profile_id, paper_id, category, content, content_zh, created_at, relevance_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        profile_id,
                        row["paper_id"],
                        row["category"],
                        row["content"],
                        row.get("content_zh", ""),
                        row["created_at"],
                        row["relevance_score"],
                    )
                    for row in knowledge_rows
                ],
            )
        if style_rows:
            self._conn.executemany(
                "INSERT INTO style_preferences (profile_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                [
                    (profile_id, row["key"], row["value"], row["updated_at"])
                    for row in style_rows
                ],
            )
        if link_rows:
            self._conn.executemany(
                "INSERT INTO paper_links (profile_id, source_paper_id, target_paper_id, relation_type, summary, summary_zh, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        profile_id,
                        row["source_paper_id"],
                        row["target_paper_id"],
                        row["relation_type"],
                        row["summary"],
                        row.get("summary_zh", ""),
                        row["created_at"],
                    )
                    for row in link_rows
                ],
            )
        self._conn.commit()

    def recompute_profile_paper_count(self, profile_id: int) -> None:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT CASE WHEN paper_id != '' THEN paper_id ELSE job_id END) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
            (profile_id,),
        ).fetchone()
        self._conn.execute(
            "UPDATE profiles SET paper_count = ? WHERE id = ?",
            (int(row["cnt"] if row else 0), profile_id),
        )
        self._conn.commit()

    def query_domain_knowledge(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, paper_id, category, content, content_zh, relevance_score, created_at FROM domain_knowledge WHERE profile_id = ? ORDER BY relevance_score DESC, created_at DESC",
            (profile_id,),
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["content_localized"] = _make_localized_text(
                item.get("content", ""), item.get("content_zh", "")
            )
        if not keywords:
            return items[:top_k]
        return self._rank_text_items(
            items,
            keywords=keywords,
            text_fields=["category", "content", "paper_id"],
            top_k=top_k,
        )

    def add_domain_knowledge(
        self,
        profile_id: int,
        paper_id: str,
        category: str,
        content: str,
        relevance_score: float = 1.0,
    ) -> bool:
        # Legacy compat writer kept for migration/repair only; V2 tables remain source-of-truth.
        cleaned = _normalize_whitespace(content)
        if not cleaned:
            return False
        translated = self._localize_fields(
            "knowledge_item",
            {"content": cleaned},
            context={"category": category, "paper_id": paper_id},
            step_label=f"knowledge localization {category}",
        )
        self._conn.execute(
            "INSERT INTO domain_knowledge (profile_id, paper_id, category, content, content_zh, created_at, relevance_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                _normalize_whitespace(paper_id),
                _normalize_whitespace(category) or "general",
                cleaned,
                translated.get("content", ""),
                _timestamp(),
                float(relevance_score),
            ),
        )
        self._conn.commit()
        return True

    def get_style_preferences(self, profile_id: int) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM style_preferences WHERE profile_id = ? ORDER BY updated_at DESC",
            (profile_id,),
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_style_preference(self, profile_id: int, key: str, value: str) -> None:
        # Legacy compat writer kept for migration/repair only; V2 tables remain source-of-truth.
        normalized_key = _normalize_whitespace(key)
        normalized_value = _normalize_whitespace(value)
        if not normalized_key or not normalized_value:
            return
        self._conn.execute(
            "INSERT INTO style_preferences (profile_id, key, value, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(profile_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (profile_id, normalized_key, normalized_value, _timestamp()),
        )
        self._conn.commit()

    def add_paper_link(
        self,
        profile_id: int,
        source_paper_id: str,
        target_paper_id: str,
        relation_type: str = "related_to",
        summary: str = "",
    ) -> bool:
        # Legacy compat writer kept for migration/repair only; V2 tables remain source-of-truth.
        src = _normalize_whitespace(source_paper_id)
        tgt = _normalize_whitespace(target_paper_id)
        if not src or not tgt or src == tgt:
            return False
        translated = self._localize_fields(
            "paper_link",
            {"summary": _normalize_whitespace(summary)},
            context={
                "source_paper_id": src,
                "target_paper_id": tgt,
                "relation_type": relation_type,
            },
            step_label=f"paper link localization {src}->{tgt}",
        )
        try:
            self._conn.execute(
                "INSERT INTO paper_links (profile_id, source_paper_id, target_paper_id, relation_type, summary, summary_zh, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    profile_id,
                    src,
                    tgt,
                    _normalize_whitespace(relation_type) or "related_to",
                    _normalize_whitespace(summary),
                    translated.get("summary", ""),
                    _timestamp(),
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def query_paper_links(
        self,
        profile_id: int,
        *,
        paper_id: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, source_paper_id, target_paper_id, relation_type, summary, summary_zh, created_at FROM paper_links WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["summary_localized"] = _make_localized_text(
                item.get("summary", ""), item.get("summary_zh", "")
            )
        if paper_id:
            normalized_paper_id = paper_id.lower()
            matched = [
                item
                for item in items
                if normalized_paper_id in str(item.get("source_paper_id", "")).lower()
                or normalized_paper_id in str(item.get("target_paper_id", "")).lower()
            ]
            if matched:
                return matched[:limit]
        if keywords:
            return self._rank_text_items(
                items,
                keywords=keywords,
                text_fields=[
                    "source_paper_id",
                    "target_paper_id",
                    "relation_type",
                    "summary",
                ],
                top_k=limit,
            )
        return items[:limit]

    def _build_cognition_blocks(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        current_paper_id: str | None = None,
        for_selector: bool = False,
    ) -> list[str]:
        sections: list[str] = []
        max_chars = (_MAX_MEMORY_CONTEXT_TOKENS + (250 if for_selector else 0)) * 4
        total_chars = 0

        def try_append(block: str) -> None:
            nonlocal total_chars
            candidate = block.strip()
            if not candidate:
                return
            projected = total_chars + len(candidate) + (2 if sections else 0)
            if projected > max_chars:
                return
            sections.append(candidate)
            total_chars = projected

        synthesis_items = self.list_synthesis_items(profile_id, limit=40)
        if keywords:
            synthesis_items = self._rank_text_items(
                synthesis_items,
                keywords=keywords,
                text_fields=["item_type", "title", "summary", "default_resolution"],
                top_k=8,
            )
        else:
            synthesis_items = synthesis_items[:8]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in synthesis_items:
            grouped[str(item.get("item_type", "consensus"))].append(item)
        for item_type, heading in [
            ("consensus", "[Consensus]"),
            ("debate", "[Debates]"),
            ("evolution", "[Method Evolution]"),
            ("open_question", "[Open Questions]"),
        ]:
            items = grouped.get(item_type, [])
            if not items:
                continue
            lines = [heading]
            for item in items[: 3 if item_type != "debate" else 4]:
                summary = _normalize_whitespace(
                    item.get("default_resolution", "") or item.get("summary", "")
                )
                lines.append(f"- {item.get('title', '')}: {summary}")
            try_append("\n".join(lines))

        claims = self.list_claims(profile_id, limit=30)
        if current_paper_id:
            claims = [
                claim
                for claim in claims
                if _normalize_whitespace(claim.get("paper_id", ""))
                != _normalize_whitespace(current_paper_id)
            ] or claims
        if keywords:
            claims = self._rank_text_items(
                claims,
                keywords=keywords,
                text_fields=["title", "body", "default_resolution", "claim_type"],
                top_k=6,
            )
        else:
            claims = claims[:6]
        if claims:
            lines = ["[Evidence-backed Claims]"]
            for claim in claims:
                claim_text = _normalize_whitespace(
                    claim.get("default_resolution", "") or claim.get("body", "")
                )
                suffix = (
                    " (review pending)"
                    if str(claim.get("review_status", "none")) == "pending"
                    else ""
                )
                lines.append(f"- {claim.get('title', '')}: {claim_text}{suffix}")
            try_append("\n".join(lines))

        review_rows = self.list_review_items(profile_id, limit=6)
        pending_reviews = [
            item for item in review_rows if str(item.get("status", "")) == "pending"
        ]
        if pending_reviews:
            lines = ["[Pending Human Reviews]"]
            for item in pending_reviews[:4]:
                default_resolution = _normalize_whitespace(
                    item.get("default_resolution", "")
                )
                lines.append(
                    f"- {item.get('title', '')}: default currently used -> {default_resolution or 'keep existing version'}"
                )
            try_append("\n".join(lines))

        preferences = self.get_style_preferences(profile_id)
        if preferences and not for_selector:
            pref_lines = ["[Style Preferences]"]
            for key, value in preferences.items():
                pref_lines.append(f"- {key}: {value}")
            try_append("\n".join(pref_lines))

        related_links = self.query_paper_links(
            profile_id,
            paper_id=current_paper_id,
            keywords=keywords,
            limit=_MAX_RELATED_LINKS,
        )
        if related_links:
            link_lines = ["[Related Papers]"]
            for item in related_links[:_MAX_RELATED_LINKS]:
                link_lines.append(
                    f"- {item['source_paper_id']} --[{item['relation_type']}]--> {item['target_paper_id']}: {item['summary']}"
                )
            try_append("\n".join(link_lines))
        return sections

    def _build_cognition_blocks_v2(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        current_paper_id: str | None = None,
        for_selector: bool = False,
    ) -> list[str]:
        sections: list[str] = []
        max_chars = (_MAX_MEMORY_CONTEXT_TOKENS + (250 if for_selector else 0)) * 4
        total_chars = 0

        def try_append(block: str) -> bool:
            nonlocal total_chars
            candidate = block.strip()
            if not candidate:
                return False
            projected = total_chars + len(candidate) + (2 if sections else 0)
            if projected > max_chars:
                return False
            sections.append(candidate)
            total_chars = projected
            return True

        synthesis_items = self.list_synthesis_items(profile_id, limit=48)
        if keywords:
            synthesis_items = self._rank_text_items(
                synthesis_items,
                keywords=keywords,
                text_fields=["item_type", "title", "summary", "default_resolution"],
                top_k=12,
            )
        else:
            synthesis_items = synthesis_items[:12]
        for item in synthesis_items:
            item["claim_count"] = len(item.get("claim_ids", []))
        synthesis_items.sort(
            key=lambda item: self._compute_salience_score(
                item,
                primary_key="confidence",
                support_count_key="claim_count",
            ),
            reverse=True,
        )

        claims = self.list_claims(profile_id, limit=40)
        if current_paper_id:
            claims = [
                claim
                for claim in claims
                if _normalize_whitespace(claim.get("paper_id", ""))
                != _normalize_whitespace(current_paper_id)
            ] or claims
        if keywords:
            claims = self._rank_text_items(
                claims,
                keywords=keywords,
                text_fields=[
                    "title",
                    "body",
                    "default_resolution",
                    "claim_type",
                    "paper_id",
                ],
                top_k=12,
            )
        else:
            claims = claims[:12]
        claims.sort(key=lambda item: self._compute_salience_score(item), reverse=True)

        review_rows = self.list_review_items(profile_id, limit=10)
        pending_reviews = [
            item for item in review_rows if str(item.get("status", "")) == "pending"
        ]

        summary_header = [
            "[Profile Research Memory Summary]",
            f"- synthesis items considered: {len(synthesis_items)}",
            f"- claims considered: {len(claims)}",
            f"- pending human reviews: {len(pending_reviews)}",
        ]
        try_append("\n".join(summary_header))

        if pending_reviews:
            lines = ["[Pending Human Reviews]"]
            for item in pending_reviews[:4]:
                default_resolution = (
                    _normalize_whitespace(item.get("default_resolution", ""))
                    or "keep existing version"
                )
                lines.append(
                    f"- {item.get('title', '')}: default currently used -> {default_resolution}"
                )
            try_append("\n".join(lines))

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in synthesis_items:
            grouped[str(item.get("item_type", "consensus"))].append(item)
        synthesis_headings = [
            ("consensus", "[Consensus]"),
            ("debate", "[Debates]"),
            ("evolution", "[Method Evolution]"),
            ("open_question", "[Open Questions]"),
        ]
        surfaced_claim_ids: set[int] = set()
        for item_type, heading in synthesis_headings:
            items = grouped.get(item_type, [])
            if not items:
                continue
            lines = [heading]
            for item in items[: 3 if item_type != "debate" else 4]:
                summary = _normalize_whitespace(
                    item.get("default_resolution", "") or item.get("summary", "")
                )
                claim_count = len(item.get("claim_ids", []))
                review_suffix = (
                    " (human review pending)"
                    if str(item.get("review_status", "none")) == "pending"
                    else ""
                )
                lines.append(
                    f"- {item.get('title', '')}: {summary} ({claim_count} supporting claims){review_suffix}"
                )
                surfaced_claim_ids.update(
                    int(claim_id)
                    for claim_id in item.get("claim_ids", [])
                    if _maybe_int(claim_id) is not None
                )
            try_append("\n".join(lines))

        compact_claims: list[dict[str, Any]] = []
        for claim in claims:
            claim_id = _maybe_int(claim.get("id"))
            if (
                claim_id is not None
                and claim_id in surfaced_claim_ids
                and str(claim.get("review_status", "none")) != "pending"
                and str(claim.get("status", "active")) != "conflicted"
                and not bool(claim.get("manual_locked"))
            ):
                continue
            compact_claims.append(claim)
        if compact_claims:
            lines = ["[Evidence-backed Claims]"]
            for claim in compact_claims[:6]:
                claim_text = _normalize_whitespace(
                    claim.get("default_resolution", "") or claim.get("body", "")
                )
                evidence_count = int(claim.get("evidence_count", 0) or 0)
                paper_id = _normalize_whitespace(claim.get("paper_id", ""))
                suffix_parts: list[str] = []
                if (
                    str(claim.get("review_status", "none")) == "pending"
                    or str(claim.get("status", "")) == "conflicted"
                ):
                    suffix_parts.append(
                        "CONFLICTED: default resolution in use, human review pending"
                    )
                if evidence_count > 0:
                    suffix_parts.append(f"{evidence_count} evidence fragments")
                if paper_id:
                    suffix_parts.append(f"source={paper_id}")
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                lines.append(f"- {claim.get('title', '')}: {claim_text}{suffix}")
            try_append("\n".join(lines))

        preferences = self.get_style_preferences(profile_id)
        if preferences and not for_selector:
            pref_lines = ["[Style Preferences]"]
            for key, value in preferences.items():
                pref_lines.append(f"- {key}: {value}")
            try_append("\n".join(pref_lines))

        related_links = self.query_paper_links(
            profile_id,
            paper_id=current_paper_id,
            keywords=keywords,
            limit=_MAX_RELATED_LINKS,
        )
        if related_links:
            link_lines = ["[Related Papers]"]
            for item in related_links[:_MAX_RELATED_LINKS]:
                link_lines.append(
                    f"- {item['source_paper_id']} --[{item['relation_type']}]--> {item['target_paper_id']}: {item['summary']}"
                )
            try_append("\n".join(link_lines))
        return sections

    def build_memory_context(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        current_paper_id: str | None = None,
    ) -> str:
        blocks = self._build_cognition_blocks_v2(
            profile_id,
            keywords=keywords,
            current_paper_id=current_paper_id,
            for_selector=False,
        )
        if not blocks:
            blocks = self._build_cognition_blocks(
                profile_id,
                keywords=keywords,
                current_paper_id=current_paper_id,
                for_selector=False,
            )
        return "\n\n".join(blocks)

    def retrieve_for_interpreter(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        current_paper_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_keywords = _dedupe_strings(keywords or [], limit=12)

        synthesis_items = self.list_synthesis_items(profile_id, limit=48)
        if normalized_keywords:
            synthesis_items = self._rank_text_items(
                synthesis_items,
                keywords=normalized_keywords,
                text_fields=["item_type", "title", "summary", "default_resolution"],
                top_k=12,
            )
        else:
            synthesis_items = synthesis_items[:12]
        for item in synthesis_items:
            item["claim_count"] = len(item.get("claim_ids", []))
        synthesis_items.sort(
            key=lambda item: self._compute_salience_score(
                item, primary_key="confidence", support_count_key="claim_count"
            ),
            reverse=True,
        )

        claims = self.list_claims(profile_id, limit=48)
        if current_paper_id:
            claims = [
                claim
                for claim in claims
                if _normalize_whitespace(claim.get("paper_id", ""))
                != _normalize_whitespace(current_paper_id)
            ] or claims
        if normalized_keywords:
            claims = self._rank_text_items(
                claims,
                keywords=normalized_keywords,
                text_fields=[
                    "title",
                    "body",
                    "default_resolution",
                    "claim_type",
                    "paper_id",
                    "entity_names",
                ],
                top_k=12,
            )
        else:
            claims = claims[:12]
        claims.sort(key=lambda item: self._compute_salience_score(item), reverse=True)

        evidence_items = self.list_evidence(profile_id, limit=120)
        if current_paper_id:
            evidence_items = [
                item
                for item in evidence_items
                if _normalize_whitespace(item.get("paper_id", ""))
                != _normalize_whitespace(current_paper_id)
            ] or evidence_items
        if normalized_keywords:
            evidence_items = self._rank_text_items(
                evidence_items,
                keywords=normalized_keywords,
                text_fields=[
                    "claim_title",
                    "section_title",
                    "snippet",
                    "evidence_summary",
                    "paper_id",
                ],
                top_k=8,
            )
        else:
            evidence_items = evidence_items[:8]
        evidence_items.sort(
            key=lambda item: (
                self._compute_salience_score(
                    item,
                    primary_key="weight",
                    support_count_key="manual_locked",
                ),
                float(item.get("updated_at", item.get("created_at", 0.0)) or 0.0),
            ),
            reverse=True,
        )

        review_rows = self.list_review_items(profile_id, limit=12)
        active_conflicts = [
            item for item in review_rows if str(item.get("status", "")) == "pending"
        ][:4]

        related_links = self.query_paper_links(
            profile_id,
            paper_id=current_paper_id,
            keywords=normalized_keywords,
            limit=6,
        )
        style_preferences = self.get_style_preferences(profile_id)

        bundle = {
            "high_level_digest": synthesis_items[:_INTERPRETER_DIGEST_BUDGET],
            "priority_claims": claims[:_INTERPRETER_CLAIM_BUDGET],
            "relevant_evidence": evidence_items[:_INTERPRETER_EVIDENCE_BUDGET],
            "active_conflicts": active_conflicts[:_INTERPRETER_CONFLICT_BUDGET],
            "related_papers": related_links[:_INTERPRETER_LINK_BUDGET],
            "style_preferences": style_preferences,
            "keywords": normalized_keywords,
        }
        return bundle

    def render_interpreter_context(self, bundle: dict[str, Any]) -> str:
        sections: list[str] = []
        max_chars = (_MAX_MEMORY_CONTEXT_TOKENS + 120) * 4
        total_chars = 0

        def try_append(block: str) -> None:
            nonlocal total_chars
            candidate = block.strip()
            if not candidate:
                return
            projected = total_chars + len(candidate) + (2 if sections else 0)
            if projected > max_chars:
                return
            sections.append(candidate)
            total_chars = projected

        digest = bundle.get("high_level_digest", [])
        if digest:
            lines = ["[High-level Digest]"]
            for item in digest[:5]:
                summary = _normalize_whitespace(
                    item.get("default_resolution", "") or item.get("summary", "")
                )
                claim_count = len(item.get("claim_ids", []))
                lines.append(
                    f"- {item.get('title', '')}: {summary} ({claim_count} supporting claims)"
                )
            try_append("\n".join(lines))

        claims = bundle.get("priority_claims", [])
        if claims:
            lines = ["[Priority Claims]"]
            for claim in claims[:6]:
                text = _normalize_whitespace(
                    claim.get("default_resolution", "") or claim.get("body", "")
                )
                evidence_count = int(claim.get("evidence_count", 0) or 0)
                source = _normalize_whitespace(claim.get("paper_id", ""))
                suffix_parts: list[str] = []
                if evidence_count > 0:
                    suffix_parts.append(f"{evidence_count} evidence fragments")
                if source:
                    suffix_parts.append(f"source={source}")
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                lines.append(f"- {claim.get('title', '')}: {text}{suffix}")
            try_append("\n".join(lines))

        evidence_items = bundle.get("relevant_evidence", [])
        if evidence_items:
            lines = ["[Relevant Evidence]"]
            for item in evidence_items[:5]:
                snippet = _normalize_whitespace(item.get("snippet", ""))
                summary = _normalize_whitespace(item.get("evidence_summary", ""))
                label = _normalize_whitespace(item.get("page_label", ""))
                claim_title = _normalize_whitespace(item.get("claim_title", ""))
                suffix = f" ({label})" if label else ""
                lines.append(f"- {claim_title}: {summary or snippet[:120]}{suffix}")
            try_append("\n".join(lines))

        conflicts = bundle.get("active_conflicts", [])
        if conflicts:
            lines = ["[Active Conflicts]"]
            for item in conflicts[:3]:
                default_resolution = (
                    _normalize_whitespace(item.get("default_resolution", ""))
                    or "keep existing version"
                )
                lines.append(
                    f"- {item.get('title', '')}: default currently used -> {default_resolution}"
                )
            try_append("\n".join(lines))

        preferences = bundle.get("style_preferences", {})
        if preferences:
            lines = ["[Style Preferences]"]
            for key, value in preferences.items():
                lines.append(f"- {key}: {value}")
            try_append("\n".join(lines))

        related = bundle.get("related_papers", [])
        if related:
            lines = ["[Related Papers]"]
            for item in related[:5]:
                lines.append(
                    f"- {item['source_paper_id']} --[{item['relation_type']}]--> {item['target_paper_id']}: {item['summary']}"
                )
            try_append("\n".join(lines))

        return "\n\n".join(sections)

    def retrieve_for_selector(
        self,
        profile_id: int,
        *,
        topics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        topic_keywords: list[str] = []
        for topic in topics or []:
            topic_keywords.extend(
                str(item) for item in topic.get("keywords", []) if str(item).strip()
            )
            for key in ("name", "query"):
                value = _normalize_whitespace(topic.get(key, ""))
                if value:
                    topic_keywords.append(value)
        normalized_keywords = _dedupe_strings(topic_keywords, limit=12)

        digest = self.list_synthesis_items(profile_id, limit=24)
        digest = self._rank_text_items(
            digest,
            keywords=normalized_keywords,
            text_fields=["item_type", "title", "summary", "default_resolution"],
            top_k=_SELECTOR_DIGEST_BUDGET,
        )
        for item in digest:
            item["claim_count"] = len(item.get("claim_ids", []))
        digest.sort(
            key=lambda item: self._compute_salience_score(
                item, primary_key="confidence", support_count_key="claim_count"
            ),
            reverse=True,
        )

        claims = self.list_claims(profile_id, limit=24)
        claims = self._rank_text_items(
            claims,
            keywords=normalized_keywords,
            text_fields=["title", "body", "default_resolution", "paper_id"],
            top_k=_SELECTOR_CLAIM_BUDGET,
        )
        claims.sort(key=lambda item: self._compute_salience_score(item), reverse=True)

        related_links = self.query_paper_links(
            profile_id,
            keywords=normalized_keywords,
            limit=_SELECTOR_LINK_BUDGET,
        )

        return {
            "keywords": normalized_keywords,
            "high_level_digest": digest[:_SELECTOR_DIGEST_BUDGET],
            "priority_claims": claims[:_SELECTOR_CLAIM_BUDGET],
            "related_papers": related_links[:_SELECTOR_LINK_BUDGET],
        }

    def render_selection_context(self, bundle: dict[str, Any]) -> str:
        sections: list[str] = []
        digest = bundle.get("high_level_digest", [])
        if digest:
            lines = ["[Relevant Research Themes]"]
            for item in digest[:_SELECTOR_DIGEST_BUDGET]:
                summary = _normalize_whitespace(
                    item.get("default_resolution", "") or item.get("summary", "")
                )
                lines.append(f"- {item.get('title', '')}: {summary}")
            sections.append("\n".join(lines))

        claims = bundle.get("priority_claims", [])
        if claims:
            lines = ["[Useful Prior Findings]"]
            for claim in claims[:_SELECTOR_CLAIM_BUDGET]:
                text = _normalize_whitespace(
                    claim.get("default_resolution", "") or claim.get("body", "")
                )
                lines.append(f"- {claim.get('title', '')}: {text}")
            sections.append("\n".join(lines))

        related = bundle.get("related_papers", [])
        if related:
            lines = ["[Related Papers]"]
            for item in related[:_SELECTOR_LINK_BUDGET]:
                lines.append(
                    f"- {item['source_paper_id']} --[{item['relation_type']}]--> {item['target_paper_id']}: {item['summary']}"
                )
            sections.append("\n".join(lines))
        if not sections:
            return ""
        return "\n\n".join(["[Profile Research Memory for Paper Selection]", *sections])

    def retrieve_for_review_conflict(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
        target_text: str = "",
    ) -> dict[str, Any]:
        normalized_keywords = _dedupe_strings(
            [*(keywords or []), target_text], limit=12
        )
        claims = self._rank_text_items(
            self.list_claims(profile_id, limit=40),
            keywords=normalized_keywords,
            text_fields=["title", "body", "default_resolution", "claim_type"],
            top_k=_REVIEW_CLAIM_BUDGET,
        )
        evidence = self._rank_text_items(
            self.list_evidence(profile_id, limit=80),
            keywords=normalized_keywords,
            text_fields=["claim_title", "snippet", "evidence_summary", "section_title"],
            top_k=_REVIEW_EVIDENCE_BUDGET,
        )
        reviews = [
            item
            for item in self.list_review_items(profile_id, limit=20)
            if str(item.get("status", "")) == "pending"
        ]
        reviews = self._rank_text_items(
            reviews,
            keywords=normalized_keywords,
            text_fields=["title", "description", "default_resolution"],
            top_k=_REVIEW_CONFLICT_BUDGET,
        )
        return {
            "keywords": normalized_keywords,
            "priority_claims": claims,
            "relevant_evidence": evidence,
            "active_conflicts": reviews,
        }

    def render_review_conflict_context(self, bundle: dict[str, Any]) -> str:
        sections: list[str] = []
        claims = bundle.get("priority_claims", [])
        if claims:
            lines = ["[Nearby Claims]"]
            for claim in claims[:_REVIEW_CLAIM_BUDGET]:
                text = _normalize_whitespace(
                    claim.get("default_resolution", "") or claim.get("body", "")
                )
                lines.append(f"- {claim.get('title', '')}: {text}")
            sections.append("\n".join(lines))

        evidence = bundle.get("relevant_evidence", [])
        if evidence:
            lines = ["[Nearby Evidence]"]
            for item in evidence[:_REVIEW_EVIDENCE_BUDGET]:
                summary = _normalize_whitespace(
                    item.get("evidence_summary", "") or item.get("snippet", "")
                )
                lines.append(f"- {item.get('claim_title', '')}: {summary}")
            sections.append("\n".join(lines))

        conflicts = bundle.get("active_conflicts", [])
        if conflicts:
            lines = ["[Pending Conflict Queue]"]
            for item in conflicts[:_REVIEW_CONFLICT_BUDGET]:
                default_resolution = (
                    _normalize_whitespace(item.get("default_resolution", ""))
                    or "keep existing version"
                )
                lines.append(
                    f"- {item.get('title', '')}: default currently used -> {default_resolution}"
                )
            sections.append("\n".join(lines))
        return "\n\n".join(section for section in sections if section.strip())

    def retrieve_for_translation_style(
        self,
        profile_id: int,
        *,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_keywords = _dedupe_strings(keywords or [], limit=12)
        style_preferences = self.get_style_preferences(profile_id)
        synthesis_items = self._rank_text_items(
            self.list_synthesis_items(profile_id, limit=30),
            keywords=normalized_keywords,
            text_fields=["title", "summary", "default_resolution"],
            top_k=6,
        )
        claims = self._rank_text_items(
            self.list_claims(profile_id, limit=40),
            keywords=normalized_keywords,
            text_fields=["title", "body", "default_resolution"],
            top_k=10,
        )

        terminology_hints: list[str] = []
        seen: set[str] = set()
        for item in synthesis_items:
            title = _normalize_whitespace(item.get("title", ""))
            if title and title.casefold() not in seen:
                seen.add(title.casefold())
                terminology_hints.append(title)
        for claim in claims:
            title = _normalize_whitespace(claim.get("title", ""))
            if title and title.casefold() not in seen:
                seen.add(title.casefold())
                terminology_hints.append(title)
            for entity_name in claim.get("entity_names", [])[:2]:
                cleaned = _normalize_whitespace(entity_name)
                if cleaned and cleaned.casefold() not in seen:
                    seen.add(cleaned.casefold())
                    terminology_hints.append(cleaned)
            if len(terminology_hints) >= _TRANSLATION_TERM_BUDGET:
                break

        return {
            "keywords": normalized_keywords,
            "style_preferences": style_preferences,
            "terminology_hints": terminology_hints[:_TRANSLATION_TERM_BUDGET],
        }

    def render_translation_style_context(self, bundle: dict[str, Any]) -> str:
        sections: list[str] = []
        style_preferences = bundle.get("style_preferences", {})
        if style_preferences:
            lines = ["[Translation Style Preferences]"]
            for key, value in style_preferences.items():
                lines.append(f"- {key}: {value}")
            sections.append("\n".join(lines))
        terminology_hints = bundle.get("terminology_hints", [])
        if terminology_hints:
            lines = ["[Terminology Hints]"]
            for term in terminology_hints[:_TRANSLATION_TERM_BUDGET]:
                lines.append(f"- {term}")
            sections.append("\n".join(lines))
        return "\n\n".join(section for section in sections if section.strip())

    def build_selection_context(
        self,
        profile_id: int,
        *,
        topics: list[dict[str, Any]] | None = None,
    ) -> str:
        bundle = self.retrieve_for_selector(profile_id, topics=topics)
        rendered = self.render_selection_context(bundle)
        if rendered.strip():
            return rendered

        topic_keywords: list[str] = []
        for topic in topics or []:
            topic_keywords.extend(
                str(item) for item in topic.get("keywords", []) if str(item).strip()
            )
            for key in ("name", "query"):
                value = _normalize_whitespace(topic.get(key, ""))
                if value:
                    topic_keywords.append(value)
        blocks = self._build_cognition_blocks_v2(
            profile_id,
            keywords=_dedupe_strings(topic_keywords, limit=12),
            for_selector=True,
        )
        if not blocks:
            blocks = self._build_cognition_blocks(
                profile_id,
                keywords=_dedupe_strings(topic_keywords, limit=12),
                for_selector=True,
            )
        if not blocks:
            return ""
        return "\n\n".join(["[Profile Research Memory for Paper Selection]", *blocks])

    def write_memories(
        self,
        profile_id: int,
        paper_id: str,
        extraction: dict[str, Any],
        *,
        job_id: str | None = None,
        paper_title: str = "",
    ) -> None:
        paper_ctx = f"- Title: {paper_title}" if paper_title else ""
        self._deferred_ctx = _DeferredLocalizationContext(paper_context=paper_ctx)
        delta = _DeltaCollector()
        try:
            self._write_memories_inner(
                profile_id, paper_id, extraction, job_id=job_id, delta=delta
            )
            self._deferred_ctx.flush(self._conn)
            # Persist delta to the writeback row
            resolved_job_id = _normalize_whitespace(job_id)
            if resolved_job_id:
                delta_json_str = _safe_json_dumps(delta.to_dict())
                self._conn.execute(
                    "UPDATE memory_writebacks SET delta_json = ? WHERE profile_id = ? AND job_id = ? AND deleted_at IS NULL",
                    (delta_json_str, profile_id, resolved_job_id),
                )
            self._conn.commit()
            self._invalidate_profile_views(profile_id)
            self.rebuild_profile_memory(profile_id)
            self.recompute_profile_paper_count(profile_id)
            self.touch_profile(profile_id)
        except Exception:
            self._deferred_ctx = None
            raise
        finally:
            self._deferred_ctx = None

    def _write_memories_inner(
        self,
        profile_id: int,
        paper_id: str,
        extraction: dict[str, Any],
        *,
        job_id: str | None = None,
        delta: _DeltaCollector | None = None,
    ) -> None:
        self.ensure_profile_memory_provenance(profile_id)
        style_observations = (
            extraction.get("style_observations", [])
            if isinstance(extraction, dict)
            else []
        )
        paper_relations = (
            extraction.get("paper_relations", [])
            if isinstance(extraction, dict)
            else []
        )
        entities = (
            extraction.get("entities", []) if isinstance(extraction, dict) else []
        )
        claims = extraction.get("claims", []) if isinstance(extraction, dict) else []
        synthesis_items = (
            extraction.get("synthesis_items", [])
            if isinstance(extraction, dict)
            else []
        )
        domain_facts = (
            extraction.get("domain_facts", []) if isinstance(extraction, dict) else []
        )

        resolved_paper_id = _normalize_whitespace(paper_id) or "unknown"
        resolved_job_id = _normalize_whitespace(job_id)
        if not resolved_job_id:
            raise ValueError("job_id is required for memory writeback")
        writeback_id = self._ensure_writeback(
            profile_id,
            resolved_job_id,
            resolved_paper_id,
            provenance_mode="exact",
            created_at=_timestamp(),
        )
        if writeback_id is None:
            raise ValueError(
                "Unable to create memory writeback without a job identifier"
            )

        self._delete_writeback_events(writeback_id)
        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_claim_evidence SET deleted_at = ?, updated_at = ? WHERE writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (deleted_at, deleted_at, writeback_id),
        )
        self._conn.execute(
            "UPDATE memory_graph_edges SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (deleted_at, deleted_at, writeback_id),
        )
        self._conn.execute(
            "UPDATE memory_claims SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (deleted_at, deleted_at, writeback_id),
        )
        self._conn.execute(
            "UPDATE memory_synthesis_items SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (deleted_at, deleted_at, writeback_id),
        )

        base_created_at = _timestamp()
        derived_fact_rows: list[dict[str, Any]] = []
        for index, observation in enumerate(style_observations):
            if not isinstance(observation, dict):
                continue
            key = _normalize_whitespace(observation.get("key", ""))
            value = _normalize_whitespace(observation.get("value", ""))
            if not key or not value:
                continue
            self._conn.execute(
                "INSERT INTO memory_style_events (writeback_id, key, value, created_at) VALUES (?, ?, ?, ?)",
                (writeback_id, key, value, base_created_at + 5 + index * 0.0001),
            )

        # Collect existing entity names to detect new vs existing after upsert
        _existing_entity_names: set[str] = set()
        if delta is not None:
            rows = self._conn.execute(
                "SELECT normalized_name FROM memory_entities WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchall()
            _existing_entity_names = {str(row["normalized_name"]) for row in rows}

        entity_id_cache: dict[str, int] = {}
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            ent_name = str(entity.get("name", ""))
            ent_type = str(entity.get("type", entity.get("entity_type", "concept")))
            entity_id = self._upsert_entity(
                profile_id,
                name=ent_name,
                entity_type=ent_type,
                summary=str(entity.get("summary", "")),
                actor_type="ai",
            )
            if entity_id is not None:
                norm_key = _normalize_lookup_key(ent_name)
                entity_id_cache[norm_key] = entity_id
                if delta is not None and norm_key not in _existing_entity_names:
                    delta.record_new_entity(ent_name, ent_type)

        claim_id_by_key: dict[str, int] = {}
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_title_raw = str(claim.get("title", ""))
            claim_body_raw = str(claim.get("body", ""))
            claim_key = _normalize_whitespace(
                claim.get("claim_key", "")
            ) or _claim_default_key(claim_title_raw, claim_body_raw)
            # Check if claim already exists before upsert for delta detection
            _pre_existing_claim = (
                self._get_active_claim_by_key(profile_id, claim_key)
                if delta is not None
                else None
            )
            claim_id = self._upsert_claim_from_extraction(
                profile_id, writeback_id, claim, entity_id_cache
            )
            if claim_id is not None:
                claim_id_by_key[claim_key] = claim_id
                claim_row = self._conn.execute(
                    "SELECT * FROM memory_claims WHERE id = ?", (claim_id,)
                ).fetchone()
                if claim_row is not None:
                    self._ensure_derived_synthesis_from_claim(
                        profile_id, self._claim_row_to_dict(claim_row)
                    )
                # Delta tracking
                if delta is not None and claim_row is not None:
                    row_title = _normalize_whitespace(claim_row["title"])
                    row_stance = str(claim_row["stance"])
                    row_status = str(claim_row["status"])
                    claim_type_val = str(claim_row["claim_type"])
                    if _pre_existing_claim is None:
                        delta.record_new_claim(row_title, claim_type_val, row_stance)
                    elif row_status == "conflicted":
                        delta.record_claim_challenged(
                            row_title,
                            "stance_conflict",
                            triggered_review=str(claim_row["review_status"])
                            == "pending",
                        )
                    else:
                        delta.record_claim_reinforced(row_title)
                claim_text = _normalize_whitespace(claim.get("body", ""))
                if claim_text:
                    derived_fact_rows.append(
                        {
                            "category": f"claim/{_normalize_whitespace(claim.get('claim_type', 'finding')) or 'finding'}",
                            "content": f"{_normalize_whitespace(claim.get('title', ''))}: {claim_text}",
                            "relevance_score": min(
                                max(_maybe_float(claim.get("importance"), 0.5), 0.0),
                                1.0,
                            ),
                        }
                    )

        for synthesis_item in synthesis_items:
            if not isinstance(synthesis_item, dict):
                continue
            syn_key = _normalize_whitespace(synthesis_item.get("synthesis_key", ""))
            _pre_existing_syn = None
            if delta is not None and syn_key:
                _pre_existing_syn = self._get_active_synthesis_by_key(
                    profile_id, syn_key
                )
            synthesis_id = self._upsert_synthesis_from_extraction(
                profile_id, writeback_id, synthesis_item, claim_id_by_key
            )
            if synthesis_id is not None:
                summary = _normalize_whitespace(synthesis_item.get("summary", ""))
                title = _normalize_whitespace(synthesis_item.get("title", ""))
                if delta is not None:
                    item_type = (
                        _normalize_whitespace(
                            synthesis_item.get("item_type", "consensus")
                        )
                        or "consensus"
                    )
                    if _pre_existing_syn is None:
                        delta.record_new_synthesis(title or summary[:60], item_type)
                        if item_type == "debate":
                            delta.record_new_debate(title or summary[:60])
                    else:
                        delta.record_updated_synthesis(
                            title or summary[:60], f"confidence updated"
                        )
                if summary or title:
                    derived_fact_rows.append(
                        {
                            "category": _normalize_whitespace(
                                synthesis_item.get("item_type", "consensus")
                            )
                            or "consensus",
                            "content": f"{title}: {summary or title}",
                            "relevance_score": min(
                                max(
                                    _maybe_float(synthesis_item.get("confidence"), 0.6),
                                    0.0,
                                ),
                                1.0,
                            ),
                        }
                    )

        for index, relation in enumerate(paper_relations):
            if not isinstance(relation, dict):
                continue
            target = _normalize_whitespace(relation.get("target", ""))
            if not target:
                continue
            relation_type = (
                _normalize_whitespace(relation.get("relation", "related_to"))
                or "related_to"
            )
            summary = _normalize_whitespace(relation.get("summary", ""))
            self._conn.execute(
                "INSERT INTO memory_link_events (writeback_id, source_paper_id, target_paper_id, relation_type, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    writeback_id,
                    resolved_paper_id,
                    target,
                    relation_type,
                    summary,
                    base_created_at + 10 + index * 0.0001,
                ),
            )
            self._upsert_graph_edge_from_extraction(
                profile_id, writeback_id, relation, source_paper_id=resolved_paper_id
            )

        if domain_facts:
            for index, fact in enumerate(domain_facts):
                if not isinstance(fact, dict):
                    continue
                content = _normalize_whitespace(fact.get("content", ""))
                if not content:
                    continue
                self._conn.execute(
                    "INSERT INTO memory_knowledge_events (writeback_id, category, content, relevance_score, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        writeback_id,
                        _normalize_whitespace(fact.get("category", "general"))
                        or "general",
                        content,
                        min(
                            max(_maybe_float(fact.get("relevance_score"), 1.0), 0.0),
                            2.0,
                        ),
                        base_created_at + index * 0.0001,
                    ),
                )
        else:
            for index, fact in enumerate(derived_fact_rows[:12]):
                self._conn.execute(
                    "INSERT INTO memory_knowledge_events (writeback_id, category, content, relevance_score, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        writeback_id,
                        fact["category"],
                        fact["content"],
                        fact["relevance_score"],
                        base_created_at + index * 0.0001,
                    ),
                )

        self._prune_orphaned_claims(profile_id)
        self._prune_orphaned_synthesis(profile_id)
        log.info(
            "Memory V3 writeback prepared for %s on profile %d (job=%s): %d entities, %d claims, %d synthesis items, %d paper edges",
            resolved_paper_id,
            profile_id,
            resolved_job_id,
            len(entity_id_cache),
            len(claims),
            len(synthesis_items),
            len(paper_relations),
        )

    def _dismiss_pending_reviews_for_targets(
        self,
        profile_id: int,
        *,
        target_type: str,
        target_ids: list[int],
        deleted_at: float,
    ) -> None:
        if not target_ids:
            return
        placeholders = ", ".join("?" for _ in target_ids)
        self._conn.execute(
            f"UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? "
            f"WHERE profile_id = ? AND status = 'pending' AND target_type = ? AND target_id IN ({placeholders})",
            (deleted_at, deleted_at, profile_id, target_type, *target_ids),
        )

    def _delete_writeback_bundle(
        self, profile_id: int, row: sqlite3.Row
    ) -> dict[str, Any]:
        writeback_id = int(row["id"])
        resolved_job_id = _normalize_whitespace(row["job_id"])
        resolved_paper_id = _normalize_whitespace(row["paper_id"])
        provenance_mode = str(row["provenance_mode"] or "exact")

        deleted_knowledge = int(
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_knowledge_events WHERE writeback_id = ?",
                (writeback_id,),
            ).fetchone()["cnt"]
        )
        deleted_style = int(
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_style_events WHERE writeback_id = ?",
                (writeback_id,),
            ).fetchone()["cnt"]
        )
        deleted_links = int(
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_link_events WHERE writeback_id = ?",
                (writeback_id,),
            ).fetchone()["cnt"]
        )
        deleted_evidence = int(
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claim_evidence WHERE writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (writeback_id,),
            ).fetchone()["cnt"]
        )
        claim_ids = [
            int(item["id"])
            for item in self._conn.execute(
                "SELECT id FROM memory_claims WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (writeback_id,),
            ).fetchall()
        ]
        synthesis_ids = [
            int(item["id"])
            for item in self._conn.execute(
                "SELECT id FROM memory_synthesis_items WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (writeback_id,),
            ).fetchall()
        ]
        edge_ids = [
            int(item["id"])
            for item in self._conn.execute(
                "SELECT id FROM memory_graph_edges WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (writeback_id,),
            ).fetchall()
        ]

        deleted_at = _timestamp()
        self._conn.execute(
            "UPDATE memory_writebacks SET deleted_at = ? WHERE id = ?",
            (deleted_at, writeback_id),
        )
        self._conn.execute(
            "UPDATE memory_claim_evidence SET deleted_at = ?, updated_at = ? WHERE writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
            (deleted_at, deleted_at, writeback_id),
        )
        if edge_ids:
            self._conn.execute(
                "UPDATE memory_graph_edges SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (deleted_at, deleted_at, writeback_id),
            )
        if claim_ids:
            self._conn.execute(
                "UPDATE memory_claims SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (deleted_at, deleted_at, writeback_id),
            )
            claim_placeholders = ", ".join("?" for _ in claim_ids)
            self._conn.execute(
                f"DELETE FROM memory_claim_entities WHERE claim_id IN ({claim_placeholders})",
                claim_ids,
            )
            self._conn.execute(
                f"DELETE FROM memory_synthesis_claims WHERE claim_id IN ({claim_placeholders})",
                claim_ids,
            )
        if synthesis_ids:
            self._conn.execute(
                "UPDATE memory_synthesis_items SET deleted_at = ?, updated_at = ? WHERE origin_writeback_id = ? AND deleted_at IS NULL AND manual_locked = 0",
                (deleted_at, deleted_at, writeback_id),
            )
            synthesis_placeholders = ", ".join("?" for _ in synthesis_ids)
            self._conn.execute(
                f"DELETE FROM memory_synthesis_claims WHERE synthesis_id IN ({synthesis_placeholders})",
                synthesis_ids,
            )

        self._dismiss_pending_reviews_for_targets(
            profile_id,
            target_type="claim",
            target_ids=claim_ids,
            deleted_at=deleted_at,
        )
        self._dismiss_pending_reviews_for_targets(
            profile_id,
            target_type="synthesis",
            target_ids=synthesis_ids,
            deleted_at=deleted_at,
        )
        self._dismiss_pending_reviews_for_targets(
            profile_id,
            target_type="edge",
            target_ids=edge_ids,
            deleted_at=deleted_at,
        )

        revision_summary = f"Deleted job-linked memory bundle {resolved_job_id}"
        revision_summary_zh = (
            f"已删除与作业 {resolved_job_id} 关联的记忆写回包"
            if resolved_job_id
            else "已删除作业关联的记忆写回包"
        )
        self._log_revision(
            profile_id,
            target_type="writeback",
            target_id=writeback_id,
            action="delete",
            actor_type="user",
            summary=revision_summary,
            summary_zh=revision_summary_zh,
            before={
                "job_id": resolved_job_id,
                "paper_id": resolved_paper_id,
                "writeback_id": writeback_id,
            },
        )
        return {
            "job_id": resolved_job_id,
            "paper_id": resolved_paper_id,
            "writeback_id": writeback_id,
            "deleted_knowledge_events": deleted_knowledge,
            "deleted_style_events": deleted_style,
            "deleted_link_events": deleted_links,
            "deleted_evidence": deleted_evidence,
            "deleted_claims": len(claim_ids),
            "deleted_synthesis": len(synthesis_ids),
            "deleted_edges": len(edge_ids),
            "provenance_mode": provenance_mode,
            "deleted_at": deleted_at,
        }

    def _delete_writeback_bundles(
        self,
        profile_id: int,
        rows: list[sqlite3.Row],
        *,
        job_id: str = "",
        paper_id: str = "",
    ) -> dict[str, Any]:
        self.ensure_profile_memory_provenance(profile_id)
        deleted_rows = [self._delete_writeback_bundle(profile_id, row) for row in rows]
        deleted_orphaned_claims = self._prune_orphaned_claims(profile_id)
        deleted_orphaned_synthesis = self._prune_orphaned_synthesis(profile_id)
        deleted_orphaned_entities = self._prune_orphaned_entities(profile_id)
        self._conn.commit()
        self.rebuild_profile_memory(profile_id)
        self.recompute_profile_paper_count(profile_id)
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)

        provenance_modes = sorted(
            {
                str(item.get("provenance_mode") or "exact")
                for item in deleted_rows
                if str(item.get("provenance_mode") or "").strip()
            }
        )
        aggregate_deleted_at = max(
            (float(item.get("deleted_at", 0.0) or 0.0) for item in deleted_rows),
            default=0.0,
        )
        return {
            "profile_id": profile_id,
            "job_id": job_id,
            "paper_id": paper_id,
            "deleted_job_ids": [str(item.get("job_id") or "") for item in deleted_rows],
            "deleted_writeback_count": len(deleted_rows),
            "deleted_knowledge_events": sum(
                int(item.get("deleted_knowledge_events", 0) or 0)
                for item in deleted_rows
            ),
            "deleted_style_events": sum(
                int(item.get("deleted_style_events", 0) or 0) for item in deleted_rows
            ),
            "deleted_link_events": sum(
                int(item.get("deleted_link_events", 0) or 0) for item in deleted_rows
            ),
            "deleted_evidence": sum(
                int(item.get("deleted_evidence", 0) or 0) for item in deleted_rows
            ),
            "deleted_claims": sum(
                int(item.get("deleted_claims", 0) or 0) for item in deleted_rows
            ),
            "deleted_synthesis": sum(
                int(item.get("deleted_synthesis", 0) or 0) for item in deleted_rows
            ),
            "deleted_edges": sum(
                int(item.get("deleted_edges", 0) or 0) for item in deleted_rows
            ),
            "deleted_orphaned_claims": deleted_orphaned_claims,
            "deleted_orphaned_synthesis": deleted_orphaned_synthesis,
            "deleted_orphaned_entities": deleted_orphaned_entities,
            "provenance_mode": provenance_modes[0]
            if len(provenance_modes) == 1
            else "mixed",
            "provenance_modes": provenance_modes,
            "approximate": any(mode != "exact" for mode in provenance_modes),
            "deleted_at": aggregate_deleted_at,
        }

    def delete_job_memories(
        self, profile_id: int, job_id: str
    ) -> dict[str, Any] | None:
        resolved_job_id = _normalize_whitespace(job_id)
        row = self._conn.execute(
            "SELECT id, job_id, paper_id, provenance_mode FROM memory_writebacks WHERE profile_id = ? AND job_id = ? AND deleted_at IS NULL",
            (profile_id, resolved_job_id),
        ).fetchone()
        if row is None:
            return None
        return self._delete_writeback_bundles(
            profile_id,
            [row],
            job_id=resolved_job_id,
            paper_id=_normalize_whitespace(row["paper_id"]),
        )

    def delete_paper_memories(
        self, profile_id: int, paper_id: str
    ) -> dict[str, Any] | None:
        resolved_paper_id = _normalize_whitespace(paper_id)
        rows = self._conn.execute(
            "SELECT id, job_id, paper_id, provenance_mode FROM memory_writebacks "
            "WHERE profile_id = ? AND paper_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC",
            (profile_id, resolved_paper_id),
        ).fetchall()
        if not rows:
            return None
        return self._delete_writeback_bundles(
            profile_id,
            list(rows),
            paper_id=resolved_paper_id,
        )

    def move_job_memories(
        self,
        source_profile_id: int,
        target_profile_id: int,
        job_ids: list[str],
    ) -> dict[str, Any]:
        resolved_job_ids = _dedupe_strings(
            [_normalize_whitespace(item) for item in job_ids if str(item).strip()],
            limit=200,
        )
        if not resolved_job_ids:
            raise ValueError("At least one job must be selected")
        if int(source_profile_id) == int(target_profile_id):
            raise ValueError("Source and target profiles must be different")

        source_profile = self.get_profile_by_id(source_profile_id)
        target_profile = self.get_profile_by_id(target_profile_id)
        if source_profile is None:
            raise ValueError("Source profile not found")
        if target_profile is None:
            raise ValueError("Target profile not found")

        self.ensure_profile_memory_provenance(source_profile_id)
        self.ensure_profile_memory_provenance(target_profile_id)

        placeholders = ", ".join("?" for _ in resolved_job_ids)
        writeback_rows = list(
            self._conn.execute(
                f"""
                SELECT id, job_id, paper_id, provenance_mode, created_at
                FROM memory_writebacks
                WHERE profile_id = ?
                  AND deleted_at IS NULL
                  AND job_id IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                """,
                (source_profile_id, *resolved_job_ids),
            ).fetchall()
        )
        found_job_ids = {
            _normalize_whitespace(row["job_id"]) for row in writeback_rows if row["job_id"]
        }
        missing_job_ids = [
            job_id for job_id in resolved_job_ids if job_id not in found_job_ids
        ]
        if missing_job_ids:
            raise ValueError(
                "Selected jobs were not found in the source profile: "
                + ", ".join(missing_job_ids)
            )

        duplicate_target_job_rows = self._conn.execute(
            f"""
            SELECT job_id
            FROM memory_writebacks
            WHERE profile_id = ?
              AND job_id IN ({placeholders})
            """,
            (target_profile_id, *resolved_job_ids),
        ).fetchall()
        if duplicate_target_job_rows:
            duplicate_job_ids = [
                _normalize_whitespace(row["job_id"])
                for row in duplicate_target_job_rows
                if row["job_id"]
            ]
            raise ValueError(
                "Target profile already has memory history for: "
                + ", ".join(sorted(set(duplicate_job_ids)))
            )

        writeback_ids = [int(row["id"]) for row in writeback_rows]
        writeback_placeholders = ", ".join("?" for _ in writeback_ids)
        moved_paper_ids = _dedupe_strings(
            [_normalize_whitespace(row["paper_id"]) for row in writeback_rows if row["paper_id"]],
            limit=400,
        )

        def _soft_delete_orphaned_entity(entity_id: int) -> None:
            deleted_at = _timestamp()
            self._conn.execute(
                "UPDATE memory_entities SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (deleted_at, deleted_at, entity_id),
            )
            self._conn.execute(
                "DELETE FROM memory_claim_entities WHERE entity_id = ?",
                (entity_id,),
            )
            self._conn.execute(
                "UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? "
                "WHERE profile_id = ? AND target_type = 'entity' AND target_id = ? AND status = 'pending'",
                (deleted_at, deleted_at, source_profile_id, entity_id),
            )

        def _move_review_items(target_type: str, ids: list[int], *, to_profile_id: int) -> None:
            if not ids:
                return
            placeholders_inner = ", ".join("?" for _ in ids)
            self._conn.execute(
                f"""
                UPDATE memory_review_items
                SET profile_id = ?
                WHERE profile_id = ?
                  AND target_type = ?
                  AND target_id IN ({placeholders_inner})
                """,
                (to_profile_id, source_profile_id, _normalize_whitespace(target_type), *ids),
            )

        def _move_revisions_for_target(
            target_type: str,
            ids: list[int],
            *,
            to_profile_id: int,
            target_id_override: int | None = None,
        ) -> None:
            if not ids:
                return
            resolved_target_ids = [
                str(int(target_id_override if target_id_override is not None else item))
                for item in ids
            ]
            placeholders_inner = ", ".join("?" for _ in resolved_target_ids)
            self._conn.execute(
                f"""
                UPDATE memory_revisions
                SET profile_id = ?
                WHERE profile_id = ?
                  AND target_type = ?
                  AND target_id IN ({placeholders_inner})
                """,
                (
                    to_profile_id,
                    source_profile_id,
                    _normalize_whitespace(target_type),
                    *resolved_target_ids,
                ),
            )

        claim_rows = list(
            self._conn.execute(
                f"""
                SELECT id, origin_writeback_id
                FROM memory_claims
                WHERE profile_id = ?
                  AND deleted_at IS NULL
                  AND origin_writeback_id IN ({writeback_placeholders})
                ORDER BY id ASC
                """,
                (source_profile_id, *writeback_ids),
            ).fetchall()
        )
        claim_ids = [int(row["id"]) for row in claim_rows]
        claim_placeholders = ", ".join("?" for _ in claim_ids) if claim_ids else ""

        synthesis_rows = list(
            self._conn.execute(
                f"""
                SELECT id, origin_writeback_id
                FROM memory_synthesis_items
                WHERE profile_id = ?
                  AND deleted_at IS NULL
                  AND origin_writeback_id IN ({writeback_placeholders})
                ORDER BY id ASC
                """,
                (source_profile_id, *writeback_ids),
            ).fetchall()
        )
        synthesis_ids = [int(row["id"]) for row in synthesis_rows]

        edge_rows = list(
            self._conn.execute(
                f"""
                SELECT *
                FROM memory_graph_edges
                WHERE profile_id = ?
                  AND deleted_at IS NULL
                  AND origin_writeback_id IN ({writeback_placeholders})
                ORDER BY id ASC
                """,
                (source_profile_id, *writeback_ids),
            ).fetchall()
        )

        moved_entity_count = 0
        cloned_entity_count = 0
        relinked_entity_count = 0

        if claim_ids:
            entity_rows = list(
                self._conn.execute(
                    f"""
                    SELECT DISTINCT e.*
                    FROM memory_entities e
                    JOIN memory_claim_entities ce ON ce.entity_id = e.id
                    WHERE e.deleted_at IS NULL
                      AND ce.claim_id IN ({claim_placeholders})
                    ORDER BY e.id ASC
                    """,
                    (*claim_ids,),
                ).fetchall()
            )
            for entity_row in entity_rows:
                old_entity_id = int(entity_row["id"])
                normalized_name = _normalize_whitespace(entity_row["normalized_name"])
                remaining_ref_row = self._conn.execute(
                    f"""
                    SELECT COUNT(*) AS cnt
                    FROM memory_claim_entities ce
                    JOIN memory_claims c ON c.id = ce.claim_id
                    WHERE ce.entity_id = ?
                      AND c.deleted_at IS NULL
                      AND c.profile_id = ?
                      AND c.id NOT IN ({claim_placeholders})
                    """,
                    (old_entity_id, source_profile_id, *claim_ids),
                ).fetchone()
                remaining_source_refs = int(remaining_ref_row["cnt"] if remaining_ref_row else 0)

                target_entity = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_entities
                    WHERE profile_id = ?
                      AND normalized_name = ?
                    ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END,
                             manual_locked DESC,
                             updated_at DESC,
                             id DESC
                    LIMIT 1
                    """,
                    (target_profile_id, normalized_name),
                ).fetchone()

                target_entity_id = old_entity_id
                if target_entity is not None:
                    target_entity_id = int(target_entity["id"])
                    self._conn.execute(
                        """
                        UPDATE memory_entities
                        SET canonical_name = ?, canonical_name_zh = ?, entity_type = ?, summary = ?, summary_zh = ?,
                            manual_locked = ?, status = ?, updated_at = ?, deleted_at = NULL
                        WHERE id = ?
                        """,
                        (
                            _normalize_whitespace(entity_row["canonical_name"]),
                            _normalize_whitespace(entity_row["canonical_name_zh"]),
                            _normalize_whitespace(entity_row["entity_type"]) or "concept",
                            _normalize_whitespace(entity_row["summary"]),
                            _normalize_whitespace(entity_row["summary_zh"]),
                            _bool_to_int(
                                _int_to_bool(entity_row["manual_locked"])
                                or _int_to_bool(target_entity["manual_locked"])
                            ),
                            _normalize_whitespace(entity_row["status"]) or "active",
                            _timestamp(),
                            target_entity_id,
                        ),
                    )
                elif remaining_source_refs == 0:
                    self._conn.execute(
                        "UPDATE memory_entities SET profile_id = ?, updated_at = ? WHERE id = ?",
                        (target_profile_id, _timestamp(), old_entity_id),
                    )
                    moved_entity_count += 1
                    _move_review_items("entity", [old_entity_id], to_profile_id=target_profile_id)
                    _move_revisions_for_target(
                        "entity", [old_entity_id], to_profile_id=target_profile_id
                    )
                    target_entity_id = old_entity_id
                else:
                    cloned = self._conn.execute(
                        """
                        INSERT INTO memory_entities (
                            profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type,
                            summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            target_profile_id,
                            _normalize_whitespace(entity_row["canonical_name"]),
                            _normalize_whitespace(entity_row["canonical_name_zh"]),
                            normalized_name,
                            _normalize_whitespace(entity_row["entity_type"]) or "concept",
                            _normalize_whitespace(entity_row["summary"]),
                            _normalize_whitespace(entity_row["summary_zh"]),
                            _bool_to_int(_int_to_bool(entity_row["manual_locked"])),
                            _normalize_whitespace(entity_row["status"]) or "active",
                            float(entity_row["created_at"] or _timestamp()),
                            float(entity_row["updated_at"] or _timestamp()),
                        ),
                    )
                    target_entity_id = int(cloned.lastrowid)
                    cloned_entity_count += 1

                alias_rows = self._conn.execute(
                    """
                    SELECT alias, normalized_alias, created_at
                    FROM memory_entity_aliases
                    WHERE entity_id = ?
                    ORDER BY id ASC
                    """,
                    (old_entity_id,),
                ).fetchall()
                if target_entity_id != old_entity_id:
                    relink_row = self._conn.execute(
                        f"""
                        SELECT COUNT(*) AS cnt
                        FROM memory_claim_entities
                        WHERE entity_id = ?
                          AND claim_id IN ({claim_placeholders})
                        """,
                        (old_entity_id, *claim_ids),
                    ).fetchone()
                    relinked_entity_count += int(relink_row["cnt"] if relink_row else 0)
                    self._conn.execute(
                        f"""
                        UPDATE OR IGNORE memory_claim_entities
                        SET entity_id = ?
                        WHERE entity_id = ?
                          AND claim_id IN ({claim_placeholders})
                        """,
                        (target_entity_id, old_entity_id, *claim_ids),
                    )
                    self._conn.execute(
                        f"""
                        DELETE FROM memory_claim_entities
                        WHERE entity_id = ?
                          AND claim_id IN ({claim_placeholders})
                        """,
                        (old_entity_id, *claim_ids),
                    )
                for alias_row in alias_rows:
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_entity_aliases (entity_id, alias, normalized_alias, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            target_entity_id,
                            _normalize_whitespace(alias_row["alias"]),
                            _normalize_whitespace(alias_row["normalized_alias"]),
                            float(alias_row["created_at"] or _timestamp()),
                        ),
                    )
                if target_entity_id != old_entity_id and remaining_source_refs == 0:
                    _soft_delete_orphaned_entity(old_entity_id)

        self._conn.execute(
            f"""
            UPDATE memory_writebacks
            SET profile_id = ?, deleted_at = NULL
            WHERE id IN ({writeback_placeholders})
            """,
            (target_profile_id, *writeback_ids),
        )
        if claim_ids:
            self._conn.execute(
                f"""
                UPDATE memory_claims
                SET profile_id = ?, updated_at = ?
                WHERE id IN ({claim_placeholders})
                """,
                (target_profile_id, _timestamp(), *claim_ids),
            )
        if synthesis_ids:
            synthesis_placeholders = ", ".join("?" for _ in synthesis_ids)
            self._conn.execute(
                f"""
                UPDATE memory_synthesis_items
                SET profile_id = ?, updated_at = ?
                WHERE id IN ({synthesis_placeholders})
                """,
                (target_profile_id, _timestamp(), *synthesis_ids),
            )

        _move_review_items("writeback", writeback_ids, to_profile_id=target_profile_id)
        _move_revisions_for_target(
            "writeback", writeback_ids, to_profile_id=target_profile_id
        )
        self._conn.execute(
            f"""
            UPDATE memory_revisions
            SET profile_id = ?
            WHERE profile_id = ?
              AND writeback_id IN ({writeback_placeholders})
            """,
            (target_profile_id, source_profile_id, *writeback_ids),
        )
        _move_review_items("claim", claim_ids, to_profile_id=target_profile_id)
        _move_revisions_for_target("claim", claim_ids, to_profile_id=target_profile_id)
        _move_review_items("synthesis", synthesis_ids, to_profile_id=target_profile_id)
        _move_revisions_for_target(
            "synthesis", synthesis_ids, to_profile_id=target_profile_id
        )

        moved_edge_count = 0
        merged_edge_count = 0
        for edge_row in edge_rows:
            edge_id = int(edge_row["id"])
            existing_target_edge = self._conn.execute(
                """
                SELECT *
                FROM memory_graph_edges
                WHERE profile_id = ?
                  AND source_kind = ?
                  AND source_ref = ?
                  AND target_kind = ?
                  AND target_ref = ?
                  AND relation_type = ?
                  AND id != ?
                ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END,
                         manual_locked DESC,
                         updated_at DESC,
                         id DESC
                LIMIT 1
                """,
                (
                    target_profile_id,
                    _normalize_whitespace(edge_row["source_kind"]),
                    _normalize_whitespace(edge_row["source_ref"]),
                    _normalize_whitespace(edge_row["target_kind"]),
                    _normalize_whitespace(edge_row["target_ref"]),
                    _normalize_whitespace(edge_row["relation_type"]),
                    edge_id,
                ),
            ).fetchone()
            if existing_target_edge is not None:
                target_edge_id = int(existing_target_edge["id"])
                next_summary = _normalize_whitespace(existing_target_edge["summary"])
                candidate_summary = _normalize_whitespace(edge_row["summary"])
                if not next_summary or (
                    candidate_summary and len(candidate_summary) > len(next_summary)
                ):
                    next_summary = candidate_summary
                next_summary_zh = _normalize_whitespace(existing_target_edge["summary_zh"])
                candidate_summary_zh = _normalize_whitespace(edge_row["summary_zh"])
                if not next_summary_zh or (
                    candidate_summary_zh
                    and len(candidate_summary_zh) > len(next_summary_zh)
                ):
                    next_summary_zh = candidate_summary_zh
                self._conn.execute(
                    """
                    UPDATE memory_graph_edges
                    SET summary = ?, summary_zh = ?, weight = ?, manual_locked = ?, updated_at = ?, deleted_at = NULL
                    WHERE id = ?
                    """,
                    (
                        next_summary,
                        next_summary_zh,
                        max(
                            float(existing_target_edge["weight"] or 0.0),
                            float(edge_row["weight"] or 0.0),
                        ),
                        _bool_to_int(
                            _int_to_bool(existing_target_edge["manual_locked"])
                            or _int_to_bool(edge_row["manual_locked"])
                        ),
                        _timestamp(),
                        target_edge_id,
                    ),
                )
                dismissed_at = _timestamp()
                self._conn.execute(
                    "UPDATE memory_graph_edges SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (dismissed_at, dismissed_at, edge_id),
                )
                self._conn.execute(
                    "UPDATE memory_review_items SET status = 'dismissed', reminder_active = 0, updated_at = ?, resolved_at = ? "
                    "WHERE profile_id = ? AND target_type = 'edge' AND target_id = ? AND status = 'pending'",
                    (dismissed_at, dismissed_at, source_profile_id, edge_id),
                )
                merged_edge_count += 1
            else:
                self._conn.execute(
                    "UPDATE memory_graph_edges SET profile_id = ?, updated_at = ? WHERE id = ?",
                    (target_profile_id, _timestamp(), edge_id),
                )
                _move_review_items("edge", [edge_id], to_profile_id=target_profile_id)
                _move_revisions_for_target(
                    "edge", [edge_id], to_profile_id=target_profile_id
                )
                moved_edge_count += 1

        self._prune_orphaned_entities(source_profile_id)
        self._conn.commit()

        source_rebuild = self.rebuild_profile_cognition(source_profile_id)
        target_rebuild = self.rebuild_profile_cognition(target_profile_id)
        self.recompute_profile_paper_count(source_profile_id)
        self.recompute_profile_paper_count(target_profile_id)
        self._invalidate_brief_cache(source_profile_id)
        self._invalidate_brief_cache(target_profile_id)

        source_active_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
            (source_profile_id,),
        ).fetchone()
        target_active_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
            (target_profile_id,),
        ).fetchone()

        return {
            "source_profile_id": source_profile_id,
            "target_profile_id": target_profile_id,
            "moved_job_ids": resolved_job_ids,
            "moved_paper_ids": moved_paper_ids,
            "moved_writeback_count": len(writeback_ids),
            "moved_claim_count": len(claim_ids),
            "moved_synthesis_count": len(synthesis_ids),
            "moved_edge_count": moved_edge_count,
            "merged_edge_count": merged_edge_count,
            "moved_entity_count": moved_entity_count,
            "cloned_entity_count": cloned_entity_count,
            "relinked_entity_count": relinked_entity_count,
            "source_active_writeback_count": int(
                source_active_row["cnt"] if source_active_row else 0
            ),
            "target_active_writeback_count": int(
                target_active_row["cnt"] if target_active_row else 0
            ),
            "source_rebuilt_items": int(
                source_rebuild.get("rebuilt_items", 0) if source_rebuild else 0
            ),
            "source_active_claims": int(
                source_rebuild.get("active_claims", 0) if source_rebuild else 0
            ),
            "target_rebuilt_items": int(
                target_rebuild.get("rebuilt_items", 0) if target_rebuild else 0
            ),
            "target_active_claims": int(
                target_rebuild.get("active_claims", 0) if target_rebuild else 0
            ),
        }

    def list_review_items(
        self, profile_id: int, *, limit: int | None = 80
    ) -> list[dict[str, Any]]:
        resolved_limit = self._resolve_limit(limit, 80)
        query = (
            "SELECT * FROM memory_review_items WHERE profile_id = ? ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'resolved' THEN 1 ELSE 2 END, updated_at DESC"
        )
        params: tuple[Any, ...]
        if resolved_limit > 0:
            query += " LIMIT ?"
            params = (profile_id, resolved_limit)
        else:
            params = (profile_id,)
        rows = self._conn.execute(query, params).fetchall()
        return [self._review_row_to_dict(row) for row in rows]

    def resolve_review_item(
        self,
        profile_id: int,
        review_id: int,
        *,
        resolution_note: str = "",
        adopt_suggested: bool = False,
        dismiss: bool = False,
    ) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM memory_review_items WHERE id = ? AND profile_id = ?",
            (review_id, profile_id),
        ).fetchone()
        if row is None:
            raise ValueError("Review item not found")
        review = self._review_row_to_dict(row)
        if adopt_suggested and review.get("suggested_payload"):
            payload = (
                review["suggested_payload"]
                if isinstance(review.get("suggested_payload"), dict)
                else None
            )
            if payload:
                payload = {
                    key: value
                    for key, value in dict(payload).items()
                    if not str(key).endswith("_zh")
                }
                target_type = str(review.get("target_type", ""))
                target_id = int(review.get("target_id", 0) or 0)
                if target_type == "claim":
                    claim_payload = dict(payload)
                    evidence_payload = claim_payload.pop("evidence", [])
                    saved = self.save_claim(
                        profile_id, claim_payload, claim_id=target_id
                    )
                    for evidence in evidence_payload:
                        if isinstance(evidence, dict):
                            clean_evidence = {
                                key: value
                                for key, value in dict(evidence).items()
                                if not str(key).endswith("_zh")
                            }
                            self.save_evidence(
                                profile_id,
                                {
                                    **clean_evidence,
                                    "claim_id": int(saved.get("id", target_id)),
                                },
                            )
                elif target_type == "synthesis":
                    self.save_synthesis_item(
                        profile_id,
                        dict(payload),
                        synthesis_id=int(review.get("target_id", 0) or 0),
                    )
                elif target_type == "entity":
                    self.save_entity(
                        profile_id,
                        dict(payload),
                        entity_id=int(review.get("target_id", 0) or 0),
                    )
                elif target_type == "edge":
                    self.save_graph_edge(
                        profile_id,
                        dict(payload),
                        edge_id=int(review.get("target_id", 0) or 0),
                    )
        status = "dismissed" if dismiss else "resolved"
        now = _timestamp()
        self._conn.execute(
            "UPDATE memory_review_items SET status = ?, reminder_active = 0, resolution_note = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
            (status, _normalize_whitespace(resolution_note), now, now, review_id),
        )
        review_revision_translation = self._localize_fields(
            "review_revision",
            {"summary": f"{status.title()} review item #{review_id}"},
            context={"status": status, "review_type": review.get("review_type", "")},
            step_label=f"review revision localization {review_id}",
        )
        self._log_revision(
            profile_id,
            target_type="review",
            target_id=review_id,
            action=status,
            actor_type="user",
            summary=f"{status.title()} review item #{review_id}",
            summary_zh=review_revision_translation.get("summary", ""),
            before=review,
            after={
                **review,
                "status": status,
                "resolution_note": _normalize_whitespace(resolution_note),
                "resolved_at": now,
            },
        )
        self._conn.commit()
        self.touch_profile(profile_id)
        self._invalidate_profile_views(profile_id)
        updated = self._conn.execute(
            "SELECT * FROM memory_review_items WHERE id = ?", (review_id,)
        ).fetchone()
        return self._review_row_to_dict(updated) if updated else review

    def list_revision_history(
        self, profile_id: int, *, limit: int = 120
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memory_revisions WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["before_json"] = _safe_json_loads(str(item.get("before_json", "")))
            item["after_json"] = _safe_json_loads(str(item.get("after_json", "")))
            item["summary_localized"] = _make_localized_text(
                item.get("summary", ""), item.get("summary_zh", "")
            )
            payloads.append(item)
        return payloads

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _get_cached_artifact(
        self, profile_id: int, artifact_key: str, artifact_version: str
    ) -> Any | None:
        if not self._table_exists("memory_derived_artifacts"):
            return None
        row = self._conn.execute(
            "SELECT payload_json, stale, artifact_version FROM memory_derived_artifacts WHERE profile_id = ? AND artifact_key = ?",
            (profile_id, _normalize_whitespace(artifact_key)),
        ).fetchone()
        if row is None:
            return None
        if int(row["stale"] if row["stale"] is not None else 1) != 0:
            return None
        if _safe_text(row["artifact_version"]) != _safe_text(artifact_version):
            return None
        payload = _safe_json_loads(str(row["payload_json"] or ""))
        return payload if isinstance(payload, (dict, list)) else None

    def _save_cached_artifact(
        self,
        profile_id: int,
        artifact_key: str,
        artifact_version: str,
        payload: Any,
    ) -> Any:
        now = _timestamp()
        self._conn.execute(
            "INSERT INTO memory_derived_artifacts (profile_id, artifact_key, artifact_version, payload_json, stale, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(profile_id, artifact_key) DO UPDATE SET artifact_version = excluded.artifact_version, payload_json = excluded.payload_json, stale = 0, updated_at = excluded.updated_at",
            (
                profile_id,
                _normalize_whitespace(artifact_key),
                _normalize_whitespace(artifact_version),
                _safe_json_dumps(payload),
                now,
            ),
        )
        self._conn.commit()
        return payload

    def _invalidate_artifacts(
        self, profile_id: int, artifact_keys: list[str] | None = None
    ) -> None:
        if not self._table_exists("memory_derived_artifacts"):
            return
        now = _timestamp()
        normalized_keys = [
            _normalize_whitespace(key)
            for key in artifact_keys or []
            if _normalize_whitespace(key)
        ]
        if normalized_keys:
            placeholders = ",".join("?" for _ in normalized_keys)
            self._conn.execute(
                f"UPDATE memory_derived_artifacts SET stale = 1, updated_at = ? WHERE profile_id = ? AND artifact_key IN ({placeholders})",
                (now, profile_id, *normalized_keys),
            )
        else:
            self._conn.execute(
                "UPDATE memory_derived_artifacts SET stale = 1, updated_at = ? WHERE profile_id = ?",
                (now, profile_id),
            )
        self._conn.commit()

    def _invalidate_profile_views(self, profile_id: int) -> None:
        self.mark_claim_relations_stale(profile_id)
        self._invalidate_brief_cache(profile_id)
        self._invalidate_artifacts(
            profile_id,
            [
                _THEME_ARTIFACT_KEY,
                _GAP_ARTIFACT_KEY,
                _SURVEY_ARTIFACT_KEY,
                _OPPORTUNITY_ARTIFACT_KEY,
                _MEMORY_HEALTH_ARTIFACT_KEY,
                _FIELD_MAP_ARTIFACT_KEY,
                _EVIDENCE_MATRIX_ARTIFACT_KEY,
            ],
        )

    def _resolve_limit(self, limit: int | None, default: int) -> int:
        if limit is None or int(limit) <= 0:
            return -1
        return int(limit)

    def _fetch_claim_entity_names_map(
        self, claim_ids: list[int]
    ) -> dict[int, list[str]]:
        normalized_claim_ids = [int(item) for item in claim_ids if int(item) > 0]
        if not normalized_claim_ids:
            return {}
        placeholders = ", ".join(["?"] * len(normalized_claim_ids))
        rows = self._conn.execute(
            f"SELECT ce.claim_id, e.canonical_name FROM memory_claim_entities ce JOIN memory_entities e ON e.id = ce.entity_id WHERE ce.claim_id IN ({placeholders}) AND e.deleted_at IS NULL ORDER BY ce.claim_id ASC, e.canonical_name ASC",
            tuple(normalized_claim_ids),
        ).fetchall()
        mapping: defaultdict[int, list[str]] = defaultdict(list)
        for row in rows:
            mapping[int(row["claim_id"])].append(str(row["canonical_name"]))
        return dict(mapping)

    def _get_active_claim_rows(
        self, profile_id: int, *, limit: int | None = 200
    ) -> list[sqlite3.Row]:
        resolved_limit = self._resolve_limit(limit, 200)
        query = (
            "SELECT c.*, COUNT(e.id) AS evidence_count, COALESCE(w.job_id, '') AS job_id, COALESCE(w.paper_id, '') AS paper_id "
            "FROM memory_claims c "
            "LEFT JOIN memory_claim_evidence e ON e.claim_id = c.id AND e.deleted_at IS NULL "
            "LEFT JOIN memory_writebacks w ON w.id = c.origin_writeback_id "
            "WHERE c.profile_id = ? AND c.deleted_at IS NULL "
            "GROUP BY c.id ORDER BY c.manual_locked DESC, c.importance DESC, c.updated_at DESC"
        )
        params: tuple[Any, ...]
        if resolved_limit > 0:
            query += " LIMIT ?"
            params = (profile_id, resolved_limit)
        else:
            params = (profile_id,)
        return list(self._conn.execute(query, params).fetchall())

    def _get_active_evidence_rows(
        self, profile_id: int, *, limit: int | None = 300
    ) -> list[sqlite3.Row]:
        resolved_limit = self._resolve_limit(limit, 300)
        query = (
            "SELECT e.*, c.title AS claim_title, c.title_zh AS claim_title_zh, c.claim_key AS claim_key, COALESCE(w.job_id, '') AS job_id, COALESCE(w.paper_id, '') AS paper_id "
            "FROM memory_claim_evidence e "
            "JOIN memory_claims c ON c.id = e.claim_id "
            "LEFT JOIN memory_writebacks w ON w.id = e.writeback_id "
            "WHERE c.profile_id = ? AND c.deleted_at IS NULL AND e.deleted_at IS NULL "
            "ORDER BY e.manual_locked DESC, e.updated_at DESC, e.created_at DESC"
        )
        params: tuple[Any, ...]
        if resolved_limit > 0:
            query += " LIMIT ?"
            params = (profile_id, resolved_limit)
        else:
            params = (profile_id,)
        return list(self._conn.execute(query, params).fetchall())

    def get_artifact_meta(self, profile_id: int, artifact_key: str) -> dict[str, Any]:
        if not self._table_exists("memory_derived_artifacts"):
            return {"exists": False, "stale": True, "updated_at": 0.0}
        row = self._conn.execute(
            "SELECT artifact_version, stale, updated_at, payload_json FROM memory_derived_artifacts WHERE profile_id = ? AND artifact_key = ?",
            (profile_id, _normalize_whitespace(artifact_key)),
        ).fetchone()
        if row is None:
            return {"exists": False, "stale": True, "updated_at": 0.0}
        payload = _safe_json_loads(str(row["payload_json"] or ""))
        section_count = 0
        if isinstance(payload, dict):
            if isinstance(payload.get("sections"), list):
                section_count = len(payload.get("sections", []))
            elif isinstance(payload.get("items"), list):
                section_count = len(payload.get("items", []))
        return {
            "exists": True,
            "artifact_version": _safe_text(row["artifact_version"]),
            "stale": bool(int(row["stale"] if row["stale"] is not None else 1)),
            "updated_at": float(row["updated_at"] or 0.0),
            "section_count": section_count,
        }

    def _compute_salience_score(
        self,
        item: dict[str, Any],
        *,
        primary_key: str = "importance",
        support_count_key: str = "evidence_count",
    ) -> float:
        score = float(
            item.get(
                primary_key, item.get("confidence", item.get("relevance_score", 0.0))
            )
            or 0.0
        )
        if bool(item.get("manual_locked")):
            score += 0.3
        review_status = _normalize_whitespace(
            item.get("review_status", "")
        ) or _normalize_whitespace(item.get("status", ""))
        if review_status == "pending":
            score += 0.25
        elif review_status == "conflicted":
            score += 0.2
        support_count = int(item.get(support_count_key, 0) or 0)
        score += min(support_count * 0.06, 0.3)
        degree = int(item.get("degree", 0) or 0)
        score += min(degree * 0.03, 0.15)
        return round(score, 4)

    # --- Brief (domain snapshot) ---

    def _invalidate_brief_cache(self, profile_id: int) -> None:
        try:
            self._conn.execute(
                "UPDATE profiles SET brief_stale = 1 WHERE id = ?", (profile_id,)
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Column may not exist yet during initial migration

    def get_or_build_brief(self, profile_id: int) -> dict[str, Any]:
        """Return cached brief or rebuild if stale."""
        try:
            row = self._conn.execute(
                "SELECT brief_json, brief_stale FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if (
            row
            and _safe_text(row["brief_json"])
            and int(row["brief_stale"] if row["brief_stale"] is not None else 1) == 0
        ):
            cached = _safe_json_loads(str(row["brief_json"]))
            if isinstance(cached, dict) and cached.get("stage"):
                return cached
        brief = self._build_brief(profile_id)
        try:
            self._conn.execute(
                "UPDATE profiles SET brief_json = ?, brief_stale = 0 WHERE id = ?",
                (_safe_json_dumps(brief), profile_id),
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass
        return brief

    def _build_brief(self, profile_id: int) -> dict[str, Any]:
        """Build a domain brief for human consumption.

        Three stages:
        - empty (0 papers): guidance message only
        - initial (1-2 papers): key concepts + core findings
        - full (3+ papers): themes + consensus + debates + open questions + recent delta
        """
        paper_count_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
            (profile_id,),
        ).fetchone()
        paper_count = int(paper_count_row["cnt"]) if paper_count_row else 0

        profile = self.get_profile_by_id(profile_id) or {}
        base = {
            "profile_name": str(profile.get("name", "")),
            "paper_count": paper_count,
            "generated_at": _timestamp(),
        }

        if paper_count == 0:
            return {**base, "stage": "empty"}

        entities = self.list_entities(profile_id, limit=200)
        claims = self.list_claims(profile_id, limit=200)
        synthesis_items = self.list_synthesis_items(profile_id, limit=160)
        active_entities = [e for e in entities if not e.get("deleted_at")]
        active_claims = [c for c in claims if not c.get("deleted_at")]
        active_synthesis = [s for s in synthesis_items if not s.get("deleted_at")]

        if paper_count <= 2:
            return self._build_brief_initial(
                base, active_entities, active_claims, active_synthesis
            )

        return self._build_brief_full(
            base, profile_id, active_entities, active_claims, active_synthesis
        )

    def _build_brief_initial(
        self,
        base: dict[str, Any],
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        synthesis: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Brief for 1-2 papers: key concepts + findings."""
        key_concepts = sorted(
            [
                {
                    "name": _safe_text(e.get("canonical_name", "")),
                    "name_zh": _safe_text(e.get("canonical_name_zh", "")),
                    "type": _safe_text(e.get("entity_type", "concept")),
                    "claim_count": int(e.get("claim_count", 0) or 0),
                }
                for e in entities
                if e.get("entity_type") in ("task", "problem", "method", "concept")
            ],
            key=lambda x: x["claim_count"],
            reverse=True,
        )[:8]

        core_findings = sorted(
            [
                {
                    "title": _safe_text(c.get("title", "")),
                    "title_zh": _safe_text(c.get("title_zh", "")),
                    "body": _safe_text(c.get("body", "")),
                    "body_zh": _safe_text(c.get("body_zh", "")),
                    "claim_type": _safe_text(c.get("claim_type", "finding")),
                    "importance": float(c.get("importance", 0.5) or 0.5),
                }
                for c in claims
            ],
            key=lambda x: x["importance"],
            reverse=True,
        )[:5]

        return {
            **base,
            "stage": "initial",
            "key_concepts": key_concepts,
            "core_findings": core_findings,
        }

    def _build_brief_full(
        self,
        base: dict[str, Any],
        profile_id: int,
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        synthesis: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Brief for 3+ papers: themes + consensus + debates + open questions + delta."""
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        gap_snapshot = self.get_or_build_gap_snapshot(profile_id)
        themes = []
        claim_papers = {
            int(claim.get("id", 0) or 0): _normalize_whitespace(
                claim.get("paper_id", "")
            )
            for claim in claims
        }
        for item in theme_snapshot.get("items", [])[:7]:
            anchor_entities = item.get("anchor_entities", [])
            primary_anchor = anchor_entities[0] if anchor_entities else {}
            themes.append(
                {
                    "theme_key": _safe_text(item.get("theme_key", "")),
                    "anchor": _safe_text(
                        primary_anchor.get("name", item.get("title", ""))
                    ),
                    "anchor_zh": _safe_text(
                        primary_anchor.get("name_zh", item.get("title_zh", ""))
                    ),
                    "anchor_type": _safe_text(
                        primary_anchor.get("entity_type", "theme")
                    ),
                    "methods": [
                        _safe_text(method.get("name", ""))
                        for method in item.get("method_entities", [])[:5]
                        if _safe_text(method.get("name", ""))
                    ],
                    "claim_count": int(item.get("claim_count", 0) or 0),
                    "paper_count": int(item.get("paper_count", 0) or 0),
                    "maturity": _safe_text(item.get("maturity", "emerging")),
                    "summary": _safe_text(item.get("summary", "")),
                    "summary_zh": _safe_text(item.get("summary_zh", "")),
                    "has_debate": int(item.get("debate_count", 0) or 0) > 0,
                    "has_open_question": int(item.get("open_question_count", 0) or 0)
                    > 0,
                }
            )

        # --- Synthesis categories ---
        consensus_items = [
            {
                "title": _safe_text(s.get("title", "")),
                "title_zh": _safe_text(s.get("title_zh", "")),
                "confidence": float(s.get("confidence", 0.5) or 0.5),
                "claim_count": len(s.get("claim_ids", [])),
                "paper_count": len(
                    {
                        claim_papers.get(int(claim_id), "")
                        for claim_id in s.get("claim_ids", [])
                        if claim_papers.get(int(claim_id), "")
                    }
                ),
            }
            for s in synthesis
            if s.get("item_type") == "consensus"
        ]
        consensus_items.sort(key=lambda x: x["confidence"], reverse=True)

        debate_items = [
            {
                "title": _safe_text(s.get("title", "")),
                "title_zh": _safe_text(s.get("title_zh", "")),
                "summary": _safe_text(s.get("summary", "")),
                "summary_zh": _safe_text(s.get("summary_zh", "")),
                "claim_count": len(s.get("claim_ids", [])),
                "paper_count": len(
                    {
                        claim_papers.get(int(claim_id), "")
                        for claim_id in s.get("claim_ids", [])
                        if claim_papers.get(int(claim_id), "")
                    }
                ),
            }
            for s in synthesis
            if s.get("item_type") == "debate"
        ]

        open_questions = [
            {
                "title": _safe_text(s.get("title", "")),
                "title_zh": _safe_text(s.get("title_zh", "")),
            }
            for s in synthesis
            if s.get("item_type") == "open_question"
        ]

        # --- Recent delta ---
        recent_delta = self._get_recent_delta(profile_id)

        return {
            **base,
            "stage": "full",
            "key_themes": themes,
            "top_consensus": consensus_items[:3],
            "top_debates": debate_items[:3],
            "open_questions": open_questions[:3],
            "gap_watchlist": gap_snapshot.get("items", [])[:4],
            "recent_delta": recent_delta,
        }

    def _get_recent_delta(self, profile_id: int) -> dict[str, Any] | None:
        """Return delta from the most recent writeback."""
        row = self._conn.execute(
            "SELECT job_id, paper_id, delta_json FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        delta_str = _safe_text(row["delta_json"]) if row["delta_json"] else ""
        if not delta_str:
            return None
        delta = _safe_json_loads(delta_str)
        if not isinstance(delta, dict):
            return None
        # Resolve paper title
        paper_title = ""
        job_id = _normalize_whitespace(row["job_id"])
        if self._table_exists("papers") and job_id:
            title_row = self._conn.execute(
                "SELECT title FROM papers WHERE job_id = ? LIMIT 1", (job_id,)
            ).fetchone()
            if title_row:
                paper_title = _normalize_whitespace(title_row["title"])
        if not paper_title and self._table_exists("jobs") and job_id:
            title_row = self._conn.execute(
                "SELECT paper_title FROM jobs WHERE id = ? LIMIT 1", (job_id,)
            ).fetchone()
            if title_row:
                paper_title = _normalize_whitespace(title_row["paper_title"])
        return {
            "paper_title": paper_title or _normalize_whitespace(row["paper_id"]),
            "paper_id": _normalize_whitespace(row["paper_id"]),
            **delta,
        }

    def _load_profile_memory_basis(
        self,
        profile_id: int,
        *,
        include_graph: bool = False,
        include_timeline: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "entities": [
                item
                for item in self.list_entities(profile_id, limit=None)
                if not item.get("deleted_at")
            ],
            "claims": [
                item
                for item in self.list_claims(profile_id, limit=None)
                if not item.get("deleted_at")
            ],
            "evidence_fragments": [
                item
                for item in self.list_evidence(profile_id, limit=None)
                if not item.get("deleted_at")
            ],
            "synthesis_items": [
                item
                for item in self.list_synthesis_items(profile_id, limit=None)
                if not item.get("deleted_at")
            ],
            "reviews": self.list_review_items(profile_id, limit=None),
            "writebacks": [
                dict(row)
                for row in self._conn.execute(
                    "SELECT id, job_id, paper_id, created_at, delta_json FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                    (profile_id,),
                ).fetchall()
            ],
        }
        payload["claim_relations"] = self.list_claim_relations(profile_id, limit=-1, ensure_fresh=True)
        if include_graph:
            payload["graph"] = self.build_graph_snapshot(profile_id)
        if include_timeline:
            payload["timeline"] = self.build_timeline(profile_id)
        return payload

    def get_or_build_opportunity_snapshot(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _OPPORTUNITY_ARTIFACT_KEY, _OPPORTUNITY_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        gap_snapshot = self.get_or_build_gap_snapshot(profile_id)
        snapshot = build_opportunity_snapshot(
            profile_id,
            theme_snapshot=theme_snapshot,
            gap_snapshot=gap_snapshot,
            claim_relations=basis.get("claim_relations", []),
            claims=basis.get("claims", []),
            reviews=basis.get("reviews", []),
        )
        return self._save_cached_artifact(
            profile_id,
            _OPPORTUNITY_ARTIFACT_KEY,
            _OPPORTUNITY_ARTIFACT_VERSION,
            snapshot,
        )

    def get_or_build_memory_health(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _MEMORY_HEALTH_ARTIFACT_KEY, _MEMORY_HEALTH_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and "summary" in cached:
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        snapshot = self._build_memory_health(profile_id, **basis)
        return self._save_cached_artifact(
            profile_id,
            _MEMORY_HEALTH_ARTIFACT_KEY,
            _MEMORY_HEALTH_ARTIFACT_VERSION,
            snapshot,
        )

    def _build_memory_health(
        self,
        profile_id: int,
        *,
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence_fragments: list[dict[str, Any]],
        synthesis_items: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        claim_relations: list[dict[str, Any]],
        writebacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del entities, synthesis_items, writebacks
        evidence_count_by_claim: defaultdict[int, int] = defaultdict(int)
        valid_claim_ids = {int(item.get("id", 0) or 0) for item in claims}
        for evidence in evidence_fragments:
            claim_id = _maybe_int(evidence.get("claim_id"))
            if claim_id is not None:
                evidence_count_by_claim[claim_id] += 1

        pending_review_count = sum(
            1 for item in reviews if _safe_text(item.get("status")) == "pending"
        )
        contradicted_claim_ids = {
            int(row.get(key, 0) or 0)
            for row in claim_relations
            if _safe_text(row.get("relation_type")) == "contradicts"
            for key in ("source_claim_id", "target_claim_id")
        }
        issues: list[dict[str, Any]] = []

        def add_issue(
            *,
            issue_type: str,
            severity: str,
            title: str,
            title_zh: str,
            count: int,
            target_type: str = "claim",
            target_ids: list[int] | None = None,
        ) -> None:
            issues.append(
                {
                    "issue_type": issue_type,
                    "severity": severity,
                    "title": title,
                    "title_zh": title_zh,
                    "title_localized": _make_localized_text(title, title_zh),
                    "count": count,
                    "target_type": target_type,
                    "target_ids": (target_ids or [])[:20],
                }
            )

        unsupported_ids = [
            int(claim.get("id", 0) or 0)
            for claim in claims
            if int(claim.get("id", 0) or 0) > 0
            and evidence_count_by_claim.get(int(claim.get("id", 0) or 0), 0) == 0
        ]
        thin_ids = [
            int(claim.get("id", 0) or 0)
            for claim in claims
            if int(claim.get("id", 0) or 0) > 0
            and float(claim.get("importance", 0.5) or 0.5) >= 0.7
            and evidence_count_by_claim.get(int(claim.get("id", 0) or 0), 0) < 2
        ]
        contested_ids = sorted(
            {
                int(claim.get("id", 0) or 0)
                for claim in claims
                if int(claim.get("id", 0) or 0) > 0
                and (
                    _safe_text(claim.get("status")) == "conflicted"
                    or _safe_text(claim.get("review_status")) == "pending"
                    or _safe_text(claim.get("lifecycle_state")) == "contested"
                    or int(claim.get("id", 0) or 0) in contradicted_claim_ids
                )
            }
        )
        deprecated_ids = [
            int(claim.get("id", 0) or 0)
            for claim in claims
            if _safe_text(claim.get("lifecycle_state")) == "deprecated"
        ]

        def scope_incomplete(claim: dict[str, Any]) -> bool:
            scope = claim.get("scope") if isinstance(claim.get("scope"), dict) else {}
            return not any(
                [
                    scope.get("conditions"),
                    _safe_text(scope.get("boundary")),
                    _safe_text(scope.get("population")),
                    _safe_text(scope.get("notes")),
                ]
            )

        scope_incomplete_ids = [
            int(claim.get("id", 0) or 0)
            for claim in claims
            if int(claim.get("id", 0) or 0) > 0 and scope_incomplete(claim)
        ]
        orphan_evidence_rows = self._conn.execute(
            "SELECT e.id "
            "FROM memory_claim_evidence e "
            "LEFT JOIN memory_claims c ON c.id = e.claim_id "
            "LEFT JOIN memory_writebacks w ON w.id = e.writeback_id "
            "WHERE e.deleted_at IS NULL "
            "AND (c.profile_id = ? OR w.profile_id = ?) "
            "AND (c.id IS NULL OR c.deleted_at IS NOT NULL OR (w.profile_id IS NOT NULL AND c.profile_id != w.profile_id))",
            (profile_id, profile_id),
        ).fetchall()
        orphan_evidence_ids = [int(row["id"] or 0) for row in orphan_evidence_rows]
        stale_artifact_rows = self._conn.execute(
            "SELECT artifact_key FROM memory_derived_artifacts WHERE profile_id = ? AND stale = 1 AND artifact_key != ?",
            (profile_id, _MEMORY_HEALTH_ARTIFACT_KEY),
        ).fetchall()
        stale_artifact_count = len(stale_artifact_rows)

        if unsupported_ids:
            add_issue(
                issue_type="unsupported_claim",
                severity="high",
                title="Claims without evidence",
                title_zh="缺少证据的 Claims",
                count=len(unsupported_ids),
                target_ids=unsupported_ids,
            )
        if thin_ids:
            add_issue(
                issue_type="thin_evidence_claim",
                severity="medium",
                title="Important claims with thin evidence",
                title_zh="重要但证据薄弱的 Claims",
                count=len(thin_ids),
                target_ids=thin_ids,
            )
        if contested_ids:
            add_issue(
                issue_type="contested_claim",
                severity="medium",
                title="Contested claims",
                title_zh="存在争议的 Claims",
                count=len(contested_ids),
                target_ids=contested_ids,
            )
        if pending_review_count:
            add_issue(
                issue_type="pending_review",
                severity="medium",
                title="Pending review items",
                title_zh="待处理审阅项",
                count=pending_review_count,
                target_type="review",
                target_ids=[
                    int(item.get("id", 0) or 0)
                    for item in reviews
                    if _safe_text(item.get("status")) == "pending"
                ],
            )
        if scope_incomplete_ids:
            add_issue(
                issue_type="scope_incomplete_claim",
                severity="low",
                title="Claims missing scope boundaries",
                title_zh="缺少适用范围的 Claims",
                count=len(scope_incomplete_ids),
                target_ids=scope_incomplete_ids,
            )
        if orphan_evidence_ids:
            add_issue(
                issue_type="orphan_evidence",
                severity="medium",
                title="Evidence without an active claim",
                title_zh="缺少有效 Claim 的证据",
                count=len(orphan_evidence_ids),
                target_type="evidence",
                target_ids=orphan_evidence_ids,
            )
        if stale_artifact_count:
            add_issue(
                issue_type="stale_artifact",
                severity="low",
                title="Stale derived artifacts",
                title_zh="过期派生产物",
                count=stale_artifact_count,
                target_type="artifact",
            )

        severity_order = {"high": 3, "medium": 2, "low": 1}
        max_severity = max(
            (severity_order.get(_safe_text(issue.get("severity")), 0) for issue in issues),
            default=0,
        )
        status = "good" if max_severity == 0 else "attention" if max_severity < 3 else "critical"
        summary = {
            "unsupported_claim_count": len(unsupported_ids),
            "thin_evidence_claim_count": len(thin_ids),
            "contested_claim_count": len(contested_ids),
            "pending_review_count": pending_review_count,
            "deprecated_claim_count": len(deprecated_ids),
            "scope_incomplete_claim_count": len(scope_incomplete_ids),
            "orphan_evidence_count": len(orphan_evidence_ids),
            "stale_artifact_count": stale_artifact_count,
        }
        total_issue_count = sum(int(value or 0) for value in summary.values())
        return {
            "profile_id": profile_id,
            "generated_at": _timestamp(),
            "status": status,
            "score": round(max(0.0, 1.0 - min(total_issue_count, 20) / 20), 3),
            "summary": summary,
            "issues": issues,
        }

    def get_or_build_field_map(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _FIELD_MAP_ARTIFACT_KEY, _FIELD_MAP_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and isinstance(cached.get("clusters"), list):
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        snapshot = self._build_field_map(profile_id, theme_snapshot=theme_snapshot, **basis)
        return self._save_cached_artifact(
            profile_id,
            _FIELD_MAP_ARTIFACT_KEY,
            _FIELD_MAP_ARTIFACT_VERSION,
            snapshot,
        )

    def _build_field_map(
        self,
        profile_id: int,
        *,
        theme_snapshot: dict[str, Any],
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence_fragments: list[dict[str, Any]],
        synthesis_items: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        claim_relations: list[dict[str, Any]],
        writebacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del entities, claims, evidence_fragments, synthesis_items, writebacks
        review_claim_ids = {
            int(item.get("target_id", 0) or 0)
            for item in reviews
            if _safe_text(item.get("status")) == "pending"
            and _safe_text(item.get("target_type")) == "claim"
        }
        theme_by_claim: dict[int, str] = {}
        theme_title: dict[str, dict[str, str]] = {}
        for theme in theme_snapshot.get("items", []):
            theme_key = _safe_text(theme.get("theme_key", ""))
            if not theme_key:
                continue
            theme_title[theme_key] = {
                "title": _safe_text(theme.get("title", "")),
                "title_zh": _safe_text(theme.get("title_zh", "")),
            }
            for claim_id in theme.get("claim_ids", []):
                normalized = _maybe_int(claim_id)
                if normalized is not None:
                    theme_by_claim[normalized] = theme_key

        link_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation in claim_relations:
            source_theme = theme_by_claim.get(int(relation.get("source_claim_id", 0) or 0))
            target_theme = theme_by_claim.get(int(relation.get("target_claim_id", 0) or 0))
            if not source_theme or not target_theme or source_theme == target_theme:
                continue
            sorted_pair = tuple(sorted([source_theme, target_theme]))
            link_key = (sorted_pair[0], sorted_pair[1], _safe_text(relation.get("relation_type", "related")))
            current = link_map.setdefault(
                link_key,
                {
                    "source_cluster_key": sorted_pair[0],
                    "target_cluster_key": sorted_pair[1],
                    "relation_type": link_key[2],
                    "weight": 0,
                    "claim_relation_ids": [],
                    "claim_ids": [],
                },
            )
            current["weight"] += 1
            current["claim_relation_ids"].append(int(relation.get("id", 0) or 0))
            current["claim_ids"].extend(
                [
                    int(relation.get("source_claim_id", 0) or 0),
                    int(relation.get("target_claim_id", 0) or 0),
                ]
            )

        clusters: list[dict[str, Any]] = []
        for theme in theme_snapshot.get("items", []):
            theme_key = _safe_text(theme.get("theme_key", ""))
            if not theme_key:
                continue
            claim_ids = [
                int(item)
                for item in theme.get("claim_ids", [])
                if _maybe_int(item) is not None
            ]
            controversy_count = sum(1 for claim_id in claim_ids if claim_id in review_claim_ids)
            controversy_count += sum(
                1
                for relation in claim_relations
                if _safe_text(relation.get("relation_type")) == "contradicts"
                and (
                    int(relation.get("source_claim_id", 0) or 0) in claim_ids
                    or int(relation.get("target_claim_id", 0) or 0) in claim_ids
                )
            )
            cluster_type = "problem"
            anchors = theme.get("anchor_entities", [])
            if anchors:
                cluster_type = _safe_text(anchors[0].get("entity_type", "problem")) or "problem"
            clusters.append(
                {
                    "cluster_key": theme_key,
                    "cluster_type": cluster_type,
                    "title": _safe_text(theme.get("title", "")),
                    "title_zh": _safe_text(theme.get("title_zh", "")),
                    "title_localized": theme.get(
                        "title_localized",
                        _make_localized_text(theme.get("title", ""), theme.get("title_zh", "")),
                    ),
                    "summary": _safe_text(theme.get("summary", "")),
                    "summary_zh": _safe_text(theme.get("summary_zh", "")),
                    "summary_localized": theme.get(
                        "summary_localized",
                        _make_localized_text(theme.get("summary", ""), theme.get("summary_zh", "")),
                    ),
                    "maturity": _safe_text(theme.get("maturity", "emerging")),
                    "paper_count": int(theme.get("paper_count", 0) or 0),
                    "claim_count": int(theme.get("claim_count", 0) or 0),
                    "evidence_count": int(theme.get("evidence_count", 0) or 0),
                    "controversy_count": controversy_count,
                    "claim_ids": claim_ids,
                    "paper_ids": [
                        _safe_text(item)
                        for item in theme.get("paper_ids", [])
                        if _safe_text(item)
                    ],
                }
            )
        clusters.sort(
            key=lambda item: (
                int(item.get("paper_count", 0) or 0),
                int(item.get("claim_count", 0) or 0),
                int(item.get("evidence_count", 0) or 0),
            ),
            reverse=True,
        )
        entry_points = [
            {
                "audience": "newcomer",
                "title": "Start from the largest theme",
                "title_zh": "先读最大的主题簇",
                "cluster_keys": [item.get("cluster_key", "") for item in clusters[:3]],
                "rationale": "These clusters have the broadest paper and claim coverage in this profile.",
                "rationale_zh": "这些领域簇在当前 profile 中覆盖最多论文和 claims。",
            },
            {
                "audience": "reviewer",
                "title": "Inspect contested clusters",
                "title_zh": "优先审查有争议的簇",
                "cluster_keys": [
                    item.get("cluster_key", "")
                    for item in clusters
                    if int(item.get("controversy_count", 0) or 0) > 0
                ][:3],
                "rationale": "Contested clusters are the most likely to need audit or scope refinement.",
                "rationale_zh": "存在争议的簇最需要审计或补充适用范围。",
            },
        ]
        return {
            "profile_id": profile_id,
            "generated_at": _timestamp(),
            "cluster_count": len(clusters),
            "link_count": len(link_map),
            "clusters": clusters[:12],
            "links": list(link_map.values())[:40],
            "entry_points": entry_points,
        }

    def get_or_build_evidence_matrix(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id,
            _EVIDENCE_MATRIX_ARTIFACT_KEY,
            _EVIDENCE_MATRIX_ARTIFACT_VERSION,
        )
        if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        snapshot = self._build_evidence_matrix(profile_id, **basis)
        return self._save_cached_artifact(
            profile_id,
            _EVIDENCE_MATRIX_ARTIFACT_KEY,
            _EVIDENCE_MATRIX_ARTIFACT_VERSION,
            snapshot,
        )

    def _build_evidence_matrix(
        self,
        profile_id: int,
        *,
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence_fragments: list[dict[str, Any]],
        synthesis_items: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        claim_relations: list[dict[str, Any]],
        writebacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del entities, synthesis_items, reviews, claim_relations, writebacks
        claim_by_id = {int(claim.get("id", 0) or 0): claim for claim in claims}
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        incomplete_count = 0
        for evidence in evidence_fragments:
            signal = _safe_json_loads(_safe_text(evidence.get("structured_signal_json", "")))
            if not isinstance(signal, dict):
                signal = {}
            claim = claim_by_id.get(int(evidence.get("claim_id", 0) or 0), {})
            task = _normalize_whitespace(signal.get("task", "")) or _normalize_whitespace(
                (claim.get("scope") or {}).get("population", "") if isinstance(claim.get("scope"), dict) else ""
            ) or "Unspecified task"
            dataset = _normalize_whitespace(signal.get("dataset", "")) or "Unspecified dataset"
            metric = _normalize_whitespace(signal.get("metric", "")) or "Unspecified metric"
            method = _normalize_whitespace(signal.get("method", "")) or ", ".join(
                claim.get("entity_names", [])[:2]
            )
            value = _normalize_whitespace(signal.get("value", ""))
            baseline = _normalize_whitespace(signal.get("baseline", "")) or _normalize_whitespace(
                signal.get("comparator", "")
            )
            setting = _normalize_whitespace(signal.get("setting", ""))
            limitation = _normalize_whitespace(signal.get("limitation", ""))
            scope_note = _normalize_whitespace(signal.get("scope_note", "")) or _normalize_whitespace(
                (claim.get("scope") or {}).get("boundary", "") if isinstance(claim.get("scope"), dict) else ""
            )
            incomplete_fields = [
                field
                for field, current in {
                    "task": task if task != "Unspecified task" else "",
                    "dataset": dataset if dataset != "Unspecified dataset" else "",
                    "metric": metric if metric != "Unspecified metric" else "",
                    "method": method,
                    "value": value,
                    "setting": setting,
                    "scope_note": scope_note,
                }.items()
                if not current
            ]
            if incomplete_fields:
                incomplete_count += 1
            group_key = (task, dataset, metric)
            row = grouped.setdefault(
                group_key,
                {
                    "row_key": _slugify("|".join(group_key)),
                    "task": task,
                    "dataset": dataset,
                    "metric": metric,
                    "cell_count": 0,
                    "incomplete_count": 0,
                    "cells": [],
                },
            )
            row["cell_count"] += 1
            if incomplete_fields:
                row["incomplete_count"] += 1
            row["cells"].append(
                {
                    "evidence_id": int(evidence.get("id", 0) or 0),
                    "claim_id": int(evidence.get("claim_id", 0) or 0),
                    "claim_title": _safe_text(evidence.get("claim_title"))
                    or _safe_text(claim.get("title", "")),
                    "claim_title_zh": _safe_text(evidence.get("claim_title_zh"))
                    or _safe_text(claim.get("title_zh", "")),
                    "claim_title_localized": evidence.get(
                        "claim_title_localized",
                        _make_localized_text(claim.get("title", ""), claim.get("title_zh", "")),
                    ),
                    "method": method,
                    "value": value,
                    "baseline": baseline,
                    "setting": setting,
                    "limitation": limitation,
                    "scope_note": scope_note,
                    "paper_id": _safe_text(evidence.get("paper_id", "")),
                    "section_key": _safe_text(evidence.get("section_key", "other")),
                    "anchor_kind": _safe_text(evidence.get("anchor_kind", "text")),
                    "snippet": _safe_text(evidence.get("snippet", "")),
                    "snippet_zh": _safe_text(evidence.get("snippet_zh", "")),
                    "snippet_localized": evidence.get(
                        "snippet_localized",
                        _make_localized_text(evidence.get("snippet", ""), evidence.get("snippet_zh", "")),
                    ),
                    "incomplete_fields": incomplete_fields,
                }
            )
        rows = list(grouped.values())
        rows.sort(
            key=lambda item: (int(item.get("cell_count", 0) or 0), item.get("row_key", "")),
            reverse=True,
        )
        for row in rows:
            row["cells"] = row["cells"][:8]
        return {
            "profile_id": profile_id,
            "generated_at": _timestamp(),
            "row_count": len(rows),
            "evidence_count": len(evidence_fragments),
            "incomplete_count": incomplete_count,
            "rows": rows[:20],
        }

    def get_or_build_theme_snapshot(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _THEME_ARTIFACT_KEY, _THEME_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        snapshot = self._build_theme_snapshot(profile_id, **basis)
        return self._save_cached_artifact(
            profile_id,
            _THEME_ARTIFACT_KEY,
            _THEME_ARTIFACT_VERSION,
            snapshot,
        )

    def _build_theme_snapshot(
        self,
        profile_id: int,
        *,
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence_fragments: list[dict[str, Any]],
        synthesis_items: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        claim_relations: list[dict[str, Any]],
        writebacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del claim_relations, writebacks
        entity_by_key = {
            _normalize_lookup_key(item.get("canonical_name", "")): item
            for item in entities
            if _normalize_lookup_key(item.get("canonical_name", ""))
        }
        anchor_candidates = sorted(
            [
                item
                for item in entities
                if item.get("entity_type") in {"task", "problem", "method", "concept"}
                and int(item.get("claim_count", 0) or 0) > 0
            ],
            key=lambda item: (
                1 if str(item.get("entity_type", "")) in {"task", "problem"} else 0,
                int(item.get("claim_count", 0) or 0),
                _safe_text(item.get("canonical_name", "")),
            ),
            reverse=True,
        )
        anchor_lookup = {
            _normalize_lookup_key(item.get("canonical_name", "")): item
            for item in anchor_candidates
        }
        claims_by_anchor: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        fallback_key = "general-research-direction"
        for claim in claims:
            candidate_keys = [
                _normalize_lookup_key(name) for name in claim.get("entity_names", [])
            ]
            matched = [
                anchor_lookup[key] for key in candidate_keys if key in anchor_lookup
            ]
            if matched:
                matched.sort(
                    key=lambda item: (
                        1
                        if str(item.get("entity_type", "")) in {"task", "problem"}
                        else 0,
                        int(item.get("claim_count", 0) or 0),
                    ),
                    reverse=True,
                )
                anchor_key = _normalize_lookup_key(matched[0].get("canonical_name", ""))
            else:
                anchor_key = fallback_key
            claims_by_anchor[anchor_key].append(claim)

        evidence_by_claim: defaultdict[int, int] = defaultdict(int)
        for evidence in evidence_fragments:
            claim_id = _maybe_int(evidence.get("claim_id"))
            if claim_id is not None:
                evidence_by_claim[claim_id] += 1

        synthesis_by_claim: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in synthesis_items:
            for claim_id in item.get("claim_ids", []):
                normalized_claim_id = _maybe_int(claim_id)
                if normalized_claim_id is not None:
                    synthesis_by_claim[normalized_claim_id].append(item)

        pending_reviews = [
            item for item in reviews if str(item.get("status", "")) == "pending"
        ]
        items: list[dict[str, Any]] = []
        for anchor_key, grouped_claims in claims_by_anchor.items():
            if not grouped_claims:
                continue
            anchor_entity = anchor_lookup.get(anchor_key)
            claim_ids = [
                int(item.get("id", 0) or 0)
                for item in grouped_claims
                if int(item.get("id", 0) or 0) > 0
            ]
            claim_id_set = set(claim_ids)
            paper_ids = sorted(
                {
                    _normalize_whitespace(item.get("paper_id", ""))
                    for item in grouped_claims
                    if _normalize_whitespace(item.get("paper_id", ""))
                }
            )
            method_entities: dict[str, dict[str, Any]] = {}
            synthesis_map: dict[int, dict[str, Any]] = {}
            for claim in grouped_claims:
                for entity_name in claim.get("entity_names", []):
                    normalized_name = _normalize_lookup_key(entity_name)
                    entity = entity_by_key.get(normalized_name)
                    if not entity or str(entity.get("entity_type", "")) not in {
                        "method",
                        "module",
                    }:
                        continue
                    method_entities[normalized_name] = {
                        "id": int(entity.get("id", 0) or 0),
                        "name": _safe_text(entity.get("canonical_name", "")),
                        "name_zh": _safe_text(entity.get("canonical_name_zh", "")),
                        "entity_type": _safe_text(entity.get("entity_type", "method")),
                    }
                claim_id = int(claim.get("id", 0) or 0)
                for synthesis in synthesis_by_claim.get(claim_id, []):
                    synthesis_map[int(synthesis.get("id", 0) or 0)] = synthesis
            theme_synthesis = list(synthesis_map.values())
            evidence_count = sum(
                evidence_by_claim.get(claim_id, 0) for claim_id in claim_ids
            )
            pending_review_count = 0
            for review in pending_reviews:
                target_type = _safe_text(review.get("target_type", ""))
                target_id = int(review.get("target_id", 0) or 0)
                if target_type == "claim" and target_id in claim_id_set:
                    pending_review_count += 1
                elif target_type == "synthesis" and target_id in synthesis_map:
                    pending_review_count += 1
            consensus_count = sum(
                1 for item in theme_synthesis if item.get("item_type") == "consensus"
            )
            debate_count = sum(
                1 for item in theme_synthesis if item.get("item_type") == "debate"
            )
            open_question_count = sum(
                1
                for item in theme_synthesis
                if item.get("item_type") == "open_question"
            )
            paper_count = len(paper_ids)
            if paper_count >= 4 and consensus_count > 0:
                maturity = "mature"
            elif paper_count >= 2 or len(claim_id_set) >= 4:
                maturity = "growing"
            else:
                maturity = "emerging"
            anchor_entities = []
            if anchor_entity:
                anchor_entities.append(
                    {
                        "id": int(anchor_entity.get("id", 0) or 0),
                        "name": _safe_text(anchor_entity.get("canonical_name", "")),
                        "name_zh": _safe_text(
                            anchor_entity.get("canonical_name_zh", "")
                        ),
                        "entity_type": _safe_text(
                            anchor_entity.get("entity_type", "theme")
                        ),
                        "claim_count": int(anchor_entity.get("claim_count", 0) or 0),
                    }
                )
            title = _safe_text(
                anchor_entity.get("canonical_name", "")
                if anchor_entity
                else "General Research Direction"
            )
            title_zh = _safe_text(
                anchor_entity.get("canonical_name_zh", "")
                if anchor_entity
                else "通用研究方向"
            )
            method_names = [item.get("name", "") for item in method_entities.values()][
                :3
            ]
            summary_parts = [
                f"{paper_count} paper(s)",
                f"{len(claim_id_set)} structured claims",
            ]
            if method_names:
                summary_parts.append(f"methods include {', '.join(method_names)}")
            if debate_count > 0:
                summary_parts.append(f"{debate_count} active debates")
            elif consensus_count > 0:
                summary_parts.append(f"{consensus_count} consensus signals")
            if open_question_count > 0:
                summary_parts.append(f"{open_question_count} open questions")
            summary = f"Focuses on {title}; " + ", ".join(summary_parts) + "."
            summary_zh = f"围绕{title_zh or title}展开；覆盖 {paper_count} 篇论文、{len(claim_id_set)} 条结构化 claims。"
            representative_claims = sorted(
                grouped_claims,
                key=lambda item: self._compute_salience_score(item),
                reverse=True,
            )[:3]
            representative_synthesis = sorted(
                theme_synthesis,
                key=lambda item: self._compute_salience_score(
                    item, primary_key="confidence", support_count_key="claim_count"
                ),
                reverse=True,
            )[:3]
            salience_score = round(
                len(claim_id_set) * 0.18
                + paper_count * 0.24
                + consensus_count * 0.2
                + debate_count * 0.14
                + min(evidence_count, 8) * 0.04
                + pending_review_count * 0.06,
                4,
            )
            items.append(
                {
                    "theme_key": f"theme:{anchor_key}",
                    "title": title,
                    "title_zh": title_zh,
                    "title_localized": _make_localized_text(title, title_zh),
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "maturity": maturity,
                    "paper_count": paper_count,
                    "claim_count": len(claim_id_set),
                    "evidence_count": evidence_count,
                    "consensus_count": consensus_count,
                    "debate_count": debate_count,
                    "open_question_count": open_question_count,
                    "pending_review_count": pending_review_count,
                    "anchor_entities": anchor_entities,
                    "method_entities": list(method_entities.values())[:5],
                    "representative_claims": [
                        {
                            "id": int(claim.get("id", 0) or 0),
                            "title": _safe_text(claim.get("title", "")),
                            "title_zh": _safe_text(claim.get("title_zh", "")),
                            "title_localized": claim.get(
                                "title_localized",
                                _make_localized_text(
                                    claim.get("title", ""),
                                    claim.get("title_zh", ""),
                                ),
                            ),
                            "importance": float(claim.get("importance", 0.5) or 0.5),
                            "evidence_count": int(claim.get("evidence_count", 0) or 0),
                            "paper_id": _safe_text(claim.get("paper_id", "")),
                        }
                        for claim in representative_claims
                    ],
                    "representative_synthesis": [
                        {
                            "id": int(item.get("id", 0) or 0),
                            "item_type": _safe_text(item.get("item_type", "consensus")),
                            "title": _safe_text(item.get("title", "")),
                            "title_zh": _safe_text(item.get("title_zh", "")),
                            "title_localized": item.get(
                                "title_localized",
                                _make_localized_text(
                                    item.get("title", ""), item.get("title_zh", "")
                                ),
                            ),
                            "confidence": float(item.get("confidence", 0.5) or 0.5),
                            "claim_count": len(item.get("claim_ids", [])),
                        }
                        for item in representative_synthesis
                    ],
                    "paper_ids": paper_ids,
                    "claim_ids": sorted(claim_id_set),
                    "synthesis_ids": sorted(synthesis_map.keys()),
                    "salience_score": salience_score,
                }
            )
        items.sort(
            key=lambda item: (
                float(item.get("salience_score", 0.0) or 0.0),
                int(item.get("paper_count", 0) or 0),
                int(item.get("claim_count", 0) or 0),
            ),
            reverse=True,
        )
        items = items[:7]
        return {
            "profile_id": profile_id,
            "generated_at": _timestamp(),
            "item_count": len(items),
            "items": items,
        }

    def get_or_build_gap_snapshot(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _GAP_ARTIFACT_KEY, _GAP_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return cached
        basis = self._load_profile_memory_basis(profile_id)
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        snapshot = self._build_gap_snapshot(
            profile_id,
            theme_snapshot=theme_snapshot,
            **basis,
        )
        return self._save_cached_artifact(
            profile_id,
            _GAP_ARTIFACT_KEY,
            _GAP_ARTIFACT_VERSION,
            snapshot,
        )

    def _build_gap_snapshot(
        self,
        profile_id: int,
        *,
        theme_snapshot: dict[str, Any],
        entities: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence_fragments: list[dict[str, Any]],
        synthesis_items: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        claim_relations: list[dict[str, Any]],
        writebacks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del claim_relations, entities, writebacks
        theme_by_claim: dict[int, dict[str, Any]] = {}
        theme_by_synthesis: dict[int, dict[str, Any]] = {}
        for theme in theme_snapshot.get("items", []):
            for claim_id in theme.get("claim_ids", []):
                normalized_claim_id = _maybe_int(claim_id)
                if normalized_claim_id is not None:
                    theme_by_claim[normalized_claim_id] = theme
            for synthesis_id in theme.get("synthesis_ids", []):
                normalized_synthesis_id = _maybe_int(synthesis_id)
                if normalized_synthesis_id is not None:
                    theme_by_synthesis[normalized_synthesis_id] = theme
        evidence_count_by_claim: defaultdict[int, int] = defaultdict(int)
        for evidence in evidence_fragments:
            claim_id = _maybe_int(evidence.get("claim_id"))
            if claim_id is not None:
                evidence_count_by_claim[claim_id] += 1
        items: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        def append_gap(payload: dict[str, Any]) -> None:
            gap_key = _safe_text(payload.get("gap_key", ""))
            if not gap_key or gap_key in seen_keys:
                return
            seen_keys.add(gap_key)
            items.append(payload)

        for review in reviews:
            if str(review.get("status", "")) != "pending":
                continue
            target_type = _safe_text(review.get("target_type", ""))
            target_id = int(review.get("target_id", 0) or 0)
            theme = (
                theme_by_claim.get(target_id)
                if target_type == "claim"
                else theme_by_synthesis.get(target_id)
            )
            append_gap(
                {
                    "gap_key": f"review:{int(review.get('id', 0) or 0)}",
                    "gap_type": "unresolved_debate",
                    "priority": "high",
                    "title": _safe_text(review.get("title", ""))
                    or f"Pending {target_type} review",
                    "title_zh": _safe_text(review.get("title_zh", "")),
                    "title_localized": review.get(
                        "title_localized",
                        _make_localized_text(
                            review.get("title", ""), review.get("title_zh", "")
                        ),
                    ),
                    "summary": _safe_text(review.get("description", ""))
                    or _safe_text(review.get("default_resolution", "")),
                    "summary_zh": _safe_text(review.get("description_zh", ""))
                    or _safe_text(review.get("default_resolution_zh", "")),
                    "summary_localized": review.get(
                        "description_localized",
                        _make_localized_text(
                            review.get("description", ""),
                            review.get("description_zh", ""),
                        ),
                    ),
                    "theme_key": _safe_text(theme.get("theme_key", ""))
                    if theme
                    else "",
                    "theme_title": _safe_text(theme.get("title", "")) if theme else "",
                    "theme_title_zh": _safe_text(theme.get("title_zh", ""))
                    if theme
                    else "",
                    "theme_title_localized": _make_localized_text(
                        _safe_text(theme.get("title", "")) if theme else "",
                        _safe_text(theme.get("title_zh", "")) if theme else "",
                    ),
                    "reason_codes": ["pending_review", target_type or "unknown_target"],
                    "claim_ids": [target_id] if target_type == "claim" else [],
                    "synthesis_ids": [target_id] if target_type == "synthesis" else [],
                    "review_ids": [int(review.get("id", 0) or 0)],
                    "paper_ids": [],
                    "evidence_count": int(
                        evidence_count_by_claim.get(target_id, 0)
                        if target_type == "claim"
                        else 0
                    ),
                    "updated_at": float(review.get("updated_at", 0.0) or 0.0),
                }
            )

        for item in synthesis_items:
            if item.get("item_type") != "open_question":
                continue
            theme = theme_by_synthesis.get(int(item.get("id", 0) or 0))
            append_gap(
                {
                    "gap_key": f"open-question:{int(item.get('id', 0) or 0)}",
                    "gap_type": "open_question",
                    "priority": "medium",
                    "title": _safe_text(item.get("title", "")),
                    "title_zh": _safe_text(item.get("title_zh", "")),
                    "title_localized": item.get(
                        "title_localized",
                        _make_localized_text(
                            item.get("title", ""), item.get("title_zh", "")
                        ),
                    ),
                    "summary": _safe_text(item.get("summary", ""))
                    or _safe_text(item.get("default_resolution", "")),
                    "summary_zh": _safe_text(item.get("summary_zh", ""))
                    or _safe_text(item.get("default_resolution_zh", "")),
                    "summary_localized": item.get(
                        "summary_localized",
                        _make_localized_text(
                            item.get("summary", ""), item.get("summary_zh", "")
                        ),
                    ),
                    "theme_key": _safe_text(theme.get("theme_key", ""))
                    if theme
                    else "",
                    "theme_title": _safe_text(theme.get("title", "")) if theme else "",
                    "theme_title_zh": _safe_text(theme.get("title_zh", ""))
                    if theme
                    else "",
                    "theme_title_localized": _make_localized_text(
                        _safe_text(theme.get("title", "")) if theme else "",
                        _safe_text(theme.get("title_zh", "")) if theme else "",
                    ),
                    "reason_codes": ["open_question"],
                    "claim_ids": [
                        int(claim_id)
                        for claim_id in item.get("claim_ids", [])
                        if _maybe_int(claim_id) is not None
                    ],
                    "synthesis_ids": [int(item.get("id", 0) or 0)],
                    "review_ids": [],
                    "paper_ids": [],
                    "evidence_count": 0,
                    "updated_at": float(
                        item.get("updated_at", item.get("created_at", 0.0)) or 0.0
                    ),
                }
            )

        for claim in claims:
            claim_id = int(claim.get("id", 0) or 0)
            evidence_count = int(evidence_count_by_claim.get(claim_id, 0))
            if (
                claim_id <= 0
                or float(claim.get("importance", 0.5) or 0.5) < 0.7
                or evidence_count >= 2
            ):
                continue
            theme = theme_by_claim.get(claim_id)
            append_gap(
                {
                    "gap_key": f"evidence-thin:{claim_id}",
                    "gap_type": "evidence_thin",
                    "priority": "high" if evidence_count == 0 else "medium",
                    "title": _safe_text(claim.get("title", "")),
                    "title_zh": _safe_text(claim.get("title_zh", "")),
                    "title_localized": claim.get(
                        "title_localized",
                        _make_localized_text(
                            claim.get("title", ""), claim.get("title_zh", "")
                        ),
                    ),
                    "summary": _safe_text(claim.get("default_resolution", ""))
                    or _safe_text(claim.get("body", "")),
                    "summary_zh": _safe_text(claim.get("default_resolution_zh", ""))
                    or _safe_text(claim.get("body_zh", "")),
                    "summary_localized": claim.get(
                        "default_resolution_localized",
                        _make_localized_text(
                            claim.get("default_resolution", ""),
                            claim.get("default_resolution_zh", ""),
                        ),
                    ),
                    "theme_key": _safe_text(theme.get("theme_key", ""))
                    if theme
                    else "",
                    "theme_title": _safe_text(theme.get("title", "")) if theme else "",
                    "theme_title_zh": _safe_text(theme.get("title_zh", ""))
                    if theme
                    else "",
                    "theme_title_localized": _make_localized_text(
                        _safe_text(theme.get("title", "")) if theme else "",
                        _safe_text(theme.get("title_zh", "")) if theme else "",
                    ),
                    "reason_codes": [
                        "thin_evidence",
                        f"evidence_count:{evidence_count}",
                    ],
                    "claim_ids": [claim_id],
                    "synthesis_ids": [],
                    "review_ids": [],
                    "paper_ids": [_safe_text(claim.get("paper_id", ""))]
                    if _safe_text(claim.get("paper_id", ""))
                    else [],
                    "evidence_count": evidence_count,
                    "updated_at": float(
                        claim.get("updated_at", claim.get("created_at", 0.0)) or 0.0
                    ),
                }
            )

        for theme in theme_snapshot.get("items", []):
            if (
                int(theme.get("paper_count", 0) or 0) < 2
                or int(theme.get("consensus_count", 0) or 0) > 0
            ):
                continue
            append_gap(
                {
                    "gap_key": f"coverage:{theme.get('theme_key')}",
                    "gap_type": "coverage_thin",
                    "priority": "medium",
                    "title": _safe_text(theme.get("title", "")),
                    "title_zh": _safe_text(theme.get("title_zh", "")),
                    "title_localized": theme.get(
                        "title_localized",
                        _make_localized_text(
                            theme.get("title", ""), theme.get("title_zh", "")
                        ),
                    ),
                    "summary": "This theme has multiple papers and claims, but still lacks stable consensus-level cognition.",
                    "summary_zh": "该主题已经积累了多篇论文和若干 claims，但仍缺少稳定的共识层认知。",
                    "summary_localized": _make_localized_text(
                        "This theme has multiple papers and claims, but still lacks stable consensus-level cognition.",
                        "该主题已经积累了多篇论文和若干 claims，但仍缺少稳定的共识层认知。",
                    ),
                    "theme_key": _safe_text(theme.get("theme_key", "")),
                    "theme_title": _safe_text(theme.get("title", "")),
                    "theme_title_zh": _safe_text(theme.get("title_zh", "")),
                    "theme_title_localized": _make_localized_text(
                        _safe_text(theme.get("title", "")),
                        _safe_text(theme.get("title_zh", "")),
                    ),
                    "reason_codes": ["low_consensus_density"],
                    "claim_ids": [
                        int(item)
                        for item in theme.get("claim_ids", [])
                        if _maybe_int(item) is not None
                    ],
                    "synthesis_ids": [
                        int(item)
                        for item in theme.get("synthesis_ids", [])
                        if _maybe_int(item) is not None
                    ],
                    "review_ids": [],
                    "paper_ids": [
                        _safe_text(item)
                        for item in theme.get("paper_ids", [])
                        if _safe_text(item)
                    ],
                    "evidence_count": int(theme.get("evidence_count", 0) or 0),
                    "updated_at": float(
                        theme.get("generated_at", _timestamp()) or _timestamp()
                    ),
                }
            )

        priority_rank = {"high": 0, "medium": 1, "low": 2}
        items.sort(
            key=lambda item: (
                priority_rank.get(str(item.get("priority", "medium")), 1),
                -float(item.get("updated_at", 0.0) or 0.0),
                str(item.get("title", "")),
            )
        )
        return {
            "profile_id": profile_id,
            "generated_at": _timestamp(),
            "item_count": len(items),
            "high_priority_count": sum(
                1 for item in items if str(item.get("priority", "")) == "high"
            ),
            "items": items[:16],
        }

    def get_or_build_living_survey(self, profile_id: int) -> dict[str, Any]:
        cached = self._get_cached_artifact(
            profile_id, _SURVEY_ARTIFACT_KEY, _SURVEY_ARTIFACT_VERSION
        )
        if isinstance(cached, dict) and isinstance(cached.get("sections"), list):
            return cached
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        gap_snapshot = self.get_or_build_gap_snapshot(profile_id)
        opportunity_snapshot = self.get_or_build_opportunity_snapshot(profile_id)
        curated = self.get_workspace_curated(profile_id)
        overview = self.get_workspace_overview(profile_id)
        survey = self._build_living_survey(
            profile_id,
            theme_snapshot=theme_snapshot,
            gap_snapshot=gap_snapshot,
            opportunity_snapshot=opportunity_snapshot,
            curated=curated,
            overview=overview,
            recent_delta=self._get_recent_delta(profile_id),
        )
        return self._save_cached_artifact(
            profile_id,
            _SURVEY_ARTIFACT_KEY,
            _SURVEY_ARTIFACT_VERSION,
            survey,
        )

    def _build_living_survey(
        self,
        profile_id: int,
        *,
        theme_snapshot: dict[str, Any],
        gap_snapshot: dict[str, Any],
        opportunity_snapshot: dict[str, Any],
        curated: dict[str, Any],
        overview: dict[str, Any],
        recent_delta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        profile = self.get_profile_by_id(profile_id) or {}
        overview_text = f"This profile covers {overview.get('paper_source_count', 0)} paper sources, {theme_snapshot.get('item_count', 0)} research themes, and {overview.get('claim_count', 0)} claims."
        overview_text_zh = f"当前 Profile 覆盖了 {overview.get('paper_source_count', 0)} 个论文来源、{theme_snapshot.get('item_count', 0)} 个研究主题，以及 {overview.get('claim_count', 0)} 条 claims。"
        sections: list[dict[str, Any]] = [
            {
                "section_key": "overview",
                "title": "Field Overview",
                "title_zh": "领域概览",
                "title_localized": _make_localized_text("Field Overview", "领域概览"),
                "summary": overview_text,
                "summary_zh": overview_text_zh,
                "summary_localized": _make_localized_text(
                    overview_text, overview_text_zh
                ),
                "blocks": [
                    {
                        "block_key": "overview:coverage",
                        "title": "Current memory coverage",
                        "title_zh": "当前记忆覆盖范围",
                        "title_localized": _make_localized_text(
                            "Current memory coverage", "当前记忆覆盖范围"
                        ),
                        "summary": overview_text,
                        "summary_zh": overview_text_zh,
                        "summary_localized": _make_localized_text(
                            overview_text, overview_text_zh
                        ),
                        "badges": [
                            f"{overview.get('paper_source_count', 0)} papers",
                            f"{overview.get('claim_count', 0)} claims",
                            f"{theme_snapshot.get('item_count', 0)} themes",
                        ],
                        "claim_ids": [
                            int(item.get("id", 0) or 0)
                            for item in curated.get("priority_claims", [])[:8]
                            if int(item.get("id", 0) or 0) > 0
                        ],
                        "paper_ids": [
                            _safe_text(item.get("paper_id", ""))
                            for item in curated.get("source_bundles", [])[:8]
                            if _safe_text(item.get("paper_id", ""))
                        ],
                    }
                ],
            }
        ]
        sections.append(
            {
                "section_key": "themes",
                "title": "Research Themes",
                "title_zh": "研究主题",
                "title_localized": _make_localized_text("Research Themes", "研究主题"),
                "summary": "The profile is organized into a compact set of reusable research themes.",
                "summary_zh": "当前 Profile 可以被归纳为少数几个可复用的研究主题。",
                "summary_localized": _make_localized_text(
                    "The profile is organized into a compact set of reusable research themes.",
                    "当前 Profile 可以被归纳为少数几个可复用的研究主题。",
                ),
                "blocks": [
                    {
                        "block_key": _safe_text(item.get("theme_key", "")),
                        "title": _safe_text(item.get("title", "")),
                        "title_zh": _safe_text(item.get("title_zh", "")),
                        "title_localized": item.get(
                            "title_localized",
                            _make_localized_text(
                                item.get("title", ""), item.get("title_zh", "")
                            ),
                        ),
                        "summary": _safe_text(item.get("summary", "")),
                        "summary_zh": _safe_text(item.get("summary_zh", "")),
                        "summary_localized": item.get(
                            "summary_localized",
                            _make_localized_text(
                                item.get("summary", ""), item.get("summary_zh", "")
                            ),
                        ),
                        "badges": [
                            _safe_text(item.get("maturity", "emerging")),
                            f"{int(item.get('paper_count', 0) or 0)} papers",
                            f"{int(item.get('claim_count', 0) or 0)} claims",
                        ],
                        "theme_key": _safe_text(item.get("theme_key", "")),
                        "claim_ids": list(item.get("claim_ids", []))[:12],
                        "synthesis_ids": list(item.get("synthesis_ids", []))[:12],
                        "paper_ids": list(item.get("paper_ids", []))[:12],
                    }
                    for item in theme_snapshot.get("items", [])[:6]
                ],
            }
        )
        sections.append(
            {
                "section_key": "gaps",
                "title": "Open Questions and Gaps",
                "title_zh": "开放问题与知识空白",
                "title_localized": _make_localized_text(
                    "Open Questions and Gaps", "开放问题与知识空白"
                ),
                "summary": "These are the most important unresolved uncertainties still visible in the current profile.",
                "summary_zh": "这些是当前 Profile 中最值得优先关注的未解决不确定性与知识空白。",
                "summary_localized": _make_localized_text(
                    "These are the most important unresolved uncertainties still visible in the current profile.",
                    "这些是当前 Profile 中最值得优先关注的未解决不确定性与知识空白。",
                ),
                "blocks": [
                    {
                        "block_key": _safe_text(item.get("gap_key", "")),
                        "title": _safe_text(item.get("title", "")),
                        "title_zh": _safe_text(item.get("title_zh", "")),
                        "title_localized": item.get(
                            "title_localized",
                            _make_localized_text(
                                item.get("title", ""), item.get("title_zh", "")
                            ),
                        ),
                        "summary": _safe_text(item.get("summary", "")),
                        "summary_zh": _safe_text(item.get("summary_zh", "")),
                        "summary_localized": item.get(
                            "summary_localized",
                            _make_localized_text(
                                item.get("summary", ""), item.get("summary_zh", "")
                            ),
                        ),
                        "badges": [
                            _safe_text(item.get("gap_type", "gap")),
                            _safe_text(item.get("priority", "medium")),
                        ],
                        "gap_key": _safe_text(item.get("gap_key", "")),
                        "theme_key": _safe_text(item.get("theme_key", "")),
                        "claim_ids": list(item.get("claim_ids", []))[:12],
                        "synthesis_ids": list(item.get("synthesis_ids", []))[:12],
                        "review_ids": list(item.get("review_ids", []))[:12],
                    }
                    for item in gap_snapshot.get("items", [])[:8]
                ],
            }
        )
        sections.append(
            {
                "section_key": "opportunities",
                "title": "Research Opportunities",
                "title_zh": "研究机会",
                "title_localized": _make_localized_text(
                    "Research Opportunities", "研究机会"
                ),
                "summary": "These opportunities are derived conservatively from contradictions, thin evidence, and under-specified boundaries in the current profile.",
                "summary_zh": "这些研究机会是从当前 Profile 中的矛盾、薄弱证据和边界未明之处保守派生出来的。",
                "summary_localized": _make_localized_text(
                    "These opportunities are derived conservatively from contradictions, thin evidence, and under-specified boundaries in the current profile.",
                    "这些研究机会是从当前 Profile 中的矛盾、薄弱证据和边界未明之处保守派生出来的。",
                ),
                "blocks": [
                    {
                        "block_key": _safe_text(item.get("opportunity_key", "")),
                        "title": _safe_text(item.get("title", "")),
                        "title_zh": _safe_text(item.get("title_zh", "")),
                        "title_localized": item.get(
                            "title_localized",
                            _make_localized_text(
                                item.get("title", ""), item.get("title_zh", "")
                            ),
                        ),
                        "summary": _safe_text(item.get("summary", "")),
                        "summary_zh": _safe_text(item.get("summary_zh", "")),
                        "summary_localized": item.get(
                            "summary_localized",
                            _make_localized_text(
                                item.get("summary", ""), item.get("summary_zh", "")
                            ),
                        ),
                        "badges": [
                            _safe_text(item.get("opportunity_type", "opportunity")),
                            _safe_text(item.get("priority", "medium")),
                        ],
                        "theme_key": _safe_text(
                            (item.get("theme_keys", [""]) or [""])[0]
                        ),
                        "gap_key": "",
                        "claim_ids": [
                            int(claim_id)
                            for claim_id in item.get("claim_ids", [])
                            if _maybe_int(claim_id) is not None
                        ][:12],
                        "synthesis_ids": [
                            int(synthesis_id)
                            for synthesis_id in item.get("synthesis_ids", [])
                            if _maybe_int(synthesis_id) is not None
                        ][:12],
                        "review_ids": [
                            int(review_id)
                            for review_id in item.get("review_ids", [])
                            if _maybe_int(review_id) is not None
                        ][:12],
                        "paper_ids": [
                            _safe_text(paper_id)
                            for paper_id in item.get("paper_ids", [])
                            if _safe_text(paper_id)
                        ][:12],
                    }
                    for item in opportunity_snapshot.get("items", [])[:8]
                ],
            }
        )
        digest_blocks: list[dict[str, Any]] = []
        for section in curated.get("domain_digest", [])[:4]:
            for item in section.get("items", [])[:3]:
                digest_blocks.append(
                    {
                        "block_key": f"{section.get('section_type', 'digest')}:{int(item.get('id', 0) or 0)}",
                        "title": _safe_text(
                            item.get("title_localized", {}).get("en", "")
                        ),
                        "title_zh": _safe_text(
                            item.get("title_localized", {}).get("zh", "")
                        ),
                        "title_localized": item.get(
                            "title_localized", _make_localized_text("", "")
                        ),
                        "summary": _safe_text(
                            item.get("summary_localized", {}).get("en", "")
                        ),
                        "summary_zh": _safe_text(
                            item.get("summary_localized", {}).get("zh", "")
                        ),
                        "summary_localized": item.get(
                            "summary_localized", _make_localized_text("", "")
                        ),
                        "badges": [
                            _safe_text(
                                section.get(
                                    "section_label",
                                    section.get("section_type", "digest"),
                                )
                            )
                        ],
                        "synthesis_ids": [int(item.get("id", 0) or 0)],
                    }
                )
        sections.append(
            {
                "section_key": "digest",
                "title": "Consensus, Debate, and Evolution",
                "title_zh": "共识、争议与演化",
                "title_localized": _make_localized_text(
                    "Consensus, Debate, and Evolution", "共识、争议与演化"
                ),
                "summary": "These blocks summarize the most reusable promoted cognition above the claim layer.",
                "summary_zh": "这些条目概括了当前最值得复用的高层领域认知。",
                "summary_localized": _make_localized_text(
                    "These blocks summarize the most reusable promoted cognition above the claim layer.",
                    "这些条目概括了当前最值得复用的高层领域认知。",
                ),
                "blocks": digest_blocks[:12],
            }
        )
        source_blocks = [
            {
                "block_key": _safe_text(item.get("job_id", "")),
                "title": _safe_text(item.get("paper_title", ""))
                or _safe_text(item.get("paper_id", "")),
                "title_zh": _safe_text(item.get("paper_title", ""))
                or _safe_text(item.get("paper_id", "")),
                "title_localized": _make_localized_text(
                    _safe_text(item.get("paper_title", ""))
                    or _safe_text(item.get("paper_id", "")),
                    _safe_text(item.get("paper_title", ""))
                    or _safe_text(item.get("paper_id", "")),
                ),
                "summary": (
                    f"Claims {int(item.get('claim_count', 0) or 0)}, entities {int(item.get('entity_count', 0) or 0)}, synthesis {int(item.get('synthesis_count', 0) or 0)}."
                ),
                "summary_zh": (
                    f"带来了 {int(item.get('claim_count', 0) or 0)} 条 claims、{int(item.get('entity_count', 0) or 0)} 个实体、{int(item.get('synthesis_count', 0) or 0)} 条高层认知。"
                ),
                "summary_localized": _make_localized_text(
                    f"Claims {int(item.get('claim_count', 0) or 0)}, entities {int(item.get('entity_count', 0) or 0)}, synthesis {int(item.get('synthesis_count', 0) or 0)}.",
                    f"带来了 {int(item.get('claim_count', 0) or 0)} 条 claims、{int(item.get('entity_count', 0) or 0)} 个实体、{int(item.get('synthesis_count', 0) or 0)} 条高层认知。",
                ),
                "badges": [
                    _safe_text(item.get("paper_id", "")) or "manual",
                    f"job {item.get('job_id', '')}",
                ],
                "paper_ids": [_safe_text(item.get("paper_id", ""))]
                if _safe_text(item.get("paper_id", ""))
                else [],
            }
            for item in curated.get("source_bundles", [])[:10]
        ]
        sections.append(
            {
                "section_key": "sources",
                "title": "Source Coverage",
                "title_zh": "来源覆盖",
                "title_localized": _make_localized_text("Source Coverage", "来源覆盖"),
                "summary": "The survey is backed by concrete writeback bundles rather than free-floating summaries.",
                "summary_zh": "这份综述对应的是具体的 writeback bundles，而不是脱离来源的自由摘要。",
                "summary_localized": _make_localized_text(
                    "The survey is backed by concrete writeback bundles rather than free-floating summaries.",
                    "这份综述对应的是具体的 writeback bundles，而不是脱离来源的自由摘要。",
                ),
                "blocks": source_blocks,
            }
        )
        if recent_delta:
            change_bits = []
            recent_blocks: list[dict[str, Any]] = []
            if recent_delta.get("new_entities"):
                change_bits.append(
                    f"{len(recent_delta.get('new_entities', []))} new concepts"
                )
                recent_blocks.append(
                    {
                        "block_key": "recent:new-entities",
                        "title": "New concepts were added",
                        "title_zh": "新增概念",
                        "title_localized": _make_localized_text(
                            "New concepts were added", "新增概念"
                        ),
                        "summary": ", ".join(
                            _safe_text(item.get("name", ""))
                            for item in recent_delta.get("new_entities", [])[:6]
                        ),
                        "summary_zh": "最近写回新增了这些概念节点。",
                        "summary_localized": _make_localized_text(
                            ", ".join(
                                _safe_text(item.get("name", ""))
                                for item in recent_delta.get("new_entities", [])[:6]
                            ),
                            "最近写回新增了这些概念节点。",
                        ),
                        "badges": ["new_entities"],
                        "paper_ids": [_safe_text(recent_delta.get("paper_id", ""))]
                        if _safe_text(recent_delta.get("paper_id", ""))
                        else [],
                    }
                )
            if recent_delta.get("reinforced_claims"):
                change_bits.append(
                    f"{len(recent_delta.get('reinforced_claims', []))} reinforced claims"
                )
                recent_blocks.append(
                    {
                        "block_key": "recent:reinforced-claims",
                        "title": "Claims were reinforced",
                        "title_zh": "Claims 被强化",
                        "title_localized": _make_localized_text(
                            "Claims were reinforced", "Claims 被强化"
                        ),
                        "summary": ", ".join(
                            _safe_text(item.get("title", ""))
                            for item in recent_delta.get("reinforced_claims", [])[:5]
                        ),
                        "summary_zh": "最近写回让部分已有结论获得了更多支持。",
                        "summary_localized": _make_localized_text(
                            ", ".join(
                                _safe_text(item.get("title", ""))
                                for item in recent_delta.get("reinforced_claims", [])[:5]
                            ),
                            "最近写回让部分已有结论获得了更多支持。",
                        ),
                        "badges": ["reinforced"],
                        "paper_ids": [_safe_text(recent_delta.get("paper_id", ""))]
                        if _safe_text(recent_delta.get("paper_id", ""))
                        else [],
                    }
                )
            if recent_delta.get("challenged_claims"):
                change_bits.append(
                    f"{len(recent_delta.get('challenged_claims', []))} challenged claims"
                )
                recent_blocks.append(
                    {
                        "block_key": "recent:challenged-claims",
                        "title": "Claims were challenged",
                        "title_zh": "Claims 被挑战",
                        "title_localized": _make_localized_text(
                            "Claims were challenged", "Claims 被挑战"
                        ),
                        "summary": ", ".join(
                            _safe_text(item.get("title", ""))
                            for item in recent_delta.get("challenged_claims", [])[:5]
                        ),
                        "summary_zh": "最近写回触发了挑战或争议，需要在工作台中审阅。",
                        "summary_localized": _make_localized_text(
                            ", ".join(
                                _safe_text(item.get("title", ""))
                                for item in recent_delta.get("challenged_claims", [])[:5]
                            ),
                            "最近写回触发了挑战或争议，需要在工作台中审阅。",
                        ),
                        "badges": ["challenged"],
                        "paper_ids": [_safe_text(recent_delta.get("paper_id", ""))]
                        if _safe_text(recent_delta.get("paper_id", ""))
                        else [],
                    }
                )
            sections.append(
                {
                    "section_key": "recent_changes",
                    "title": "Recent Changes",
                    "title_zh": "最近变化",
                    "title_localized": _make_localized_text(
                        "Recent Changes", "最近变化"
                    ),
                    "summary": ", ".join(change_bits)
                    or "Recent paper ingestion changed the current profile.",
                    "summary_zh": "最近一次论文写回改变了当前 Profile。",
                    "summary_localized": _make_localized_text(
                        ", ".join(change_bits)
                        or "Recent paper ingestion changed the current profile.",
                        "最近一次论文写回改变了当前 Profile。",
                    ),
                    "blocks": recent_blocks,
                }
            )
        return {
            "profile_id": profile_id,
            "profile_name": _safe_text(profile.get("name", "")),
            "generated_at": _timestamp(),
            "paper_count": int(overview.get("paper_source_count", 0) or 0),
            "theme_count": int(theme_snapshot.get("item_count", 0) or 0),
            "gap_count": int(gap_snapshot.get("item_count", 0) or 0),
            "overview_localized": _make_localized_text(overview_text, overview_text_zh),
            "sections": sections,
        }

    def _count_graph_nodes(self, profile_id: int) -> int:
        rows = [
            self._conn.execute(
                "SELECT COUNT(DISTINCT CASE WHEN paper_id != '' THEN paper_id ELSE job_id END) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_entities WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claims WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_items WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
        ]
        return sum(int(row["cnt"] if row else 0) for row in rows)

    def _count_graph_edges(self, profile_id: int) -> int:
        rows = [
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_graph_edges WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claim_entities ce JOIN memory_claims c ON c.id = ce.claim_id JOIN memory_entities e ON e.id = ce.entity_id WHERE c.profile_id = ? AND c.deleted_at IS NULL AND e.deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_claims sc JOIN memory_synthesis_items s ON s.id = sc.synthesis_id JOIN memory_claims c ON c.id = sc.claim_id WHERE s.profile_id = ? AND s.deleted_at IS NULL AND c.deleted_at IS NULL",
                (profile_id,),
            ).fetchone(),
            self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claims c JOIN memory_writebacks w ON w.id = c.origin_writeback_id WHERE c.profile_id = ? AND c.deleted_at IS NULL AND w.deleted_at IS NULL AND (w.paper_id != '' OR w.job_id != '')",
                (profile_id,),
            ).fetchone(),
        ]
        return sum(int(row["cnt"] if row else 0) for row in rows)

    def get_workspace_overview(self, profile_id: int) -> dict[str, Any]:
        basis = self._load_profile_memory_basis(profile_id)
        theme_snapshot = self.get_or_build_theme_snapshot(profile_id)
        gap_snapshot = self.get_or_build_gap_snapshot(profile_id)
        opportunity_snapshot = self.get_or_build_opportunity_snapshot(profile_id)
        return {
            "paper_source_count": int(
                self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone()["cnt"]
            ),
            "entity_count": len(basis.get("entities", [])),
            "claim_count": len(basis.get("claims", [])),
            "synthesis_count": len(basis.get("synthesis_items", [])),
            "pending_review_count": len(
                [
                    item
                    for item in basis.get("reviews", [])
                    if str(item.get("status", "")) == "pending"
                ]
            ),
            "revision_count": len(self.list_revision_history(profile_id, limit=240)),
            "graph_node_count": self._count_graph_nodes(profile_id),
            "graph_edge_count": self._count_graph_edges(profile_id),
            "theme_count": int(theme_snapshot.get("item_count", 0) or 0),
            "gap_count": int(gap_snapshot.get("item_count", 0) or 0),
            "high_priority_gap_count": int(
                gap_snapshot.get("high_priority_count", 0) or 0
            ),
            "opportunity_count": int(opportunity_snapshot.get("item_count", 0) or 0),
            "high_priority_opportunity_count": int(
                opportunity_snapshot.get("high_priority_count", 0) or 0
            ),
        }

    def get_workspace_curated(self, profile_id: int) -> dict[str, Any]:
        basis = self._load_profile_memory_basis(profile_id, include_graph=True)
        return self._build_curated_sections(
            profile_id,
            entities=basis.get("entities", []),
            claims=basis.get("claims", []),
            evidence_fragments=basis.get("evidence_fragments", []),
            synthesis_items=basis.get("synthesis_items", []),
            reviews=basis.get("reviews", []),
            graph=basis.get("graph", {}),
        )

    def build_graph_snapshot(self, profile_id: int) -> dict[str, Any]:
        entities = self.list_entities(profile_id, limit=120)
        claims = self.list_claims(profile_id, limit=80)
        synthesis_items = self.list_synthesis_items(profile_id, limit=80)
        explicit_edges = self.list_graph_edges(profile_id, limit=160)
        paper_rows = self._conn.execute(
            "SELECT DISTINCT job_id, paper_id, created_at FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 60",
            (profile_id,),
        ).fetchall()

        nodes: dict[str, dict[str, Any]] = {}
        for row in paper_rows:
            paper_id = _normalize_whitespace(row["paper_id"]) or _normalize_whitespace(
                row["job_id"]
            )
            node_id = f"paper:{paper_id}"
            paper_summary = f"Source job: {row['job_id']}"
            nodes[node_id] = {
                "id": node_id,
                "label": paper_id,
                "label_zh": "",
                "label_localized": _make_localized_text(paper_id, ""),
                "node_type": "paper",
                "summary": paper_summary,
                "summary_zh": "",
                "summary_localized": _make_localized_text(paper_summary, ""),
                "status": "active",
                "ref": paper_id,
                "degree": 0,
            }
        for entity in entities:
            node_id = f"entity:{entity['id']}"
            nodes[node_id] = {
                "id": node_id,
                "label": entity.get("canonical_name", ""),
                "label_zh": entity.get("canonical_name_zh", ""),
                "label_localized": _make_localized_text(
                    entity.get("canonical_name", ""),
                    entity.get("canonical_name_zh", ""),
                ),
                "node_type": "entity",
                "summary": entity.get("summary", ""),
                "summary_zh": entity.get("summary_zh", ""),
                "summary_localized": _make_localized_text(
                    entity.get("summary", ""), entity.get("summary_zh", "")
                ),
                "status": entity.get("status", "active"),
                "ref": str(entity.get("id", "")),
                "degree": 0,
            }
        for claim in claims:
            node_id = f"claim:{claim['id']}"
            label_en = claim.get("title", "") or claim.get("claim_key", "claim")
            label_zh = claim.get("title_zh", "")
            summary_en = claim.get("default_resolution", "") or claim.get("body", "")
            summary_zh = claim.get("default_resolution_zh", "") or claim.get(
                "body_zh", ""
            )
            nodes[node_id] = {
                "id": node_id,
                "label": label_en,
                "label_zh": label_zh,
                "label_localized": _make_localized_text(label_en, label_zh),
                "node_type": "claim",
                "summary": summary_en,
                "summary_zh": summary_zh,
                "summary_localized": _make_localized_text(summary_en, summary_zh),
                "status": claim.get("status", "active"),
                "ref": str(claim.get("id", "")),
                "degree": 0,
            }
        for item in synthesis_items:
            node_id = f"synthesis:{item['id']}"
            label_en = item.get("title", "") or item.get("synthesis_key", "synthesis")
            label_zh = item.get("title_zh", "")
            summary_en = item.get("default_resolution", "") or item.get("summary", "")
            summary_zh = item.get("default_resolution_zh", "") or item.get(
                "summary_zh", ""
            )
            nodes[node_id] = {
                "id": node_id,
                "label": label_en,
                "label_zh": label_zh,
                "label_localized": _make_localized_text(label_en, label_zh),
                "node_type": "synthesis",
                "summary": summary_en,
                "summary_zh": summary_zh,
                "summary_localized": _make_localized_text(summary_en, summary_zh),
                "status": item.get("review_status", "none"),
                "ref": str(item.get("id", "")),
                "degree": 0,
            }

        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edge(
            source_id: str,
            target_id: str,
            relation_type: str,
            summary: str,
            *,
            edge_id: str,
            weight: float = 1.0,
            summary_zh: str = "",
        ) -> None:
            if source_id not in nodes or target_id not in nodes:
                return
            dedupe_key = (source_id, target_id, relation_type)
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            nodes[source_id]["degree"] += 1
            nodes[target_id]["degree"] += 1
            edges.append(
                {
                    "id": edge_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_type": relation_type,
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "weight": weight,
                }
            )

        for edge in explicit_edges:
            source_id = f"{edge['source_kind']}:{edge['source_ref']}"
            target_id = f"{edge['target_kind']}:{edge['target_ref']}"
            if source_id not in nodes:
                nodes[source_id] = {
                    "id": source_id,
                    "label": edge["source_ref"],
                    "label_zh": "",
                    "label_localized": _make_localized_text(edge["source_ref"], ""),
                    "node_type": edge["source_kind"],
                    "summary": "",
                    "summary_zh": "",
                    "summary_localized": _make_localized_text("", ""),
                    "status": "active",
                    "ref": edge["source_ref"],
                    "degree": 0,
                }
            if target_id not in nodes:
                nodes[target_id] = {
                    "id": target_id,
                    "label": edge["target_ref"],
                    "label_zh": "",
                    "label_localized": _make_localized_text(edge["target_ref"], ""),
                    "node_type": edge["target_kind"],
                    "summary": "",
                    "summary_zh": "",
                    "summary_localized": _make_localized_text("", ""),
                    "status": "active",
                    "ref": edge["target_ref"],
                    "degree": 0,
                }
            add_edge(
                source_id,
                target_id,
                str(edge.get("relation_type", "related_to")),
                str(edge.get("summary", "")),
                edge_id=f"edge:{edge['id']}",
                weight=float(edge.get("weight", 1.0) or 1.0),
                summary_zh=str(edge.get("summary_zh", "")),
            )

        claim_entity_rows = self._conn.execute(
            "SELECT ce.claim_id, ce.entity_id, ce.role FROM memory_claim_entities ce JOIN memory_claims c ON c.id = ce.claim_id JOIN memory_entities e ON e.id = ce.entity_id WHERE c.profile_id = ? AND c.deleted_at IS NULL AND e.deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in claim_entity_rows:
            add_edge(
                f"claim:{row['claim_id']}",
                f"entity:{row['entity_id']}",
                str(row["role"] or "mentions"),
                "Claim references entity",
                edge_id=f"claim-entity:{row['claim_id']}:{row['entity_id']}",
                summary_zh="该 claim 指向此实体",
            )

        synthesis_claim_rows = self._conn.execute(
            "SELECT sc.synthesis_id, sc.claim_id, sc.role FROM memory_synthesis_claims sc JOIN memory_synthesis_items s ON s.id = sc.synthesis_id JOIN memory_claims c ON c.id = sc.claim_id WHERE s.profile_id = ? AND s.deleted_at IS NULL AND c.deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in synthesis_claim_rows:
            add_edge(
                f"synthesis:{row['synthesis_id']}",
                f"claim:{row['claim_id']}",
                str(row["role"] or "supports"),
                "High-level cognition references claim",
                edge_id=f"synthesis-claim:{row['synthesis_id']}:{row['claim_id']}",
                summary_zh="高层认知对象引用该 claim",
            )

        claim_rows = self._conn.execute(
            "SELECT id, origin_writeback_id FROM memory_claims WHERE profile_id = ? AND deleted_at IS NULL",
            (profile_id,),
        ).fetchall()
        for row in claim_rows:
            writeback = self._conn.execute(
                "SELECT job_id, paper_id FROM memory_writebacks WHERE id = ? AND deleted_at IS NULL",
                (row["origin_writeback_id"],),
            ).fetchone()
            if writeback is None:
                continue
            paper_ref = _normalize_whitespace(
                writeback["paper_id"]
            ) or _normalize_whitespace(writeback["job_id"])
            if not paper_ref:
                continue
            add_edge(
                f"paper:{paper_ref}",
                f"claim:{row['id']}",
                "supports_claim",
                "Paper contributes evidence for claim",
                edge_id=f"paper-claim:{paper_ref}:{row['id']}",
                summary_zh="该论文为此 claim 提供证据",
            )

        return {"nodes": list(nodes.values()), "edges": edges}

    def _build_curated_sections(
        self,
        profile_id: int,
        *,
        entities: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        evidence_fragments: list[dict[str, Any]] | None = None,
        synthesis_items: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entities = (
            entities
            if entities is not None
            else self.list_entities(profile_id, limit=200)
        )
        claims = (
            claims if claims is not None else self.list_claims(profile_id, limit=200)
        )
        evidence_fragments = (
            evidence_fragments
            if evidence_fragments is not None
            else self.list_evidence(profile_id, limit=300)
        )
        synthesis_items = (
            synthesis_items
            if synthesis_items is not None
            else self.list_synthesis_items(profile_id, limit=160)
        )
        reviews = (
            reviews
            if reviews is not None
            else self.list_review_items(profile_id, limit=120)
        )
        graph = graph if graph is not None else self.build_graph_snapshot(profile_id)

        node_degree_by_id = {
            str(node.get("id", "")): int(node.get("degree", 0) or 0)
            for node in graph.get("nodes", [])
            if isinstance(node, dict)
        }
        evidence_by_claim: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for evidence in evidence_fragments:
            claim_id = _maybe_int(evidence.get("claim_id"))
            if claim_id is None:
                continue
            evidence_by_claim[claim_id].append(evidence)

        def evidence_priority(
            evidence: dict[str, Any],
        ) -> tuple[float, float, float, float]:
            return (
                float(evidence.get("weight", 1.0) or 1.0),
                1.0 if evidence.get("manual_locked") else 0.0,
                1.0
                if _normalize_whitespace(evidence.get("evidence_summary", ""))
                else 0.0,
                float(
                    evidence.get("updated_at", evidence.get("created_at", 0.0)) or 0.0
                ),
            )

        section_meta = {
            "consensus": {"label": "Consensus", "label_zh": "领域共识"},
            "debate": {"label": "Debates", "label_zh": "关键争议"},
            "evolution": {"label": "Method Evolution", "label_zh": "方法演化"},
            "open_question": {"label": "Open Questions", "label_zh": "开放问题"},
        }
        grouped_synthesis: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in synthesis_items:
            enriched = dict(item)
            enriched["degree"] = node_degree_by_id.get(f"synthesis:{item['id']}", 0)
            grouped_synthesis[str(item.get("item_type", "consensus"))].append(enriched)

        domain_digest: list[dict[str, Any]] = []
        for section_type in ["consensus", "debate", "evolution", "open_question"]:
            items = grouped_synthesis.get(section_type, [])
            if not items:
                continue
            items.sort(
                key=lambda item: self._compute_salience_score(
                    item, primary_key="confidence", support_count_key="claim_count"
                ),
                reverse=True,
            )
            domain_digest.append(
                {
                    "section_type": section_type,
                    "section_label": section_meta[section_type]["label"],
                    "section_label_zh": section_meta[section_type]["label_zh"],
                    "items": [
                        {
                            "id": int(item["id"]),
                            "title_localized": item.get(
                                "title_localized",
                                _make_localized_text(
                                    item.get("title", ""), item.get("title_zh", "")
                                ),
                            ),
                            "summary_localized": item.get(
                                "default_resolution_localized",
                                _make_localized_text(
                                    item.get("default_resolution", ""),
                                    item.get("default_resolution_zh", ""),
                                ),
                            )
                            if _normalize_whitespace(item.get("default_resolution", ""))
                            or _normalize_whitespace(
                                item.get("default_resolution_zh", "")
                            )
                            else item.get(
                                "summary_localized",
                                _make_localized_text(
                                    item.get("summary", ""), item.get("summary_zh", "")
                                ),
                            ),
                            "confidence": float(item.get("confidence", 0.5) or 0.5),
                            "claim_count": len(item.get("claim_ids", [])),
                            "review_status": str(item.get("review_status", "none")),
                            "manual_locked": bool(item.get("manual_locked")),
                        }
                        for item in items[:3]
                    ],
                }
            )

        enriched_claims: list[dict[str, Any]] = []
        for claim in claims:
            enriched = dict(claim)
            enriched["degree"] = node_degree_by_id.get(f"claim:{claim['id']}", 0)
            enriched_claims.append(enriched)
        enriched_claims.sort(
            key=lambda item: self._compute_salience_score(item), reverse=True
        )

        priority_claims: list[dict[str, Any]] = []
        for claim in enriched_claims[:10]:
            top_evidence_payload: dict[str, Any] | None = None
            evidences = sorted(
                evidence_by_claim.get(int(claim["id"]), []),
                key=evidence_priority,
                reverse=True,
            )
            if evidences:
                top_evidence = evidences[0]
                top_evidence_payload = {
                    "id": int(top_evidence.get("id", 0) or 0),
                    "section_key": str(
                        top_evidence.get("section_key", "other") or "other"
                    ),
                    "section_title_localized": top_evidence.get(
                        "section_title_localized",
                        _make_localized_text(
                            top_evidence.get("section_title", ""),
                            top_evidence.get("section_title_zh", ""),
                        ),
                    ),
                    "snippet_localized": top_evidence.get(
                        "snippet_localized",
                        _make_localized_text(
                            top_evidence.get("snippet", ""),
                            top_evidence.get("snippet_zh", ""),
                        ),
                    ),
                    "evidence_summary_localized": top_evidence.get(
                        "evidence_summary_localized",
                        _make_localized_text(
                            top_evidence.get("evidence_summary", ""),
                            top_evidence.get("evidence_summary_zh", ""),
                        ),
                    ),
                    "page_label": str(top_evidence.get("page_label", "")),
                }
            priority_claims.append(
                {
                    "id": int(claim["id"]),
                    "title_localized": claim.get(
                        "title_localized",
                        _make_localized_text(
                            claim.get("title", ""), claim.get("title_zh", "")
                        ),
                    ),
                    "summary_localized": claim.get(
                        "default_resolution_localized",
                        _make_localized_text(
                            claim.get("default_resolution", ""),
                            claim.get("default_resolution_zh", ""),
                        ),
                    )
                    if _normalize_whitespace(claim.get("default_resolution", ""))
                    or _normalize_whitespace(claim.get("default_resolution_zh", ""))
                    else claim.get(
                        "body_localized",
                        _make_localized_text(
                            claim.get("body", ""), claim.get("body_zh", "")
                        ),
                    ),
                    "claim_type": str(claim.get("claim_type", "finding") or "finding"),
                    "stance": str(claim.get("stance", "support") or "support"),
                    "importance": float(claim.get("importance", 0.5) or 0.5),
                    "evidence_count": int(claim.get("evidence_count", 0) or 0),
                    "top_evidence": top_evidence_payload,
                    "entity_names": [
                        str(name) for name in claim.get("entity_names", [])
                    ],
                    "paper_id": str(claim.get("paper_id", "")),
                    "review_status": str(claim.get("review_status", "none")),
                    "manual_locked": bool(claim.get("manual_locked")),
                }
            )

        active_conflicts = [
            {
                "review_id": int(review["id"]),
                "target_type": str(review.get("target_type", "")),
                "target_id": int(review.get("target_id", 0) or 0),
                "title_localized": review.get(
                    "title_localized",
                    _make_localized_text(
                        review.get("title", ""), review.get("title_zh", "")
                    ),
                ),
                "default_resolution_localized": review.get(
                    "default_resolution_localized",
                    _make_localized_text(
                        review.get("default_resolution", ""),
                        review.get("default_resolution_zh", ""),
                    ),
                ),
                "review_type": str(review.get("review_type", "candidate_update")),
                "has_suggested_payload": isinstance(
                    review.get("suggested_payload"), dict
                ),
            }
            for review in reviews
            if str(review.get("status", "")) == "pending"
        ]

        entity_type_labels = {
            "task": "任务",
            "problem": "问题",
            "method": "方法",
            "module": "模块",
            "dataset": "数据集",
            "metric": "指标",
            "concept": "概念",
        }
        grouped_entities: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entity in entities:
            enriched = dict(entity)
            enriched["degree"] = node_degree_by_id.get(f"entity:{entity['id']}", 0)
            grouped_entities[
                str(entity.get("entity_type", "concept") or "concept")
            ].append(enriched)
        entity_clusters: list[dict[str, Any]] = []
        for entity_type, items in grouped_entities.items():
            items.sort(
                key=lambda item: self._compute_salience_score(
                    item, primary_key="claim_count", support_count_key="claim_count"
                ),
                reverse=True,
            )
            entity_clusters.append(
                {
                    "entity_type": entity_type,
                    "label_zh": entity_type_labels.get(entity_type, entity_type),
                    "count": len(items),
                    "top_entities": [
                        {
                            "id": int(item["id"]),
                            "name_localized": item.get(
                                "name_localized",
                                _make_localized_text(
                                    item.get("canonical_name", ""),
                                    item.get("canonical_name_zh", ""),
                                ),
                            ),
                            "claim_count": int(item.get("claim_count", 0) or 0),
                            "manual_locked": bool(item.get("manual_locked")),
                        }
                        for item in items[:3]
                    ],
                }
            )
        entity_clusters.sort(
            key=lambda item: (
                int(item.get("count", 0)),
                str(item.get("entity_type", "")),
            ),
            reverse=True,
        )

        paper_title_by_job: dict[str, str] = {}
        if self._table_exists("papers"):
            paper_rows = self._conn.execute(
                "SELECT job_id, title, paper_id FROM papers WHERE job_id IS NOT NULL ORDER BY created_at DESC"
            ).fetchall()
            for row in paper_rows:
                job_id = _normalize_whitespace(row["job_id"])
                title = _normalize_whitespace(row["title"])
                if job_id and title and job_id not in paper_title_by_job:
                    paper_title_by_job[job_id] = title
        if self._table_exists("jobs"):
            job_rows = self._conn.execute(
                "SELECT id, paper_title FROM jobs ORDER BY created_at DESC"
            ).fetchall()
            for row in job_rows:
                job_id = _normalize_whitespace(row["id"])
                title = _normalize_whitespace(row["paper_title"])
                if job_id and title and job_id not in paper_title_by_job:
                    paper_title_by_job[job_id] = title

        source_bundles: list[dict[str, Any]] = []
        writeback_rows = self._conn.execute(
            "SELECT id, job_id, paper_id, created_at FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 24",
            (profile_id,),
        ).fetchall()
        for row in writeback_rows:
            writeback_id = int(row["id"])
            claim_count_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claims WHERE origin_writeback_id = ? AND deleted_at IS NULL",
                (writeback_id,),
            ).fetchone()
            entity_count_row = self._conn.execute(
                "SELECT COUNT(DISTINCT ce.entity_id) AS cnt FROM memory_claim_entities ce "
                "JOIN memory_claims c ON c.id = ce.claim_id "
                "WHERE c.origin_writeback_id = ? AND c.deleted_at IS NULL",
                (writeback_id,),
            ).fetchone()
            synthesis_count_row = self._conn.execute(
                "SELECT COUNT(DISTINCT s.id) AS cnt FROM memory_synthesis_items s "
                "LEFT JOIN memory_synthesis_claims sc ON sc.synthesis_id = s.id "
                "LEFT JOIN memory_claims c ON c.id = sc.claim_id "
                "WHERE s.deleted_at IS NULL AND (s.origin_writeback_id = ? OR c.origin_writeback_id = ?)",
                (writeback_id, writeback_id),
            ).fetchone()
            job_id = _normalize_whitespace(row["job_id"])
            paper_id = _normalize_whitespace(row["paper_id"])
            source_bundles.append(
                {
                    "job_id": job_id,
                    "paper_id": paper_id,
                    "paper_title": paper_title_by_job.get(job_id, paper_id or job_id),
                    "created_at": float(row["created_at"] or _timestamp()),
                    "claim_count": int(
                        claim_count_row["cnt"] if claim_count_row else 0
                    ),
                    "entity_count": int(
                        entity_count_row["cnt"] if entity_count_row else 0
                    ),
                    "synthesis_count": int(
                        synthesis_count_row["cnt"] if synthesis_count_row else 0
                    ),
                }
            )

        return {
            "domain_digest": domain_digest,
            "priority_claims": priority_claims,
            "active_conflicts": active_conflicts,
            "source_bundles": source_bundles,
            "entity_clusters": entity_clusters,
        }

    def build_timeline(self, profile_id: int) -> list[dict[str, Any]]:
        paper_title_by_job: dict[str, str] = {}
        if self._table_exists("papers"):
            paper_rows = self._conn.execute(
                "SELECT job_id, title FROM papers WHERE job_id IS NOT NULL ORDER BY created_at DESC"
            ).fetchall()
            for row in paper_rows:
                job_id = _normalize_whitespace(row["job_id"])
                title = _normalize_whitespace(row["title"])
                if job_id and title and job_id not in paper_title_by_job:
                    paper_title_by_job[job_id] = title
        if self._table_exists("jobs"):
            job_rows = self._conn.execute(
                "SELECT id, paper_title FROM jobs ORDER BY created_at DESC"
            ).fetchall()
            for row in job_rows:
                job_id = _normalize_whitespace(row["id"])
                title = _normalize_whitespace(row["paper_title"])
                if job_id and title and job_id not in paper_title_by_job:
                    paper_title_by_job[job_id] = title

        writeback_rows = self._conn.execute(
            "SELECT id, job_id, paper_id, created_at, delta_json FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 40",
            (profile_id,),
        ).fetchall()
        writeback_lookup: dict[int, dict[str, Any]] = {}
        for row in writeback_rows:
            writeback_id = int(row["id"])
            job_id = _normalize_whitespace(row["job_id"])
            paper_id = _normalize_whitespace(row["paper_id"])
            bundle_label = paper_title_by_job.get(
                job_id, paper_id or job_id or f"writeback-{writeback_id}"
            )
            delta_raw = _safe_text(row["delta_json"]) if row["delta_json"] else ""
            delta_parsed = _safe_json_loads(delta_raw) if delta_raw else None
            writeback_lookup[writeback_id] = {
                "source_job_id": job_id,
                "source_paper_id": paper_id,
                "bundle_label": bundle_label,
                "timestamp": float(row["created_at"] or _timestamp()),
                "delta": delta_parsed if isinstance(delta_parsed, dict) else None,
            }

        def get_bundle_meta(writeback_id: Any) -> dict[str, Any]:
            normalized_id = _maybe_int(writeback_id)
            if normalized_id is None:
                return {"source_job_id": "", "source_paper_id": "", "bundle_label": ""}
            return writeback_lookup.get(
                normalized_id,
                {"source_job_id": "", "source_paper_id": "", "bundle_label": ""},
            )

        def get_synthesis_bundle_meta(synthesis_item: dict[str, Any]) -> dict[str, Any]:
            origin_writeback_id = _maybe_int(synthesis_item.get("origin_writeback_id"))
            if (
                origin_writeback_id is not None
                and origin_writeback_id in writeback_lookup
            ):
                return get_bundle_meta(origin_writeback_id)
            claim_ids = [
                claim_id
                for claim_id in (
                    _maybe_int(item) for item in synthesis_item.get("claim_ids", [])
                )
                if claim_id is not None
            ]
            if not claim_ids:
                return {
                    "source_job_id": "",
                    "source_paper_id": "",
                    "bundle_label": "Cross-paper synthesis",
                }
            placeholders = ", ".join(["?"] * len(claim_ids))
            rows = self._conn.execute(
                f"SELECT DISTINCT origin_writeback_id FROM memory_claims WHERE id IN ({placeholders}) AND deleted_at IS NULL AND origin_writeback_id IS NOT NULL",
                tuple(claim_ids),
            ).fetchall()
            source_ids = [
                source_id
                for source_id in (
                    _maybe_int(row["origin_writeback_id"]) for row in rows
                )
                if source_id is not None
            ]
            if len(source_ids) == 1:
                return get_bundle_meta(source_ids[0])
            return {
                "source_job_id": "",
                "source_paper_id": "",
                "bundle_label": "Cross-paper synthesis",
            }

        def get_review_bundle_meta(review: dict[str, Any]) -> dict[str, Any]:
            target_type = str(review.get("target_type", ""))
            target_id = _maybe_int(review.get("target_id"))
            if target_id is None:
                return {"source_job_id": "", "source_paper_id": "", "bundle_label": ""}
            if target_type == "claim":
                row = self._conn.execute(
                    "SELECT origin_writeback_id FROM memory_claims WHERE id = ? AND deleted_at IS NULL",
                    (target_id,),
                ).fetchone()
                return (
                    get_bundle_meta(row["origin_writeback_id"])
                    if row
                    else {
                        "source_job_id": "",
                        "source_paper_id": "",
                        "bundle_label": "",
                    }
                )
            if target_type == "synthesis":
                row = self._conn.execute(
                    "SELECT * FROM memory_synthesis_items WHERE id = ? AND deleted_at IS NULL",
                    (target_id,),
                ).fetchone()
                if row:
                    synthesis_item = self._synthesis_row_to_dict(row)
                    claim_rows = self._conn.execute(
                        "SELECT claim_id FROM memory_synthesis_claims WHERE synthesis_id = ? ORDER BY claim_id ASC",
                        (target_id,),
                    ).fetchall()
                    synthesis_item["claim_ids"] = [
                        int(claim_row["claim_id"]) for claim_row in claim_rows
                    ]
                    return get_synthesis_bundle_meta(synthesis_item)
            if target_type == "edge":
                row = self._conn.execute(
                    "SELECT origin_writeback_id FROM memory_graph_edges WHERE id = ? AND deleted_at IS NULL",
                    (target_id,),
                ).fetchone()
                return (
                    get_bundle_meta(row["origin_writeback_id"])
                    if row
                    else {
                        "source_job_id": "",
                        "source_paper_id": "",
                        "bundle_label": "",
                    }
                )
            return {"source_job_id": "", "source_paper_id": "", "bundle_label": ""}

        items: list[dict[str, Any]] = []
        for writeback_id, meta in writeback_lookup.items():
            claim_count_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_claims WHERE origin_writeback_id = ? AND deleted_at IS NULL",
                (writeback_id,),
            ).fetchone()
            synthesis_count_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memory_synthesis_items WHERE origin_writeback_id = ? AND deleted_at IS NULL",
                (writeback_id,),
            ).fetchone()
            claim_count = int(claim_count_row["cnt"] if claim_count_row else 0)
            synthesis_count = int(
                synthesis_count_row["cnt"] if synthesis_count_row else 0
            )
            summary = f"Memory bundle ingested for this paper/job. It currently anchors {claim_count} claims and {synthesis_count} synthesis items in the profile."
            summary_zh = f"该论文/任务的记忆 bundle 已写入当前 profile，目前关联 {claim_count} 条 claim 和 {synthesis_count} 条高层认知。"
            # Enrich summary with delta info if available
            wb_delta = meta.get("delta")
            if isinstance(wb_delta, dict) and wb_delta.get("impact_score", 0) > 0:
                delta_parts_en: list[str] = []
                delta_parts_zh: list[str] = []
                n_new_ent = len(wb_delta.get("new_entities", []))
                n_new_claims = len(wb_delta.get("new_claims", []))
                n_reinforced = len(wb_delta.get("reinforced_claims", []))
                n_challenged = len(wb_delta.get("challenged_claims", []))
                n_debates = len(wb_delta.get("new_debates", []))
                if n_new_ent:
                    delta_parts_en.append(f"+{n_new_ent} new concepts")
                    delta_parts_zh.append(f"+{n_new_ent} 个新概念")
                if n_new_claims:
                    delta_parts_en.append(f"+{n_new_claims} new claims")
                    delta_parts_zh.append(f"+{n_new_claims} 条新结论")
                if n_reinforced:
                    delta_parts_en.append(f"{n_reinforced} reinforced")
                    delta_parts_zh.append(f"{n_reinforced} 条被强化")
                if n_challenged:
                    delta_parts_en.append(f"{n_challenged} challenged")
                    delta_parts_zh.append(f"{n_challenged} 条被挑战")
                if n_debates:
                    delta_parts_en.append(f"+{n_debates} new debates")
                    delta_parts_zh.append(f"+{n_debates} 个新争议")
                if delta_parts_en:
                    summary += f" Delta: {', '.join(delta_parts_en)}."
                    summary_zh += f" 变化：{'、'.join(delta_parts_zh)}。"
            items.append(
                {
                    "id": f"writeback:{writeback_id}",
                    "item_type": "paper_ingested",
                    "title": meta.get("bundle_label", "")
                    or meta.get("source_paper_id", "")
                    or meta.get("source_job_id", "")
                    or f"writeback-{writeback_id}",
                    "title_zh": "",
                    "title_localized": _make_localized_text(
                        meta.get("bundle_label", ""), ""
                    ),
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "timestamp": float(
                        meta.get("timestamp", _timestamp()) or _timestamp()
                    ),
                    "status": "active",
                    "target_type": "writeback",
                    "target_id": str(writeback_id),
                    "source_job_id": str(meta.get("source_job_id", "")),
                    "source_paper_id": str(meta.get("source_paper_id", "")),
                    "bundle_label": str(meta.get("bundle_label", "")),
                }
            )
        for synthesis_item in self.list_synthesis_items(profile_id, limit=80):
            synthesis_bundle_meta = get_synthesis_bundle_meta(synthesis_item)
            title = str(synthesis_item.get("title", ""))
            title_zh = str(synthesis_item.get("title_zh", ""))
            summary = str(
                synthesis_item.get("default_resolution", "")
                or synthesis_item.get("summary", "")
            )
            summary_zh = str(
                synthesis_item.get("default_resolution_zh", "")
                or synthesis_item.get("summary_zh", "")
            )
            items.append(
                {
                    "id": f"synthesis:{synthesis_item['id']}",
                    "item_type": str(synthesis_item.get("item_type", "synthesis")),
                    "title": title,
                    "title_zh": title_zh,
                    "title_localized": _make_localized_text(title, title_zh),
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "timestamp": float(
                        synthesis_item.get(
                            "updated_at", synthesis_item.get("created_at", _timestamp())
                        )
                        or _timestamp()
                    ),
                    "status": str(synthesis_item.get("review_status", "none")),
                    "target_type": "synthesis",
                    "target_id": str(synthesis_item.get("id", "")),
                    "source_job_id": str(
                        synthesis_bundle_meta.get("source_job_id", "")
                    ),
                    "source_paper_id": str(
                        synthesis_bundle_meta.get("source_paper_id", "")
                    ),
                    "bundle_label": str(synthesis_bundle_meta.get("bundle_label", "")),
                }
            )
        for claim in self.list_claims(profile_id, limit=80):
            claim_bundle_meta = get_bundle_meta(claim.get("origin_writeback_id"))
            title = str(claim.get("title", ""))
            title_zh = str(claim.get("title_zh", ""))
            summary = str(claim.get("default_resolution", "") or claim.get("body", ""))
            summary_zh = str(
                claim.get("default_resolution_zh", "") or claim.get("body_zh", "")
            )
            items.append(
                {
                    "id": f"claim:{claim['id']}",
                    "item_type": f"claim/{claim.get('claim_type', 'finding')}",
                    "title": title,
                    "title_zh": title_zh,
                    "title_localized": _make_localized_text(title, title_zh),
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "timestamp": float(
                        claim.get("updated_at", claim.get("created_at", _timestamp()))
                        or _timestamp()
                    ),
                    "status": str(claim.get("review_status", "none")),
                    "target_type": "claim",
                    "target_id": str(claim.get("id", "")),
                    "source_job_id": str(claim_bundle_meta.get("source_job_id", "")),
                    "source_paper_id": str(
                        claim_bundle_meta.get("source_paper_id", "")
                    ),
                    "bundle_label": str(claim_bundle_meta.get("bundle_label", "")),
                }
            )
        for review in self.list_review_items(profile_id, limit=40):
            if str(review.get("status", "")) != "pending":
                continue
            review_bundle_meta = get_review_bundle_meta(review)
            title = str(review.get("title", ""))
            title_zh = str(review.get("title_zh", ""))
            summary = str(
                review.get("default_resolution", "") or review.get("description", "")
            )
            summary_zh = str(
                review.get("default_resolution_zh", "")
                or review.get("description_zh", "")
            )
            target_type = str(review.get("target_type", ""))
            target_id = str(review.get("target_id", ""))
            items.append(
                {
                    "id": f"review:{review['id']}",
                    "item_type": f"review/{review.get('review_type', 'pending')}",
                    "title": title,
                    "title_zh": title_zh,
                    "title_localized": _make_localized_text(title, title_zh),
                    "summary": summary,
                    "summary_zh": summary_zh,
                    "summary_localized": _make_localized_text(summary, summary_zh),
                    "timestamp": float(
                        review.get("updated_at", review.get("created_at", _timestamp()))
                        or _timestamp()
                    ),
                    "status": str(review.get("status", "pending")),
                    "target_type": target_type,
                    "target_id": target_id,
                    "source_job_id": str(review_bundle_meta.get("source_job_id", "")),
                    "source_paper_id": str(
                        review_bundle_meta.get("source_paper_id", "")
                    ),
                    "bundle_label": str(review_bundle_meta.get("bundle_label", "")),
                }
            )
        items.sort(
            key=lambda item: (
                float(item.get("timestamp", 0.0)),
                1 if str(item.get("item_type", "")) == "paper_ingested" else 0,
            ),
            reverse=True,
        )
        return items

    def get_workspace_snapshot(self, profile_id: int) -> dict[str, Any]:
        profile = self.get_profile_by_id(profile_id)
        entities = self.list_entities(profile_id, limit=200)
        claims = self.list_claims(profile_id, limit=200)
        evidence_fragments = self.list_evidence(profile_id, limit=300)
        synthesis_items = self.list_synthesis_items(profile_id, limit=160)
        reviews = self.list_review_items(profile_id, limit=120)
        revisions = self.list_revision_history(profile_id, limit=160)
        knowledge_items = self.query_domain_knowledge(profile_id, top_k=50)
        links = self.query_paper_links(profile_id, limit=20)
        graph = self.build_graph_snapshot(profile_id)
        timeline = self.build_timeline(profile_id)
        curated = self._build_curated_sections(
            profile_id,
            entities=entities,
            claims=claims,
            evidence_fragments=evidence_fragments,
            synthesis_items=synthesis_items,
            reviews=reviews,
            graph=graph,
        )
        overview = self.get_workspace_overview(profile_id)
        return {
            "profile": profile,
            "overview": overview,
            "knowledge_items": knowledge_items,
            "style": self.get_style_preferences(profile_id),
            "links": links,
            "entities": entities,
            "claims": claims,
            "evidence_fragments": evidence_fragments,
            "synthesis_items": synthesis_items,
            "editable_edges": self.list_graph_edges(profile_id, limit=200),
            "graph": graph,
            "reviews": reviews,
            "revisions": revisions,
            "timeline": timeline,
            "curated": curated,
            "themes": self.get_or_build_theme_snapshot(profile_id),
            "gaps": self.get_or_build_gap_snapshot(profile_id),
            "opportunities": self.get_or_build_opportunity_snapshot(profile_id),
            "health": self.get_or_build_memory_health(profile_id),
            "field_map": self.get_or_build_field_map(profile_id),
            "evidence_matrix": self.get_or_build_evidence_matrix(profile_id),
        }


def build_memory_extraction_prompt(
    paper_notes: dict[str, Any],
    task_summary: str,
    *,
    promotion_candidates: list[dict[str, Any]] | None = None,
    review_context: str = "",
) -> str:
    metadata = paper_notes.get("metadata", {}) if isinstance(paper_notes, dict) else {}
    promotion_block = ""
    if promotion_candidates:
        promotion_block = (
            "Distilled promotion candidates from the current job "
            "(treat these as high-value hints, not hard constraints):\n"
            f"{json.dumps(promotion_candidates, ensure_ascii=False, indent=2)}\n\n"
        )
    review_block = ""
    if review_context.strip():
        review_block = (
            "Nearby long-term conflicts and review context "
            "(avoid duplicating unresolved disputes as settled facts):\n"
            f"{review_context.strip()}\n\n"
        )
    return (
        "You are updating a long-term research memory profile after reading one paper.\n\n"
        "Goal: extract reusable layered memory that helps both the Agent and a human researcher build a clearer domain understanding over time.\n"
        "Focus on consensus, debate, method evolution, open questions, evidence-backed claims, reusable entities, and important paper-to-paper relations.\n\n"
        f"Paper title: {metadata.get('title_en', '')}\n"
        f"Chinese title (if available): {metadata.get('title_cn', '')}\n"
        f"Research problems: {json.dumps(paper_notes.get('problem', []), ensure_ascii=False)}\n"
        f"Method steps: {json.dumps(paper_notes.get('method_steps', []), ensure_ascii=False)}\n"
        f"Main results: {json.dumps(paper_notes.get('main_results', []), ensure_ascii=False)}\n"
        f"Limitations: {json.dumps(paper_notes.get('limitations', []), ensure_ascii=False)}\n"
        f"Glossary seed: {json.dumps(paper_notes.get('glossary_seed', []), ensure_ascii=False)}\n\n"
        f"{promotion_block}"
        f"{review_block}"
        f"Interpretation summary:\n{task_summary}\n\n"
        "Return JSON only in the following format:\n"
        "{\n"
        '  "entities": [\n'
        '    {"name": "term", "type": "task|problem|method|module|dataset|metric|concept", "summary": "1 concise sentence"}\n'
        "  ],\n"
        '  "claims": [\n'
        "    {\n"
        '      "claim_key": "stable-short-key",\n'
        '      "title": "short claim title",\n'
        '      "body": "1-2 sentence claim summary",\n'
        '      "claim_type": "finding|comparison|limitation|hypothesis|open_question",\n'
        '      "stance": "support|oppose|mixed|open",\n'
        '      "importance": 0.0,\n'
        '      "scope": {"conditions": ["when it holds"], "boundary": "where it may fail", "population": "optional task/data regime", "notes": "optional concise scope note"},\n'
        '      "entity_names": ["entity A", "entity B"],\n'
        '      "evidence": [\n'
        '        {"section_key": "background|method|experiments|ablation|limitations|conclusion|other", "section_title": "optional section title", "snippet": "short evidence snippet", "summary": "why this snippet matters", "page_label": "optional page hint", "anchor_kind": "quote|result|metric|table|figure|claim|other", "context_before": "optional very short preceding context", "context_after": "optional very short trailing context", "structured_signal": {"metric": "optional metric", "value": "optional value", "comparator": "optional comparator", "dataset": "optional dataset"}}\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "synthesis_items": [\n'
        '    {"item_type": "consensus|debate|evolution|open_question", "title": "short title", "summary": "1-3 sentence higher-level cognition", "claim_keys": ["claim-key-1"], "entity_names": ["entity A"], "confidence": 0.0}\n'
        "  ],\n"
        '  "paper_relations": [\n'
        '    {"target": "related paper title or canonical paper id", "relation": "cites|extends|competes|compares_with|related_to", "summary": "short relationship summary"}\n'
        "  ],\n"
        '  "style_observations": [\n'
        '    {"key": "detail_level|formula_depth|tone", "value": "short style preference inferred from the output"}\n'
        "  ],\n"
        '  "domain_facts": [\n'
        '    {"category": "general|claim/finding|claim/limitation|consensus|debate|evolution|open_question", "content": "1 concise reusable fact for profile-level retrieval", "relevance_score": 0.0}\n'
        "  ]\n"
        "}\n\n"
        "Requirements:\n"
        "1. Extract 3-8 entities if possible.\n"
        "2. Extract 3-8 claims with useful evidence snippets; keep snippets concise and readable.\n"
        "3. Extract 2-6 synthesis_items focused on consensus, debate, evolution, and open questions.\n"
        "3a. Extract 0-6 domain_facts when the paper contains durable reusable knowledge that should surface in profile retrieval even outside raw claims/synthesis.\n"
        "4. Keep claim_key stable and short.\n"
        "5. Do not output empty strings, explanations, Markdown, or any text outside the JSON.\n"
        "5a. When a claim only holds under specific data regimes, retrieval quality, scale, or benchmark settings, capture that boundary in `scope`.\n"
        "5b. Prefer evidence anchors that preserve experimental structure; if a metric, dataset, comparator, or result type is explicit, include it in `structured_signal`.\n"
        "6. TEXT LENGTH CONSTRAINTS (strictly enforced):\n"
        "   - claim title: max 100 chars, one concise sentence\n"
        "   - claim body: max 2-3 sentences (~280 chars), first sentence is the core conclusion, second gives scope/conditions\n"
        "   - synthesis title: max 100 chars\n"
        "   - synthesis summary: max 3-4 sentences (~360 chars)\n"
        "   - entity summary: max 1 sentence (~120 chars)\n"
        "   - evidence snippet: max 2 sentences (~200 chars)"
    )


def parse_memory_extraction(resp: str) -> dict[str, Any]:
    payload = json.loads(_strip_code_fence(resp))
    if not isinstance(payload, dict):
        raise ValueError("Memory extraction response is not a JSON object")

    normalized_entities: list[dict[str, str]] = []
    if isinstance(payload.get("entities"), list):
        for item in payload.get("entities", []):
            if not isinstance(item, dict):
                continue
            name = _normalize_whitespace(item.get("name", ""))
            if not name:
                continue
            normalized_entities.append(
                {
                    "name": name,
                    "type": _normalize_whitespace(
                        item.get("type", item.get("entity_type", "concept"))
                    )
                    or "concept",
                    "summary": _normalize_whitespace(item.get("summary", "")),
                }
            )

    normalized_claims: list[dict[str, Any]] = []
    if isinstance(payload.get("claims"), list):
        for item in payload.get("claims", []):
            if not isinstance(item, dict):
                continue
            title = _normalize_whitespace(item.get("title", ""))
            body = _normalize_whitespace(item.get("body", ""))
            if not title and not body:
                continue
            evidence_items: list[dict[str, Any]] = []
            if isinstance(item.get("evidence"), list):
                for evidence in item.get("evidence", []):
                    if not isinstance(evidence, dict):
                        continue
                    snippet = _normalize_whitespace(evidence.get("snippet", ""))
                    if not snippet:
                        continue
                    evidence_items.append(
                        {
                            "section_key": _normalize_whitespace(
                                evidence.get("section_key", "other")
                            )
                            or "other",
                            "section_title": _normalize_whitespace(
                                evidence.get("section_title", "")
                            ),
                            "snippet": snippet,
                            "summary": _normalize_whitespace(
                                evidence.get(
                                    "summary", evidence.get("evidence_summary", "")
                                )
                            ),
                            "page_label": _normalize_whitespace(
                                evidence.get("page_label", "")
                            ),
                            "anchor_kind": _normalize_whitespace(
                                evidence.get("anchor_kind", "text")
                            )
                            or "text",
                            "context_before": _normalize_whitespace(
                                evidence.get("context_before", "")
                            ),
                            "context_after": _normalize_whitespace(
                                evidence.get("context_after", "")
                            ),
                            "structured_signal": evidence.get("structured_signal", {}),
                            "page_start": _maybe_int(evidence.get("page_start")),
                            "page_end": _maybe_int(evidence.get("page_end")),
                        }
                    )
            normalized_claims.append(
                {
                    "claim_key": _normalize_whitespace(item.get("claim_key", ""))
                    or _claim_default_key(title, body),
                    "title": title or body[:80],
                    "body": body or title,
                    "claim_type": _normalize_whitespace(
                        item.get("claim_type", "finding")
                    )
                    or "finding",
                    "stance": _normalize_whitespace(item.get("stance", "support"))
                    or "support",
                    "importance": min(
                        max(_maybe_float(item.get("importance"), 0.5), 0.0), 1.0
                    ),
                    "scope": item.get("scope", {}),
                    "entity_names": _dedupe_strings(
                        [
                            str(entity_name)
                            for entity_name in item.get("entity_names", [])
                        ]
                    ),
                    "evidence": evidence_items,
                }
            )

    normalized_synthesis_items: list[dict[str, Any]] = []
    if isinstance(payload.get("synthesis_items"), list):
        for item in payload.get("synthesis_items", []):
            if not isinstance(item, dict):
                continue
            title = _normalize_whitespace(item.get("title", ""))
            summary = _normalize_whitespace(item.get("summary", ""))
            if not title and not summary:
                continue
            item_type = (
                _normalize_whitespace(item.get("item_type", "consensus")) or "consensus"
            )
            normalized_synthesis_items.append(
                {
                    "synthesis_key": _normalize_whitespace(
                        item.get("synthesis_key", "")
                    )
                    or f"{item_type}:{_slugify(title or summary[:80])}",
                    "item_type": item_type,
                    "title": title or summary[:80],
                    "summary": summary or title,
                    "claim_keys": _dedupe_strings(
                        [str(claim_key) for claim_key in item.get("claim_keys", [])]
                    ),
                    "entity_names": _dedupe_strings(
                        [
                            str(entity_name)
                            for entity_name in item.get("entity_names", [])
                        ]
                    ),
                    "confidence": min(
                        max(_maybe_float(item.get("confidence"), 0.6), 0.0), 1.0
                    ),
                }
            )

    normalized_paper_relations: list[dict[str, str]] = []
    if isinstance(payload.get("paper_relations"), list):
        for item in payload.get("paper_relations", []):
            if not isinstance(item, dict):
                continue
            target = _normalize_whitespace(item.get("target", ""))
            if not target:
                continue
            normalized_paper_relations.append(
                {
                    "target": target,
                    "relation": _normalize_whitespace(
                        item.get("relation", "related_to")
                    )
                    or "related_to",
                    "summary": _normalize_whitespace(item.get("summary", "")),
                }
            )

    normalized_style_observations: list[dict[str, str]] = []
    if isinstance(payload.get("style_observations"), list):
        for item in payload.get("style_observations", []):
            if not isinstance(item, dict):
                continue
            key = _normalize_whitespace(item.get("key", ""))
            value = _normalize_whitespace(item.get("value", ""))
            if key and value:
                normalized_style_observations.append({"key": key, "value": value})

    domain_facts: list[dict[str, Any]] = []
    if isinstance(payload.get("domain_facts"), list):
        for item in payload.get("domain_facts", []):
            if not isinstance(item, dict):
                continue
            content = _normalize_whitespace(item.get("content", ""))
            if not content:
                continue
            domain_facts.append(
                {
                    "category": _normalize_whitespace(item.get("category", "general"))
                    or "general",
                    "content": content,
                    "relevance_score": min(
                        max(_maybe_float(item.get("relevance_score"), 1.0), 0.0), 2.0
                    ),
                }
            )

    return {
        "entities": normalized_entities,
        "claims": normalized_claims,
        "synthesis_items": normalized_synthesis_items,
        "paper_relations": normalized_paper_relations,
        "style_observations": normalized_style_observations,
        "domain_facts": domain_facts,
    }
