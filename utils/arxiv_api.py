from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger

log = get_logger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _entry_to_candidate(entry: ET.Element) -> dict[str, Any]:
    entry_id = _clean_text(entry.findtext("atom:id", default="", namespaces=ARXIV_NS))
    arxiv_id = entry_id.rstrip("/").split("/")[-1]
    pdf_url = ""
    for link in entry.findall("atom:link", ARXIV_NS):
        title = link.attrib.get("title", "")
        if title == "pdf":
            pdf_url = link.attrib.get("href", "")
            break

    authors = [
        _clean_text(author.findtext("atom:name", default="", namespaces=ARXIV_NS))
        for author in entry.findall("atom:author", ARXIV_NS)
    ]

    return {
        "paper_id": arxiv_id,
        "arxiv_id": arxiv_id,
        "doi": "",
        "title": _clean_text(entry.findtext("atom:title", default="", namespaces=ARXIV_NS)),
        "authors": [name for name in authors if name],
        "abstract": _clean_text(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS)),
        "url": entry_id,
        "pdf_url": pdf_url,
        "venue": "arXiv",
        "date": _clean_text(entry.findtext("atom:published", default="", namespaces=ARXIV_NS))[:10],
        "citations": 0,
        "institutions": [],
        "source": "arxiv",
    }


async def search_arxiv(query: str, max_results: int = 20) -> list[dict[str, Any]]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API_URL}?{urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        log.warning("ArXiv search failed for query=%s: %s", query, exc)
        return []

    root = ET.fromstring(response.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    return [_entry_to_candidate(entry) for entry in entries]


async def download_pdf(pdf_url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
    output_path.write_bytes(response.content)
    log.info("Downloaded PDF to %s", output_path)
    return output_path