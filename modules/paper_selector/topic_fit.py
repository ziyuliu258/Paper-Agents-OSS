from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "about",
    "across",
    "after",
    "among",
    "approach",
    "based",
    "between",
    "from",
    "into",
    "method",
    "model",
    "models",
    "paper",
    "task",
    "tasks",
    "their",
    "there",
    "these",
    "this",
    "those",
    "using",
    "with",
    "without",
}
_ANCHOR_GROUPS = {
    "time_series": ("time", "series", "temporal"),
    "forecasting": ("forecast", "forecasting", "prediction", "predict"),
    "tracking": ("tracking", "tracker", "track"),
    "detection": ("detection", "detect"),
    "classification": ("classification", "classify"),
    "retrieval": ("retrieval", "retrieve", "rag", "grounding"),
    "reasoning": ("reasoning", "reason"),
    "generation": ("generation", "generative", "synthesis"),
    "segmentation": ("segmentation", "segment"),
    "agent": ("agent", "agentic", "planning", "tool"),
}


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(str(text or "").lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _topic_fragments(topics: list[dict[str, Any]]) -> list[str]:
    fragments: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        for value in (topic.get("name"), topic.get("query"), topic.get("query_en")):
            text = _normalize_text(str(value or ""))
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                fragments.append(text)
        for keyword in (
            *(topic.get("keywords", []) or []),
            *(topic.get("auto_keywords", []) or []),
            *(topic.get("heuristic_keywords", []) or []),
        ):
            text = _normalize_text(str(keyword or ""))
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                fragments.append(text)
    return fragments


def _anchor_groups_for_text(text: str) -> list[str]:
    lowered = text.lower()
    matched: list[str] = []
    for key, aliases in _ANCHOR_GROUPS.items():
        if any(alias in lowered for alias in aliases):
            matched.append(key)
    return matched


def _score_text_against_topics(
    candidate_text: str,
    topics: list[dict[str, Any]],
    *,
    base_semantic_score: float,
) -> dict[str, Any]:
    normalized_candidate = _normalize_text(candidate_text)
    lowered_candidate = normalized_candidate.lower()
    topic_fragments = _topic_fragments(topics)
    topic_tokens = set(_tokenize("\n".join(topic_fragments)))
    candidate_tokens = set(_tokenize(normalized_candidate))

    phrase_matches = [
        fragment for fragment in topic_fragments if fragment and fragment.lower() in lowered_candidate
    ]
    token_matches = sorted(topic_tokens & candidate_tokens)
    phrase_score = len(phrase_matches) / max(len(topic_fragments), 1) if topic_fragments else 0.0
    token_score = len(token_matches) / max(len(topic_tokens), 1) if topic_tokens else 0.0

    topic_anchor_groups = _anchor_groups_for_text("\n".join(topic_fragments))
    missing_anchor_groups: list[str] = []
    for group in topic_anchor_groups:
        aliases = _ANCHOR_GROUPS[group]
        if not any(alias in lowered_candidate for alias in aliases):
            missing_anchor_groups.append(group)

    penalty = min(0.45, 0.15 * len(missing_anchor_groups))
    topic_fit_score = max(
        0.0,
        min(
            1.0,
            0.45 * float(base_semantic_score)
            + 0.35 * token_score
            + 0.20 * phrase_score
            - penalty,
        ),
    )

    mismatch_reasons: list[str] = []
    if missing_anchor_groups:
        mismatch_reasons.append(
            "Missing required topic anchors: " + ", ".join(missing_anchor_groups)
        )
    if not phrase_matches and token_score < 0.2:
        mismatch_reasons.append("Candidate text barely overlaps with the requested topic.")

    if mismatch_reasons and topic_fit_score < 0.72:
        fit_label = "mismatch"
    elif topic_fit_score >= 0.82:
        fit_label = "strong_match"
    elif topic_fit_score >= 0.72:
        fit_label = "match"
    elif topic_fit_score >= 0.58:
        fit_label = "weak_match"
    else:
        fit_label = "mismatch"

    matched_aspects = phrase_matches[:6] or token_matches[:8]
    return {
        "fit_label": fit_label,
        "topic_fit_score": round(topic_fit_score, 4),
        "matched_aspects": matched_aspects,
        "mismatch_reasons": mismatch_reasons,
        "token_overlap_score": round(token_score, 4),
        "phrase_overlap_score": round(phrase_score, 4),
        "missing_anchor_groups": missing_anchor_groups,
    }


def build_candidate_topic_text(candidate: dict[str, Any]) -> str:
    parts = [
        _normalize_text(str(candidate.get("title") or "")),
        _normalize_text(str(candidate.get("abstract") or "")),
        _normalize_text(str(candidate.get("aic_text") or "")),
        _normalize_text(str(candidate.get("venue") or "")),
    ]
    return "\n".join(part for part in parts if part)


def judge_candidate_topic_fit(
    candidate: dict[str, Any],
    topics: list[dict[str, Any]],
    *,
    base_semantic_score: float | None = None,
) -> dict[str, Any]:
    return {
        "paper_id": str(candidate.get("paper_id") or ""),
        "title": str(candidate.get("title") or ""),
        **_score_text_against_topics(
            build_candidate_topic_text(candidate),
            topics,
            base_semantic_score=float(
                candidate.get("semantic_score", 0.0)
                if base_semantic_score is None
                else base_semantic_score
            ),
        ),
    }


def judge_paper_notes_topic_fit(
    paper_notes: dict[str, Any],
    topics: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = paper_notes.get("metadata") if isinstance(paper_notes, dict) else {}
    parts = [
        _normalize_text(str((metadata or {}).get("title_en") or "")),
        _normalize_text(str(paper_notes.get("paper_summary") or "")),
    ]
    for key in ("problem", "method_steps"):
        for item in paper_notes.get(key, []) if isinstance(paper_notes, dict) else []:
            text = _normalize_text(str(item or ""))
            if text:
                parts.append(text)
    return _score_text_against_topics(
        "\n".join(part for part in parts if part),
        topics,
        base_semantic_score=1.0,
    )
