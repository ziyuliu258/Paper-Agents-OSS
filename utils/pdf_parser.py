"""PyMuPDF wrapper — used ONLY for image extraction from PDF pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from utils.logger import get_logger
from utils.repo_paths import to_repo_relative_path

log = get_logger(__name__)

_MIN_IMAGE_DIM = 80
_MIN_PIXEL_AREA = 20_000
_MIN_PAGE_COVERAGE = 0.03
_LOW_INFO_UNIQUE_COLORS = 4
_MAX_COLOR_SAMPLES = 4096
_PAGE_RENDER_SCALE = 2.0


def _to_rgb_pixmap(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    """Normalize pixmaps so downstream heuristics see RGB pixels only."""
    if pix.n > 4 or pix.alpha:
        return pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix


def _estimate_unique_colors(pix: pymupdf.Pixmap, max_samples: int = _MAX_COLOR_SAMPLES) -> int:
    """Roughly estimate how much visual information an image contains."""
    pixel_count = pix.width * pix.height
    if pixel_count <= 0:
        return 0

    step = max(1, pixel_count // max_samples)
    channels = pix.n
    sample = pix.samples
    colors: set[tuple[int, ...]] = set()

    for pixel_index in range(0, pixel_count, step):
        offset = pixel_index * channels
        if channels >= 3:
            colors.add((sample[offset], sample[offset + 1], sample[offset + 2]))
        else:
            colors.add((sample[offset],))
        if len(colors) > _LOW_INFO_UNIQUE_COLORS:
            return len(colors)

    return len(colors)


def is_meaningful_pixmap(
    pix: pymupdf.Pixmap,
    *,
    page_coverage: float | None = None,
) -> bool:
    """Filter out tiny, decorative or near-blank image objects."""
    pix = _to_rgb_pixmap(pix)
    if pix.width < _MIN_IMAGE_DIM or pix.height < _MIN_IMAGE_DIM:
        return False
    if pix.width * pix.height < _MIN_PIXEL_AREA:
        return False
    if page_coverage is not None and page_coverage < _MIN_PAGE_COVERAGE:
        return False
    if _estimate_unique_colors(pix) <= _LOW_INFO_UNIQUE_COLORS:
        return False
    return True


def is_meaningful_image_file(image_path: Path) -> bool:
    """Validate already-exported image files before embedding them in Markdown."""
    try:
        pix = pymupdf.Pixmap(str(image_path))
    except Exception:
        return False
    return is_meaningful_pixmap(pix)


def extract_images_from_pages(
    pdf_path: Path,
    page_nums: list[int],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Extract images from specified pages (0-indexed) and save to output_dir.

    Returns list of dicts with keys such as:
    id, page, image_path, order, sort_x, sort_y, coverage, source
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    extracted: list[dict[str, Any]] = []
    img_counter = 0

    for pn in page_nums:
        if pn < 0 or pn >= len(doc):
            continue
        page = doc[pn]
        images = page.get_images(full=True)
        page_area = page.rect.get_area() or 1.0
        page_candidates: list[dict[str, Any]] = []
        seen_keys: set[tuple[int, int, int, int, int]] = set()

        for img_info in images:
            xref = img_info[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                pix = _to_rgb_pixmap(pix)

                rects = page.get_image_rects(xref)
                best_rect = max(rects, key=lambda rect: rect.get_area(), default=None)
                page_coverage = (
                    best_rect.get_area() / page_area if best_rect is not None else None
                )

                if not is_meaningful_pixmap(pix, page_coverage=page_coverage):
                    continue

                if best_rect is not None:
                    dedupe_key = (
                        xref,
                        round(best_rect.x0),
                        round(best_rect.y0),
                        round(best_rect.x1),
                        round(best_rect.y1),
                    )
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)

                page_candidates.append({
                    "pix": pix,
                    "xref": xref,
                    "rect": best_rect,
                    "coverage": float(page_coverage or 0.0),
                    "pixel_area": pix.width * pix.height,
                })
            except Exception as e:
                log.warning("Failed to extract image xref=%d page=%d: %s", xref, pn + 1, e)

        page_candidates.sort(key=lambda item: (
            item["rect"].y0 if item["rect"] is not None else 0,
            item["rect"].x0 if item["rect"] is not None else 0,
            -item["coverage"],
        ))

        for order_on_page, candidate in enumerate(page_candidates, start=1):
            pix = candidate["pix"]
            rect = candidate["rect"]
            coverage = candidate["coverage"]

            try:
                img_counter += 1
                fname = f"page{pn + 1}_img{img_counter}.png"
                out_path = output_dir / fname
                pix.save(str(out_path))

                extracted.append({
                    "id": f"Image_{img_counter}",
                    "page": str(pn + 1),
                    "image_path": to_repo_relative_path(out_path),
                    "order": order_on_page,
                    "sort_x": float(rect.x0) if rect is not None else 0.0,
                    "sort_y": float(rect.y0) if rect is not None else 0.0,
                    "coverage": coverage,
                    "pixel_area": candidate["pixel_area"],
                    "source": "embedded",
                })
                log.info("Extracted %s from page %d", fname, pn + 1)
            except Exception as e:
                log.warning("Failed to save image from page=%d: %s", pn + 1, e)

    doc.close()
    return extracted


def render_page_snapshot(pdf_path: Path, page_num: int, output_dir: Path) -> str:
    """Render a whole-page fallback when embedded images are unusable."""
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        if page_num < 0 or page_num >= len(doc):
            return ""
        page = doc[page_num]
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(_PAGE_RENDER_SCALE, _PAGE_RENDER_SCALE),
            alpha=False,
        )
        out_path = output_dir / f"page{page_num + 1}_fullpage.png"
        pix.save(str(out_path))
        log.info("Rendered fallback snapshot for page %d", page_num + 1)
        return to_repo_relative_path(out_path)
    finally:
        doc.close()


def crop_figure_from_page(
    pdf_path: Path,
    page_num: int,
    bbox: list[float],
    output_path: Path,
    *,
    scale: float = _PAGE_RENDER_SCALE,
    padding: float = 0.01,
) -> str:
    """Crop a figure region from a PDF page using normalized bbox [x0, y0, x1, y1] (0.0-1.0).

    Returns the output path as string, or empty string on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        if page_num < 0 or page_num >= len(doc):
            log.warning("crop_figure_from_page: page %d out of range", page_num)
            return ""
        page = doc[page_num]
        pw, ph = page.rect.width, page.rect.height

        x0 = max(0.0, bbox[0] - padding) * pw
        y0 = max(0.0, bbox[1] - padding) * ph
        x1 = min(1.0, bbox[2] + padding) * pw
        y1 = min(1.0, bbox[3] + padding) * ph

        if x1 <= x0 or y1 <= y0:
            log.warning("crop_figure_from_page: invalid bbox after conversion: [%.1f, %.1f, %.1f, %.1f]", x0, y0, x1, y1)
            return ""

        clip = pymupdf.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
        pix.save(str(output_path))
        log.info("Cropped figure from page %d -> %s (%dx%d)", page_num + 1, output_path.name, pix.width, pix.height)
        return to_repo_relative_path(output_path)
    except Exception as e:
        log.warning("crop_figure_from_page failed for page %d: %s", page_num + 1, e)
        return ""
    finally:
        doc.close()


def get_page_count(pdf_path: Path) -> int:
    doc = pymupdf.open(str(pdf_path))
    count = len(doc)
    doc.close()
    return count
