"""Assemble T1-T7 results into final Markdown with metadata and embedded figures."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.paper_interpreter.translator import review_translation, translate_to_chinese
from utils.job_paths import get_job_report_path, get_job_results_dir
from utils.llm import call_llm_fallback, call_llm_with_image, call_llm_with_pdf
from utils.logger import get_logger
from utils.markdown import build_markdown_en, save_markdown
from utils.pdf_parser import is_meaningful_image_file
from utils.report_styles import normalize_report_structure_mode
from utils.repo_paths import resolve_repo_path

log = get_logger(__name__)
_METADATA_EXTRACT_MODELS = ("gem_flash", "gem_pro", "gpt_pro")
_FIGURE_SECTION_MODELS = ("gem_flash", "gem_pro", "gpt_pro")
_SECTION_LABELS = {
    "background": "Research Background and Motivation",
    "method": "Core Method",
    "experiments": "Experiments and Results",
    "ablation": "Ablation Studies",
}
_VALID_FIGURE_SECTIONS = set(_SECTION_LABELS) | {"none"}


def _normalize_figure_id(fig_id: str) -> str:
    return re.sub(r"[\s.]", "", str(fig_id).strip().lower())


def _default_metadata() -> dict[str, str]:
    return {
        "title_en": "Unknown Paper",
        "title_cn": "",
        "venue": "Unknown",
        "pub_date": "Unknown",
        "institution": "Unknown",
        "code_repository_url": "",
        "code_repository_source": "none",
        "code_repository_warning": "",
    }


def _format_generated_at(dt: datetime | None = None) -> str:
    current = dt or datetime.now()
    return current.strftime("%Y-%m-%d %H:%M")


def _normalize_pub_date(pub_date: str) -> str:
    return str(pub_date).strip()


def _normalize_institution(institution: str) -> str:
    return str(institution).strip()


def _metadata_from_paper_notes(parsed_paper: dict[str, Any]) -> dict[str, str] | None:
    paper_notes = parsed_paper.get("paper_notes") or {}
    metadata = paper_notes.get("metadata")
    if not isinstance(metadata, dict):
        return None

    normalized = {
        "title_en": str(metadata.get("title_en", "")).strip(),
        "title_cn": str(metadata.get("title_cn", "")).strip(),
        "venue": str(metadata.get("venue", "")).strip(),
        "pub_date": _normalize_pub_date(str(metadata.get("pub_date", "")).strip()),
        "institution": _normalize_institution(str(metadata.get("institution", "")).strip()),
        "code_repository_url": str(metadata.get("code_repository_url", "")).strip(),
        "code_repository_source": str(metadata.get("code_repository_source", "")).strip() or "paper",
        "code_repository_warning": str(metadata.get("code_repository_warning", "")).strip(),
    }
    if normalized["title_en"]:
        return normalized
    return None


def _figure_sections_from_paper_notes(parsed_paper: dict[str, Any]) -> dict[str, str]:
    paper_notes = parsed_paper.get("paper_notes") or {}
    figure_highlights = paper_notes.get("figure_highlights", [])
    if not isinstance(figure_highlights, list):
        return {}

    section_map: dict[str, str] = {}
    figure_index = parsed_paper.get("figure_index", {})
    for item in figure_highlights:
        if not isinstance(item, dict):
            continue
        section = str(item.get("role", "")).strip().lower()
        fig_id = str(item.get("id", "")).strip()
        norm_id = _normalize_figure_id(fig_id)
        indexed = figure_index.get(norm_id)
        if section in _VALID_FIGURE_SECTIONS and indexed:
            section_map[str(indexed.get("id", fig_id)).strip()] = section
    return section_map


def _parse_metadata_response(resp: str) -> dict[str, str]:
    text = resp.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Metadata output is not a JSON object")

    meta = {
        "title_en": str(raw.get("title_en", "")).strip(),
        "title_cn": str(raw.get("title_cn", "")).strip(),
        "venue": str(raw.get("venue", "")).strip(),
        "pub_date": str(raw.get("pub_date", "")).strip(),
        "institution": str(raw.get("institution", "")).strip(),
        "code_repository_url": str(raw.get("code_repository_url", "")).strip(),
        "code_repository_source": "paper" if str(raw.get("code_repository_url", "")).strip() else "none",
        "code_repository_warning": "",
    }
    if not meta["title_en"]:
        raise ValueError("Metadata output misses title_en")
    return meta


def _build_html_metadata_context(parsed_paper: dict[str, Any]) -> str:
    html_bundle = parsed_paper.get("html_bundle")
    if not isinstance(html_bundle, dict):
        return ""

    title = str(html_bundle.get("title", "")).strip()
    abstract = str(html_bundle.get("abstract", "")).strip()
    sections = html_bundle.get("sections", [])
    if not isinstance(sections, list):
        sections = []
    section_bits: list[str] = []
    for item in sections[:10]:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading", "")).strip() or "Body"
        content = str(item.get("content", "")).strip()
        if content:
            section_bits.append(f"## {heading}\n{content[:1600]}")
    lines = ["The paper source is extracted HTML rather than PDF."]
    if title:
        lines.append(f"HTML title: {title}")
    if abstract:
        lines.append(f"Abstract:\n{abstract}")
    if section_bits:
        lines.append("Extracted sections:\n" + "\n\n".join(section_bits))
    return "\n\n".join(lines).strip()


async def _extract_metadata(pdf_path: Path) -> dict[str, str]:
    prompt = (
        "Read the paper and extract the following metadata as JSON:\n"
        "{\n"
        '  "title_en": "English paper title",\n'
        '  "title_cn": "Optional Chinese title if clearly inferable, otherwise an empty string",\n'
        '  "venue": "Conference/journal name, or Arxiv if unknown",\n'
        '  "pub_date": "Publication date",\n'
        '  "institution": "First author institution or corresponding author institution",\n'
        '  "code_repository_url": "Exact repository/project code URL explicitly mentioned in the paper text if available, otherwise an empty string"\n'
        "}\n\n"
        "Return JSON only, with no extra text."
    )
    failures: list[tuple[str, Exception]] = []
    for idx, model_alias in enumerate(_METADATA_EXTRACT_MODELS):
        if idx > 0:
            log.warning("Metadata extraction auto-downgrades to %s", model_alias)
        try:
            resp = await call_llm_with_pdf(
                model_alias,
                pdf_path,
                prompt,
                temperature=0.1,
            )
            meta = _parse_metadata_response(resp)
            log.info("Metadata extraction succeeded with %s", model_alias)
            return meta
        except Exception as err:
            failures.append((model_alias, err))
            log.warning("Metadata extraction via %s failed: %s", model_alias, err)

    summary = "; ".join(f"{alias} -> {type(err).__name__}: {err}" for alias, err in failures)
    log.warning("All metadata extraction models failed, using defaults: %s", summary)
    return _default_metadata()


async def _extract_metadata_from_html(parsed_paper: dict[str, Any]) -> dict[str, str]:
    prompt = (
        "Extract the following paper metadata from the provided HTML-derived paper text as JSON:\n"
        "{\n"
        '  "title_en": "English paper title",\n'
        '  "title_cn": "Optional Chinese title if clearly inferable, otherwise an empty string",\n'
        '  "venue": "Conference/journal name, or Arxiv if unknown",\n'
        '  "pub_date": "Publication date",\n'
        '  "institution": "First author institution or corresponding author institution",\n'
        '  "code_repository_url": "Exact repository/project code URL explicitly mentioned in the paper text if available, otherwise an empty string"\n'
        "}\n\n"
        "Return JSON only, with no extra text."
    )
    context = _build_html_metadata_context(parsed_paper)
    if not context:
        return _default_metadata()

    failures: list[tuple[str, Exception]] = []
    for idx, model_alias in enumerate(_METADATA_EXTRACT_MODELS):
        if idx > 0:
            log.warning("Metadata extraction auto-downgrades to %s", model_alias)
        try:
            resp = await call_llm_fallback(
                [model_alias],
                [
                    {"role": "system", "content": "You extract academic paper metadata from text."},
                    {"role": "user", "content": prompt},
                    {"role": "user", "content": context},
                ],
                step_label="metadata extraction (html)",
                temperature=0.1,
                max_tokens=2048,
                step_timeout=180.0,
            )
            meta = _parse_metadata_response(resp)
            log.info("HTML metadata extraction succeeded with %s", model_alias)
            return meta
        except Exception as err:
            failures.append((model_alias, err))
            log.warning("HTML metadata extraction via %s failed: %s", model_alias, err)

    summary = "; ".join(f"{alias} -> {type(err).__name__}: {err}" for alias, err in failures)
    log.warning("All HTML metadata extraction models failed, using defaults: %s", summary)
    return _default_metadata()


def _normalize_repo_url(url: str) -> str:
    cleaned = str(url).strip().strip(".,);]>\"'")
    if not cleaned:
        return ""
    if cleaned.startswith("www."):
        cleaned = f"https://{cleaned}"
    if re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
        return cleaned
    return ""


def _parse_repo_resolution_response(resp: str) -> dict[str, str]:
    raw = json.loads(_strip_code_fence(resp))
    if not isinstance(raw, dict):
        raise ValueError("Repository resolution output is not a JSON object")

    url = _normalize_repo_url(str(raw.get("selected_url", "")))
    source = str(raw.get("source", "")).strip().lower()
    warning = str(raw.get("warning", "")).strip()
    if source not in {"paper_verified", "paper_unverified", "search_candidate", "none"}:
        source = "none"
    if not url:
        source = "none"
        warning = ""

    return {
        "code_repository_url": url,
        "code_repository_source": source,
        "code_repository_warning": warning,
    }


async def _resolve_code_repository(meta: dict[str, str], parsed_paper: dict[str, Any], task_results: dict[str, Any]) -> dict[str, str]:
    title = str(meta.get("title_en", "")).strip()
    if not title:
        return meta

    paper_candidate = _normalize_repo_url(meta.get("code_repository_url", ""))
    paper_summary = str((parsed_paper.get("paper_notes") or {}).get("paper_summary", "")).strip()
    one_line_summary = str(task_results.get("t1_summary", "")).strip()

    messages = [
        {
            "role": "system",
            "content": (
                "You are verifying whether an academic paper has a public code repository. "
                "Use the web search tool when needed. Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Determine the most reliable repository URL for this paper.\n\n"
                f"Paper title: {title}\n"
                f"Paper summary: {paper_summary or one_line_summary or '(not available)'}\n"
                f"Repository URL explicitly mentioned in the paper text: {paper_candidate or '(none)'}\n\n"
                "Selection rules:\n"
                "1. If the paper text explicitly provides a repository URL, treat it as the first candidate.\n"
                "2. Search the web for repositories related to this paper.\n"
                "3. If web search agrees with the paper-mentioned URL, return that URL with source=`paper_verified` and empty warning.\n"
                "4. If the paper mentions a URL but web search cannot confidently confirm it, return that paper URL with source=`paper_unverified` and a short warning.\n"
                "5. If the paper does not mention a URL, but web search finds a likely matching repository, return it with source=`search_candidate` and a short warning that it was inferred from web search and may be inaccurate.\n"
                "6. If there is no credible repository, return source=`none` and an empty URL.\n\n"
                "Return exactly this JSON schema:\n"
                "{\n"
                '  "selected_url": "https://...",\n'
                '  "source": "paper_verified | paper_unverified | search_candidate | none",\n'
                '  "warning": "Short warning text, or empty string when not needed"\n'
                "}"
            ),
        },
    ]

    try:
        resolved = await call_llm_fallback(
            ["gpt_pro"],
            messages,
            step_label="code repository resolution",
            temperature=0.1,
            max_tokens=1200,
            step_timeout=120.0,
            tools=[{"type": "web_search_preview"}],
        )
        meta.update(_parse_repo_resolution_response(resolved))
    except Exception as err:
        if paper_candidate:
            meta["code_repository_url"] = paper_candidate
            meta["code_repository_source"] = "paper_unverified"
            meta["code_repository_warning"] = f"论文正文提到了该仓库链接，但联网校验失败：{err}"
        else:
            log.warning("Code repository resolution failed: %s", err)
    return meta


def _strip_code_fence(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return payload


def _parse_figure_section_response(resp: str) -> str:
    raw = json.loads(_strip_code_fence(resp))
    if not isinstance(raw, dict):
        raise ValueError("Figure section output is not a JSON object")

    section = str(raw.get("section", "")).strip().lower()
    if section not in _VALID_FIGURE_SECTIONS:
        raise ValueError(f"Unsupported figure section: {section}")
    return section


def _build_figure_section_prompt(fig: dict[str, Any]) -> str:
    fig_id = str(fig.get("id", "")).strip() or "Unknown"
    caption = str(fig.get("caption", "")).strip() or "No caption"
    page = fig.get("page", "")
    return (
        "You are curating figures for a paper-interpretation Markdown document. The goal is to help the reader understand the most important ideas quickly, not to include every image.\n"
        "Based on the visual content, the figure/table ID, and the caption, decide which section this figure best belongs to.\n\n"
        "The only valid sections are:\n"
        "1. background: task background, problem setup, motivation, or conceptual overview\n"
        "2. method: framework diagrams, system architecture, workflow, modules, or pipeline illustrations\n"
        "3. experiments: main results, comparisons, quantitative findings, or qualitative examples\n"
        "4. ablation: ablation studies, component contributions, or sensitivity analysis\n"
        "5. none: appendix figure, supplementary material, low-information image, or not worth placing in the main document\n\n"
        f"Figure/Table ID: {fig_id}\n"
        f"Page: {page}\n"
        f"Caption: {caption}\n\n"
        "Return JSON only, for example:\n"
        '{"section": "method"}'
    )


def _build_figure_map(figures: list[dict[str, Any]], results_dir: Path) -> dict[str, str]:
    fig_map: dict[str, str] = {}
    for fig in figures:
        img_path = fig.get("image_path", "")
        if not img_path:
            continue
        abs_path = resolve_repo_path(img_path)
        if not abs_path.exists():
            continue
        if not is_meaningful_image_file(abs_path):
            log.info("Skip low-information figure asset for %s: %s", fig.get("id", ""), abs_path.name)
            continue
        try:
            rel = abs_path.relative_to(results_dir)
        except ValueError:
            rel = abs_path
        fig_id = fig.get("id", "")
        caption = fig.get("caption", "")
        fig_map[fig_id] = f"![{fig_id}: {caption}]({rel.as_posix()})"
    return fig_map


async def _classify_single_figure(fig: dict[str, Any], available_ids: set[str], system_prompt: str) -> tuple[str | None, str | None]:
    fig_id = str(fig.get("id", "")).strip()
    if not fig_id or fig_id not in available_ids:
        return None, None

    img_path = resolve_repo_path(fig.get("image_path", ""))
    if not img_path.exists():
        return fig_id, "none"

    prompt = _build_figure_section_prompt(fig)
    failures: list[tuple[str, Exception]] = []
    for idx, model_alias in enumerate(_FIGURE_SECTION_MODELS):
        if idx > 0:
            log.warning("Figure section classification auto-downgrades to %s for %s", model_alias, fig_id)
        try:
            resp = await call_llm_with_image(
                model_alias,
                img_path,
                prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )
            chosen_section = _parse_figure_section_response(resp)
            log.info("Figure %s assigned to %s by %s", fig_id, chosen_section, model_alias)
            return fig_id, chosen_section
        except Exception as err:
            failures.append((model_alias, err))
            log.warning("Figure section classification via %s failed for %s: %s", model_alias, fig_id, err)

    if failures:
        summary = "; ".join(f"{alias} -> {type(err).__name__}: {err}" for alias, err in failures)
        log.warning("All figure section classifiers failed for %s, defaulting to none: %s", fig_id, summary)
    return fig_id, "none"


async def _classify_figures(figures: list[dict[str, Any]], available_ids: set[str]) -> dict[str, str]:
    section_map: dict[str, str] = {}
    system_prompt = (
        "You are an academic editor selecting the most helpful figures for a paper-interpretation Markdown document. "
        "Judge each figure by whether it helps the reader understand the target section faster, not by whether it appears in the main paper body."
    )
    tasks = [
        _classify_single_figure(fig, available_ids, system_prompt)
        for fig in figures
        if str(fig.get("id", "")).strip() in available_ids
    ]
    if not tasks:
        return section_map

    results = await asyncio.gather(*tasks)
    for fig_id, section in results:
        if not fig_id or section is None:
            continue
        section_map[fig_id] = section
    for fig in figures:
        fig_id = str(fig.get("id", "")).strip()
        if fig_id in section_map:
            fig["section"] = section_map[fig_id]
    return section_map


def _embed_figures_in_section(text: str, fig_map: dict[str, str]) -> str:
    if not fig_map:
        return text

    lines = text.split("\n")
    result_lines: list[str] = []
    inserted: set[str] = set()
    for line in lines:
        result_lines.append(line)
        for fig_id, md_img in fig_map.items():
            norm_id = fig_id.lower().replace(" ", "").replace(".", "")
            norm_line = line.lower().replace(" ", "").replace(".", "")
            if norm_id in norm_line and fig_id not in inserted:
                result_lines.append("")
                result_lines.append(md_img)
                result_lines.append("")
                inserted.add(fig_id)
    return "\n".join(result_lines)


def _ordered_section_figures(
    figures: list[dict[str, Any]],
    fig_map: dict[str, str],
    section_map: dict[str, str],
    section: str,
) -> list[str]:
    figure_ids: list[str] = []
    for fig in figures:
        fig_id = str(fig.get("id", "")).strip()
        if fig_id and fig_id in fig_map and section_map.get(fig_id) == section:
            figure_ids.append(fig_id)
    return figure_ids


def _append_remaining_section_figures(text: str, figure_ids: list[str], fig_map: dict[str, str]) -> str:
    scoped_fig_map = {fig_id: fig_map[fig_id] for fig_id in figure_ids if fig_id in fig_map}
    if not scoped_fig_map:
        return text

    embedded = _find_embedded(text, scoped_fig_map)
    remaining = [scoped_fig_map[fig_id] for fig_id in figure_ids if fig_id not in embedded]
    if not remaining:
        return text

    base_text = text.rstrip()
    suffix = "\n\n".join(remaining)
    if base_text:
        return f"{base_text}\n\n{suffix}"
    return suffix


async def assemble(
    parsed_paper: dict[str, Any],
    task_results: dict[str, Any],
    paper_name: str,
) -> Path:
    source_type = str(parsed_paper.get("source_type") or "pdf").strip().lower() or "pdf"
    pdf_path_raw = str(parsed_paper.get("pdf_path") or "").strip()
    pdf_path = resolve_repo_path(pdf_path_raw) if pdf_path_raw else None
    job_id = str(parsed_paper.get("job_id", "")).strip()
    results_dir_raw = str(parsed_paper.get("job_results_dir", "")).strip()
    report_path_raw = str(parsed_paper.get("job_report_path", "")).strip()
    if not job_id:
        raise ValueError("job_id is required for report assembly")
    results_dir = resolve_repo_path(results_dir_raw) if results_dir_raw else get_job_results_dir(job_id)
    output_path = resolve_repo_path(report_path_raw) if report_path_raw else get_job_report_path(job_id)
    english_output_path = output_path.with_name("report.en.md")
    if "paper_notes" in task_results and "paper_notes" not in parsed_paper:
        parsed_paper["paper_notes"] = task_results["paper_notes"]
    report_options = parsed_paper.get("report_options") if isinstance(parsed_paper.get("report_options"), dict) else {}
    structure_mode = normalize_report_structure_mode(report_options.get("structure_mode"))

    paper_notes_meta = _metadata_from_paper_notes(parsed_paper)
    if paper_notes_meta is not None:
        meta = paper_notes_meta
        log.info("Using metadata extracted from paper_notes")
    else:
        if source_type == "html":
            log.info("Extracting paper metadata from HTML bundle...")
            meta = await _extract_metadata_from_html(parsed_paper)
        else:
            if pdf_path is None:
                raise ValueError("PDF path is required to extract PDF metadata")
            log.info("Extracting paper metadata from PDF...")
            meta = await _extract_metadata(pdf_path)
    meta = await _resolve_code_repository(meta, parsed_paper, task_results)

    fig_map = _build_figure_map(parsed_paper.get("figures", []), results_dir)
    log.info("Figure map: %d figures available for embedding", len(fig_map))

    section_map = _figure_sections_from_paper_notes(parsed_paper)
    if section_map:
        log.info("Using %d figure section assignments from paper_notes", len(section_map))
        for fig in parsed_paper.get("figures", []):
            fig_id = str(fig.get("id", "")).strip()
            if fig_id in section_map:
                fig["section"] = section_map[fig_id]

    missing_ids = {fig_id for fig_id in fig_map if fig_id not in section_map}
    if missing_ids:
        section_map.update(await _classify_figures(parsed_paper.get("figures", []), missing_ids))

    background_ids = _ordered_section_figures(parsed_paper.get("figures", []), fig_map, section_map, "background")
    method_ids = _ordered_section_figures(parsed_paper.get("figures", []), fig_map, section_map, "method")
    experiments_ids = _ordered_section_figures(parsed_paper.get("figures", []), fig_map, section_map, "experiments")
    ablation_ids = _ordered_section_figures(parsed_paper.get("figures", []), fig_map, section_map, "ablation")

    log.info(
        "Figure sections: background=%d method=%d experiments=%d ablation=%d hidden=%d",
        len(background_ids),
        len(method_ids),
        len(experiments_ids),
        len(ablation_ids),
        sum(1 for fig_id in fig_map if section_map.get(fig_id) == "none"),
    )

    background_with_figs = _embed_figures_in_section(
        task_results["t2_background"].strip(),
        {fig_id: fig_map[fig_id] for fig_id in background_ids},
    )
    background_with_figs = _append_remaining_section_figures(background_with_figs, background_ids, fig_map)

    method_with_figs = _embed_figures_in_section(
        task_results["t3_method"].strip(),
        {fig_id: fig_map[fig_id] for fig_id in method_ids},
    )
    method_with_figs = _append_remaining_section_figures(method_with_figs, method_ids, fig_map)

    experiments_with_figs = _embed_figures_in_section(
        task_results["t4_experiments"].strip(),
        {fig_id: fig_map[fig_id] for fig_id in experiments_ids},
    )
    experiments_with_figs = _append_remaining_section_figures(experiments_with_figs, experiments_ids, fig_map)

    ablation_with_figs = _embed_figures_in_section(
        task_results["t5_ablation"].strip(),
        {fig_id: fig_map[fig_id] for fig_id in ablation_ids},
    )
    ablation_with_figs = _append_remaining_section_figures(ablation_with_figs, ablation_ids, fig_map)

    english_md = build_markdown_en(
        title_en=meta.get("title_en", "Unknown Paper"),
        venue=meta.get("venue", "Unknown"),
        pub_date=_normalize_pub_date(meta.get("pub_date", "Unknown")),
        institution=_normalize_institution(meta.get("institution", "Unknown")),
        code_repository_url=_normalize_repo_url(meta.get("code_repository_url", "")),
        code_repository_source=str(meta.get("code_repository_source", "")).strip(),
        code_repository_warning=str(meta.get("code_repository_warning", "")).strip(),
        generated_at=_format_generated_at(),
        one_line_summary=task_results["t1_summary"].strip(),
        background=background_with_figs,
        method=method_with_figs,
        experiments=experiments_with_figs,
        ablation=ablation_with_figs,
        limitations=task_results["t6_limitations"].strip(),
        conclusion=task_results["t7_conclusion"].strip(),
        glossary=task_results.get("glossary"),
        structure_mode=structure_mode,
    )
    log.info("English Markdown assembled (%d chars)", len(english_md))
    save_markdown(english_md, english_output_path)
    log.info("English Markdown saved to %s", english_output_path)

    translated_md = await translate_to_chinese(
        english_md,
        glossary=task_results.get("glossary"),
        paper_title=meta.get("title_en", ""),
        paper_summary=task_results.get("t1_summary", "").strip(),
        style_guidance=str(task_results.get("translation_style_context", "")).strip(),
    )
    log.info("Chinese translation completed (%d chars)", len(translated_md))

    try:
        final_md = await review_translation(translated_md, english_md)
        log.info("Chinese translation review completed (%d chars)", len(final_md))
    except Exception as err:
        log.warning("Translation review failed, using first-pass translation: %s", err)
        final_md = translated_md

    save_markdown(final_md, output_path)
    log.info("Markdown saved to %s", output_path)
    return output_path


def _find_embedded(text: str, fig_map: dict[str, str]) -> set[str]:
    embedded: set[str] = set()
    for fig_id, md_img in fig_map.items():
        if md_img in text:
            embedded.add(fig_id)
    return embedded
