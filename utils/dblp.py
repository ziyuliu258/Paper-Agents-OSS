from __future__ import annotations

import asyncio
import random
import re
from typing import Any
from urllib.parse import quote

import httpx

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger

log = get_logger(__name__)

DBLP_SEARCH_API = "https://dblp.org/search/publ/api"
DBLP_MAX_RETRIES = 3
DBLP_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
DBLP_TIMEOUT = 30.0

# Maps uppercase venue name -> DBLP stream key
DBLP_VENUES: dict[str, str] = {
    "CVPR": "conf/cvpr",
    "IJCAI": "conf/ijcai",
    "ACL": "conf/acl",
    "ECCV": "conf/eccv",
    "EMNLP": "conf/emnlp",
    "ICLR": "conf/iclr",
    "NEURIPS": "conf/nips",
    "ICML": "conf/icml",
    "AAAI": "conf/aaai",
}


def supports_venue(venue_name: str) -> bool:
    return venue_name.strip().upper() in DBLP_VENUES


def _clean_author(name: str) -> str:
    """Remove trailing disambiguation numbers like '0001'."""
    return re.sub(r"\s+\d{4}$", "", name).strip()


def _normalize_paper(hit: dict[str, Any], venue_name: str) -> dict[str, Any]:
    info = hit.get("info") or {}

    title = str(info.get("title") or "").rstrip(".").strip()
    year = str(info.get("year") or "")
    doi = str(info.get("doi") or "").strip()

    # Authors can be a single dict or a list
    raw_authors = (info.get("authors") or {}).get("author") or []
    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]
    authors = []
    for a in raw_authors:
        name = a.get("text", "") if isinstance(a, dict) else str(a)
        name = _clean_author(name)
        if name:
            authors.append(name)

    # Electronic edition URL (usually DOI link)
    ee = info.get("ee") or ""
    if isinstance(ee, list):
        ee = ee[0] if ee else ""
    ee = str(ee).strip()

    url = str(info.get("url") or ee).strip()
    pdf_url = ""  # DBLP doesn't provide direct PDF links

    return {
        "paper_id": doi or url,
        "arxiv_id": "",
        "doi": doi,
        "title": title,
        "authors": authors,
        "abstract": "",  # DBLP doesn't provide abstracts
        "url": ee or url,
        "pdf_url": pdf_url,
        "venue": venue_name.strip().upper(),
        "date": f"{year}-01-01" if year else "",
        "citations": 0,  # DBLP doesn't provide citation counts
        "institutions": [],
        "source": "dblp",
    }


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in DBLP_RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


def _retry_delay(attempt: int) -> float:
    base_delay = min(4.0, 0.75 * (2 ** attempt))
    return base_delay + random.uniform(0.0, 0.25)


async def _fetch_payload(
    client: httpx.AsyncClient,
    *,
    url: str,
    venue_name: str,
    year: int,
    offset: int,
) -> dict[str, Any] | None:
    last_exc: Exception | None = None
    for attempt in range(DBLP_MAX_RETRIES):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
            if attempt < DBLP_MAX_RETRIES - 1 and _is_retryable_exception(exc):
                delay = _retry_delay(attempt)
                log.info(
                    "DBLP retry for %s %d offset=%d after %s (%d/%d, %.2fs)",
                    venue_name,
                    year,
                    offset,
                    type(exc).__name__,
                    attempt + 1,
                    DBLP_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break

    if last_exc is not None:
        log.warning(
            "DBLP fetch failed for %s %d (offset=%d): %s: %s",
            venue_name,
            year,
            offset,
            type(last_exc).__name__,
            last_exc,
        )
    return None


async def fetch_dblp_papers(
    venue_name: str,
    year: int,
    max_results: int = 500,
) -> list[dict[str, Any]]:
    key = venue_name.strip().upper()
    stream_key = DBLP_VENUES.get(key)
    if stream_key is None:
        return []

    candidates: list[dict[str, Any]] = []
    offset = 0
    page_size = min(max_results, 1000)
    query = f"stream:streams/{stream_key}: year:{year}"

    log.info("DBLP: start fetch for %s %d (max_results=%d)", venue_name, year, max_results)
    async with httpx.AsyncClient(timeout=DBLP_TIMEOUT, follow_redirects=True, **get_httpx_client_kwargs()) as client:
        while len(candidates) < max_results:
            q_encoded = quote(query, safe=':/').replace('%20', '+')
            params = f"q={q_encoded}&format=json&h={page_size}&f={offset}"
            url = f"{DBLP_SEARCH_API}?{params}"

            payload = await _fetch_payload(
                client,
                url=url,
                venue_name=venue_name,
                year=year,
                offset=offset,
            )
            if payload is None:
                break

            hits_obj = payload.get("result", {}).get("hits", {})
            hits = hits_obj.get("hit") or []
            if not hits:
                break

            for hit in hits:
                candidates.append(_normalize_paper(hit, venue_name))
                if len(candidates) >= max_results:
                    break

            total = int(hits_obj.get("@total", 0))
            offset += len(hits)
            if offset >= total or len(hits) < page_size:
                break

    log.info("DBLP: fetched %d papers for %s %d", len(candidates), venue_name, year)
    return candidates
