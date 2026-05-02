"""Interactive report refinement with file-backed report variants."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from utils.llm import call_llm_fallback
from utils.logger import get_logger
from utils.markdown import save_markdown
from utils.report_styles import (
    normalize_report_detail_level,
    normalize_report_structure_mode,
    normalize_report_target_structure_mode,
    render_structure_heading_spec,
)
from utils.repo_paths import to_repo_relative_path

log = get_logger(__name__)

_PLAN_ACTION_TYPES = {
    "locate",
    "ground",
    "restructure",
    "expand",
    "condense",
    "polish",
}
_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "background": ("background", "motivation", "problem", "背景", "动机", "问题", "引言"),
    "method": ("method", "approach", "pipeline", "framework", "方法", "机制", "架构", "流程"),
    "experiments": ("experiment", "result", "results", "benchmark", "实验", "结果", "性能", "指标"),
    "ablation": ("ablation", "消融"),
    "conclusion": ("conclusion", "summary", "takeaway", "limitations", "局限", "总结", "结论", "启示"),
    "glossary": ("glossary", "term", "术语", "名词"),
}


def _strip_code_fence(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return payload


def _safe_json_loads(text: str) -> Any:
    payload = _strip_code_fence(text)
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(payload[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _clip_text(text: str, max_chars: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_title_from_markdown(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("# "):
                    return stripped[2:].strip()
    except Exception:
        pass
    return path.stem


def _detect_language(markdown_text: str) -> str:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", markdown_text))
    ascii_letters = len(re.findall(r"[A-Za-z]", markdown_text))
    return "zh" if chinese_chars >= max(12, ascii_letters // 3) else "en"


def _infer_section_key(title: str) -> str:
    normalized = str(title or "").strip().lower()
    for section_key, keywords in _SECTION_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return section_key
    return "other"


def _extract_sections(markdown_text: str) -> list[dict[str, Any]]:
    lines = markdown_text.splitlines()
    sections: list[dict[str, Any]] = []
    current_title = "Preamble"
    current_level = 0
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_body
        body = "\n".join(current_body).strip()
        if body or current_title != "Preamble":
            sections.append(
                {
                    "title": current_title,
                    "level": current_level,
                    "key": _infer_section_key(current_title),
                    "body": body,
                    "preview": _clip_text(body.replace("\n", " "), 220),
                }
            )
        current_body = []

    for line in lines:
        match = re.match(r"^(#{2,4})\s+(.+?)\s*$", line)
        if match:
            flush()
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            continue
        current_body.append(line)

    flush()
    return sections


def _score_section(description: str, section: dict[str, Any]) -> int:
    score = 0
    normalized_description = description.lower()
    title = str(section.get("title") or "").lower()
    body = str(section.get("preview") or "").lower()
    if title and title in normalized_description:
        score += 6
    section_key = str(section.get("key") or "other")
    for keyword in _SECTION_KEYWORDS.get(section_key, ()):
        if keyword in normalized_description:
            score += 2
        if keyword in title:
            score += 1
        if keyword in body:
            score += 1
    return score


def _locate_relevant_sections(description: str, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sections:
        return []
    scored = [
        (section, _score_section(description, section))
        for section in sections
        if int(section.get("level") or 0) <= 3
    ]
    matched = [section for section, score in scored if score > 0]
    if matched:
        unique: dict[str, dict[str, Any]] = {}
        for section in matched:
            unique[str(section.get("title") or "")] = section
        return list(unique.values())[:4]
    return sections[: min(4, len(sections))]


def _build_section_outline(sections: list[dict[str, Any]]) -> str:
    if not sections:
        return "- No named sections detected."
    return "\n".join(
        f"- {section['title']} [{section['key']}]: {section['preview'] or '(empty section)'}"
        for section in sections[:12]
    )


def _build_working_memory_excerpt(working_memory_path: Path, relevant_sections: list[dict[str, Any]]) -> str:
    payload = _load_json(working_memory_path)
    if not payload:
        return ""

    section_keys = {str(item.get("key") or "") for item in relevant_sections if str(item.get("key") or "")}
    observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
    draft_claims = payload.get("draft_claims") if isinstance(payload.get("draft_claims"), list) else []
    open_questions = payload.get("open_questions") if isinstance(payload.get("open_questions"), list) else []
    promotion_candidates = payload.get("promotion_candidates") if isinstance(payload.get("promotion_candidates"), list) else []
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}

    lines = []
    if metrics:
        retrieved_claims = metrics.get("retrieved_claim_count")
        retrieved_evidence = metrics.get("retrieved_evidence_count")
        if isinstance(retrieved_claims, (int, float)) or isinstance(retrieved_evidence, (int, float)):
            lines.append(
                f"- Retrieval footprint: claims={int(retrieved_claims or 0)} evidence={int(retrieved_evidence or 0)}"
            )

    filtered_observations = [
        item for item in observations
        if not section_keys or str(item.get("section_key") or "") in section_keys
    ]
    for item in filtered_observations[:5]:
        summary = _clip_text(str(item.get("summary") or ""), 220)
        if not summary:
            continue
        section_key = str(item.get("section_key") or "other")
        kind = str(item.get("kind") or "observation")
        lines.append(f"- Observation [{section_key}/{kind}]: {summary}")

    filtered_claims = [
        item for item in draft_claims
        if not section_keys or str(item.get("section_key") or "") in section_keys
    ]
    for item in filtered_claims[:4]:
        claim = _clip_text(str(item.get("claim") or ""), 220)
        if not claim:
            continue
        section_key = str(item.get("section_key") or "other")
        lines.append(f"- Draft claim [{section_key}]: {claim}")

    filtered_questions = [
        item for item in open_questions
        if str(item.get("status") or "") in {"", "open"}
        and (not section_keys or str(item.get("section_key") or "") in section_keys)
    ]
    for item in filtered_questions[:3]:
        question = _clip_text(str(item.get("question") or ""), 220)
        reason = _clip_text(str(item.get("reason") or ""), 120)
        if question:
            suffix = f" ({reason})" if reason else ""
            lines.append(f"- Open question: {question}{suffix}")

    for item in promotion_candidates[:4]:
        if str(item.get("status") or "") not in {"accepted", "review_required"}:
            continue
        payload_fields = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        text = _clip_text(
            str(payload_fields.get("summary") or payload_fields.get("body") or payload_fields.get("title") or ""),
            220,
        )
        if text:
            lines.append(f"- Promotion candidate [{item.get('status') or 'candidate'}]: {text}")

    return "\n".join(lines)


def _default_plan(
    *,
    instruction: str,
    target_structure_mode: str,
    detail_level: str,
) -> dict[str, Any]:
    actions: list[dict[str, str]] = [
        {"type": "locate", "description": f"Identify the sections most relevant to: {instruction}"},
        {"type": "ground", "description": "Collect grounded evidence from the current report and saved memory artifacts."},
    ]
    if target_structure_mode != "preserve":
        actions.append({"type": "restructure", "description": f"Rewrite the report into the {target_structure_mode} narrative structure."})
    if detail_level == "concise":
        actions.append({"type": "condense", "description": "Shorten repetitive detail while keeping the strongest evidence."})
    elif detail_level == "detailed":
        actions.append({"type": "expand", "description": "Expand key explanations with grounded technical details."})
    actions.append({"type": "polish", "description": "Improve narrative flow and readability without changing facts."})
    return {
        "label": "",
        "reasoning_summary": "Fallback plan",
        "actions": actions[:5],
    }


def _normalize_plan(
    payload: Any,
    *,
    instruction: str,
    target_structure_mode: str,
    detail_level: str,
) -> dict[str, Any]:
    fallback = _default_plan(
        instruction=instruction,
        target_structure_mode=target_structure_mode,
        detail_level=detail_level,
    )
    if not isinstance(payload, dict):
        return fallback

    label = _clip_text(str(payload.get("label") or "").strip(), 48)
    reasoning_summary = _clip_text(str(payload.get("reasoning_summary") or payload.get("summary") or "").strip(), 240)
    raw_actions = payload.get("actions")
    actions: list[dict[str, str]] = []
    if isinstance(raw_actions, list):
        for raw_action in raw_actions[:5]:
            if not isinstance(raw_action, dict):
                continue
            action_type = str(raw_action.get("type") or "").strip().lower()
            if action_type not in _PLAN_ACTION_TYPES:
                continue
            description = _clip_text(str(raw_action.get("description") or "").strip(), 220)
            if not description:
                continue
            actions.append({"type": action_type, "description": description})

    normalized = {
        "label": label,
        "reasoning_summary": reasoning_summary or fallback["reasoning_summary"],
        "actions": actions or fallback["actions"],
    }

    action_types = {action["type"] for action in normalized["actions"]}
    if target_structure_mode != "preserve" and "restructure" not in action_types:
        normalized["actions"].append(
            {"type": "restructure", "description": f"Rewrite the report into the {target_structure_mode} structure."}
        )
    if detail_level == "concise" and "condense" not in action_types:
        normalized["actions"].append(
            {"type": "condense", "description": "Condense repetitive detail and keep the most decision-relevant content."}
        )
    if detail_level == "detailed" and "expand" not in action_types:
        normalized["actions"].append(
            {"type": "expand", "description": "Expand key explanations in the most relevant sections with grounded detail."}
        )
    if "polish" not in {action["type"] for action in normalized["actions"]}:
        normalized["actions"].append(
            {"type": "polish", "description": "Polish clarity, transitions, and section-level readability."}
        )
    normalized["actions"] = normalized["actions"][:5]
    return normalized


def _build_plan_prompt(
    *,
    instruction: str,
    target_structure_mode: str,
    detail_level: str,
    source_structure_mode: str,
    section_outline: str,
) -> str:
    return (
        "Plan a bounded ReAct-style refinement for a paper-interpretation markdown report.\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "label": "short variant label",\n'
        '  "reasoning_summary": "1-2 sentence plan summary",\n'
        '  "actions": [\n'
        '    {"type": "locate|ground|restructure|expand|condense|polish", "description": "what the step should accomplish"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "1. Use 3-5 actions.\n"
        "2. Prefer locate + ground first.\n"
        "3. Add restructure only when the target structure differs from the source structure or the user explicitly asks for a new structure.\n"
        "4. Add expand when the user asks for more detail; add condense when the user asks for simplification.\n"
        "5. Keep the label short and user-facing.\n\n"
        f"User instruction:\n{instruction}\n\n"
        f"Source structure mode: {source_structure_mode}\n"
        f"Target structure mode: {target_structure_mode}\n"
        f"Requested detail level: {detail_level}\n\n"
        f"Current section outline:\n{section_outline}"
    )


def _render_detail_level_guidance(detail_level: str) -> str:
    normalized = normalize_report_detail_level(detail_level)
    if normalized == "concise":
        return "Condense repetitive wording, keep the clearest evidence, and bias toward scan-friendly explanations."
    if normalized == "detailed":
        return "Expand key reasoning steps, experimental interpretation, and caveats where the sources provide enough support."
    if normalized == "auto":
        return "Let the user's instruction dominate the granularity choice while staying grounded."
    return "Keep a balanced density: clearer than the original, but not longer unless needed for fidelity."


def _build_observations_block(
    *,
    plan: dict[str, Any],
    relevant_sections: list[dict[str, Any]],
    working_memory_excerpt: str,
    distilled_summary: str,
    target_structure_mode: str,
    language: str,
) -> str:
    lines = []
    for action in plan.get("actions", []):
        action_type = str(action.get("type") or "")
        description = str(action.get("description") or "")
        lines.append(f"- Action `{action_type}`: {description}")
        if action_type == "locate":
            if relevant_sections:
                for section in relevant_sections:
                    lines.append(
                        f"  Relevant section -> {section['title']}: {section['preview'] or '(empty section)'}"
                    )
            else:
                lines.append("  Relevant section -> No explicit section match.")
        elif action_type == "ground":
            if distilled_summary:
                lines.append(f"  Distilled summary -> {_clip_text(distilled_summary, 1200)}")
            if working_memory_excerpt:
                lines.append(f"  Working memory -> {_clip_text(working_memory_excerpt, 1800)}")
        elif action_type == "restructure":
            lines.append(
                f"  Heading spec -> {render_structure_heading_spec(target_structure_mode, language=language)}"
            )
    return "\n".join(lines)


def _build_rewrite_prompt(
    *,
    base_markdown: str,
    base_title: str,
    base_language: str,
    source_structure_mode: str,
    target_structure_mode: str,
    detail_level: str,
    instruction: str,
    plan: dict[str, Any],
    tool_observations: str,
    distilled_summary: str,
    working_memory_excerpt: str,
    english_source_report: str,
) -> str:
    structure_guidance = (
        "Keep the existing top-level structure unless the user clearly requests a reorganization."
        if target_structure_mode == "preserve"
        else render_structure_heading_spec(target_structure_mode, language=base_language)
    )
    distilled_block = _clip_text(distilled_summary, 5000)
    working_memory_block = _clip_text(working_memory_excerpt, 5000)
    english_source_block = _clip_text(english_source_report, 14000)
    return (
        "You are the final synthesis step of a ReAct-style report editor for an academic paper interpretation workspace.\n\n"
        "Rewrite the complete markdown report according to the user's instruction.\n\n"
        "Hard constraints:\n"
        "1. Preserve factual accuracy. Use only information grounded in the current report, distilled summary, working memory excerpt, and optional English source report.\n"
        "2. Preserve all image paths and link targets exactly as they appear inside parentheses.\n"
        "3. Output a full markdown document, not a diff and not an explanation.\n"
        "4. Keep the output language the same as the current report.\n"
        "5. Preserve the title, metadata block, and closing disclaimer unless the user explicitly requests a presentational change that requires rewording them.\n"
        "6. If the user asks for more detail, elaborate only where the grounded context supports it. If the user asks for simplification, compress without erasing key evidence or caveats.\n"
        "7. If a requested addition is not supported by the grounded context, stay conservative rather than inventing content.\n\n"
        f"Current report title: {base_title}\n"
        f"Current report language: {base_language}\n"
        f"Source structure mode: {source_structure_mode}\n"
        f"Target structure mode: {target_structure_mode}\n"
        f"Detail-level guidance: {_render_detail_level_guidance(detail_level)}\n"
        f"Structure guidance: {structure_guidance}\n\n"
        f"User instruction:\n{instruction}\n\n"
        f"Plan summary:\n{plan.get('reasoning_summary', '')}\n\n"
        f"Tool observations:\n{tool_observations or '- No tool observations were produced.'}\n\n"
        f"Distilled summary:\n{distilled_block or '(not available)'}\n\n"
        f"Working memory excerpt:\n{working_memory_block or '(not available)'}\n\n"
        f"English source report:\n{english_source_block or '(not available)'}\n\n"
        f"Current markdown report:\n\n{base_markdown}"
    )


def _variant_summary_from_path(
    variant_path: Path,
    *,
    variant_id: str,
    label: str,
    kind: str,
    instruction: str,
    structure_mode: str,
    detail_level: str,
    source_variant_id: str,
    created_at: float,
) -> dict[str, Any]:
    stat = variant_path.stat()
    return {
        "variant_id": variant_id,
        "label": label,
        "kind": kind,
        "instruction": instruction,
        "structure_mode": normalize_report_structure_mode(structure_mode),
        "detail_level": normalize_report_detail_level(detail_level, default="balanced"),
        "source_variant_id": source_variant_id,
        "report_path": to_repo_relative_path(variant_path),
        "created_at": created_at,
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def list_report_variants(
    *,
    job_id: str,
    original_report_path: Path,
    original_structure_mode: str,
) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    if original_report_path.exists():
        variants.append(
            _variant_summary_from_path(
                original_report_path,
                variant_id="original",
                label="Original",
                kind="original",
                instruction="",
                structure_mode=original_structure_mode,
                detail_level="balanced",
                source_variant_id="",
                created_at=original_report_path.stat().st_mtime,
            )
        )

    variants_dir = original_report_path.parent / "variants"
    if not variants_dir.exists():
        return variants

    refined_variants: list[dict[str, Any]] = []
    for meta_path in sorted(variants_dir.glob("*.json")):
        meta = _load_json(meta_path)
        variant_id = str(meta.get("variant_id") or meta_path.stem).strip() or meta_path.stem
        variant_path = variants_dir / f"{variant_id}.md"
        if not variant_path.exists():
            variant_path = variants_dir / f"{meta_path.stem}.md"
        if not variant_path.exists():
            continue
        refined_variants.append(
            _variant_summary_from_path(
                variant_path,
                variant_id=variant_id,
                label=str(meta.get("label") or variant_id).strip() or variant_id,
                kind=str(meta.get("kind") or "refined").strip() or "refined",
                instruction=str(meta.get("instruction") or "").strip(),
                structure_mode=str(meta.get("structure_mode") or original_structure_mode).strip(),
                detail_level=str(meta.get("detail_level") or "balanced").strip(),
                source_variant_id=str(meta.get("source_variant_id") or "").strip(),
                created_at=float(meta.get("created_at") or variant_path.stat().st_mtime),
            )
        )

    refined_variants.sort(key=lambda item: (item.get("created_at") or 0.0), reverse=True)
    return variants + refined_variants


def load_report_variant(
    *,
    job_id: str,
    original_report_path: Path,
    original_structure_mode: str,
    variant_id: str | None = None,
) -> dict[str, Any]:
    normalized_variant_id = str(variant_id or "original").strip() or "original"
    if normalized_variant_id == "original":
        if not original_report_path.exists() or not original_report_path.is_file():
            raise FileNotFoundError(f"Original report not found for job {job_id}")
        summary = _variant_summary_from_path(
            original_report_path,
            variant_id="original",
            label="Original",
            kind="original",
            instruction="",
            structure_mode=original_structure_mode,
            detail_level="balanced",
            source_variant_id="",
            created_at=original_report_path.stat().st_mtime,
        )
        return {
            **summary,
            "title": _extract_title_from_markdown(original_report_path),
            "content": _read_text(original_report_path),
            "path": original_report_path,
        }

    variants_dir = original_report_path.parent / "variants"
    meta_path = variants_dir / f"{normalized_variant_id}.json"
    variant_path = variants_dir / f"{normalized_variant_id}.md"
    if not meta_path.exists() or not variant_path.exists():
        raise FileNotFoundError(f"Variant {normalized_variant_id} not found for job {job_id}")
    meta = _load_json(meta_path)
    summary = _variant_summary_from_path(
        variant_path,
        variant_id=normalized_variant_id,
        label=str(meta.get("label") or normalized_variant_id).strip() or normalized_variant_id,
        kind=str(meta.get("kind") or "refined").strip() or "refined",
        instruction=str(meta.get("instruction") or "").strip(),
        structure_mode=str(meta.get("structure_mode") or original_structure_mode).strip(),
        detail_level=str(meta.get("detail_level") or "balanced").strip(),
        source_variant_id=str(meta.get("source_variant_id") or "").strip(),
        created_at=float(meta.get("created_at") or variant_path.stat().st_mtime),
    )
    return {
        **summary,
        "title": _extract_title_from_markdown(variant_path),
        "content": _read_text(variant_path),
        "path": variant_path,
    }


async def refine_report_variant(
    *,
    job_id: str,
    original_report_path: Path,
    original_structure_mode: str,
    instruction: str,
    target_structure_mode: str,
    detail_level: str,
    base_variant_id: str,
) -> dict[str, Any]:
    cleaned_instruction = str(instruction or "").strip()
    if not cleaned_instruction:
        raise ValueError("Refinement instruction must not be empty")

    normalized_target_structure_mode = normalize_report_target_structure_mode(target_structure_mode)
    normalized_detail_level = normalize_report_detail_level(detail_level, default="balanced")
    source = load_report_variant(
        job_id=job_id,
        original_report_path=original_report_path,
        original_structure_mode=original_structure_mode,
        variant_id=base_variant_id,
    )
    base_markdown = str(source.get("content") or "").strip()
    if not base_markdown:
        raise ValueError("Base report content is empty")

    sections = _extract_sections(base_markdown)
    relevant_sections = _locate_relevant_sections(cleaned_instruction, sections)
    section_outline = _build_section_outline(sections)
    base_language = _detect_language(base_markdown)
    source_structure_mode = normalize_report_structure_mode(
        str(source.get("structure_mode") or original_structure_mode).strip(),
        default=normalize_report_structure_mode(original_structure_mode),
    )
    applied_structure_mode = (
        source_structure_mode
        if normalized_target_structure_mode == "preserve"
        else normalize_report_structure_mode(normalized_target_structure_mode, default=source_structure_mode)
    )

    plan_payload: Any = None
    try:
        plan_raw = await call_llm_fallback(
            ["gpt_pro", "gem_pro"],
            [
                {
                    "role": "system",
                    "content": (
                        "You plan bounded ReAct-style refinements for academic markdown reports. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_plan_prompt(
                        instruction=cleaned_instruction,
                        target_structure_mode=normalized_target_structure_mode,
                        detail_level=normalized_detail_level,
                        source_structure_mode=source_structure_mode,
                        section_outline=section_outline,
                    ),
                },
            ],
            step_label="report refinement planning",
            temperature=0.1,
            max_tokens=1200,
            step_timeout=90.0,
            response_format={"type": "json_object"},
        )
        plan_payload = _safe_json_loads(plan_raw)
    except Exception as err:
        log.warning("Report refinement planning fell back to default plan for job %s: %s", job_id, err)

    plan = _normalize_plan(
        plan_payload,
        instruction=cleaned_instruction,
        target_structure_mode=normalized_target_structure_mode,
        detail_level=normalized_detail_level,
    )

    distilled_summary = _read_text(original_report_path.parent / "distilled_memory_summary.md")
    working_memory_excerpt = _build_working_memory_excerpt(
        original_report_path.parent / "working_memory.json",
        relevant_sections,
    )
    english_source_report = _read_text(original_report_path.with_name("report.en.md"))
    tool_observations = _build_observations_block(
        plan=plan,
        relevant_sections=relevant_sections,
        working_memory_excerpt=working_memory_excerpt,
        distilled_summary=distilled_summary,
        target_structure_mode=applied_structure_mode,
        language=base_language,
    )

    rewritten = await call_llm_fallback(
        ["gpt_pro", "gem_pro"],
        [
            {
                "role": "system",
                "content": (
                    "You rewrite research-paper interpretation reports for clarity and fidelity. "
                    "Return markdown only."
                ),
            },
            {
                "role": "user",
                "content": _build_rewrite_prompt(
                    base_markdown=base_markdown,
                    base_title=str(source.get("title") or ""),
                    base_language=base_language,
                    source_structure_mode=source_structure_mode,
                    target_structure_mode=applied_structure_mode
                    if normalized_target_structure_mode != "preserve"
                    else "preserve",
                    detail_level=normalized_detail_level,
                    instruction=cleaned_instruction,
                    plan=plan,
                    tool_observations=tool_observations,
                    distilled_summary=distilled_summary,
                    working_memory_excerpt=working_memory_excerpt,
                    english_source_report=english_source_report,
                ),
            },
        ],
        step_label="report refinement rewrite",
        temperature=0.2,
        max_tokens=16384,
        step_timeout=240.0,
    )
    refined_markdown = _strip_code_fence(rewritten)
    if not refined_markdown.strip():
        raise RuntimeError("Refined markdown is empty")

    label = _clip_text(str(plan.get("label") or "").strip(), 48)
    if not label:
        label = f"Refined {time.strftime('%H:%M')}"

    variant_id = f"variant-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    variants_dir = original_report_path.parent / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    variant_path = variants_dir / f"{variant_id}.md"
    variant_meta_path = variants_dir / f"{variant_id}.json"
    save_markdown(refined_markdown, variant_path)
    metadata = {
        "variant_id": variant_id,
        "job_id": job_id,
        "kind": "refined",
        "label": label,
        "instruction": cleaned_instruction,
        "structure_mode": applied_structure_mode,
        "detail_level": normalized_detail_level,
        "source_variant_id": str(base_variant_id or "original").strip() or "original",
        "created_at": time.time(),
        "report_path": to_repo_relative_path(variant_path),
        "plan": plan,
    }
    variant_meta_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return load_report_variant(
        job_id=job_id,
        original_report_path=original_report_path,
        original_structure_mode=original_structure_mode,
        variant_id=variant_id,
    )
