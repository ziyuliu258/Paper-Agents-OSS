"""Shared report-structure and refinement style helpers."""

from __future__ import annotations

from typing import Literal

ReportStructureMode = Literal["classic", "pmrc"]
ReportTargetStructureMode = Literal["preserve", "classic", "pmrc"]
ReportDetailLevel = Literal["auto", "concise", "balanced", "detailed"]

_REPORT_STRUCTURE_MODES = {"classic", "pmrc"}
_REPORT_TARGET_STRUCTURE_MODES = {"preserve", "classic", "pmrc"}
_REPORT_DETAIL_LEVELS = {"auto", "concise", "balanced", "detailed"}


def normalize_report_structure_mode(value: str | None, *, default: ReportStructureMode = "classic") -> ReportStructureMode:
    normalized = str(value or "").strip().lower()
    if normalized in _REPORT_STRUCTURE_MODES:
        return normalized  # type: ignore[return-value]
    return default


def normalize_report_target_structure_mode(
    value: str | None,
    *,
    default: ReportTargetStructureMode = "preserve",
) -> ReportTargetStructureMode:
    normalized = str(value or "").strip().lower()
    if normalized in _REPORT_TARGET_STRUCTURE_MODES:
        return normalized  # type: ignore[return-value]
    return default


def normalize_report_detail_level(value: str | None, *, default: ReportDetailLevel = "balanced") -> ReportDetailLevel:
    normalized = str(value or "").strip().lower()
    if normalized in _REPORT_DETAIL_LEVELS:
        return normalized  # type: ignore[return-value]
    return default


def render_structure_heading_spec(structure_mode: str, *, language: str = "zh") -> str:
    normalized_mode = normalize_report_structure_mode(structure_mode)
    normalized_language = "zh" if str(language or "").strip().lower() == "zh" else "en"

    if normalized_mode == "pmrc":
        if normalized_language == "zh":
            return (
                "Use exactly these top-level headings in order:\n"
                "1. ## 一、问题定义与研究动机\n"
                "2. ## 二、方法概览与关键机制\n"
                "3. ## 三、结果、对比与消融\n"
                "4. ## 四、结论、局限与启示"
            )
        return (
            "Use exactly these top-level headings in order:\n"
            "1. ## 1. Problem and Motivation\n"
            "2. ## 2. Method and Key Mechanisms\n"
            "3. ## 3. Results, Comparisons, and Ablations\n"
            "4. ## 4. Conclusions, Limitations, and Takeaways"
        )

    if normalized_language == "zh":
        return (
            "Use exactly these top-level headings in order:\n"
            "1. ## 一、研究背景与动机\n"
            "2. ## 二、核心方法详解\n"
            "3. ## 三、实验与结果分析\n"
            "4. ## 四、消融实验\n"
            "5. ## 五、局限性与未来方向\n"
            "6. ## 六、总结与评价"
        )
    return (
        "Use exactly these top-level headings in order:\n"
        "1. ## 1. Research Background and Motivation\n"
        "2. ## 2. Core Method\n"
        "3. ## 3. Experiments and Results\n"
        "4. ## 4. Ablation Studies\n"
        "5. ## 5. Limitations and Future Directions\n"
        "6. ## 6. Overall Assessment"
    )
