from __future__ import annotations

import re
from typing import Any

from server.database import Database
from utils.embedding import cosine_similarity
from utils.embedding import embed_texts_sync
from utils.memory import MemoryManager

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "and",
    "are",
    "because",
    "between",
    "from",
    "into",
    "more",
    "most",
    "paper",
    "their",
    "them",
    "they",
    "this",
    "that",
    "than",
    "then",
    "uses",
    "using",
    "with",
    "without",
    "through",
    "while",
    "where",
    "which",
}
_SOFT_ROUTE_THRESHOLD = 0.66
_FINAL_ROUTE_THRESHOLD = 0.62
_MATCH_MARGIN = 0.08
_TOPIC_PRIOR_MARGIN = 0.14
_DOMAIN_PATTERNS = [
    ("Time Series", ("time series", "forecast", "forecasting", "temporal")),
    ("Agents", ("agent", "agentic", "tool use", "planning")),
    ("Retrieval", ("retrieval", "rag", "evidence", "grounding")),
    ("Multimodal", ("multimodal", "vision-language", "audio-visual", "cross-modal")),
    ("Computer Vision", ("computer vision", "image", "video", "visual", "segmentation", "tracking")),
    ("NLP", ("language model", "llm", "nlp", "text generation", "question answering")),
]
_TASK_PATTERNS = [
    ("Forecasting", ("forecast", "forecasting", "prediction")),
    ("Segmentation", ("segmentation", "segment")),
    ("Tracking", ("tracking", "tracker")),
    ("Detection", ("detection", "detect")),
    ("Classification", ("classification", "classify")),
    ("Retrieval", ("retrieval", "retrieve")),
    ("Reasoning", ("reasoning", "reason")),
    ("Generation", ("generation", "generative", "synthesis")),
]
_SEMANTIC_ALIASES = {
    "time series": (
        "time series",
        "temporal sequence",
        "temporal data",
        "时间序列",
        "时序",
    ),
    "forecasting": (
        "forecasting",
        "forecast",
        "prediction",
        "predictive",
        "预测",
        "预报",
    ),
    "multivariate": (
        "multivariate",
        "multi-variate",
        "多变量",
    ),
    "long-term": (
        "long-term",
        "long horizon",
        "long-range",
        "长期",
    ),
    "channel dependency": (
        "channel dependency",
        "channel dependence",
        "cross-variable",
        "通道依赖",
        "变量依赖",
    ),
    "agents": (
        "agent",
        "agentic",
        "智能体",
        "agent工程",
    ),
    "retrieval": (
        "retrieval",
        "rag",
        "grounding",
        "检索",
        "检索增强",
    ),
    "computer vision": (
        "computer vision",
        "visual",
        "vision",
        "图像",
        "视觉",
        "计算机视觉",
    ),
    "segmentation": (
        "segmentation",
        "segment",
        "分割",
    ),
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


def _semantic_concepts(text: str) -> set[str]:
    normalized = _normalize_text(text).casefold()
    concepts: set[str] = set()
    for canonical, aliases in _SEMANTIC_ALIASES.items():
        if any(alias.casefold() in normalized for alias in aliases):
            concepts.add(canonical)
    return concepts


def _expand_semantic_aliases(text: str) -> str:
    normalized = _normalize_text(text)
    concepts = _semantic_concepts(normalized)
    if not concepts:
        return normalized
    expansions = [normalized, *sorted(concepts)]
    return "\n".join(part for part in expansions if part)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _topic_fragments(topics: list[dict[str, Any]]) -> list[str]:
    fragments: list[str] = []
    for topic in topics:
        for value in (topic.get("name"), topic.get("query"), topic.get("query_en")):
            text = _normalize_text(str(value or ""))
            if text:
                fragments.append(text)
        for keyword in (
            *(topic.get("keywords", []) or []),
            *(topic.get("auto_keywords", []) or []),
            *(topic.get("heuristic_keywords", []) or []),
        ):
            text = _normalize_text(str(keyword or ""))
            if text:
                fragments.append(text)
    return _dedupe_keep_order(fragments)


def build_topic_context(topics: list[dict[str, Any]]) -> str:
    return "\n".join(_topic_fragments(topics))


def build_paper_context(
    paper_notes: dict[str, Any], *, topics: list[dict[str, Any]] | None = None
) -> str:
    metadata = paper_notes.get("metadata") if isinstance(paper_notes, dict) else {}
    title = _normalize_text(str((metadata or {}).get("title_en") or ""))
    title_cn = _normalize_text(str((metadata or {}).get("title_cn") or ""))
    venue = _normalize_text(str((metadata or {}).get("venue") or ""))
    summary = _normalize_text(str(paper_notes.get("paper_summary") or ""))
    problem = [
        _normalize_text(str(item))
        for item in paper_notes.get("problem", [])
        if _normalize_text(str(item))
    ]
    method_steps = [
        _normalize_text(str(item))
        for item in paper_notes.get("method_steps", [])
        if _normalize_text(str(item))
    ]
    fragments = [title, title_cn, venue, summary, *problem[:4], *method_steps[:4]]
    if topics:
        fragments.extend(_topic_fragments(topics))
    return "\n".join(fragment for fragment in fragments if fragment)


def _build_profile_fingerprint(
    mm: MemoryManager,
    db: Database,
    profile: dict[str, Any],
) -> str:
    profile_id = int(profile["id"])
    fragments = [
        _normalize_text(str(profile.get("name") or "")),
        _normalize_text(str(profile.get("description") or "")),
    ]
    try:
        brief = mm.get_or_build_brief(profile_id)
    except Exception:
        brief = {}
    if isinstance(brief, dict):
        for key in ("profile_name",):
            value = _normalize_text(str(brief.get(key) or ""))
            if value:
                fragments.append(value)
        for item in brief.get("key_themes", []) or []:
            if isinstance(item, dict):
                fragments.append(_normalize_text(str(item.get("anchor") or "")))
                fragments.append(_normalize_text(str(item.get("anchor_zh") or "")))
        for item in brief.get("key_concepts", []) or []:
            if isinstance(item, dict):
                fragments.append(_normalize_text(str(item.get("name") or "")))
                fragments.append(_normalize_text(str(item.get("name_zh") or "")))
        for item in brief.get("top_consensus", []) or []:
            if isinstance(item, dict):
                fragments.append(_normalize_text(str(item.get("title") or "")))
                fragments.append(_normalize_text(str(item.get("title_zh") or "")))
                fragments.append(_normalize_text(str(item.get("summary") or "")))
                fragments.append(_normalize_text(str(item.get("summary_zh") or "")))
    try:
        activity = db.list_profile_activity(profile_id, limit=6)
    except Exception:
        activity = []
    for item in activity:
        if not isinstance(item, dict):
            continue
        title = _normalize_text(
            str(item.get("paper_title") or item.get("job_paper_title") or "")
        )
        if title:
            fragments.append(title)
    return "\n".join(fragment for fragment in _dedupe_keep_order(fragments) if fragment)


def _lexical_score(query_text: str, fingerprint: str) -> float:
    expanded_query = _expand_semantic_aliases(query_text)
    expanded_fingerprint = _expand_semantic_aliases(fingerprint)
    query_tokens = set(_tokenize(expanded_query))
    profile_tokens = set(_tokenize(expanded_fingerprint))
    if not query_tokens or not profile_tokens:
        token_overlap = 0.0
    else:
        token_overlap = len(query_tokens & profile_tokens) / max(len(query_tokens), 1)
    phrases = [
        fragment.lower()
        for fragment in re.split(r"[\n,;|]", expanded_query)
        if len(fragment.strip()) >= 4
    ]
    lowered_fingerprint = expanded_fingerprint.lower()
    phrase_hits = sum(1 for phrase in phrases if phrase and phrase in lowered_fingerprint)
    phrase_score = phrase_hits / max(len(phrases), 1) if phrases else 0.0
    query_concepts = _semantic_concepts(query_text)
    profile_concepts = _semantic_concepts(fingerprint)
    concept_overlap = (
        len(query_concepts & profile_concepts) / max(len(query_concepts), 1)
        if query_concepts
        else 0.0
    )
    return min(1.0, 0.4 * token_overlap + 0.2 * phrase_score + 0.4 * concept_overlap)


def _semantic_scores(query_text: str, fingerprints: list[str]) -> list[float]:
    if not fingerprints:
        return []
    expanded_inputs = [
        _expand_semantic_aliases(query_text),
        *[_expand_semantic_aliases(text) for text in fingerprints],
    ]
    vectors = embed_texts_sync(expanded_inputs)
    if len(vectors) != len(expanded_inputs):
        return [0.0] * len(fingerprints)
    query_vec = vectors[0]
    return [max(0.0, cosine_similarity(query_vec, vec)) for vec in vectors[1:]]


def _score_match(query_text: str, fingerprint: str, *, semantic_score: float = 0.0) -> float:
    lexical_score = _lexical_score(query_text, fingerprint)
    return min(1.0, 0.55 * semantic_score + 0.45 * lexical_score)


def _rank_profiles(
    mm: MemoryManager,
    db: Database,
    query_text: str,
    *,
    include_default: bool,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for profile in mm.list_profiles():
        name = str(profile.get("name") or "").strip()
        if not include_default and name.lower() == "default":
            continue
        fingerprint = _build_profile_fingerprint(mm, db, profile)
        candidates.append(
            {
                "profile_id": int(profile["id"]),
                "profile_name": name,
                "fingerprint": fingerprint,
                "paper_count": int(profile.get("paper_count") or 0),
            }
        )

    semantic_scores = _semantic_scores(
        query_text,
        [str(item.get("fingerprint") or "") for item in candidates],
    )

    ranked: list[dict[str, Any]] = []
    for item, semantic_score in zip(candidates, semantic_scores):
        fingerprint = str(item.get("fingerprint") or "")
        paper_count = int(item.get("paper_count") or 0)
        score = _score_match(query_text, fingerprint, semantic_score=semantic_score)
        if paper_count > 0 and _semantic_concepts(query_text) & _semantic_concepts(fingerprint):
            score = min(1.0, score + min(0.10, 0.02 * paper_count))
        ranked.append(
            {
                **item,
                "semantic_score": semantic_score,
                "score": score,
            }
        )

    ranked.sort(
        key=lambda item: (
            float(item["score"]),
            float(item.get("semantic_score") or 0.0),
            int(item.get("paper_count") or 0),
            -int(item["profile_id"]),
        ),
        reverse=True,
    )
    return ranked


def _pick_ranked_profile(
    ranked: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any] | None:
    if not ranked:
        return None
    best = ranked[0]
    second_score = float(ranked[1]["score"]) if len(ranked) > 1 else 0.0
    if float(best["score"]) < threshold:
        return None
    if float(best["score"]) - second_score < _MATCH_MARGIN:
        return None
    return best


def suggest_profile_for_topics(
    mm: MemoryManager,
    db: Database,
    topics: list[dict[str, Any]],
) -> dict[str, Any]:
    query_text = build_topic_context(topics)
    ranked = _rank_profiles(mm, db, query_text, include_default=False)
    matched = _pick_ranked_profile(ranked, threshold=_SOFT_ROUTE_THRESHOLD)
    return {
        "query_text": query_text,
        "ranked_profiles": ranked[:6],
        "matched_profile": matched,
    }


def _infer_profile_name(paper_context: str) -> str:
    lowered = paper_context.lower()
    domain = "General AI"
    for label, patterns in _DOMAIN_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            domain = label
            break
    task = "Paper Study"
    for label, patterns in _TASK_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            task = label
            break
    return f"{domain} - {task}"


def assign_profile_for_paper(
    mm: MemoryManager,
    db: Database,
    paper_notes: dict[str, Any],
    *,
    topics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    paper_context = build_paper_context(paper_notes, topics=topics)
    ranked = _rank_profiles(mm, db, paper_context, include_default=False)
    matched = _pick_ranked_profile(ranked, threshold=_FINAL_ROUTE_THRESHOLD)
    topic_prior: dict[str, Any] | None = None
    topic_prior_ranked: dict[str, Any] | None = None
    if topics:
        topic_route = suggest_profile_for_topics(mm, db, topics)
        candidate = topic_route.get("matched_profile")
        if isinstance(candidate, dict):
            topic_prior = candidate
            candidate_id = int(candidate["profile_id"])
            topic_prior_ranked = next(
                (
                    item
                    for item in ranked
                    if int(item.get("profile_id") or 0) == candidate_id
                ),
                None,
            )

    if topic_prior is not None:
        if matched is None:
            return {
                "status": "matched",
                "profile_id": int(topic_prior["profile_id"]),
                "profile_name": str(topic_prior["profile_name"]),
                "score": float((topic_prior_ranked or {}).get("score") or topic_prior.get("score") or 0.0),
                "note": (
                    f"Auto matched to existing profile '{topic_prior['profile_name']}' "
                    "via topic prior and paper-context consistency."
                ),
                "ranked_profiles": ranked[:6],
            }
        if int(matched["profile_id"]) != int(topic_prior["profile_id"]):
            topic_score = float((topic_prior_ranked or {}).get("score") or 0.0)
            matched_score = float(matched.get("score") or 0.0)
            topic_paper_count = int((topic_prior_ranked or {}).get("paper_count") or 0)
            if (
                topic_paper_count > 0
                and matched_score - topic_score < _TOPIC_PRIOR_MARGIN
            ):
                return {
                    "status": "matched",
                    "profile_id": int(topic_prior["profile_id"]),
                    "profile_name": str(topic_prior["profile_name"]),
                    "score": topic_score,
                    "note": (
                        f"Auto matched to existing profile '{topic_prior['profile_name']}' "
                        "because the topic prior already aligned with a mature profile."
                    ),
                    "ranked_profiles": ranked[:6],
                }

    if matched is not None:
        return {
            "status": "matched",
            "profile_id": int(matched["profile_id"]),
            "profile_name": str(matched["profile_name"]),
            "score": float(matched["score"]),
            "note": f"Auto matched to existing profile '{matched['profile_name']}'",
            "ranked_profiles": ranked[:6],
        }

    metadata = paper_notes.get("metadata") if isinstance(paper_notes, dict) else {}
    summary = _normalize_text(str(paper_notes.get("paper_summary") or ""))
    title = _normalize_text(str((metadata or {}).get("title_en") or "")) or "Untitled paper"
    description = summary or title
    created = mm.create_profile(_infer_profile_name(paper_context), description[:240])
    return {
        "status": "created",
        "profile_id": int(created["id"]),
        "profile_name": str(created["name"]),
        "score": 1.0,
        "note": f"Auto created profile '{created['name']}' for this paper",
        "ranked_profiles": ranked[:6],
    }
