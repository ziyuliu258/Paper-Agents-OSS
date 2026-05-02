from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger

log = get_logger(__name__)

_SAFE_CACHE_STEM_RE = re.compile(r"[^\w.-]+")
_PDF_META_KEYS = {
    "citation_pdf_url",
    "eprints.document_url",
}


def _safe_cache_stem(value: Any, *, fallback: str = "unknown") -> str:
    normalized = _SAFE_CACHE_STEM_RE.sub("_", str(value or "").strip())
    normalized = normalized.strip("._")
    return normalized or fallback


def _cache_key(candidate: dict[str, Any]) -> str:
    for value in (candidate.get("doi"), candidate.get("paper_id"), candidate.get("url"), candidate.get("title")):
        text = str(value or "").strip()
        if text:
            return _safe_cache_stem(text, fallback="candidate")
    return "candidate"


def _normalize_doi(doi: str) -> str:
    normalized = str(doi or "").strip()
    if normalized.startswith("https://doi.org/"):
        normalized = normalized[len("https://doi.org/") :]
    return normalized.strip()


def _candidate_probe_urls(candidate: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    doi = _normalize_doi(str(candidate.get("doi") or ""))
    if doi:
        urls.append(f"https://doi.org/{doi}")

    for raw_value in (candidate.get("url"), candidate.get("pdf_url")):
        value = str(raw_value or "").strip()
        if not value.startswith(("http://", "https://")):
            continue
        if value in seen:
            continue
        seen.add(value)
        urls.append(value)

    deduped: list[str] = []
    seen.clear()
    for value in urls:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _score_pdf_href(href: str, *, text_hint: str = "", rel_hint: str = "", type_hint: str = "") -> int:
    lowered_href = href.lower()
    score = 0
    if lowered_href.endswith(".pdf"):
        score += 100
    if "/pdf/" in lowered_href or "download" in lowered_href:
        score += 40
    if "pdf" in text_hint:
        score += 20
    if "download" in text_hint:
        score += 10
    if "alternate" in rel_hint:
        score += 10
    if "application/pdf" in type_hint:
        score += 40
    return score


def _extract_pdf_url_from_html(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    scored_urls: list[tuple[int, str]] = []

    def _add_candidate(raw_url: str, *, text_hint: str = "", rel_hint: str = "", type_hint: str = "") -> None:
        href = str(raw_url or "").strip()
        if not href:
            return
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            return
        score = _score_pdf_href(absolute, text_hint=text_hint, rel_hint=rel_hint, type_hint=type_hint)
        if score <= 0:
            return
        scored_urls.append((score, absolute))

    for meta in soup.find_all("meta"):
        key = str(meta.get("name") or meta.get("property") or "").strip().lower()
        if key not in _PDF_META_KEYS:
            continue
        _add_candidate(str(meta.get("content") or ""), text_hint=key)

    for link in soup.find_all("link"):
        rel_hint = " ".join(str(part).strip().lower() for part in (link.get("rel") or []) if str(part).strip())
        type_hint = str(link.get("type") or "").strip().lower()
        _add_candidate(str(link.get("href") or ""), rel_hint=rel_hint, type_hint=type_hint)

    for anchor in soup.find_all("a"):
        text_hint = " ".join(anchor.stripped_strings).strip().lower()
        _add_candidate(str(anchor.get("href") or ""), text_hint=text_hint)

    if not scored_urls:
        return ""

    scored_urls.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored_urls[0][1]


async def _resolve_pdf_url_from_candidate(
    candidate: dict[str, Any],
    *,
    client: httpx.AsyncClient,
) -> str:
    for url in _candidate_probe_urls(candidate):
        try:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Paper-Agent/1.0",
                    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception as exc:
            log.debug("PDF probe failed for %s via %s: %s", candidate.get("paper_id"), url, exc)
            continue

        final_url = str(response.url)
        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/pdf" in content_type or final_url.lower().split("?", 1)[0].endswith(".pdf"):
            return final_url

        html = response.text
        if not html:
            continue
        extracted = _extract_pdf_url_from_html(html, final_url)
        if extracted:
            return extracted
    return ""


async def enrich_candidates_with_pdf_urls(
    candidates: list[dict[str, Any]],
    *,
    cache_dir: Path | None = None,
    concurrency: int = 6,
) -> list[dict[str, Any]]:
    pending = [
        candidate
        for candidate in candidates
        if not candidate.get("pdf_url") and _candidate_probe_urls(candidate)
    ]
    if not pending:
        return candidates

    cache_dir = cache_dir if cache_dir is not None else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    resolved_count = 0
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, **get_httpx_client_kwargs()) as client:
        async def _enrich_one(candidate: dict[str, Any]) -> None:
            nonlocal resolved_count

            cache_file = cache_dir / f"{_cache_key(candidate)}.json" if cache_dir is not None else None
            if cache_file is not None and cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    cached = {}
                cached_pdf_url = str(cached.get("pdf_url") or "").strip()
                if cached_pdf_url:
                    candidate["pdf_url"] = cached_pdf_url
                    return

            async with semaphore:
                pdf_url = await _resolve_pdf_url_from_candidate(candidate, client=client)

            if pdf_url:
                candidate["pdf_url"] = pdf_url
                resolved_count += 1

            if cache_file is not None:
                cache_file.write_text(
                    json.dumps(
                        {
                            "paper_id": candidate.get("paper_id"),
                            "doi": candidate.get("doi"),
                            "url": candidate.get("url"),
                            "pdf_url": candidate.get("pdf_url", ""),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

        await asyncio.gather(*(_enrich_one(candidate) for candidate in pending))

    log.info(
        "Landing-page PDF enrichment resolved %d/%d missing PDF URLs",
        resolved_count,
        len(pending),
    )
    return candidates
