"""Markdown assembly utilities for paper interpretation output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.report_styles import normalize_report_structure_mode


def _append_common_metadata(
    lines: list[str],
    *,
    title_label: str,
    title_value: str,
    venue_label: str,
    venue_value: str,
    pub_date_label: str,
    pub_date_value: str,
    institution_label: str,
    institution_value: str,
    code_repository_label: str,
    code_repository_url: str,
    one_line_summary_label: str,
    one_line_summary: str,
    generated_at_label: str,
    generated_at: str,
    code_repository_warning: str,
) -> None:
    lines.append(f"> **{generated_at_label}:** {generated_at}")
    lines.append(">")
    lines.append(f"> **{title_label}:** {title_value}")
    lines.append(">")
    lines.append(f"> **{venue_label}:** {venue_value}")
    lines.append(">")
    lines.append(f"> **{pub_date_label}:** {pub_date_value}")
    lines.append(">")
    lines.append(f"> **{institution_label}:** {institution_value}")
    lines.append(">")
    if code_repository_url:
        lines.append(f"> **{code_repository_label}:** [{code_repository_url}]({code_repository_url})")
        lines.append(">")
    lines.append(f"> **{one_line_summary_label}:** {one_line_summary}")
    lines.append("")
    if code_repository_warning:
        lines.append("> **Warning**")
        lines.append(f"> {code_repository_warning}")
        lines.append("")


def _append_glossary(
    lines: list[str],
    glossary: list[dict[str, str]] | None,
    *,
    heading: str,
    term_label: str,
    explanation_label: str,
) -> None:
    if not glossary:
        return
    lines.append(f"{heading}\n")
    lines.append(f"| {term_label} | {explanation_label} |")
    lines.append("|------|-------------|" if term_label == "Term" else "|------|------|")
    for item in glossary:
        term = item.get("term", "")
        explanation = item.get("explanation", "")
        lines.append(f"| {term} | {explanation} |")
    lines.append("")


def _append_footer(lines: list[str], *, disclaimer: str) -> None:
    lines.append("---")
    lines.append(disclaimer)
    lines.append("")


def build_markdown(
    *,
    title_cn: str,
    title_en: str,
    venue: str,
    pub_date: str,
    institution: str,
    code_repository_url: str = "",
    code_repository_source: str = "",
    code_repository_warning: str = "",
    generated_at: str,
    one_line_summary: str,
    background: str,
    method: str,
    experiments: str,
    ablation: str,
    limitations: str,
    conclusion: str,
    glossary: list[dict[str, str]] | None = None,
    figures: list[dict[str, Any]] | None = None,
    structure_mode: str = "classic",
) -> str:
    normalized_mode = normalize_report_structure_mode(structure_mode)
    lines: list[str] = []

    lines.append(f"# {title_cn}\n\n")
    _append_common_metadata(
        lines,
        title_label="原文标题",
        title_value=title_en,
        venue_label="发表期刊/会议",
        venue_value=venue,
        pub_date_label="发表时间",
        pub_date_value=pub_date,
        institution_label="第一/通讯作者单位",
        institution_value=institution,
        code_repository_label="代码仓库",
        code_repository_url=code_repository_url,
        one_line_summary_label="一句话总结",
        one_line_summary=one_line_summary,
        generated_at_label="生成时间",
        generated_at=generated_at,
        code_repository_warning=code_repository_warning,
    )

    if normalized_mode == "pmrc":
        lines.append("## 一、问题定义与研究动机\n")
        lines.append(background)
        lines.append("")

        lines.append("## 二、方法概览与关键机制\n")
        lines.append(method)
        lines.append("")

        lines.append("## 三、结果、对比与消融\n")
        lines.append(experiments)
        lines.append("")
        if ablation.strip():
            lines.append(ablation)
            lines.append("")

        lines.append("## 四、结论、局限与启示\n")
        lines.append(conclusion)
        lines.append("")
        if limitations.strip():
            lines.append("### 局限性与未来方向\n")
            lines.append(limitations)
            lines.append("")
    else:
        lines.append("## 一、研究背景与动机\n")
        lines.append(background)
        lines.append("")

        lines.append("## 二、核心方法详解\n")
        lines.append(method)
        lines.append("")

        lines.append("## 三、实验与结果分析\n")
        lines.append(experiments)
        lines.append("")

        lines.append("## 四、消融实验\n")
        lines.append(ablation)
        lines.append("")

        lines.append("## 五、局限性与未来方向\n")
        lines.append(limitations)
        lines.append("")

        lines.append("## 六、总结与评价\n")
        lines.append(conclusion)
        lines.append("")

    _append_glossary(
        lines,
        glossary,
        heading="## 专有名词解释",
        term_label="术语",
        explanation_label="解释",
    )
    _append_footer(lines, disclaimer="*本文由 Paper Agent 自动生成，解读仅供参考。*")

    return "\n".join(lines)


def build_markdown_en(
    *,
    title_en: str,
    venue: str,
    pub_date: str,
    institution: str,
    code_repository_url: str = "",
    code_repository_source: str = "",
    code_repository_warning: str = "",
    generated_at: str,
    one_line_summary: str,
    background: str,
    method: str,
    experiments: str,
    ablation: str,
    limitations: str,
    conclusion: str,
    glossary: list[dict[str, str]] | None = None,
    figures: list[dict[str, Any]] | None = None,
    structure_mode: str = "classic",
) -> str:
    normalized_mode = normalize_report_structure_mode(structure_mode)
    lines: list[str] = []

    lines.append(f"# {title_en}\n\n")
    _append_common_metadata(
        lines,
        title_label="Original title",
        title_value=title_en,
        venue_label="Venue",
        venue_value=venue,
        pub_date_label="Publication date",
        pub_date_value=pub_date,
        institution_label="First/corresponding author institution",
        institution_value=institution,
        code_repository_label="Code repository",
        code_repository_url=code_repository_url,
        one_line_summary_label="One-line summary",
        one_line_summary=one_line_summary,
        generated_at_label="Generated at",
        generated_at=generated_at,
        code_repository_warning=code_repository_warning,
    )

    if normalized_mode == "pmrc":
        lines.append("## 1. Problem and Motivation\n")
        lines.append(background)
        lines.append("")

        lines.append("## 2. Method and Key Mechanisms\n")
        lines.append(method)
        lines.append("")

        lines.append("## 3. Results, Comparisons, and Ablations\n")
        lines.append(experiments)
        lines.append("")
        if ablation.strip():
            lines.append(ablation)
            lines.append("")

        lines.append("## 4. Conclusions, Limitations, and Takeaways\n")
        lines.append(conclusion)
        lines.append("")
        if limitations.strip():
            lines.append("### Limitations and Future Directions\n")
            lines.append(limitations)
            lines.append("")
    else:
        lines.append("## 1. Research Background and Motivation\n")
        lines.append(background)
        lines.append("")

        lines.append("## 2. Core Method\n")
        lines.append(method)
        lines.append("")

        lines.append("## 3. Experiments and Results\n")
        lines.append(experiments)
        lines.append("")

        lines.append("## 4. Ablation Studies\n")
        lines.append(ablation)
        lines.append("")

        lines.append("## 5. Limitations and Future Directions\n")
        lines.append(limitations)
        lines.append("")

        lines.append("## 6. Overall Assessment\n")
        lines.append(conclusion)
        lines.append("")

    _append_glossary(
        lines,
        glossary,
        heading="## Glossary",
        term_label="Term",
        explanation_label="Explanation",
    )
    _append_footer(
        lines,
        disclaimer="*This document was generated automatically by Paper Agent and should be used as a reference only.*",
    )

    return "\n".join(lines)


def save_markdown(content: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
