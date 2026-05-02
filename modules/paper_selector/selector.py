from __future__ import annotations

import json
from typing import Any

from utils.llm import call_llm


def _build_prompt(candidates: list[dict[str, Any]], topics: list[dict[str, Any]], memory_context: str = "") -> str:
    topic_text = "\n".join(f"- {topic.get('name')}: {topic.get('query')} | keywords={'; '.join(topic.get('keywords', []) or [])}" for topic in topics)
    candidate_blocks = []
    for item in candidates:
        candidate_blocks.append(
            json.dumps(
                {
                    "paper_id": item.get("paper_id"),
                    "title": item.get("title"),
                    "abstract": item.get("abstract"),
                    "aic_text": item.get("aic_text", "")[:3000],
                    "venue": item.get("venue"),
                    "date": item.get("date"),
                    "citations": item.get("citations"),
                    "semantic_score": item.get("semantic_score", 0.0),
                    "source": item.get("source"),
                    "pdf_url": bool(item.get("pdf_url")),
                },
                ensure_ascii=False,
            )
        )
    joined_candidates = "\n".join(candidate_blocks)
    memory_block = f"\nProfile research memory:\n{memory_context}\n" if memory_context.strip() else ""
    return (
        "你是论文筛选助手。请根据用户关注主题，从候选论文中选择最值得生成报告的唯一一篇。\n"
        "用户主题：\n"
        f"{topic_text}\n"
        f"{memory_block}\n"
        "候选论文：\n"
        f"{joined_candidates}\n\n"
        "优先选择语义最相关、问题明确、信息充分且可下载 PDF 的论文。"
        "如果 profile memory 中已经有较成熟的共识/争议/演化脉络，请优先选择能够补足空白、推进争议裁决、或补充方法演化关键节点的论文。"
        "如果 profile memory 中仍有待人工裁决的冲突，也可优先考虑对冲突有直接证据价值的论文。\n"
        "请只返回 JSON 对象，格式为："
        '{"paper_id":"...","selection_reason":"..."}'
    )


async def select_top_paper(
    candidates: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    model_alias: str = "gem_flash",
    *,
    memory_context: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not candidates:
        raise ValueError("没有可供选择的候选论文")

    original_candidates = candidates
    eligible_candidates = [item for item in candidates if item.get("pdf_url")]
    if eligible_candidates:
        candidates = eligible_candidates

    selector_meta: dict[str, Any] = {
        "model_alias": model_alias,
        "candidate_count": len(original_candidates),
        "eligible_candidate_count": len(candidates),
        "eligible_candidates": [
            {
                "paper_id": item.get("paper_id"),
                "title": item.get("title"),
                "semantic_score": item.get("semantic_score", 0.0),
                "topic_fit_score": item.get("topic_fit_score", 0.0),
                "pdf_url": bool(item.get("pdf_url")),
            }
            for item in candidates
        ],
        "method": "",
        "llm_error": "",
        "llm_response": "",
        "fallback_basis": "",
    }

    prompt = _build_prompt(candidates, topics, memory_context)
    try:
        response = await call_llm(
            model_alias,
            [{"role": "user", "content": prompt}],
            reasoning_effort=None,
            temperature=0.1,
        )
        text = response.strip()
        selector_meta["llm_response"] = text[:2000]
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            result = json.loads(text[start:end + 1])
        paper_id = result.get("paper_id")
        for item in candidates:
            if item.get("paper_id") == paper_id:
                selected = dict(item)
                selected["selection_reason"] = result.get("selection_reason", "")
                selector_meta["method"] = "llm"
                return selected, selector_meta
        selector_meta["llm_error"] = f"LLM returned unknown paper_id: {paper_id!r}"
    except Exception as exc:
        selector_meta["llm_error"] = f"{type(exc).__name__}: {exc}"
        pass

    fallback = dict(
        max(
            candidates,
            key=lambda item: (
                item.get("topic_fit_score", 0.0),
                item.get("semantic_score", 0.0),
            ),
        )
    )
    fallback["selection_reason"] = (
        fallback.get("selection_reason")
        or "LLM 精选失败，已回退到可下载候选中的主题匹配最高分论文"
    )
    selector_meta["method"] = "fallback"
    selector_meta["fallback_basis"] = (
        "highest_topic_fit_score_then_semantic_score_among_selectable_candidates"
    )
    return fallback, selector_meta
