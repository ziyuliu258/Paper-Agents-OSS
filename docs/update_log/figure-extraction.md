# Figure Extraction: LLM BBox + PyMuPDF Crop

## Overview

Paper Agent uses a two-stage pipeline to extract figures from academic PDFs:

1. **LLM Detection** — Multimodal LLM reads the PDF and returns each figure's bounding box (like YOLO/COCO annotation)
2. **PyMuPDF Crop** — PyMuPDF uses the bbox coordinates to render and crop the exact region from the page

This approach avoids the common pitfalls of embedded image extraction (decorative icons, template watermarks, fragmented vector graphics).

## Architecture

```
PDF ──→ LLM (gem_flash/gem_pro/gpt_pro)
         │
         ├── Figure 1: page=3, bbox=[0.10, 0.05, 0.90, 0.45]
         ├── Table 1:  page=7, bbox=[0.05, 0.50, 0.95, 0.85]
         └── Figure 2: page=5, bbox=[0.08, 0.52, 0.92, 0.95]
         │
         ▼
      PyMuPDF crop_figure_from_page()
         │
         ├── Figure_1_p3.png  (precise crop)
         ├── Table_1_p7.png   (precise crop)
         └── Figure_2_p5.png  (precise crop)
```

## BBox Format

Normalized coordinates `[x0, y0, x1, y1]` where:

- `(0, 0)` = top-left corner of the page
- `(1, 1)` = bottom-right corner of the page
- `x0, y0` = top-left of the figure region
- `x1, y1` = bottom-right of the figure region

Example: `[0.1, 0.05, 0.9, 0.45]` means the figure spans from 10%-90% of page width and 5%-45% of page height.

## Key Files

| File | Role |
|------|------|
| `modules/paper_processor/agent.py` | LLM prompt, bbox parsing, orchestration |
| `utils/pdf_parser.py` | `crop_figure_from_page()` — the actual cropping |

## LLM Prompt (Simplified)

```
For each figure/table, provide:
- id: e.g. "Figure 1"
- page: page number (1-indexed)
- caption: brief description
- bbox: [x0, y0, x1, y1] normalized (0.0-1.0), tightly enclosing the figure INCLUDING caption
```

The LLM returns JSON like:
```json
[
  {"id": "Figure 1", "page": 3, "caption": "System architecture", "bbox": [0.1, 0.05, 0.9, 0.45]},
  {"id": "Table 1", "page": 7, "caption": "Main results", "bbox": [0.05, 0.5, 0.95, 0.85]}
]
```

## Cropping Implementation

```python
# utils/pdf_parser.py
def crop_figure_from_page(pdf_path, page_num, bbox, output_path, scale=2.0, padding=0.01):
    page = doc[page_num]
    pw, ph = page.rect.width, page.rect.height

    # Convert normalized coords to absolute, with 1% padding
    x0 = max(0.0, bbox[0] - padding) * pw
    y0 = max(0.0, bbox[1] - padding) * ph
    x1 = min(1.0, bbox[2] + padding) * pw
    y1 = min(1.0, bbox[3] + padding) * ph

    clip = pymupdf.Rect(x0, y0, x1, y1)
    pix = page.get_pixmap(matrix=Matrix(scale, scale), clip=clip, alpha=False)
    pix.save(output_path)
```

- **scale=2.0** — 2x rendering for crisp output
- **padding=1%** — slight margin to avoid cutting off borders

## Fallback Chain

If LLM fails to return a valid bbox for a figure:

1. **Embedded image extraction** — `page.get_images()` + heuristic filtering (size, coverage, color complexity)
2. **Full-page snapshot** — `page.get_pixmap()` on the entire page as last resort

## BBox Validation

Invalid bboxes are silently discarded (set to `None`), triggering the fallback chain:

- All 4 values must be in `[0.0, 1.0]`
- `x1 > x0` and `y1 > y0`
- Must be a list of exactly 4 numbers

## Why This Works

Multimodal LLMs (Gemini, GPT-4o) understand PDF layout at a semantic level — they can see where a figure starts and ends, including multi-panel figures and their captions. This is fundamentally more reliable than:

- **Embedded image extraction** — fails on vector graphics, picks up decorative elements
- **pdffigures2** — Java dependency, struggles with modern LaTeX templates
- **Rule-based heuristics** — brittle across different paper styles
