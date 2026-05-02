from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

import httpx

from utils.config import get_httpx_client_kwargs, load_runtime_config
from utils.logger import get_logger

log = get_logger(__name__)

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SEMANTIC_SCHOLAR_BATCH_API = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = "title,abstract,authors,year,venue,citationCount,externalIds,openAccessPdf,url"


def _get_s2_api_key() -> str:
    providers = load_runtime_config().get("providers", {})
    semantic_provider = providers.get("semantic_scholar", {})
    return str(semantic_provider.get("api_key") or "").strip()


def _normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    external_ids = item.get("externalIds") or {}
    authors = [author.get("name", "").strip() for author in item.get("authors", [])]
    open_access_pdf = item.get("openAccessPdf") or {}
    year = item.get("year")
    return {
        "paper_id": str(external_ids.get("ArXiv") or item.get("paperId") or ""),
        "arxiv_id": str(external_ids.get("ArXiv") or ""),
        "doi": str(external_ids.get("DOI") or ""),
        "title": str(item.get("title") or "").strip(),
        "authors": [name for name in authors if name],
        "abstract": str(item.get("abstract") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "pdf_url": str(open_access_pdf.get("url") or "").strip(),
        "venue": str(item.get("venue") or "").strip(),
        "date": f"{year}-01-01" if year else "",
        "citations": int(item.get("citationCount") or 0),
        "institutions": [],
        "source": "semantic_scholar",
    }


async def search_semantic_scholar(query: str, limit: int = 20) -> list[dict[str, Any]]:
    params = {"query": query, "limit": limit, "fields": FIELDS}
    url = f"{SEMANTIC_SCHOLAR_API}?{urlencode(params)}"
    for attempt, current_limit in enumerate((limit, max(5, limit // 2)), start=1):
        try:
            current_params = {"query": query, "limit": current_limit, "fields": FIELDS}
            current_url = f"{SEMANTIC_SCHOLAR_API}?{urlencode(current_params)}"
            async with httpx.AsyncClient(timeout=30.0, **get_httpx_client_kwargs()) as client:
                headers = {"User-Agent": "Paper-Agent/1.0"}
                api_key = _get_s2_api_key()
                if api_key:
                    headers["x-api-key"] = api_key
                response = await client.get(current_url, headers=headers)
                response.raise_for_status()
            payload = response.json()
            return [_normalize_paper(item) for item in payload.get("data", [])]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt == 1:
                log.warning("Semantic Scholar rate limited for query=%s, retrying with lower limit", query)
                await asyncio.sleep(2.0)
                continue
            log.warning("Semantic Scholar search failed for query=%s: %s", query, exc)
            return []
        except Exception as exc:
            log.warning("Semantic Scholar search failed for query=%s: %s", query, exc)
            return []
    return []


async def enrich_candidates_by_doi(candidates: list[dict[str, Any]], chunk_size: int = 100) -> list[dict[str, Any]]:
    """Batch-enrich candidates (e.g. from DBLP) with abstract, citations, PDF URL via Semantic Scholar DOI lookup."""
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
        doi_to_candidate[c["doi"]] = c

    all_dois = list(doi_to_candidate.keys())
    abstract_updates = 0
    pdf_updates = 0
    fields = "title,abstract,citationCount,externalIds,openAccessPdf"

    for i in range(0, len(all_dois), chunk_size):
        chunk = all_dois[i : i + chunk_size]
        ids = [f"DOI:{doi}" for doi in chunk]
        url = f"{SEMANTIC_SCHOLAR_BATCH_API}?fields={fields}"
        headers = {"User-Agent": "Paper-Agent/1.0"}
        api_key = _get_s2_api_key()
        if api_key:
            headers["x-api-key"] = api_key
        try:
            async with httpx.AsyncClient(timeout=60.0, **get_httpx_client_kwargs()) as client:
                response = await client.post(url, json={"ids": ids}, headers=headers)
                response.raise_for_status()
            results = response.json()
        except Exception as exc:
            log.warning("S2 batch enrichment failed (chunk %d): %s", i, exc)
            if i == 0:
                await asyncio.sleep(2.0)
            continue

        for item in results:
            if item is None:
                continue
            ext_ids = item.get("externalIds") or {}
            doi = str(ext_ids.get("DOI") or "")
            if doi not in doi_to_candidate:
                continue

            c = doi_to_candidate[doi]
            abstract = str(item.get("abstract") or "").strip()
            if abstract and not c.get("abstract"):
                c["abstract"] = abstract
                abstract_updates += 1
            citations = int(item.get("citationCount") or 0)
            if citations > c.get("citations", 0):
                c["citations"] = citations
            arxiv_id = str(ext_ids.get("ArXiv") or "")
            if arxiv_id and not c.get("arxiv_id"):
                c["arxiv_id"] = arxiv_id
            pdf_url = (item.get("openAccessPdf") or {}).get("url", "")
            if pdf_url and not c.get("pdf_url"):
                c["pdf_url"] = pdf_url
                pdf_updates += 1

    log.info(
        "S2 DOI enrichment: abstracts=%d pdf_urls=%d across %d candidates",
        abstract_updates,
        pdf_updates,
        len(need_enrich),
    )
    return candidates
