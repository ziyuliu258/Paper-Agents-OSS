from __future__ import annotations

import json
import re
from typing import Any

from utils.llm import call_llm_fallback
from utils.logger import get_logger

log = get_logger(__name__)

_ASCII_RE = re.compile(r"[A-Za-z]")
_JSON_RE = re.compile(r"\{.*\}", re.S)
_HEURISTIC_HINTS = {
    "时间序列": ["time series"],
    "时序": ["time series", "temporal"],
    "预测": ["forecasting", "prediction"],
    "多变量": ["multivariate"],
    "单变量": ["univariate"],
    "长期": ["long-term"],
    "短期": ["short-term"],
    "通道依赖": ["channel dependency", "channel dependence"],
    "通道独立": ["channel independent", "channel independence"],
    "检索增强生成": ["retrieval augmented generation", "RAG"],
    "检索增强": ["retrieval augmented generation", "retrieval"],
    "智能体": ["agent", "agentic"],
    "推理": ["reasoning"],
    "分割": ["segmentation"],
    "检测": ["detection"],
    "跟踪": ["tracking"],
    "生成": ["generation"],
}


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _has_ascii_search_hint(value: str) -> bool:
    return bool(_ASCII_RE.search(str(value or "")))


def topic_needs_enrichment(topic: dict[str, Any]) -> bool:
    query = _normalize_text(str(topic.get("query") or ""))
    query_en = _normalize_text(str(topic.get("query_en") or ""))
    existing_keywords = [
        _normalize_text(str(item))
        for item in topic.get("keywords", []) or []
        if _normalize_text(str(item))
    ]
    auto_keywords = [
        _normalize_text(str(item))
        for item in topic.get("auto_keywords", []) or []
        if _normalize_text(str(item))
    ]
    searchable_values = [query, query_en, *existing_keywords, *auto_keywords]
    ascii_values = [value for value in searchable_values if _has_ascii_search_hint(value)]
    return len(ascii_values) < 2


def _heuristic_keywords(topic: dict[str, Any]) -> list[str]:
    source_text = "\n".join(
        _normalize_text(str(value))
        for value in (
            topic.get("name"),
            topic.get("query"),
        )
        if _normalize_text(str(value))
    )
    hints: list[str] = []
    for marker, aliases in _HEURISTIC_HINTS.items():
        if marker in source_text:
            hints.extend(aliases)
    return _dedupe_keep_order(hints)


def _extract_json_payload(raw_text: str) -> dict[str, Any]:
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    attempts = [cleaned]
    match = _JSON_RE.search(cleaned)
    if match:
        attempts.append(match.group(0))
    for attempt in attempts:
        try:
            payload = json.loads(attempt)
        except Exception:
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Could not parse topic enrichment JSON payload")


async def _llm_topic_hints(
    topic: dict[str, Any], *, model_alias: str
) -> tuple[str, list[str]]:
    name = _normalize_text(str(topic.get("name") or ""))
    query = _normalize_text(str(topic.get("query") or ""))
    existing_keywords = _dedupe_keep_order(
        [
            _normalize_text(str(item))
            for item in topic.get("keywords", []) or []
            if _normalize_text(str(item))
        ]
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You convert research topics into compact English search hints for academic paper discovery. "
                "Return JSON only with keys `english_query` and `keywords`."
            ),
        },
        {
            "role": "user",
            "content": (
                "Given the following topic, generate a concise English search query and 4-8 English keyword phrases.\n"
                "Rules:\n"
                "1. Focus on terms likely to appear in paper titles or abstracts.\n"
                "2. Prefer established task names, problem names, or model-family descriptors.\n"
                "3. Avoid generic words like AI, deep learning, model, paper.\n"
                "4. Keep each keyword to 1-5 words.\n"
                "5. Do not repeat existing English keywords.\n"
                "6. Respond with JSON only.\n\n"
                f"Topic name: {name or '(empty)'}\n"
                f"Topic description: {query or '(empty)'}\n"
                f"Existing keywords: {', '.join(existing_keywords) if existing_keywords else '(none)'}\n\n"
                'Output schema: {"english_query":"...", "keywords":["...", "..."]}'
            ),
        },
    ]
    raw = await call_llm_fallback(
        [model_alias, "gpt_pro"],
        messages,
        step_label=f"topic enrichment {name or query or 'topic'}",
        temperature=0.1,
        max_tokens=512,
        step_timeout=60.0,
    )
    payload = _extract_json_payload(raw)
    english_query = _normalize_text(str(payload.get("english_query") or ""))
    keywords = _dedupe_keep_order(
        [
            _normalize_text(str(item))
            for item in payload.get("keywords", []) or []
            if _normalize_text(str(item))
        ]
    )
    return english_query, keywords


async def enrich_topics_for_search(
    topics: list[dict[str, Any]],
    *,
    model_alias: str = "gem_flash",
) -> list[dict[str, Any]]:
    enriched_topics: list[dict[str, Any]] = []
    for raw_topic in topics:
        topic = dict(raw_topic or {})
        name = _normalize_text(str(topic.get("name") or ""))
        query = _normalize_text(str(topic.get("query") or ""))
        existing_keywords = _dedupe_keep_order(
            [
                _normalize_text(str(item))
                for item in topic.get("keywords", []) or []
                if _normalize_text(str(item))
            ]
        )
        heuristic_keywords = _heuristic_keywords(topic)
        english_query = _normalize_text(str(topic.get("query_en") or ""))
        llm_keywords: list[str] = []
        llm_used = False

        seed_topic = {
            **topic,
            "keywords": existing_keywords + heuristic_keywords,
            "query_en": english_query,
        }
        if topic_needs_enrichment(seed_topic) and (name or query):
            try:
                generated_query, generated_keywords = await _llm_topic_hints(
                    seed_topic,
                    model_alias=model_alias,
                )
                if generated_query:
                    english_query = generated_query
                llm_keywords = generated_keywords
                llm_used = bool(generated_query or generated_keywords)
            except Exception as exc:
                log.warning(
                    "Topic enrichment fallback to heuristics for '%s': %s",
                    name or query or "topic",
                    exc,
                )

        merged_keywords = _dedupe_keep_order(
            [
                *existing_keywords,
                *heuristic_keywords,
                *llm_keywords,
            ]
        )
        topic["keywords"] = merged_keywords
        if english_query:
            topic["query_en"] = english_query
        if llm_keywords:
            topic["auto_keywords"] = llm_keywords
        if heuristic_keywords:
            topic["heuristic_keywords"] = heuristic_keywords
        if llm_used:
            topic["topic_enrichment_applied"] = "llm"
        elif heuristic_keywords:
            topic["topic_enrichment_applied"] = "heuristic"
        elif merged_keywords != existing_keywords:
            topic["topic_enrichment_applied"] = "merged"
        enriched_topics.append(topic)
    return enriched_topics
