from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from utils.arxiv_api import search_arxiv
from utils.dblp import fetch_dblp_papers
from utils.dblp import supports_venue as dblp_supports
from utils.pdf_sources import enrich_candidates_with_pdf_urls
from utils.openalex import enrich_candidates_by_doi, fetch_openalex_papers
from utils.openalex import supports_venue as openalex_supports
from utils.openreview import fetch_openreview_papers
from utils.openreview import supports_venue as openreview_supports
from utils.repo_paths import resolve_repo_path
from utils.semantic_scholar import enrich_candidates_by_doi as enrich_candidates_by_doi_s2
from utils.semantic_scholar import search_semantic_scholar

from utils.logger import get_logger

log = get_logger(__name__)

_DBLP_FETCH_SEMAPHORE = asyncio.Semaphore(2)


async def _fetch_dblp_bounded(venue: str, year: int, max_results: int) -> list[dict[str, Any]]:
    async with _DBLP_FETCH_SEMAPHORE:
        return await fetch_dblp_papers(venue, year, max_results=max_results)


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def get_candidate_dedupe_key(candidate: dict[str, Any]) -> str:
    doi = str(candidate.get("doi") or "").strip().lower()
    if doi:
        return doi
    arxiv_id = str(candidate.get("arxiv_id") or "").strip().lower()
    if arxiv_id:
        return arxiv_id
    normalized_title = _normalize_title(str(candidate.get("title") or ""))
    if normalized_title:
        return normalized_title
    return str(candidate.get("paper_id") or "").strip().lower()


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in candidates:
        key = get_candidate_dedupe_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _passes_filters(candidate: dict[str, Any], selection: dict[str, Any]) -> bool:
    preferred_venues = {v.lower() for v in selection.get("preferred_venues", [])}
    preferred_institutions = {i.lower() for i in selection.get("preferred_institutions", [])}
    date_range_days = int(selection.get("date_range_days", 7))
    classic_min_citations = int(selection.get("classic_min_citations", 50))

    candidate_date = candidate.get("date") or ""
    is_recent = False
    if candidate_date:
        try:
            paper_date = date.fromisoformat(candidate_date[:10])
            is_recent = paper_date >= date.today() - timedelta(days=date_range_days)
        except ValueError:
            is_recent = False

    venue = str(candidate.get("venue") or "").lower()
    institutions = {inst.lower() for inst in candidate.get("institutions", [])}
    has_preferred_venue = not preferred_venues or venue in preferred_venues
    has_preferred_institution = not preferred_institutions or bool(institutions & preferred_institutions)
    enough_citations = int(candidate.get("citations") or 0) >= classic_min_citations

    match_tracks: list[str] = []
    if is_recent:
        match_tracks.append("recent")
    if enough_citations:
        match_tracks.append("classic")
    candidate["match_track"] = "+".join(match_tracks) if match_tracks else "none"
    candidate["is_recent"] = is_recent
    candidate["enough_citations"] = enough_citations
    candidate["has_preferred_venue"] = has_preferred_venue
    candidate["has_preferred_institution"] = has_preferred_institution

    passes_classic = enough_citations
    passes_recent = is_recent and has_preferred_venue

    track = str(selection.get("track", "auto")).lower()
    if track == "recent":
        passes_track = passes_recent
    elif track == "classic":
        passes_track = passes_classic
    elif track == "goat":
        passes_track = passes_recent and passes_classic
    else:  # auto
        passes_track = passes_classic or passes_recent

    return passes_track and has_preferred_institution


