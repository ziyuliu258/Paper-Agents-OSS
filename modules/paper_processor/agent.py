"""Paper Processor Agent: extract key figures/tables from PDF via LLM + PyMuPDF."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from utils.job_paths import get_job_assets_dir
from utils.llm import call_llm_with_pdf
from utils.logger import get_logger
from utils.pdf_parser import crop_figure_from_page, extract_images_from_pages, get_page_count, render_page_snapshot
from utils.repo_paths import resolve_repo_path, to_repo_relative_path
from utils.source_documents import extract_html_document_bundle

log = get_logger(__name__)
_FIGURE_IDENTIFY_MODELS = ("gpt_pro", "gem_flash", "gem_pro")
_HTML_PREFIX_RE = re.compile(rb"^\s*<(?:!doctype\s+html|html|head|body)\b", re.I)


def _normalize_figure_id(fig_id: str) -> str:
    normalized = re.sub(r"\s+", "", str(fig_id).strip().lower())
    normalized = normalized.replace(".", "")
    return normalized


def _pick_best_page_image(images: list[dict[str, Any]]) -> dict[str, Any] | None:
    """For single-figure pages, prefer the dominant visual object."""
    if not images:
        return None
    return max(
        images,
        key=lambda item: (
            float(item.get("coverage", 0.0)),
            int(item.get("pixel_area", 0)),
        ),
    )


def _sort_page_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        images,
        key=lambda item: (
            float(item.get("sort_y", 0.0)),
            float(item.get("sort_x", 0.0)),
            -float(item.get("coverage", 0.0)),
        ),
    )


def _parse_figure_response(resp: str) -> list[dict[str, Any]]:
    """Normalize model output into validated figure metadata."""
    text = resp.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("Figure identification output is not a JSON list")

    figures: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        page = item.get("page")
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue

        fig_id = str(item.get("id", "")).strip()
        caption = str(item.get("caption", "")).strip()
        if not fig_id:
            continue

        bbox = item.get("bbox")
        parsed_bbox = None
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                parsed_bbox = [float(v) for v in bbox]
                if all(0.0 <= v <= 1.0 for v in parsed_bbox) and parsed_bbox[2] > parsed_bbox[0] and parsed_bbox[3] > parsed_bbox[1]:
                    pass  # valid
                else:
                    parsed_bbox = None
            except (TypeError, ValueError):
                parsed_bbox = None

        figures.append({
            "id": fig_id,
            "page": page_num,
            "caption": caption,
            "bbox": parsed_bbox,
        })

    if not figures:
        raise ValueError("Figure identification returned no valid figure entries")

    return figures


def _detect_source_type(source_path: Path, declared_type: str) -> str:
    normalized = str(declared_type or "pdf").strip().lower() or "pdf"
    if normalized == "html":
        return "html"
    try:
        prefix = source_path.read_bytes()[:256]
    except Exception:
        return normalized
    if prefix.lstrip().startswith(b"%PDF"):
        return "pdf"
    if _HTML_PREFIX_RE.match(prefix):
        return "html"
    return normalized


async def _identify_figures(pdf_path: Path, page_count: int) -> list[dict[str, Any]]:
    """LLM reads the PDF directly to identify key figures and tables with bounding boxes."""
    prompt = (
        "Read this academic paper PDF carefully and identify all key figures and tables.\n\n"
        f"The paper has {page_count} pages, numbered from 1.\n\n"
        "For each important figure or table, provide:\n"
        "1. id: the figure/table identifier (e.g. Figure 1, Table 2)\n"
        "2. page: the page number, starting from 1\n"
        "3. caption: a brief English description\n"
        "4. bbox: the bounding box of the figure/table as [x0, y0, x1, y1] in normalized coordinates (0.0 to 1.0), "
        "where (0,0) is the top-left corner and (1,1) is the bottom-right corner of the page. "
        "The bbox should tightly enclose the entire figure/table INCLUDING its caption text.\n\n"
        "Return a JSON array only, for example:\n"
        '[{"id": "Figure 1", "page": 3, "caption": "Overview of the system architecture", "bbox": [0.1, 0.05, 0.9, 0.45]}]\n\n'
        "Return JSON only, with no extra text."
    )
    failures: list[tuple[str, Exception]] = []

    for idx, model_alias in enumerate(_FIGURE_IDENTIFY_MODELS):
        if idx > 0:
            log.warning("Figure identification auto-downgrades to %s", model_alias)
        try:
            resp = await call_llm_with_pdf(
                model_alias,
                pdf_path,
                prompt,
                temperature=0.1,
            )
            figures = _parse_figure_response(resp)
            log.info(
                "Figure identification succeeded with %s (%d figures/tables)",
                model_alias,
                len(figures),
            )
            return figures
        except Exception as e:
            failures.append((model_alias, e))
            log.warning("Figure identification via %s failed: %s", model_alias, e)

    summary = "; ".join(
        f"{alias} -> {type(err).__name__}: {err}"
        for alias, err in failures
    )
    raise RuntimeError(f"Figure identification failed for all candidate models: {summary}")


class PaperProcessorAgent:
    def __init__(self) -> None:
        pass

    async def run(self, paper_meta: dict[str, Any]) -> dict[str, Any]:
        source_path = resolve_repo_path(
            str(paper_meta.get("source_path") or paper_meta.get("pdf_path") or "")
        )
        source_type = _detect_source_type(
            source_path,
            str(paper_meta.get("source_type") or "pdf"),
        )
        paper_name = source_path.stem
        log.info("Processing paper: %s", paper_name)

        if not source_path.exists():
            raise FileNotFoundError(f"Source document not found: {source_path}")

        configured_asset_dir = str(paper_meta.get("job_assets_dir", "")).strip()
        if configured_asset_dir:
            asset_dir = resolve_repo_path(configured_asset_dir)
        else:
            job_id = str(paper_meta.get("job_id", "")).strip()
            if not job_id:
                raise ValueError("job_id is required for paper processing output paths")
            asset_dir = get_job_assets_dir(job_id, paper_name)
        asset_dir.mkdir(parents=True, exist_ok=True)

        if source_type == "html":
            log.info("Source is HTML; extracting structured text bundle instead of PDF figures")
            html_bundle = extract_html_document_bundle(
                source_path,
                source_url=str(paper_meta.get("source_url") or ""),
            )
            parsed_paper: dict[str, Any] = {
                "paper_id": paper_meta.get("paper_id", paper_name),
                "pdf_path": "",
                "source_path": to_repo_relative_path(source_path),
                "source_type": "html",
                "title": paper_meta.get("title", "") or html_bundle.get("title", ""),
                "job_id": paper_meta.get("job_id"),
                "job_results_dir": paper_meta.get("job_results_dir", ""),
                "job_report_path": paper_meta.get("job_report_path", ""),
                "job_assets_dir": to_repo_relative_path(asset_dir),
                "figures": [],
                "figure_index": {},
                "html_bundle": html_bundle,
            }
            return parsed_paper

        pdf_path = source_path
        page_count = get_page_count(pdf_path)
        log.info("PDF has %d pages", page_count)

        # Step 1: LLM reads PDF to identify key figures/tables
        log.info("Identifying key figures/tables via LLM...")
        figure_identification_warning = ""
        try:
            figure_info = await _identify_figures(pdf_path, page_count)
            log.info("Identified %d figures/tables", len(figure_info))
        except Exception as exc:
            figure_info = []
            figure_identification_warning = str(exc).strip()
            log.warning(
                "Figure identification failed; continuing without extracted figures: %s",
                figure_identification_warning,
            )

        # Step 2: crop figures using LLM-provided bounding boxes
        bbox_cropped = 0
        fallback_needed: list[dict[str, Any]] = []

        for fig in figure_info:
            fig["image_path"] = ""
            page = fig.get("page")
            if not isinstance(page, int) or page < 1 or page > page_count:
                continue

            bbox = fig.get("bbox")
            if bbox is not None:
                safe_id = re.sub(r"[^\w]", "_", fig["id"])
                out_path = asset_dir / f"{safe_id}_p{page}.png"
                result = crop_figure_from_page(pdf_path, page - 1, bbox, out_path)
                if result:
                    fig["image_path"] = result
                    bbox_cropped += 1
                    continue

            fallback_needed.append(fig)

        log.info("Cropped %d figures via bbox", bbox_cropped)

        # Step 3: fallback for figures without valid bbox — try embedded image extraction, then full-page snapshot
        if fallback_needed:
            fallback_pages = sorted({f["page"] - 1 for f in fallback_needed})
            extracted_images = extract_images_from_pages(pdf_path, fallback_pages, asset_dir)
            page_to_images: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for img in extracted_images:
                try:
                    page_to_images[int(img["page"])].append(img)
                except (KeyError, TypeError, ValueError):
                    continue

            page_to_fallback: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for fig in fallback_needed:
                page_to_fallback[fig["page"]].append(fig)

            for page, figs_on_page in page_to_fallback.items():
                page_images = _sort_page_images(page_to_images.get(page, []))
                if len(figs_on_page) == 1:
                    chosen = _pick_best_page_image(page_images)
                    if chosen is not None:
                        figs_on_page[0]["image_path"] = chosen["image_path"]
                        continue

                for fig, img in zip(figs_on_page, page_images):
                    fig["image_path"] = img["image_path"]

                unmatched = [f for f in figs_on_page if not f.get("image_path")]
                if unmatched:
                    snapshot = render_page_snapshot(pdf_path, page - 1, asset_dir)
                    if snapshot:
                        for fig in unmatched:
                            fig["image_path"] = snapshot
                        log.info("Page %d: %d figure(s) fell back to full-page snapshot", page, len(unmatched))

            log.info("Fallback extraction handled %d figures", len(fallback_needed))

        parsed_paper: dict[str, Any] = {
            "paper_id": paper_meta.get("paper_id", paper_name),
            "pdf_path": to_repo_relative_path(pdf_path),
            "source_path": to_repo_relative_path(source_path),
            "source_type": "pdf",
            "title": paper_meta.get("title", ""),
            "job_id": paper_meta.get("job_id"),
            "job_results_dir": paper_meta.get("job_results_dir", ""),
            "job_report_path": paper_meta.get("job_report_path", ""),
            "job_assets_dir": to_repo_relative_path(asset_dir),
            "figures": figure_info,
            "figure_index": {
                _normalize_figure_id(str(fig.get("id", ""))): {
                    "id": fig.get("id", ""),
                    "page": fig.get("page"),
                    "caption": fig.get("caption", ""),
                    "image_path": fig.get("image_path", ""),
                }
                for fig in figure_info
                if str(fig.get("id", "")).strip()
            },
        }
        if figure_identification_warning:
            parsed_paper["figure_identification_warning"] = figure_identification_warning
        return parsed_paper
