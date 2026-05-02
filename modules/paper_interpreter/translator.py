"""Translation utilities for converting English content into polished Chinese output."""

from __future__ import annotations

import asyncio
import json
import threading
from contextvars import copy_context
from typing import Any

from utils.llm import call_lite_model, call_llm_fallback
from utils.logger import get_logger

log = get_logger(__name__)

_TRANSLATION_SYSTEM_PROMPT = (
    "You are a professional academic translator specializing in AI and computer science papers. "
    "Translate the provided English Markdown into fluent, reader-friendly Simplified Chinese while preserving structure exactly."
)

_REVIEW_SYSTEM_PROMPT = (
    "You are a senior Chinese academic editor reviewing a translated AI/CS paper interpretation. "
    "Your job is to correct terminology consistency, fluency, completeness, and Markdown formatting issues."
)

_MEMORY_TRANSLATION_SYSTEM_PROMPT = (
    "You are a bilingual research memory localization assistant for an AI paper analysis workspace. "
    "Your task is to translate short English memory fields into elegant, concise, reader-friendly Simplified Chinese for human display, "
    "while preserving the original meaning needed by a technical researcher."
)

_BRIEF_MARKDOWN_TRANSLATION_SYSTEM_PROMPT = (
    "You are a bilingual academic interface translator. "
    "Translate the provided short English Markdown summary into fluent, reader-friendly Simplified Chinese while preserving structure and evidence references."
)

_MEMORY_BATCH_SIZE = 8