def _merge_venue_candidates(
    openreview_papers: list[dict[str, Any]],
    openalex_papers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge OpenReview + OpenAlex results for the same venue.

    OpenReview wins for: title, abstract, authors, pdf_url, url.
    OpenAlex wins for: citations, doi, institutions.
    """
    openalex_by_title: dict[str, dict[str, Any]] = {}
    for paper in openalex_papers:
        key = _normalize_title(paper.get("title", ""))
        if key:
            openalex_by_title[key] = paper

    merged: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for paper in openreview_papers:
        key = _normalize_title(paper.get("title", ""))
        if key:
            seen_titles.add(key)
        alex_match = openalex_by_title.get(key)
        if alex_match:
            paper["citations"] = alex_match.get("citations", 0)
            paper["doi"] = alex_match.get("doi") or paper.get("doi", "")
            paper["institutions"] = alex_match.get("institutions") or paper.get("institutions", [])
            paper["arxiv_id"] = alex_match.get("arxiv_id") or paper.get("arxiv_id", "")
        merged.append(paper)

    for paper in openalex_papers:
        key = _normalize_title(paper.get("title", ""))
        if key and key not in seen_titles:
            seen_titles.add(key)
            merged.append(paper)

    return merged


def _build_queries(topics: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        for value in [
            topic.get("name"),
            topic.get("query"),
            topic.get("query_en"),
            *(topic.get("keywords", []) or []),
            *(topic.get("auto_keywords", []) or []),
            *(topic.get("heuristic_keywords", []) or []),
        ]:
            query = str(value or "").strip()
            if not query:
                continue
            normalized = query.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            queries.append(query)
    return queries


async def _fetch_general_search(
    topics: list[dict[str, Any]],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    """General search mode: ArXiv + Semantic Scholar keyword search."""
    per_source_limit = max(10, int(selection.get("candidate_pool_size", 80)) // max(1, len(topics) * 2))

    tasks = []
    for query in _build_queries(topics):
        tasks.append(search_arxiv(query, max_results=per_source_limit))
        tasks.append(search_semantic_scholar(query, limit=per_source_limit))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    candidates: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, BaseException):
            continue
        candidates.extend(list(result))
    return candidates


async def _fetch_venue_first(
    topics: list[dict[str, Any]],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    """Venue-first mode: pull paper lists from preferred venues, then supplement with keyword search."""
    preferred_venues: list[str] = selection.get("preferred_venues", [])
    date_range_days = int(selection.get("date_range_days", 7))
    pool_size = int(selection.get("candidate_pool_size", 80))
    per_venue_limit = max(50, pool_size // max(1, len(preferred_venues)))

    today = date.today()
    year_span = max(1, date_range_days // 365)
    years = list(range(today.year - year_span, today.year + 1))

    # Build venue fetch tasks
    venue_tasks: list[tuple[str, Any]] = []
    for venue in preferred_venues:
        for year in years:
            if openreview_supports(venue):
                venue_tasks.append(("openreview", fetch_openreview_papers(venue, year, max_results=per_venue_limit)))
            if openalex_supports(venue):
                venue_tasks.append(("openalex", fetch_openalex_papers(venue, year, max_results=per_venue_limit)))
            if dblp_supports(venue):
                venue_tasks.append(("dblp", _fetch_dblp_bounded(venue, year, max_results=per_venue_limit)))

    # Supplemental keyword search (smaller limit to avoid drowning venue results)
    supplement_limit = max(5, pool_size // (len(topics) * 4 + 1))
    keyword_tasks: list[Any] = []
    for query in _build_queries(topics):
        keyword_tasks.append(search_arxiv(query, max_results=supplement_limit))
        keyword_tasks.append(search_semantic_scholar(query, limit=supplement_limit))

    all_coros = [t[1] for t in venue_tasks] + keyword_tasks
    results = await asyncio.gather(*all_coros, return_exceptions=True)

    # Separate venue results by source for merging
    openreview_papers: list[dict[str, Any]] = []
    openalex_papers: list[dict[str, Any]] = []
    dblp_papers: list[dict[str, Any]] = []
    venue_count = len(venue_tasks)
    for i, (source_tag, _) in enumerate(venue_tasks):
        result = results[i]
        if isinstance(result, BaseException):
            log.warning("Venue fetch error (%s): %s", source_tag, result)
            continue
        if source_tag == "openreview":
            openreview_papers.extend(list(result))
        elif source_tag == "openalex":
            openalex_papers.extend(list(result))
        else:
            dblp_papers.extend(list(result))

    venue_merged = _merge_venue_candidates(openreview_papers, openalex_papers)
    # Enrich DBLP papers with abstracts + citations from OpenAlex via DOI lookup
    if dblp_papers:
        dblp_papers = await enrich_candidates_by_doi(dblp_papers)
        dblp_papers = await enrich_candidates_by_doi_s2(dblp_papers)
    all_venue = venue_merged + dblp_papers
    log.info("Venue-first: %d OpenReview + %d OpenAlex + %d DBLP -> %d total",
             len(openreview_papers), len(openalex_papers), len(dblp_papers), len(all_venue))

    # Collect keyword supplement results
    keyword_papers: list[dict[str, Any]] = []
    for result in results[venue_count:]:
        if isinstance(result, BaseException):
            continue
        keyword_papers.extend(list(result))

    log.info("Supplemental keyword search: %d papers", len(keyword_papers))
    return all_venue + keyword_papers


async def fetch_candidates(config: dict[str, Any]) -> list[dict[str, Any]]:
    topics = config.get("topics", [])
    selection = config.get("selection", {})
    cache_dir = resolve_repo_path(config.get("storage", {}).get("cache_dir", "data/cache"))
    preferred_venues = selection.get("preferred_venues", [])

    if preferred_venues:
        log.info("Venue-first mode: venues=%s", preferred_venues)
        candidates = await _fetch_venue_first(topics, selection)
    else:
        log.info("General search mode")
        candidates = await _fetch_general_search(topics, selection)

    filtered = [item for item in _dedupe_candidates(candidates) if _passes_filters(item, selection)]
    pdf_cache_dir = cache_dir / "pdf_lookup"
    pdf_probe_budget = max(
        int(selection.get("candidate_pool_size", 80)),
        int(selection.get("semantic_top_k", 8)) * 4,
    )
    probe_candidates = filtered[:pdf_probe_budget]
    if probe_candidates:
        await enrich_candidates_with_pdf_urls(probe_candidates, cache_dir=pdf_cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "candidates_raw.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (cache_dir / "candidates_filtered.json").write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return filtered
