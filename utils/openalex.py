from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger

log = get_logger(__name__)

OPENALEX_API = "https://api.openalex.org"
MAILTO = "paperagent@users.noreply.github.com"

# Pre-verified OpenAlex source IDs for major venues.
OPENALEX_SOURCES: dict[str, str] = {
    "ICLR": "S4306419637",
    "NEURIPS": "S4306420609",
    "ICML": "S4306419644",
    "AAAI": "S4210191458",
    "IJCAI": "S4393916692",
    "CVPR": "S4210176548",
    "ECCV": "S4306418318",
    "ACL": "S4306420508",
}

# In-memory cache for dynamically looked-up source IDs.
_dynamic_source_cache: dict[str, str | None] = {}


def supports_venue(venue_name: str) -> bool:
    key = venue_name.strip().upper()
    return key in OPENALEX_SOURCES or key in _dynamic_source_cache


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def _normalize_paper(item: dict[str, Any], venue_name: str) -> dict[str, Any]:
    doi_raw = str(item.get("doi") or "")
    doi = doi_raw.replace("https://doi.org/", "").strip()

    authorships = item.get("authorships") or []
    authors = []
    institutions: list[str] = []
    for authorship in authorships:
        name = (authorship.get("author") or {}).get("display_name", "").strip()
        if name:
            authors.append(name)
        for inst in authorship.get("institutions") or []:
            inst_name = inst.get("display_name", "").strip()
            if inst_name and inst_name not in institutions:
                institutions.append(inst_name)

    # Try to find ArXiv ID from locations
    arxiv_id = ""
    for loc in item.get("locations") or []:
        landing = str(loc.get("landing_page_url") or "")
        if "arxiv.org/abs/" in landing:
            arxiv_id = landing.split("arxiv.org/abs/")[-1].strip("/")
            break

    # PDF URL: prefer best_oa_location, then primary_location
    pdf_url = ""
    for loc_key in ("best_oa_location", "primary_location"):
        loc = item.get(loc_key) or {}
        url_candidate = str(loc.get("pdf_url") or "").strip()
        if url_candidate:
            pdf_url = url_candidate
            break

    url = doi_raw if doi_raw.startswith("http") else str(item.get("id") or "")
    publication_date = str(item.get("publication_date") or "")[:10]

    abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))

    # Use ArXiv ID as paper_id when available, else DOI, else OpenAlex ID
    openalex_id = str(item.get("id") or "").replace("https://openalex.org/", "")
    paper_id = arxiv_id or doi or openalex_id

    return {
        "paper_id": paper_id,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": str(item.get("display_name") or item.get("title") or "").strip(),
        "authors": authors,
        "abstract": abstract,
        "url": url,
        "pdf_url": pdf_url,
        "venue": venue_name.strip().upper(),
        "date": publication_date,
        "citations": int(item.get("cited_by_count") or 0),
        "institutions": institutions,
        "source": "openalex",
    }


async def lookup_source_id(venue_name: str) -> str | None:
    key = venue_name.strip().upper()
    if key in OPENALEX_SOURCES:
        return OPENALEX_SOURCES[key]
    if key in _dynamic_source_cache:
        return _dynamic_source_cache[key]

    search_url = f"{OPENALEX_API}/sources?search={quote(venue_name)}&per_page=1&mailto={MAILTO}"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
            response = await client.get(search_url)
            response.raise_for_status()
        results = response.json().get("results", [])
        if results:
            raw_id = str(results[0].get("id", ""))
            source_id = raw_id.replace("https://openalex.org/", "")
            _dynamic_source_cache[key] = source_id
            log.info("OpenAlex: resolved venue '%s' -> %s (%s)", venue_name, source_id, results[0].get("display_name"))
            return source_id
    except Exception as exc:
        log.warning("OpenAlex source lookup failed for '%s': %s", venue_name, exc)

    _dynamic_source_cache[key] = None
    return None


