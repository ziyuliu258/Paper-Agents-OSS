from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from bs4 import BeautifulSoup

from utils.arxiv_api import download_pdf
from utils.config import get_httpx_client_kwargs

_SAFE_CACHE_STEM_RE = re.compile(r"[^\w.-]+")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _safe_cache_stem(value: Any, *, fallback: str = "unknown") -> str:
    normalized = _SAFE_CACHE_STEM_RE.sub("_", str(value or "").strip())
    normalized = normalized.strip("._")
    return normalized or fallback


def _extract_section_text(html: str, heading_keywords: list[str]) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for heading in soup.find_all(["h1", "h2", "h3"]):
        title = _clean_text(heading.get_text(" ", strip=True)).lower()
        if any(keyword in title for keyword in heading_keywords):
            texts: list[str] = []
            for sib in heading.find_all_next():
                if sib.name in {"h1", "h2", "h3"}:
                    break
                text = _clean_text(sib.get_text(" ", strip=True))
                if text:
                    texts.append(text)
            return "\n\n".join(texts[:8])
    return ""


async def fetch_aic_from_ar5iv(arxiv_id: str) -> dict[str, str]:
    if not arxiv_id:
        return {}
    url = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
        response = await client.get(url)
        response.raise_for_status()
    html = response.text
    return {
        "abstract": _extract_section_text(html, ["abstract"]),
        "introduction": _extract_section_text(html, ["introduction"]),
        "conclusion": _extract_section_text(html, ["conclusion", "discussion"]),
    }


def extract_aic_from_pdf(pdf_path: Path) -> dict[str, str]:
    doc = pymupdf.open(str(pdf_path))
    try:
        pages = []
        for idx in list(range(min(2, len(doc)))) + list(range(max(0, len(doc) - 2), len(doc))):
            text = _clean_text(doc[idx].get_text("text"))
            if text:
                pages.append(text)
        joined = "\n\n".join(pages)
        return {
            "abstract": joined[:4000],
            "introduction": joined[:4000],
            "conclusion": joined[-4000:],
        }
    finally:
        doc.close()


async def enrich_candidate_with_aic(candidate: dict[str, Any], cache_dir: Path | None = None) -> dict[str, Any]:
    arxiv_id = str(candidate.get("arxiv_id") or "")
    aic: dict[str, str] = {}
    cache_file = None
    cache_stem = _safe_cache_stem(candidate.get("paper_id"), fallback="unknown-paper")
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_stem}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    candidate["aic"] = cached.get("aic", {})
                    candidate["aic_text"] = cached.get("aic_text", "")
                    return candidate
            except Exception:
                pass

    if arxiv_id:
        try:
            aic = await fetch_aic_from_ar5iv(arxiv_id)
        except Exception:
            aic = {}

    if not any(aic.values()):
        local_pdf = candidate.get("local_pdf_path")
        if local_pdf and Path(local_pdf).exists():
            aic = extract_aic_from_pdf(Path(local_pdf))
        else:
            pdf_url = str(candidate.get("pdf_url") or "")
            if pdf_url and cache_dir is not None:
                temp_pdf = cache_dir / f"{cache_stem}.pdf"
                try:
                    await download_pdf(pdf_url, temp_pdf)
                    candidate["local_pdf_path"] = str(temp_pdf)
                    aic = extract_aic_from_pdf(temp_pdf)
                except Exception:
                    aic = {}

    candidate["aic"] = aic
    candidate["aic_text"] = "\n\n".join(
        part for part in [aic.get("abstract", ""), aic.get("introduction", ""), aic.get("conclusion", "")] if part
    ).strip()

    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "paper_id": candidate.get("paper_id"),
                    "title": candidate.get("title"),
                    "aic": candidate.get("aic", {}),
                    "aic_text": candidate.get("aic_text", ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return candidate
