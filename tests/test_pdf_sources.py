import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.pdf_sources import _extract_pdf_url_from_html
from utils.pdf_sources import enrich_candidates_with_pdf_urls


class _FakeResponse:
    def __init__(self, url: str, *, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.text = text
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, _FakeResponse], *args, **kwargs) -> None:
        self._responses = responses

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *args, **kwargs) -> _FakeResponse:
        if url not in self._responses:
            raise AssertionError(f"Unexpected URL: {url}")
        return self._responses[url]


class PdfSourceResolutionTest(unittest.TestCase):
    def test_extract_pdf_url_from_html_prefers_citation_meta(self) -> None:
        html = """
        <html>
          <head>
            <meta name="citation_pdf_url" content="/2024.acl-long.679.pdf" />
          </head>
          <body>
            <a href="/2024.acl-long.679.pdf">PDF</a>
          </body>
        </html>
        """
        resolved = _extract_pdf_url_from_html(html, "https://aclanthology.org/2024.acl-long.679/")
        self.assertEqual(resolved, "https://aclanthology.org/2024.acl-long.679.pdf")

    def test_enrich_candidates_with_pdf_urls_uses_doi_landing_page(self) -> None:
        candidate = {
            "paper_id": "10.18653/V1/2024.ACL-LONG.679",
            "doi": "10.18653/V1/2024.ACL-LONG.679",
            "url": "https://doi.org/10.18653/v1/2024.acl-long.679",
            "pdf_url": "",
        }
        responses = {
            "https://doi.org/10.18653/V1/2024.ACL-LONG.679": _FakeResponse(
                "https://aclanthology.org/2024.acl-long.679/",
                text=(
                    '<html><head><meta name="citation_pdf_url" '
                    'content="https://aclanthology.org/2024.acl-long.679.pdf"></head></html>'
                ),
            ),
        }

        async def _run() -> list[dict]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                with patch("utils.pdf_sources.httpx.AsyncClient", new=lambda *args, **kwargs: _FakeAsyncClient(responses)):
                    return await enrich_candidates_with_pdf_urls([candidate], cache_dir=Path(tmp_dir))

        result = asyncio.run(_run())
        self.assertEqual(result[0]["pdf_url"], "https://aclanthology.org/2024.acl-long.679.pdf")


if __name__ == "__main__":
    unittest.main()