def _strip_code_fence(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return payload


def _safe_json_loads(payload: str) -> Any:
    raw = _strip_code_fence(payload)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _glossary_terms_block(glossary: list[dict[str, str]] | None) -> str:
    if not glossary:
        return "- No glossary terms were pre-extracted."
    terms = [item.get("term", "") for item in glossary if isinstance(item, dict) and str(item.get("term", "")).strip()]
    if not terms:
        return "- No glossary terms were pre-extracted."
    return "\n".join(f"- {term}" for term in terms)


def _build_translation_prompt(
    english_md: str,
    glossary: list[dict[str, str]] | None = None,
    *,
    paper_title: str = "",
    paper_summary: str = "",
    style_guidance: str = "",
) -> str:
    glossary_block = _glossary_terms_block(glossary)
    context_block = ""
    if paper_title or paper_summary:
        context_block = (
            "Paper context (use this to inform terminology choices and tone):\n"
            f"- Title: {paper_title}\n"
            f"- Summary: {paper_summary}\n\n"
        )
    if style_guidance:
        context_block += f"Translation style and terminology guidance:\n{style_guidance}\n\n"
    return (
        "Translate the following complete English Markdown document into Simplified Chinese.\n\n"
        f"{context_block}"
        "Requirements:\n"
        "1. Preserve ALL Markdown structure exactly, including headings, bullet lists, blockquotes, tables, separators, and blank-line structure.\n"
        "2. Preserve ALL image paths and link targets exactly as they are. Do not alter the URL/path part inside parentheses.\n"
        "3. Translate the prose naturally into high-quality Chinese instead of doing literal sentence-by-sentence translation.\n"
        "4. When an important English technical term first appears in the main body, prefer the format Chinese (English), for example 注意力机制（Attention Mechanism）. After the first occurrence, you may use the Chinese name alone when natural.\n"
        "5. Convert metadata labels into Chinese: Generated at -> 生成时间, Original title -> 原文标题, Venue -> 发表期刊/会议, Publication date -> 发表时间, First/corresponding author institution -> 第一/通讯作者单位, Code repository -> 代码仓库, One-line summary -> 一句话总结.\n"
        "6. Convert section headings into these exact Chinese headings when they appear: \n"
        "   - ## 1. Research Background and Motivation -> ## 一、研究背景与动机\n"
        "   - ## 2. Core Method -> ## 二、核心方法详解\n"
        "   - ## 3. Experiments and Results -> ## 三、实验与结果分析\n"
        "   - ## 4. Ablation Studies -> ## 四、消融实验\n"
        "   - ## 5. Limitations and Future Directions -> ## 五、局限性与未来方向\n"
        "   - ## 6. Overall Assessment -> ## 六、总结与评价\n"
        "   - ## 1. Problem and Motivation -> ## 一、问题定义与研究动机\n"
        "   - ## 2. Method and Key Mechanisms -> ## 二、方法概览与关键机制\n"
        "   - ## 3. Results, Comparisons, and Ablations -> ## 三、结果、对比与消融\n"
        "   - ## 4. Conclusions, Limitations, and Takeaways -> ## 四、结论、局限与启示\n"
        "   - ## Glossary -> ## 专有名词解释\n"
        "7. Convert inline labels consistently: Evidence -> 证据, body text -> 正文, Caveats -> 需要注意, Warning -> 警告.\n"
        "8. In the glossary table, keep the term column in English and translate only the explanation column into Chinese. Also translate the table headers to `| 术语 | 解释 |`.\n"
        "9. Translate the top-level title into Chinese. If an exact official Chinese title is not known, produce a faithful natural Chinese rendering.\n"
        "10. Translate the closing disclaimer into natural Chinese.\n"
        "11. Do not add or remove content. Do not wrap the answer in code fences.\n\n"
        f"Glossary terms that should remain in English in the glossary table when present:\n{glossary_block}\n\n"
        f"English Markdown to translate:\n\n{english_md}"
    )


def _build_review_prompt(chinese_md: str, english_md: str) -> str:
    return (
        "Review the Chinese Markdown translation against the English original and output the full corrected Chinese Markdown.\n\n"
        "Checklist:\n"
        "1. Terminology consistency: the same English term should be translated consistently throughout the document.\n"
        "2. Fluency: the Chinese should read naturally and not sound like rigid machine translation.\n"
        "3. Completeness: no missing paragraphs, bullet points, image references, table rows, or evidence items compared with the English original.\n"
        "4. Markdown integrity: preserve all headings, blockquotes, bullet structure, separators, tables, and image/link paths exactly.\n"
        "5. Final formatting conventions should match the Chinese target format used by this project, including section titles and metadata labels.\n"
        "6. Output the full corrected Chinese Markdown only. Do not include explanations or code fences.\n\n"
        f"Chinese Markdown draft:\n\n{chinese_md}\n\n"
        "---ENGLISH ORIGINAL FOR REFERENCE---\n\n"
        f"{english_md}"
    )


def _build_memory_translation_prompt(
    items: list[dict[str, Any]],
    *,
    paper_context: str = "",
) -> str:
    context_block = ""
    if paper_context:
        context_block = f"Paper context (use this to inform terminology and tone):\n{paper_context}\n\n"
    return (
        "Translate the English memory fields inside each item into elegant Simplified Chinese for a Chinese research-user interface.\n\n"
        f"{context_block}"
        "Requirements:\n"
        "1. English is the source of truth; only translate the values under `fields`.\n"
        "2. Use natural, contextual Chinese instead of literal word-by-word translation.\n"
        "3. Keep technical abbreviations or canonical method names in English when that is clearer for researchers.\n"
        "4. When a Chinese translation benefits from retaining the original term, use concise forms like 中文（English） only when helpful.\n"
        "5. Preserve uncertainty, stance, comparison, causality, and scientific nuance. Do not overstate claims.\n"
        "6. Keep the output concise and suitable for cards, graph nodes, side panels, and timeline items.\n"
        "7. Return JSON only in the shape {\"items\": [{\"translations\": {\"field_name\": \"translated text\"}}]}.\n"
        "8. The number and order of returned items must exactly match the input items.\n"
        "9. If a field is empty, return an empty string for it.\n\n"
        f"Items:\n{json.dumps(items, ensure_ascii=False, indent=2)}"
    )


def _build_brief_markdown_translation_prompt(markdown_text: str, *, paper_context: str = "") -> str:
    context_block = f"Paper context:\n{paper_context}\n\n" if paper_context else ""
    return (
        "Translate the following short English Markdown summary into Simplified Chinese.\n\n"
        f"{context_block}"
        "Requirements:\n"
        "1. Preserve Markdown structure, bullet hierarchy, and blank lines.\n"
        "2. Preserve evidence references, page numbers, figure/table labels, and bracket formatting.\n"
        "3. Translate naturally for a Chinese research-user interface.\n"
        "4. Keep important technical abbreviations in English when clearer.\n"
        "5. Do not omit any detail. Do not summarize further.\n"
        "6. Output Markdown only, without code fences.\n\n"
        f"English Markdown:\n\n{markdown_text}"
    )


def _normalize_memory_translation_result(items: list[dict[str, Any]], payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return [{} for _ in items]
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return [{} for _ in items]
    normalized: list[dict[str, str]] = []
    for index, source in enumerate(items):
        translated_row = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
        translated_fields = translated_row.get("translations") if isinstance(translated_row.get("translations"), dict) else {}
        allowed_fields = source.get("fields") if isinstance(source.get("fields"), dict) else {}
        normalized.append(
            {
                key: str(translated_fields.get(key, "") or "").strip()
                for key in allowed_fields.keys()
            }
        )
    return normalized


async def translate_to_chinese(
    english_md: str,
    glossary: list[dict[str, str]] | None = None,
    *,
    paper_title: str = "",
    paper_summary: str = "",
    style_guidance: str = "",
) -> str:
    messages = [
        {"role": "system", "content": _TRANSLATION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_translation_prompt(
            english_md,
            glossary,
            paper_title=paper_title,
            paper_summary=paper_summary,
            style_guidance=style_guidance,
        )},
    ]
    translated = await call_lite_model(
        messages,
        temperature=0.2,
        max_tokens=16384,
    )
    cleaned = _strip_code_fence(translated)
    log.info("Translation output ready (%d chars)", len(cleaned))
    return cleaned


async def review_translation(chinese_md: str, english_md: str) -> str:
    messages = [
        {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": _build_review_prompt(chinese_md, english_md)},
    ]
    reviewed = await call_llm_fallback(
        ["gpt_pro"],
        messages,
        step_label="final markdown translation review",
        temperature=0.1,
        max_tokens=16384,
        step_timeout=300.0,
    )
    cleaned = _strip_code_fence(reviewed)
    log.info("Translation review output ready (%d chars)", len(cleaned))
    return cleaned


async def translate_brief_markdown_to_chinese(
    markdown_text: str,
    *,
    paper_context: str = "",
) -> str:
    messages = [
        {"role": "system", "content": _BRIEF_MARKDOWN_TRANSLATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_brief_markdown_translation_prompt(
                markdown_text,
                paper_context=paper_context,
            ),
        },
    ]
    translated = await call_lite_model(
        messages,
        temperature=0.1,
        max_tokens=8192,
    )
    cleaned = _strip_code_fence(translated)
    log.info("Brief markdown translation output ready (%d chars)", len(cleaned))
    return cleaned


async def _translate_memory_sub_batch(
    sub_items: list[dict[str, Any]],
    step_label: str,
    *,
    paper_context: str = "",
) -> list[dict[str, str]]:
    """Translate a single sub-batch of memory items via the Lite model."""
    messages = [
        {"role": "system", "content": _MEMORY_TRANSLATION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_memory_translation_prompt(sub_items, paper_context=paper_context)},
    ]
    response = await call_lite_model(
        messages,
        temperature=0.1,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    payload = _safe_json_loads(response)
    translations = _normalize_memory_translation_result(sub_items, payload)
    if len(translations) != len(sub_items):
        log.warning("Unexpected memory translation batch size for %s", step_label)
        return [{} for _ in sub_items]
    return translations


async def translate_memory_batch(
    items: list[dict[str, Any]],
    *,
    step_label: str = "memory localization",
    paper_context: str = "",
) -> list[dict[str, str]]:
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        normalized_fields = {
            str(key): str(value or "").strip()
            for key, value in fields.items()
        }
        if not normalized_fields:
            normalized_items.append({"kind": str(item.get("kind", "memory")), "fields": {}, "context": item.get("context", {})})
            continue
        normalized_items.append(
            {
                "kind": str(item.get("kind", "memory")),
                "fields": normalized_fields,
                "context": item.get("context", {}),
            }
        )
    if not normalized_items:
        return []

    if len(normalized_items) <= _MEMORY_BATCH_SIZE:
        return await _translate_memory_sub_batch(normalized_items, step_label, paper_context=paper_context)

    chunks = [
        normalized_items[i : i + _MEMORY_BATCH_SIZE]
        for i in range(0, len(normalized_items), _MEMORY_BATCH_SIZE)
    ]
    log.info("Splitting %d memory items into %d parallel batches for %s",
             len(normalized_items), len(chunks), step_label)
    sub_results = await asyncio.gather(
        *[_translate_memory_sub_batch(chunk, f"{step_label} [{idx+1}/{len(chunks)}]", paper_context=paper_context)
          for idx, chunk in enumerate(chunks)]
    )
    merged: list[dict[str, str]] = []
    for sub in sub_results:
        merged.extend(sub)
    return merged


async def translate_memory_item(
    kind: str,
    fields: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    step_label: str = "memory localization item",
    paper_context: str = "",
) -> dict[str, str]:
    translated = await translate_memory_batch(
        [
            {
                "kind": kind,
                "fields": fields,
                "context": context or {},
            }
        ],
        step_label=step_label,
        paper_context=paper_context,
    )
    return translated[0] if translated else {}


def _run_async_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Any = None
    error: Exception | None = None

    ctx = copy_context()

    def runner() -> None:
        nonlocal result, error
        try:
            result = ctx.run(asyncio.run, coro)
        except Exception as exc:  # pragma: no cover - defensive bridge for sync call sites
            error = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def translate_memory_batch_sync(
    items: list[dict[str, Any]],
    *,
    step_label: str = "memory localization",
    paper_context: str = "",
) -> list[dict[str, str]]:
    try:
        return _run_async_blocking(translate_memory_batch(items, step_label=step_label, paper_context=paper_context))
    except Exception as exc:
        log.warning("Memory localization failed for %s: %s", step_label, exc)
        return [{} for _ in items]


def translate_memory_item_sync(
    kind: str,
    fields: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    step_label: str = "memory localization item",
    paper_context: str = "",
) -> dict[str, str]:
    try:
        return _run_async_blocking(translate_memory_item(kind, fields, context=context, step_label=step_label, paper_context=paper_context))
    except Exception as exc:
        log.warning("Memory localization failed for %s: %s", step_label, exc)
        return {str(key): "" for key in fields.keys()}
