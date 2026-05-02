from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from utils.config import get_httpx_client_kwargs
from utils.logger import get_logger
from utils.repo_paths import to_repo_relative_path

log = get_logger(__name__)

_HTML_PREFIX_RE = re.compile(rb"^\s*<(?:!doctype\s+html|html|head|body)\b", re.I)
_META_REFRESH_RE = re.compile(r"url\s*=\s*['\"]?([^'\";>]+)", re.I)
_SECTION_HEADING_RE = re.compile(
    r"^(abstract|introduction|background|method|methods|approach|experiment|experiments|results|discussion|conclusion|limitations?)$",
    re.I,
)


def _looks_like_pdf(content: bytes, content_type: str) -> bool:
    return "application/pdf" in content_type.lower() or content.lstrip().startswith(
        b"%PDF"
    )


def _looks_like_html(content: bytes, content_type: str) -> bool:
    lowered = content_type.lower()
    if "text/html" in lowered or "application/xhtml" in lowered:
        return True
    return bool(_HTML_PREFIX_RE.match(content[:256]))


def _decode_html_bytes(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _extract_redirect_target(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    meta_refresh = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta_refresh is not None:
        content = str(meta_refresh.get("content") or "")
        match = _META_REFRESH_RE.search(content)
        if match:
            return urljoin(base_url, unquote(match.group(1).strip()))

    redirect_input = soup.find("input", attrs={"id": "redirectURL"})
    if redirect_input is not None:
        value = str(redirect_input.get("value") or "").strip()
        if value:
            return urljoin(base_url, unquote(value))

    canonical = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    if canonical is not None:
        href = str(canonical.get("href") or "").strip()
        if href:
            return urljoin(base_url, href)

    return ""


async def _follow_html_redirect_targets(
    *,
    client: httpx.AsyncClient,
    content: bytes,
    content_type: str,
    base_url: str,
    max_hops: int = 2,
) -> tuple[bytes, str, str]:
    current_content = content
    current_content_type = content_type
    current_url = base_url
    for _ in range(max_hops):
        if not _looks_like_html(current_content, current_content_type):
            break
        current_html = _decode_html_bytes(current_content)
        target = _extract_redirect_target(current_html, current_url)
        if not target or target == current_url:
            break
        try:
            response = await client.get(
                target,
                headers={
                    "User-Agent": "Paper-Agent/1.0",
                    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("HTML redirect follow failed for %s: %s", target, exc)
            break
        current_content = response.content
        current_content_type = str(response.headers.get("content-type") or "")
        current_url = str(response.url)
        if not _looks_like_html(current_content, current_content_type):
            break
    return current_content, current_content_type, current_url


async def download_source_document(
    source_url: str,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=120.0, follow_redirects=True, **get_httpx_client_kwargs()
    ) as client:
        response = await client.get(
            source_url,
            headers={
                "User-Agent": "Paper-Agent/1.0",
                "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        content = response.content
        content_type = str(response.headers.get("content-type") or "")
        final_url = str(response.url)

        if _looks_like_pdf(content, content_type):
            final_path = output_path.with_suffix(".pdf")
            final_path.write_bytes(content)
            return {
                "source_type": "pdf",
                "source_path": to_repo_relative_path(final_path),
                "pdf_path": to_repo_relative_path(final_path),
                "source_url": final_url,
                "content_type": content_type,
            }

        if _looks_like_html(content, content_type):
            content, content_type, final_url = await _follow_html_redirect_targets(
                client=client,
                content=content,
                content_type=content_type,
                base_url=final_url,
            )
            if _looks_like_pdf(content, content_type):
                final_path = output_path.with_suffix(".pdf")
                final_path.write_bytes(content)
                return {
                    "source_type": "pdf",
                    "source_path": to_repo_relative_path(final_path),
                    "pdf_path": to_repo_relative_path(final_path),
                    "source_url": final_url,
                    "content_type": content_type or "application/pdf",
                }

            html = _decode_html_bytes(content)
            final_path = output_path.with_suffix(".html")
            final_path.write_text(html, encoding="utf-8")
            return {
                "source_type": "html",
                "source_path": to_repo_relative_path(final_path),
                "pdf_path": "",
                "source_url": final_url,
                "content_type": content_type or "text/html",
            }

    raise RuntimeError(
        f"Downloaded source from {source_url} but it was neither a PDF nor an HTML document"
    )


def extract_html_document_bundle(
    html_path: Path,
    *,
    source_url: str = "",
    max_chars: int = 24000,
    max_sections: int = 18,
) -> dict[str, Any]:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = ""
    for meta_key in ("citation_title", "og:title", "dc.title"):
        meta = soup.find("meta", attrs={"name": meta_key}) or soup.find(
            "meta", attrs={"property": meta_key}
        )
        if meta is not None:
            title = str(meta.get("content") or "").strip()
            if title:
                break
    if not title and soup.title is not None:
        title = soup.title.get_text(" ", strip=True)

    abstract = ""
    for meta_key in (
        "citation_abstract",
        "description",
        "og:description",
        "dc.description",
    ):
        meta = soup.find("meta", attrs={"name": meta_key}) or soup.find(
            "meta", attrs={"property": meta_key}
        )
        if meta is not None:
            abstract = str(meta.get("content") or "").strip()
            if abstract:
                break

    root = soup.find("article") or soup.find("main") or soup.body or soup
    section_blocks: list[dict[str, str]] = []
    current_heading = "Body"
    current_lines: list[str] = []
    flat_lines: list[str] = []

    for node in root.find_all(["h1", "h2", "h3", "p", "li"], recursive=True):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        if node.name in {"h1", "h2", "h3"}:
            if current_lines:
                section_blocks.append(
                    {
                        "heading": current_heading,
                        "content": "\n".join(current_lines).strip(),
                    }
                )
            current_heading = text
            current_lines = []
            continue
        current_lines.append(text)
        flat_lines.append(text)
        if sum(len(line) for line in flat_lines) >= max_chars:
            break

    if current_lines:
        section_blocks.append(
            {
                "heading": current_heading,
                "content": "\n".join(current_lines).strip(),
            }
        )

    if not abstract:
        for block in section_blocks:
            if _SECTION_HEADING_RE.match(block.get("heading", "")):
                abstract = block.get("content", "")[:1200].strip()
                if abstract:
                    break

    plain_text = "\n\n".join(
        f"{block['heading']}\n{block['content']}".strip()
        for block in section_blocks[:max_sections]
        if block.get("content")
    ).strip()
    if len(plain_text) > max_chars:
        plain_text = plain_text[: max_chars - 3].rstrip() + "..."

    return {
        "title": title,
        "abstract": abstract,
        "source_url": source_url,
        "sections": section_blocks[:max_sections],
        "plain_text": plain_text,
        "html_path": to_repo_relative_path(html_path),
    }
