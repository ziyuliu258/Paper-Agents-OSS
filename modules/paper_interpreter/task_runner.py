"""T1-T7 interpretation subtasks with shared context, memory injection, and bounded orchestration."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from modules.paper_interpreter.dual_model import (
    collect_dual_model_responses,
    pick_preferred_response,
)
from modules.paper_interpreter.working_memory import WorkingMemory
from utils.llm import call_llm_fallback, call_llm_with_pdf_fallback
from utils.logger import get_logger

log = get_logger(__name__)

_SYSTEM_BASE = (
    "You are an expert academic explainer who turns research papers into clear, high-signal summaries. "
    "Your readers are AI/CS researchers and engineers who want to grasp the core ideas quickly. Requirements:\n"
    "1. Output everything in English\n"
    "2. Prioritize explaining what problem the paper solves, how the method works, and what the results mean\n"
    "3. Do not translate the paper paragraph by paragraph\n"
    "4. Keep the writing concise, direct, and easy to read\n"
    "5. Retain only the details that are necessary for understanding the paper; skip minor background, secondary experiments, and appendix details unless they matter\n"
    "6. If formulas appear, explain only the most important ones and what role they play\n"
    "7. Output the final content directly without extra preamble or closing remarks\n"
    "8. You may use short Markdown subheadings and compact bullet lists when they improve readability"
)

_SECTION_REQUIREMENTS = {
    "background": (
        "Research Background and Motivation",
        "First explain why the problem matters and why it is difficult. Then summarize the 1-3 most important shortcomings of prior work, and finally state the core problem this paper addresses and why it is valuable. Target 300-450 words and avoid unnecessary historical survey.",
    ),
    "method": (
        "Core Method",
        "Start with a 2-4 sentence end-to-end overview of the full pipeline, explicitly stating what enters the model, the main transformations in order, and what is finally produced. Then break the method into 3-5 core modules or stages in execution order. For every named mechanism, augmentation, representation, or loss term that matters, explain in plain language what it literally does to the data or features, why it is introduced, and how it connects to the previous and next step. Do not stop at listing innovations: make the architecture and data flow understandable to a reader who has not read the paper. If formulas are important, keep only the 1-2 most critical ones and explain their roles. Target 700-1000 words.",
    ),
    "experiments": (
        "Experiments and Results",
        "Keep only the most important setup details, major baselines, and core findings. Clearly answer what it outperforms, by how much, and what those numbers imply. Choose only 2-4 representative numbers or tables. Target 400-700 words.",
    ),
}

_SCHEMA_EXAMPLE = {
    "section": "method",
    "summary": "1-2 sentences summarizing the section's main takeaway",
    "pipeline_overview": "2-4 sentences describing the end-to-end flow from input to output",
    "modules": [
        {
            "name": "Module or stage name",
            "order": 1,
            "what_it_is": "What this module literally is",
            "what_it_does": "What transformation it performs",
            "why_it_exists": "Why the paper needs this module",
            "inputs": "What enters this stage",
            "outputs": "What comes out of this stage",
        }
    ],
    "training_objectives": [
        {
            "name": "Loss, objective, or training signal",
            "what_it_optimizes": "What behavior it encourages",
            "why_it_matters": "Why it matters for the final task",
            "when_it_is_applied": "Where it appears in training",
        }
    ],
    "claims": [
        {
            "claim": "A core conclusion or explanation",
            "evidence": [
                {
                    "type": "figure",
                    "label": "Figure 1",
                    "page": 3,
                    "detail": "The figure shows the overall workflow",
                },
                {
                    "type": "number",
                    "label": "87.4",
                    "page": 8,
                    "detail": "A key experimental result or hyperparameter",
                },
            ],
            "importance": "high",
        }
    ],
    "risks": ["Optional. Leave this empty if the section has no notable caveats."],
}

_PAPER_NOTES_DEFAULTS: dict[str, Any] = {
    "metadata": {
        "title_en": "",
        "title_cn": "",
        "venue": "",
        "pub_date": "",
        "institution": "",
        "code_repository_url": "",
    },
    "paper_summary": "",
    "problem": [],
    "method_steps": [],
    "main_results": [],
    "limitations": [],
    "glossary_seed": [],
    "figure_highlights": [],
}

_PRUNED_PAPER_NOTES_KEYS = {
    "t1": ["metadata", "paper_summary", "problem"],
    "t5": ["main_results", "method_steps"],
    "t6": ["limitations", "paper_summary"],
    "t7": ["metadata", "paper_summary"],
    "group_a": [
        "metadata",
        "paper_summary",
        "problem",
        "method_steps",
        "main_results",
        "limitations",
    ],
    "group_b": [
        "metadata",
        "paper_summary",
        "problem",
        "method_steps",
        "main_results",
        "limitations",
        "glossary_seed",
        "figure_highlights",
    ],
}

_INTERNAL_EVIDENCE_LABELS = {
    "paper_summary",
    "problem",
    "method_steps",
    "main_results",
    "limitations",
    "glossary_seed",
    "figure_highlights",
}

_INTERNAL_EVIDENCE_LABEL_ALIASES = {
    "problemdescription": "body text",
    "existingchallenges": "body text",
    "papersummary": "body text",
    "methodsteps": "body text",
    "page": "body text",
}


def _normalize_figure_id(fig_id: str) -> str:
    return str(fig_id).strip().lower().replace(" ", "").replace(".", "")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _code_fence_payload(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return payload


def _paper_notes_with_defaults(paper_notes: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(_PAPER_NOTES_DEFAULTS)
    normalized.update(paper_notes or {})
    metadata = dict(_PAPER_NOTES_DEFAULTS["metadata"])
    metadata.update(normalized.get("metadata") or {})
    normalized["metadata"] = metadata
    for key in (
        "problem",
        "method_steps",
        "main_results",
        "limitations",
        "glossary_seed",
        "figure_highlights",
    ):
        value = normalized.get(key)
        normalized[key] = value if isinstance(value, list) else []
    normalized["paper_summary"] = str(normalized.get("paper_summary", "")).strip()
    return normalized


def _prune_paper_notes(paper_notes: dict[str, Any], task_key: str) -> dict[str, Any]:
    normalized = _paper_notes_with_defaults(paper_notes)
    keys = _PRUNED_PAPER_NOTES_KEYS.get(task_key)
    if not keys:
        return normalized
    return {key: normalized.get(key, _PAPER_NOTES_DEFAULTS[key]) for key in keys}


def _build_shared_context_block(
    paper_notes: dict[str, Any],
    memory_context: str,
    schema_example: dict[str, Any] | None = None,
) -> str:
    blocks = [
        "This is the shared context for the current task group. Unless the task instruction explicitly asks for it, do not mention the existence of this context block.",
        f"paper_notes (JSON):\n{_json_dumps(paper_notes)}",
        f"Long-term memory summary:\n{memory_context.strip() or '- No long-term memory is available for this run.'}",
    ]
    if schema_example is not None:
        blocks.append(
            f"Structured output schema example:\n{_json_dumps(schema_example)}"
        )
    return "\n\n".join(blocks)


def _build_messages(
    shared_context_block: str, task_specific_instruction: str
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _SYSTEM_BASE},
        {"role": "user", "content": shared_context_block},
        {"role": "user", "content": task_specific_instruction},
    ]


def _clip_text(text: str, *, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", str(text).strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _claim_evidence_refs(claim: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in claim.get("evidence", []) if isinstance(claim, dict) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        page = item.get("page")
        detail = re.sub(r"\s+", " ", str(item.get("detail", "")).strip())
        parts = [label] if label else []
        if isinstance(page, int) and page > 0:
            parts.append(f"p.{page}")
        if detail:
            parts.append(detail)
        ref = " | ".join(parts).strip()
        if ref:
            refs.append(ref)
    return refs


def _register_plain_section_memory(
    working_memory: WorkingMemory | None,
    *,
    task_key: str,
    section_key: str,
    text: str,
) -> None:
    if working_memory is None:
        return
    working_memory.remember_task_output(task_key, text)
    working_memory.add_observation(
        source=task_key,
        section_key=section_key,
        summary=_clip_text(text),
        confidence=0.55,
        kind="task_output",
    )


def _register_structured_section_memory(
    working_memory: WorkingMemory | None,
    *,
    task_key: str,
    section_key: str,
    payload: dict[str, Any],
) -> None:
    if working_memory is None:
        return

    working_memory.remember_task_output(task_key, payload)
    summary = str(payload.get("summary", "")).strip()
    if summary:
        working_memory.add_observation(
            source=task_key,
            section_key=section_key,
            summary=_clip_text(summary),
            confidence=0.7,
            kind="task_output",
        )

    claims = payload.get("claims", []) if isinstance(payload, dict) else []
    if not claims:
        working_memory.add_open_question(
            question=f"No stable structured claims were extracted for {section_key}.",
            section_key=section_key,
            reason="The section may need evidence-grounded refinement in a later pass.",
        )
        return

    for claim in claims[:5]:
        if not isinstance(claim, dict):
            continue
        claim_text = str(claim.get("claim", "")).strip()
        evidence_refs = _claim_evidence_refs(claim)
        importance = str(claim.get("importance", "medium")).strip() or "medium"
        confidence = 0.85 if importance == "high" else 0.7 if importance == "medium" else 0.6
        if claim_text:
            working_memory.add_draft_claim(
                section_key=section_key,
                claim=claim_text,
                evidence_refs=evidence_refs,
                importance=importance,
                confidence=confidence,
            )
            working_memory.add_observation(
                source=task_key,
                section_key=section_key,
                summary=_clip_text(claim_text),
                evidence_refs=evidence_refs,
                confidence=confidence,
                kind="evidence",
            )

    if section_key == "method":
        modules = payload.get("modules", []) if isinstance(payload, dict) else []
        if not modules:
            working_memory.add_open_question(
                question="The method section is missing an explicit module/stage breakdown.",
                section_key=section_key,
                reason="Downstream explanation may be too abstract without a stage-level pipeline.",
            )
    if section_key == "experiments":
        evidence_count = sum(
            len(claim.get("evidence", []))
            for claim in claims
            if isinstance(claim, dict) and isinstance(claim.get("evidence", []), list)
        )
        if evidence_count == 0:
            working_memory.add_open_question(
                question="The experiments section has claims but no explicit numeric/figure evidence anchors.",
                section_key=section_key,
                reason="The report may need a later evidence-grounded refinement loop.",
            )


def _build_paper_notes_prompt(parsed_paper: dict[str, Any]) -> str:
    figure_index = parsed_paper.get("figure_index", {})
    figure_lines = [
        f"- {fig.get('id', '')} | page={fig.get('page', '')} | caption={fig.get('caption', '')}"
        for fig in figure_index.values()
    ]
    figure_block = (
        "\n".join(figure_lines)
        if figure_lines
        else "- No extracted figure index is available"
    )
    return (
        "Read the paper and produce a structured `paper_notes` object that can be shared across multiple downstream tasks.\n"
        "Return JSON only. Do not add any explanation.\n"
        "Required schema:\n"
        "{\n"
        '  "metadata": {\n'
        '    "title_en": "English paper title",\n'
        '    "title_cn": "Optional Chinese title if clearly inferable, otherwise an empty string",\n'
        '    "venue": "Conference, journal, or Arxiv",\n'
        '    "pub_date": "Publication date",\n'
        '    "institution": "First author institution or corresponding author institution",\n'
        '    "code_repository_url": "Exact repository/project code URL explicitly mentioned in the paper text if available, otherwise an empty string"\n'
        "  },\n"
        '  "paper_summary": "2-4 sentence summary of the paper’s main storyline",\n'
        '  "problem": ["Research problem 1", "Research problem 2"],\n'
        '  "method_steps": ["Key method step 1", "Key method step 2"],\n'
        '  "main_results": [\n'
        '    {"metric": "Core metric", "value": "Result number", "page": 0, "evidence": "Figure/Table/paragraph description"}\n'
        "  ],\n"
        '  "limitations": ["Main limitation 1", "Main limitation 2"],\n'
        '  "glossary_seed": ["Key term 1", "Key term 2"],\n'
        '  "figure_highlights": [\n'
        '    {"id": "Figure 1", "page": 3, "caption": "Overall framework", "role": "method"}\n'
        "  ]\n"
        "}\n\n"
        "If a field is uncertain, infer it as carefully as possible from the paper instead of omitting the field.\n\n"
        f"The extracted figure index below is available as reference:\n{figure_block}"
    )


def _build_html_source_context(parsed_paper: dict[str, Any]) -> str:
    html_bundle = parsed_paper.get("html_bundle")
    if not isinstance(html_bundle, dict):
        return ""

    title = str(html_bundle.get("title", "")).strip()
    abstract = str(html_bundle.get("abstract", "")).strip()
    source_url = str(html_bundle.get("source_url", "")).strip()
    sections = html_bundle.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    lines = [
        "The paper source is HTML rather than PDF. Use the extracted HTML content below as the paper body.",
    ]
    if title:
        lines.append(f"HTML title: {title}")
    if source_url:
        lines.append(f"Source URL: {source_url}")
    if abstract:
        lines.append(f"Abstract:\n{abstract}")

    section_lines: list[str] = []
    for section in sections[:12]:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading", "")).strip() or "Body"
        content = str(section.get("content", "")).strip()
        if not content:
            continue
        section_lines.append(f"## {heading}\n{content[:1800]}")
    plain_text = str(html_bundle.get("plain_text", "")).strip()
    if section_lines:
        lines.append("Extracted sections:\n" + "\n\n".join(section_lines))
    elif plain_text:
        lines.append(f"Extracted body text:\n{plain_text}")
    return "\n\n".join(lines).strip()


def _parse_paper_notes(resp: str) -> dict[str, Any]:
    payload = json.loads(_code_fence_payload(resp))
    if not isinstance(payload, dict):
        raise ValueError("paper_notes output is not a JSON object")
    return _paper_notes_with_defaults(payload)


def paper_notes_figure_highlights(parsed_paper: dict[str, Any]) -> list[dict[str, Any]]:
    paper_notes = parsed_paper.get("paper_notes") or {}
    highlights = paper_notes.get("figure_highlights", [])
    return highlights if isinstance(highlights, list) else []


def _select_grounding_figures(
    parsed_paper: dict[str, Any],
    paper_notes: dict[str, Any],
    section_key: str,
) -> list[dict[str, Any]]:
    figure_index = parsed_paper.get("figure_index", {})
    selected: list[dict[str, Any]] = []
    role_aliases = {section_key}
    if section_key == "method":
        role_aliases.add("background")

    highlights = paper_notes.get("figure_highlights", [])
    if isinstance(highlights, list):
        for item in highlights:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            norm_id = _normalize_figure_id(str(item.get("id", "")))
            if role in role_aliases and norm_id in figure_index:
                selected.append(figure_index[norm_id])

    if selected:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in selected:
            fig_id = str(item.get("id", "")).strip()
            if fig_id and fig_id not in seen:
                seen.add(fig_id)
                deduped.append(item)
        return deduped[:4]

    fallback_roles = {
        "method": ("figure",),
        "experiments": ("table", "figure"),
        "background": ("figure",),
    }
    role_prefixes = fallback_roles.get(section_key, ("figure",))
    fallback = [
        fig
        for fig in figure_index.values()
        if str(fig.get("id", "")).strip().lower().startswith(role_prefixes)
    ]
    return fallback[:4]


def _build_structured_task_instruction(
    section_key: str,
    parsed_paper: dict[str, Any],
    paper_notes: dict[str, Any],
    prior_context: str | None = None,
) -> str:
    section_label, section_requirement = _SECTION_REQUIREMENTS[section_key]
    grounding_figures = _select_grounding_figures(
        parsed_paper, paper_notes, section_key
    )
    figure_lines = [
        f"- {fig.get('id', '')} | page={fig.get('page', '')} | caption={fig.get('caption', '')}"
        for fig in grounding_figures
    ]
    figure_block = (
        "\n".join(figure_lines)
        if figure_lines
        else "- No suitable figure anchors are available. You may cite page numbers and key numeric evidence instead."
    )

    prior_block = ""
    if prior_context:
        prior_block = (
            "Narrative anchor from the previous section (avoid repeating the same setup and transition naturally into this section):\n"
            f"{prior_context.strip()}\n\n"
        )

    section_specific_rules = ""
    if section_key == "method":
        section_specific_rules = (
            "Method-specific coverage requirements:\n"
            "- Return the extra fields `pipeline_overview`, `modules`, and `training_objectives` in addition to the generic fields.\n"
            "- `pipeline_overview` must explain the end-to-end execution flow in 2-4 sentences.\n"
            "- `modules` must contain 3-5 stages in execution order when the paper supports that level of decomposition. If the paper does not explicitly label modules, infer a stable stage breakdown from the actual pipeline.\n"
            "- Each module entry should explain what the stage is, what it does, why it exists, and the input/output around that stage.\n"
            "- `training_objectives` should summarize the main loss terms or training signals. If the paper effectively uses a single standard task loss, still explain that training objective plainly.\n"
            "- Include at least one claim that summarizes the full pipeline from raw input to final output.\n"
            "- Include claims that define the paper's key technical terms in plain language, not just their names. For each important term, state what operation is actually performed or what object it refers to.\n"
            "- When the paper uses data augmentations, feature transformations, fusion steps, memory modules, decoders, or losses, explain where each one appears in the pipeline and what changes after that step.\n"
            "- If the paper presents an architecture figure, align the explanation with that figure so the reader can reconstruct the model structure mentally.\n"
            "- Prefer concrete verbs such as crop, mix, encode, aggregate, attend, predict, and optimize over abstract praise.\n\n"
        )

    return (
        f"Produce the final structured JSON for the section '{section_label}'.\n"
        "You must first decide which conclusions are worth keeping, then express them as evidence-grounded claims.\n"
        "Every claim should cite page numbers whenever possible and must include at least one evidence item.\n"
        "If you cite figures or tables, prefer the canonical IDs listed below.\n\n"
        f"{prior_block}"
        f"Recommended figure/table anchors:\n{figure_block}\n\n"
        f"Section writing requirement: {section_requirement}\n\n"
        f"{section_specific_rules}"
        f"Rules:\n1. The `section` field must be exactly `{section_key}`\n"
        "2. `importance` must be one of: high / medium / low\n"
        "3. `evidence.type` must be one of: figure / table / number / page / quote\n"
        "4. Normalize `evidence.label` into reader-facing labels such as Figure X / Table X / numeric result / body text. Do not expose internal labels.\n"
        "5. Use the paper's natural page numbers starting from 1\n"
        "6. Return JSON only, with no extra text"
    )


def _parse_structured_section(resp: str, expected_section: str) -> dict[str, Any]:
    payload = json.loads(_code_fence_payload(resp))
    if not isinstance(payload, dict):
        raise ValueError("Structured section output is not a JSON object")
    if payload.get("section") != expected_section:
        raise ValueError(
            f"Expected section={expected_section}, got {payload.get('section')}"
        )

    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("Structured section output is missing claims")

    normalized_claims: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            continue
        normalized_evidence: list[dict[str, Any]] = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            normalized_evidence.append(
                {
                    "type": str(item.get("type", "page")).strip().lower() or "page",
                    "label": str(item.get("label", "")).strip(),
                    "page": item.get("page"),
                    "detail": str(item.get("detail", "")).strip(),
                }
            )
        if not normalized_evidence:
            continue
        normalized_claims.append(
            {
                "claim": str(claim.get("claim", "")).strip(),
                "evidence": normalized_evidence,
                "importance": str(claim.get("importance", "medium")).strip().lower()
                or "medium",
            }
        )

    if not normalized_claims:
        raise ValueError("Structured section output contains no valid claims")

    risks = payload.get("risks", [])
    normalized_payload = {
        "section": expected_section,
        "summary": str(payload.get("summary", "")).strip(),
        "claims": normalized_claims,
        "risks": [str(item).strip() for item in risks if str(item).strip()]
        if isinstance(risks, list)
        else [],
    }
    if expected_section == "method":
        modules_payload = payload.get("modules", [])
        normalized_modules: list[dict[str, Any]] = []
        if isinstance(modules_payload, list):
            for idx, item in enumerate(modules_payload, start=1):
                if not isinstance(item, dict):
                    continue
                normalized_modules.append(
                    {
                        "name": str(item.get("name", "")).strip() or f"Stage {idx}",
                        "order": int(item.get("order", idx) or idx),
                        "what_it_is": str(item.get("what_it_is", "")).strip(),
                        "what_it_does": str(item.get("what_it_does", "")).strip(),
                        "why_it_exists": str(item.get("why_it_exists", "")).strip(),
                        "inputs": str(item.get("inputs", "")).strip(),
                        "outputs": str(item.get("outputs", "")).strip(),
                    }
                )
        normalized_modules.sort(key=lambda item: item.get("order", 0))

        objectives_payload = payload.get("training_objectives", [])
        normalized_objectives: list[dict[str, str]] = []
        if isinstance(objectives_payload, list):
            for item in objectives_payload:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                details = {
                    "name": name,
                    "what_it_optimizes": str(item.get("what_it_optimizes", "")).strip(),
                    "why_it_matters": str(item.get("why_it_matters", "")).strip(),
                    "when_it_is_applied": str(
                        item.get("when_it_is_applied", "")
                    ).strip(),
                }
                if any(details.values()):
                    normalized_objectives.append(details)

        normalized_payload["pipeline_overview"] = str(
            payload.get("pipeline_overview", "")
        ).strip()
        normalized_payload["modules"] = normalized_modules
        normalized_payload["training_objectives"] = normalized_objectives
    return normalized_payload


def _build_prior_context(section_label: str, section_data: dict[str, Any]) -> str:
    fragments: list[str] = []
    summary = str(section_data.get("summary", "")).strip()
    if summary:
        fragments.append(f"Previous section ({section_label}) summary: {summary}")
    for idx, claim in enumerate(section_data.get("claims", [])[:3], start=1):
        claim_text = str(claim.get("claim", "")).strip()
        if claim_text:
            fragments.append(f"Key point {idx}: {claim_text}")
    return "\n".join(fragments)


def _build_adjudication_messages(
    section_label: str,
    section_key: str,
    shared_context_block: str,
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
) -> list[dict[str, Any]]:
    extra_rule = ""
    if section_key == "method":
        extra_rule = (
            "6. For the method section, preserve claims that help the reader reconstruct the end-to-end pipeline and understand the concrete meaning of named terms.\n"
            "7. Preserve or improve the `pipeline_overview`, `modules`, and `training_objectives` fields when the candidates provide them.\n"
            "8. Do not collapse the answer into innovation slogans if that would remove operational details.\n"
        )
    instruction = (
        f"Adjudicate the two candidates and output the final JSON for '{section_label}'.\n"
        f"The `section` field must remain `{section_key}`.\n"
        "Rules:\n"
        "1. Use the shared context as the common source of truth and compare the two candidates claim by claim.\n"
        "2. If two claims conflict, prefer the one with more specific evidence, clearer page numbers, and more complete numerical details.\n"
        "3. Write `summary` in 1-2 sentences.\n"
        "4. Keep at most 5 claims, prioritizing high-importance ones.\n"
        "5. Keep `evidence.label` reader-friendly, such as Figure X / Table X / numeric result / body text.\n"
        f"{extra_rule}"
        "9. Return JSON only with no explanation.\n\n"
        f"Candidate A:\n{_json_dumps(candidate_a)}\n\n"
        f"Candidate B:\n{_json_dumps(candidate_b)}"
    )
    return [
        {
            "role": "system",
            "content": (
                "You are an academic editor responsible for evidence-based adjudication. "
                "Your job is not to freely rewrite the answer, but to select the more trustworthy claims from two candidate JSON outputs, merging only complementary information when justified by the evidence and never inventing new facts."
            ),
        },
        {"role": "user", "content": shared_context_block},
        {"role": "user", "content": instruction},
    ]


def _clean_plain_section_text(text: str, section_label: str) -> str:
    cleaned = text.strip()
    patterns = [
        rf"^##\s*{re.escape(section_label)}\s*\n+",
        rf"^###\s*{re.escape(section_label)}\s*\n+",
        rf"^\*\*{re.escape(section_label)}\*\*[:：]?\s*\n+",
        rf"^#\s*{re.escape(section_label)}\s*\n+",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _format_evidence(evidence: dict[str, Any]) -> str:
    label = str(evidence.get("label", "")).strip()
    detail = str(evidence.get("detail", "")).strip()
    page = evidence.get("page")
    evidence_type = str(evidence.get("type", "")).strip().lower()

    normalized_label = label.lower().replace(" ", "").replace(".", "").replace("-", "")
    if normalized_label in _INTERNAL_EVIDENCE_LABELS:
        label = ""
    else:
        label = _INTERNAL_EVIDENCE_LABEL_ALIASES.get(normalized_label, label)

    if evidence_type == "page" and (not label or label == "page"):
        label = "body text"

    detail = re.sub(
        r"^(problem|paper_summary|method_steps|main_results|limitations|glossary_seed|figure_highlights)\s*[:：-]\s*",
        "",
        detail,
        flags=re.IGNORECASE,
    )
    detail = re.sub(
        r"^(problem\s*description|existing\s*challenges|paper\s*summary|method\s*steps|page\s*\d+)\s*[:：-]\s*",
        "",
        detail,
        flags=re.IGNORECASE,
    )

    if label == "body text" and not detail:
        detail = "Relevant discussion in the paper body"

    parts = []
    if label:
        parts.append(label)
    if page not in (None, ""):
        parts.append(f"p.{page}")
    if detail:
        parts.append(detail)
    return "; ".join(parts)


def _render_section_markdown(section_label: str, section_data: dict[str, Any]) -> str:
    if str(section_data.get("section", "")).strip() == "method":
        return _render_method_section_markdown(section_data)

    lines: list[str] = []
    summary = str(section_data.get("summary", "")).strip()
    if summary:
        lines.append(summary)
        lines.append("")

    for claim in section_data.get("claims", []):
        claim_text = str(claim.get("claim", "")).strip()
        if not claim_text:
            continue
        lines.append(f"- {claim_text}")
        evidence_lines: list[str] = []
        for item in claim.get("evidence", []):
            formatted = _format_evidence(item)
            if formatted:
                evidence_lines.append(formatted)
        if evidence_lines:
            lines.append(f"  - Evidence: {' | '.join(evidence_lines)}")

    risks = section_data.get("risks", [])
    if risks:
        lines.append("")
        lines.append("### Caveats")
        for risk in risks:
            lines.append(f"- {risk}")

    rendered = "\n".join(lines).strip()
    if not rendered:
        raise ValueError(f"Rendered section is empty: {section_label}")
    return rendered


def _render_method_section_markdown(section_data: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = str(section_data.get("summary", "")).strip()
    pipeline_overview = str(section_data.get("pipeline_overview", "")).strip()
    modules = section_data.get("modules", [])
    training_objectives = section_data.get("training_objectives", [])

    if summary:
        lines.append(summary)
        lines.append("")

    lines.append("### 1. Overall Pipeline")
    lines.append(
        pipeline_overview
        or "The paper describes an end-to-end pipeline, but the current run did not recover a stable pipeline overview from the source evidence."
    )

    lines.append("")
    lines.append("### 2. Key Modules")
    if isinstance(modules, list) and modules:
        for idx, item in enumerate(modules, start=1):
            name = str(item.get("name", "")).strip() or "Unnamed stage"
            lines.append(f"#### Module {idx}: {name}")
            lines.append(
                str(item.get("what_it_is", "")).strip()
                or "Core stage in the method pipeline."
            )
            if str(item.get("what_it_does", "")).strip():
                lines.append("")
                lines.append(
                    f"- **What it does:** {str(item.get('what_it_does', '')).strip()}"
                )
            if str(item.get("why_it_exists", "")).strip():
                lines.append(
                    f"- **Why it exists:** {str(item.get('why_it_exists', '')).strip()}"
                )
            io_parts: list[str] = []
            if str(item.get("inputs", "")).strip():
                io_parts.append(f"Input: {str(item.get('inputs', '')).strip()}")
            if str(item.get("outputs", "")).strip():
                io_parts.append(f"Output: {str(item.get('outputs', '')).strip()}")
            if io_parts:
                lines.append(f"- **Data flow:** {'; '.join(io_parts)}")
            lines.append("")
    else:
        for claim in section_data.get("claims", [])[:4]:
            claim_text = str(claim.get("claim", "")).strip()
            if claim_text:
                lines.append(f"- {claim_text}")

    while lines and not lines[-1].strip():
        lines.pop()

    lines.append("")
    lines.append("### 3. Training Objectives")
    if isinstance(training_objectives, list) and training_objectives:
        for idx, item in enumerate(training_objectives, start=1):
            name = str(item.get("name", "")).strip() or "Training objective"
            lines.append(f"#### Objective {idx}: {name}")
            lines.append(
                str(item.get("what_it_optimizes", "")).strip()
                or "Main optimization target described in the paper."
            )
            if str(item.get("why_it_matters", "")).strip():
                lines.append("")
                lines.append(
                    f"- **Why it matters:** {str(item.get('why_it_matters', '')).strip()}"
                )
            if str(item.get("when_it_is_applied", "")).strip():
                lines.append(
                    f"- **Where it appears:** {str(item.get('when_it_is_applied', '')).strip()}"
                )
            lines.append("")
    else:
        lines.append(
            "- The paper does not foreground a separate multi-part training objective beyond the main task objective, or the current run could not isolate it confidently."
        )

    while lines and not lines[-1].strip():
        lines.pop()

    claims = section_data.get("claims", [])
    if claims:
        lines.append("")
        lines.append("### 4. Key Takeaways")
        for claim in claims:
            claim_text = str(claim.get("claim", "")).strip()
            if not claim_text:
                continue
            lines.append(f"- {claim_text}")
            evidence_lines: list[str] = []
            for evidence in claim.get("evidence", []):
                formatted = _format_evidence(evidence)
                if formatted:
                    evidence_lines.append(formatted)
            if evidence_lines:
                lines.append(f"  - Evidence: {' | '.join(evidence_lines)}")

    risks = section_data.get("risks", [])
    if risks:
        lines.append("")
        lines.append("### Caveats")
        for risk in risks:
            lines.append(f"- {risk}")

    rendered = "\n".join(lines).strip()
    if not rendered:
        raise ValueError("Rendered section is empty: Core Method")
    return rendered


async def build_paper_notes(
    source_path: Path,
    parsed_paper: dict[str, Any],
    *,
    working_memory: WorkingMemory | None = None,
) -> dict[str, Any]:
    prompt = _build_paper_notes_prompt(parsed_paper)
    source_type = str(parsed_paper.get("source_type") or "pdf").strip().lower() or "pdf"
    if source_type == "html":
        html_context = _build_html_source_context(parsed_paper)
        if not html_context:
            raise ValueError("HTML source is missing extracted content for paper_notes")
        resp = await call_llm_fallback(
            ["gem_pro", "gpt_pro"],
            [
                {"role": "system", "content": _SYSTEM_BASE},
                {"role": "user", "content": prompt},
                {"role": "user", "content": html_context},
            ],
            step_label="shared paper_notes (html)",
            temperature=0.1,
            max_tokens=8192,
            step_timeout=600.0,
        )
    else:
        resp = await call_llm_with_pdf_fallback(
            ["gem_pro", "gpt_pro"],
            source_path,
            prompt,
            step_label="shared paper_notes",
            system_prompt=_SYSTEM_BASE,
            temperature=0.1,
            step_timeout=600.0,
        )
    notes = _parse_paper_notes(resp)
    parsed_paper["paper_notes"] = notes
    if working_memory is not None:
        working_memory.set_paper_notes(notes)
        metadata = notes.get("metadata") or {}
        title = str(metadata.get("title_en", "")).strip()
        summary = str(notes.get("paper_summary", "")).strip()
        if title:
            working_memory.add_observation(
                source="paper_notes",
                section_key="metadata",
                summary=f"Paper title identified as: {title}",
                confidence=0.95,
                kind="paper_note",
            )
        if summary:
            working_memory.add_observation(
                source="paper_notes",
                section_key="paper_summary",
                summary=_clip_text(summary),
                confidence=0.85,
                kind="paper_note",
            )
        main_results = notes.get("main_results", [])
        if not main_results:
            working_memory.add_open_question(
                question="paper_notes did not recover stable main_results entries.",
                section_key="paper_notes",
                reason="Later experiment summarization may need to rely more on section-level extraction.",
            )
    return notes


async def _run_structured_dual_section(
    section_key: str,
    parsed_paper: dict[str, Any],
    *,
    full_paper_notes: dict[str, Any],
    shared_context_block: str,
    prior_context: str | None = None,
    step_timeout: float = 420.0,
) -> dict[str, Any]:
    section_label, _ = _SECTION_REQUIREMENTS[section_key]
    instruction = _build_structured_task_instruction(
        section_key,
        parsed_paper,
        full_paper_notes,
        prior_context=prior_context,
    )
    messages = _build_messages(shared_context_block, instruction)
    successes, failures = await collect_dual_model_responses(
        messages,
        section_label,
        step_timeout=step_timeout,
        second_model_grace_period=120.0,
        temperature=0.2,
        max_tokens=8192,
    )

    if len(successes) == 2:
        parsed_candidates: list[tuple[str, dict[str, Any]]] = []
        parse_failures: list[tuple[str, Exception]] = []
        for model_alias, text in successes:
            try:
                parsed_candidates.append(
                    (model_alias, _parse_structured_section(text, section_key))
                )
            except Exception as err:
                parse_failures.append((model_alias, err))
                log.warning(
                    "%s: failed to parse structured JSON from %s, skipping it. Reason: %s",
                    section_label,
                    model_alias,
                    err,
                )

        if len(parsed_candidates) == 2:
            adjudication_messages = _build_adjudication_messages(
                section_label,
                section_key,
                shared_context_block,
                parsed_candidates[0][1],
                parsed_candidates[1][1],
            )
            try:
                adjudicated = await call_llm_fallback(
                    ["gpt_pro", "gem_pro"],
                    adjudication_messages,
                    step_label=f"{section_label} adjudication",
                    temperature=0.1,
                    max_tokens=8192,
                    step_timeout=120.0,
                )
                return _parse_structured_section(adjudicated, section_key)
            except Exception as err:
                preferred_model, preferred_data = pick_preferred_response(
                    parsed_candidates
                )
                log.warning(
                    "%s adjudication failed, falling back to preferred single-model result %s: %s",
                    section_label,
                    preferred_model,
                    err,
                )
                return preferred_data

        if len(parsed_candidates) == 1:
            preferred_model, preferred_data = parsed_candidates[0]
            log.warning(
                "%s kept only one parseable structured result: %s",
                section_label,
                preferred_model,
            )
            return preferred_data

        if parse_failures:
            summary = "; ".join(
                f"{model_alias} -> {type(err).__name__}: {err}"
                for model_alias, err in parse_failures
            )
            raise RuntimeError(
                f"Both dual-model outputs for {section_label} were unparsable: {summary}"
            )

    if len(successes) == 1:
        preferred_model, preferred_text = successes[0]
        log.warning(
            "%s automatically degraded to a single structured model output: %s",
            section_label,
            preferred_model,
        )
        return _parse_structured_section(preferred_text, section_key)

    summary = "; ".join(
        f"{model_alias} -> {type(err).__name__}: {err}" for model_alias, err in failures
    )
    raise RuntimeError(f"All dual-model attempts failed for {section_label}: {summary}")


async def run_t1(shared_context_block: str) -> str:
    instruction = (
        "Compress the paper's core contribution into a single English sentence.\n\n"
        "Requirements:\n"
        "1. Write only one sentence, ideally no more than 35 words.\n"
        "2. Highlight the method name, the problem it solves, and the key innovation.\n"
        "3. Do not restate the title and avoid generic praise or empty wording."
    )
    return await call_llm_fallback(
        ["gem_pro", "gpt_pro"],
        _build_messages(shared_context_block, instruction),
        step_label="T1 one-line summary",
        temperature=0.2,
        max_tokens=1024,
        step_timeout=120.0,
    )


async def run_t5(shared_context_block: str) -> str:
    instruction = (
        "Write the 'Ablation Studies' section in English.\n\n"
        "Requirements:\n"
        "1. Summarize only the 2-4 most important components or design choices.\n"
        "2. Explain how performance changes when each part is removed or replaced, and what conclusion follows.\n"
        "3. If the paper has no standard ablation section, infer the design takeaways from the main experiments.\n"
        "4. Target 250-450 words."
    )
    result = await call_llm_fallback(
        ["gem_pro", "gpt_pro"],
        _build_messages(shared_context_block, instruction),
        step_label="T5 ablation studies",
        temperature=0.2,
        max_tokens=4096,
        step_timeout=180.0,
    )
    return _clean_plain_section_text(result, "Ablation Studies")


async def run_t6(shared_context_block: str) -> str:
    instruction = (
        "Write the 'Limitations and Future Directions' section in English.\n\n"
        "Requirements:\n"
        "1. Start with the limitations explicitly acknowledged by the authors.\n"
        "2. Then add 1-2 additional high-value potential issues if they are strongly supported by the paper.\n"
        "3. Finally, propose corresponding future directions or improvements.\n"
        "4. Focus on the 3-5 most important points and target 350-600 words."
    )
    result = await call_llm_fallback(
        ["gpt_pro", "gem_pro"],
        _build_messages(shared_context_block, instruction),
        step_label="T6 limitations and future directions",
        temperature=0.2,
        max_tokens=4096,
        step_timeout=180.0,
    )
    return _clean_plain_section_text(result, "Limitations and Future Directions")


async def run_t7(
    t1: str,
    t2: str,
    t3: str,
    t4: str,
    t5: str,
    t6: str,
    *,
    shared_context_block: str,
) -> dict[str, Any]:
    all_content = (
        f"One-line summary: {t1}\n\n"
        f"Research Background and Motivation:\n{t2}\n\n"
        f"Core Method:\n{t3}\n\n"
        f"Experiments and Results:\n{t4}\n\n"
        f"Ablation Studies:\n{t5}\n\n"
        f"Limitations and Future Directions:\n{t6}"
    )
    instruction = (
        "Complete the following two tasks.\n\n"
        "## Task 1: Overall assessment\n"
        "Provide a concise overall assessment of the paper, covering novelty, practicality, completeness, and overall judgment. Keep it brief and focus on the highest-value takeaways for each dimension.\n\n"
        "## Task 2: Glossary\n"
        'Extract the key English technical terms that appear in the paper and return them as a JSON array in the format {"term": "English term", "explanation": "English explanation"}. Keep only the 15-30 most important terms.\n\n'
        "Use the exact output format below, separating the two tasks with ---GLOSSARY---:\n"
        "[overall assessment markdown]\n"
        "---GLOSSARY---\n"
        "[JSON array]\n\n"
        f"Existing section content you may reuse directly:\n{all_content}"
    )
    resp = await call_llm_fallback(
        ["gpt_pro", "gem_pro"],
        _build_messages(shared_context_block, instruction),
        step_label="T7 overall assessment and glossary",
        temperature=0.2,
        max_tokens=8192,
        step_timeout=240.0,
    )

    conclusion = resp.strip()
    glossary: list[dict[str, str]] = []
    if "---GLOSSARY---" in resp:
        conclusion_part, glossary_part = resp.split("---GLOSSARY---", 1)
        conclusion = conclusion_part.strip()
        glossary_payload = _code_fence_payload(glossary_part)
        try:
            loaded = json.loads(glossary_payload)
            if isinstance(loaded, list):
                glossary = [
                    {
                        "term": str(item.get("term", "")).strip(),
                        "explanation": str(item.get("explanation", "")).strip(),
                    }
                    for item in loaded
                    if isinstance(item, dict) and str(item.get("term", "")).strip()
                ]
        except Exception as err:
            log.warning("Failed to parse glossary JSON: %s", err)

    return {
        "conclusion": _clean_plain_section_text(conclusion, "Overall Assessment"),
        "glossary": glossary,
    }


async def run_all_tasks(
    parsed_paper: dict[str, Any],
    *,
    paper_notes: dict[str, Any] | None = None,
    memory_context: str = "",
    working_memory: WorkingMemory | None = None,
) -> dict[str, Any]:
    """Execute all interpretation tasks with shared context blocks and grouped orchestration."""
    effective_paper_notes = _paper_notes_with_defaults(
        paper_notes or parsed_paper.get("paper_notes") or {}
    )
    parsed_paper["paper_notes"] = effective_paper_notes
    if working_memory is not None:
        working_memory.set_paper_notes(effective_paper_notes)
        working_memory.set_retrieved_context("interpreter_memory_context", memory_context)
        working_memory.set_metric("interpreter_memory_context_chars", len(memory_context))

    group_a_shared_context = _build_shared_context_block(
        _prune_paper_notes(effective_paper_notes, "group_a"),
        memory_context,
    )
    group_b_shared_context = _build_shared_context_block(
        _prune_paper_notes(effective_paper_notes, "group_b"),
        memory_context,
        schema_example=_SCHEMA_EXAMPLE,
    )
    t7_shared_context = _build_shared_context_block(
        _prune_paper_notes(effective_paper_notes, "t7"),
        memory_context,
    )

    async def group_a() -> tuple[str, str, str]:
        log.info("Starting Group A tasks: T1 -> T5 -> T6")
        t1_result = await run_t1(group_a_shared_context)
        log.info("T1 done (%d chars)", len(t1_result))
        _register_plain_section_memory(
            working_memory,
            task_key="t1_summary",
            section_key="summary",
            text=t1_result,
        )
        t5_result = await run_t5(group_a_shared_context)
        log.info("T5 done (%d chars)", len(t5_result))
        _register_plain_section_memory(
            working_memory,
            task_key="t5_ablation",
            section_key="ablation",
            text=t5_result,
        )
        t6_result = await run_t6(group_a_shared_context)
        log.info("T6 done (%d chars)", len(t6_result))
        _register_plain_section_memory(
            working_memory,
            task_key="t6_limitations",
            section_key="limitations",
            text=t6_result,
        )
        return t1_result.strip(), t5_result.strip(), t6_result.strip()

    async def group_b() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        log.info("Starting Group B tasks: T2 -> T3 -> T4")
        t2_struct = await _run_structured_dual_section(
            "background",
            parsed_paper,
            full_paper_notes=effective_paper_notes,
            shared_context_block=group_b_shared_context,
        )
        log.info("T2 structured done (%d claims)", len(t2_struct.get("claims", [])))
        _register_structured_section_memory(
            working_memory,
            task_key="t2_background_structured",
            section_key="background",
            payload=t2_struct,
        )
        t2_prior = _build_prior_context("Research Background and Motivation", t2_struct)

        t3_struct = await _run_structured_dual_section(
            "method",
            parsed_paper,
            full_paper_notes=effective_paper_notes,
            shared_context_block=group_b_shared_context,
            prior_context=t2_prior,
        )
        log.info("T3 structured done (%d claims)", len(t3_struct.get("claims", [])))
        _register_structured_section_memory(
            working_memory,
            task_key="t3_method_structured",
            section_key="method",
            payload=t3_struct,
        )
        t3_prior = _build_prior_context("Core Method", t3_struct)

        t4_struct = await _run_structured_dual_section(
            "experiments",
            parsed_paper,
            full_paper_notes=effective_paper_notes,
            shared_context_block=group_b_shared_context,
            prior_context=t3_prior,
        )
        log.info("T4 structured done (%d claims)", len(t4_struct.get("claims", [])))
        _register_structured_section_memory(
            working_memory,
            task_key="t4_experiments_structured",
            section_key="experiments",
            payload=t4_struct,
        )
        return t2_struct, t3_struct, t4_struct

    (t1, t5, t6), (t2_struct, t3_struct, t4_struct) = await asyncio.gather(
        group_a(), group_b()
    )

    t2 = _render_section_markdown("Research Background and Motivation", t2_struct)
    t3 = _render_section_markdown("Core Method", t3_struct)
    t4 = _render_section_markdown("Experiments and Results", t4_struct)
    _register_plain_section_memory(
        working_memory,
        task_key="t2_background",
        section_key="background",
        text=t2,
    )
    _register_plain_section_memory(
        working_memory,
        task_key="t3_method",
        section_key="method",
        text=t3,
    )
    _register_plain_section_memory(
        working_memory,
        task_key="t4_experiments",
        section_key="experiments",
        text=t4,
    )

    log.info("Starting T7 after Group A/B completion")
    t7_result = await run_t7(
        t1,
        t2,
        t3,
        t4,
        t5,
        t6,
        shared_context_block=t7_shared_context,
    )
    log.info("T7 done")
    if working_memory is not None:
        working_memory.remember_task_output("glossary", t7_result["glossary"])
        _register_plain_section_memory(
            working_memory,
            task_key="t7_conclusion",
            section_key="conclusion",
            text=t7_result["conclusion"],
        )
        glossary = t7_result.get("glossary", [])
        for item in glossary:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            explanation = str(item.get("explanation", "")).strip()
            if term and explanation:
                working_memory.terminology_map.setdefault(term, explanation)
        working_memory.set_metric("glossary_count", len(glossary))
        working_memory.set_metric("task_output_count", len(working_memory.task_outputs))
        working_memory.set_metric("observation_count", len(working_memory.observations))
        working_memory.set_metric(
            "open_question_count",
            len([item for item in working_memory.open_questions if item.status == "open"]),
        )
        working_memory.set_metric("draft_claim_count", len(working_memory.draft_claims))
        log.info(
            "WorkingMemory updated after T1-T7: observations=%d open_questions=%d draft_claims=%d glossary=%d",
            len(working_memory.observations),
            len([item for item in working_memory.open_questions if item.status == "open"]),
            len(working_memory.draft_claims),
            len(glossary),
        )

    return {
        "paper_notes": effective_paper_notes,
        "memory_context": memory_context,
        "t1_summary": t1,
        "t2_background": t2,
        "t2_background_structured": t2_struct,
        "t3_method": t3,
        "t3_method_structured": t3_struct,
        "t4_experiments": t4,
        "t4_experiments_structured": t4_struct,
        "t5_ablation": t5,
        "t6_limitations": t6,
        "t7_conclusion": t7_result["conclusion"],
        "glossary": t7_result["glossary"],
    }
