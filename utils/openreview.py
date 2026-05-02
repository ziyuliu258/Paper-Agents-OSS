from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger

log = get_logger(__name__)

OPENREVIEW_API = "https://api2.openreview.net"

# Venue ID patterns for conferences hosted on OpenReview.
# {year} is replaced at query time.
OPENREVIEW_VENUES: dict[str, str] = {
    "ICLR": "ICLR.cc/{year}/Conference",
    "NEURIPS": "NeurIPS.cc/{year}/Conference",
    "ICML": "ICML.cc/{year}/Conference",
}


def supports_venue(venue_name: str) -> bool:
    return venue_name.strip().upper() in OPENREVIEW_VENUES


def _venue_id(venue_name: str, year: int) -> str:
    pattern = OPENREVIEW_VENUES[venue_name.strip().upper()]
    return pattern.format(year=year)


def _normalize_paper(note: dict[str, Any], venue_name: str) -> dict[str, Any]:
    content = note.get("content") or {}

    def _val(field: str, default: Any = "") -> Any:
        entry = content.get(field)
        if isinstance(entry, dict):
            return entry.get("value", default)
        return entry if entry is not None else default

    title = str(_val("title")).strip()
    abstract = str(_val("abstract")).strip()
    authors = _val("authors", [])
    if isinstance(authors, str):
        authors = [authors]
    authors = [str(a).strip() for a in authors if str(a).strip()]

    pdf_rel = str(_val("pdf")).strip()
    if pdf_rel and not pdf_rel.startswith("http"):
        pdf_url = f"https://openreview.net{pdf_rel}"
    else:
        pdf_url = pdf_rel

    note_id = note.get("id") or ""
    url = f"https://openreview.net/forum?id={note_id}" if note_id else ""

    cdate = note.get("cdate") or note.get("pdate") or 0
    if isinstance(cdate, (int, float)) and cdate > 1e12:
        cdate = cdate / 1000.0
    try:
        paper_date = datetime.fromtimestamp(cdate, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        paper_date = ""

    return {
        "paper_id": note_id,
        "arxiv_id": "",
        "doi": "",
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "url": url,
        "pdf_url": pdf_url,
        "venue": venue_name.strip().upper(),
        "date": paper_date,
        "citations": 0,
        "institutions": [],
        "source": "openreview",
    }


async def fetch_openreview_papers(
    venue_name: str,
    year: int,
    max_results: int = 500,
) -> list[dict[str, Any]]:
    if not supports_venue(venue_name):
        return []

    vid = _venue_id(venue_name, year)
    invitation = f"{vid}/-/Submission"

    candidates: list[dict[str, Any]] = []
    offset = 0
    page_limit = min(max_results, 1000)

    while len(candidates) < max_results:
        params: dict[str, Any] = {
            "invitation": invitation,
            "content.venueid": vid,
            "limit": page_limit,
            "offset": offset,
        }
        url = f"{OPENREVIEW_API}/notes?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
                response = await client.get(url)
                response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.warning("OpenReview fetch failed for %s %d (offset=%d): %s", venue_name, year, offset, exc)
            break

        notes = payload.get("notes", [])
        if not notes:
            break

        for note in notes:
            candidates.append(_normalize_paper(note, venue_name))
            if len(candidates) >= max_results:
                break

        if len(notes) < page_limit:
            break
        offset += len(notes)

    log.info("OpenReview: fetched %d papers for %s %d", len(candidates), venue_name, year)
    return candidates
