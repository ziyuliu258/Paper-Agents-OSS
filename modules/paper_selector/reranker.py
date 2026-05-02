from __future__ import annotations

import re
from typing import Any

from utils.embedding import cosine_similarity, embed_texts
from utils.logger import get_logger

log = get_logger(__name__)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _topic_fragments(topic: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for field in ("name", "query", "query_en"):
        value = _normalize_text(str(topic.get(field) or ""))
        if value:
            fragments.append(value)
    for raw_keyword in (
        *(topic.get("keywords", []) or []),
        *(topic.get("auto_keywords", []) or []),
        *(topic.get("heuristic_keywords", []) or []),
    ):
        keyword = _normalize_text(str(raw_keyword))
        if keyword:
            fragments.append(keyword)
    return fragments


def _meaningful_memory_fragments(memory_context: str) -> list[str]:
    fragments: list[str] = []
    for raw_line in str(memory_context or "").splitlines():
        line = _normalize_text(raw_line)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        lowered = line.lower()
        if lowered == "profile research memory for paper selection":
            continue
        fragments.append(line)
    return fragments


def _build_topic_text(topic: dict[str, Any]) -> str:
    name = _normalize_text(str(topic.get("name") or ""))
    query = _normalize_text(str(topic.get("query") or ""))
    keywords = [_normalize_text(str(keyword)) for keyword in topic.get("keywords", []) if _normalize_text(str(keyword))]

    sections: list[str] = []
    if name:
        sections.append(f"Topic: {name}")
    if query:
        sections.append(f"Description: {query}")
    if keywords:
        sections.append(f"Keywords: {'; '.join(keywords)}")
    return "\n".join(sections) or name or query or "; ".join(keywords)


def _build_candidate_text(candidate: dict[str, Any]) -> str:
    title = _normalize_text(str(candidate.get("title") or ""))
    abstract = _normalize_text(str(candidate.get("abstract") or ""))
    venue = _normalize_text(str(candidate.get("venue") or ""))
    return "\n\n".join(part for part in [title, abstract, f"Venue: {venue}" if venue else ""] if part)


def _keyword_overlap_score(
    candidate_text: str,
    topics: list[dict[str, Any]],
    _memory_context: str = "",
) -> float:
    candidate_text_lower = candidate_text.lower()
    keyword_phrases: list[str] = []
    keyword_tokens: set[str] = set()

    for topic in topics:
        for fragment in _topic_fragments(topic):
            normalized = fragment.lower()
            if not normalized:
                continue
            keyword_phrases.append(normalized)
            keyword_tokens.update(token for token in _TOKEN_RE.findall(normalized) if len(token) >= 3)

    if not keyword_phrases and not keyword_tokens:
        return 0.0

    phrase_hits = sum(1 for phrase in keyword_phrases if phrase and phrase in candidate_text_lower)
    candidate_tokens = set(token.lower() for token in _TOKEN_RE.findall(candidate_text_lower) if len(token) >= 3)
    token_hits = len(candidate_tokens & keyword_tokens)

    phrase_score = phrase_hits / max(len(keyword_phrases), 1)
    token_score = token_hits / max(len(keyword_tokens), 1)
    return max(phrase_score, token_score)


async def rerank_candidates(
    candidates: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    top_k: int,
    min_score: float = 0.0,
    *,
    memory_context: str = "",
) -> list[dict[str, Any]]:
    if not candidates or not topics:
        return candidates[:top_k]

    topic_texts = [text for topic in topics if (text := _build_topic_text(topic))]
    meaningful_memory = "\n".join(_meaningful_memory_fragments(memory_context)).strip()
    if meaningful_memory:
        topic_texts.append(f"Profile research memory:\n{meaningful_memory}")
    candidate_texts = [_build_candidate_text(item) for item in candidates]
    if not topic_texts:
        return candidates[:top_k]

    embeddings = await embed_texts(topic_texts + candidate_texts)
    if len(embeddings) != len(topic_texts) + len(candidate_texts):
        log.warning("Embedding count mismatch during rerank, falling back to lexical ordering")
        return candidates[:top_k]

    topic_vectors = embeddings[: len(topic_texts)]
    candidate_vectors = embeddings[len(topic_texts):]

    for candidate, text, vector in zip(candidates, candidate_texts, candidate_vectors):
        embedding_score = max(cosine_similarity(topic_vec, vector) for topic_vec in topic_vectors)
        keyword_score = _keyword_overlap_score(text, topics)
        memory_hint_score = 0.0
        if meaningful_memory:
            memory_tokens = set(
                token.lower()
                for token in _TOKEN_RE.findall(meaningful_memory)
                if len(token) >= 3
            )
            candidate_tokens = set(
                token.lower() for token in _TOKEN_RE.findall(text) if len(token) >= 3
            )
            if memory_tokens and candidate_tokens:
                memory_hint_score = len(memory_tokens & candidate_tokens) / max(
                    len(memory_tokens), 1
                )
        candidate["embedding_score"] = embedding_score
        candidate["keyword_overlap_score"] = keyword_score
        candidate["memory_hint_score"] = memory_hint_score
        candidate["semantic_score"] = 0.7 * embedding_score + 0.3 * keyword_score

    ranked = sorted(
        candidates,
        key=lambda item: (
            item.get("semantic_score", 0.0),
            item.get("memory_hint_score", 0.0),
        ),
        reverse=True,
    )
    if min_score > 0:
        filtered = [item for item in ranked if item.get("semantic_score", 0.0) >= min_score]
        if filtered:
            ranked = filtered
        elif ranked:
            best_score = float(ranked[0].get("semantic_score", 0.0))
            log.warning(
                "All %d candidates fell below min_score=%.3f during reranking; falling back to top %d candidates (best_score=%.3f)",
                len(ranked),
                min_score,
                top_k,
                best_score,
            )
    return ranked[:top_k]