async def fetch_openalex_papers(
    venue_name: str,
    year: int,
    max_results: int = 200,
) -> list[dict[str, Any]]:
    key = venue_name.strip().upper()
    source_id = OPENALEX_SOURCES.get(key)
    if source_id is None:
        source_id = await lookup_source_id(venue_name)
    if source_id is None:
        log.warning("OpenAlex: no source ID for venue '%s', skipping", venue_name)
        return []

    filter_str = f"primary_location.source.id:{source_id},publication_year:{year}"
    candidates: list[dict[str, Any]] = []
    cursor = "*"
    per_page = min(max_results, 200)

    while len(candidates) < max_results:
        params: dict[str, str] = {
            "filter": filter_str,
            "per_page": str(per_page),
            "cursor": cursor,
            "mailto": MAILTO,
            "select": "id,doi,display_name,publication_date,abstract_inverted_index,"
                      "authorships,cited_by_count,best_oa_location,primary_location,"
                      "locations",
        }
        url = f"{OPENALEX_API}/works?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
                response = await client.get(url)
                response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.warning("OpenAlex fetch failed for %s %d: %s", venue_name, year, exc)
            break

        results = payload.get("results", [])
        if not results:
            break

        for item in results:
            candidates.append(_normalize_paper(item, venue_name))
            if len(candidates) >= max_results:
                break

        next_cursor = (payload.get("meta") or {}).get("next_cursor")
        if not next_cursor or len(results) < per_page:
            break
        cursor = next_cursor

    log.info("OpenAlex: fetched %d papers for %s %d", len(candidates), venue_name, year)
    return candidates


async def enrich_candidates_by_doi(candidates: list[dict[str, Any]], chunk_size: int = 50) -> list[dict[str, Any]]:
    """Batch-enrich candidates (e.g. from DBLP) with abstract and citations via OpenAlex DOI lookup."""
    need_enrich = [
        c
        for c in candidates
        if c.get("doi")
        and (
            not c.get("abstract")
            or not c.get("pdf_url")
            or not c.get("arxiv_id")
            or int(c.get("citations") or 0) <= 0
        )
    ]
    if not need_enrich:
        return candidates

    doi_to_candidate: dict[str, dict[str, Any]] = {}
    for c in need_enrich:
        doi_to_candidate[c["doi"].lower()] = c

    all_dois = list(doi_to_candidate.keys())
    abstract_updates = 0
    pdf_updates = 0

    for i in range(0, len(all_dois), chunk_size):
        chunk = all_dois[i : i + chunk_size]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in chunk)
        params: dict[str, str] = {
            "filter": f"doi:{doi_filter}",
            "per_page": str(len(chunk)),
            "mailto": MAILTO,
            "select": "doi,abstract_inverted_index,cited_by_count,best_oa_location,"
                      "authorships,locations",
        }
        url = f"{OPENALEX_API}/works?{urlencode(params)}"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
                response = await client.get(url)
                response.raise_for_status()
            results = response.json().get("results", [])
        except Exception as exc:
            log.warning("OpenAlex DOI enrichment failed (chunk %d): %s", i, exc)
            continue

        for item in results:
            raw_doi = str(item.get("doi") or "")
            doi = raw_doi.replace("https://doi.org/", "").strip().lower()
            if doi not in doi_to_candidate:
                continue

            c = doi_to_candidate[doi]
            abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))
            if abstract and not c.get("abstract"):
                c["abstract"] = abstract
                abstract_updates += 1
            citations = int(item.get("cited_by_count") or 0)
            if citations > c.get("citations", 0):
                c["citations"] = citations

            # Try to find ArXiv ID and PDF URL
            for loc in item.get("locations") or []:
                landing = str(loc.get("landing_page_url") or "")
                if "arxiv.org/abs/" in landing and not c.get("arxiv_id"):
                    c["arxiv_id"] = landing.split("arxiv.org/abs/")[-1].strip("/")
            if not c.get("pdf_url"):
                for loc_key in ("best_oa_location",):
                    loc = item.get(loc_key) or {}
                    pdf = str(loc.get("pdf_url") or "").strip()
                    if pdf:
                        c["pdf_url"] = pdf
                        pdf_updates += 1
                        break

    log.info(
        "OpenAlex DOI enrichment: abstracts=%d pdf_urls=%d across %d candidates",
        abstract_updates,
        pdf_updates,
        len(need_enrich),
    )
    return candidates
