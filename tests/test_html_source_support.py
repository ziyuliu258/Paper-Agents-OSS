import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_interpreter.report_auditor import audit_and_repair_report
from modules.paper_interpreter.task_runner import build_paper_notes
from modules.paper_processor.agent import PaperProcessorAgent
from utils.source_documents import download_source_document


class _FakeResponse:
    def __init__(self, *, content: bytes, content_type: str, url: str) -> None:
        self.content = content
        self.headers = {"content-type": content_type}
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        return self._responses[url]


class HtmlSourceSupportTest(unittest.IsolatedAsyncioTestCase):
    async def test_download_source_document_follows_html_redirect_to_pdf(self) -> None:
        html = (
            '<html><head><meta http-equiv="refresh" '
            'content="0; url=https://cdn.example.com/paper.pdf"></head></html>'
        ).encode("utf-8")
        pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
        responses = {
            "https://example.com/source": _FakeResponse(
                content=html,
                content_type="text/html; charset=utf-8",
                url="https://example.com/source",
            ),
            "https://cdn.example.com/paper.pdf": _FakeResponse(
                content=pdf,
                content_type="application/pdf",
                url="https://cdn.example.com/paper.pdf",
            ),
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "paper"
            fake_client = _FakeAsyncClient(responses)
            with patch("utils.source_documents.httpx.AsyncClient", return_value=fake_client):
                payload = await download_source_document(
                    "https://example.com/source",
                    output_path,
                )

            self.assertEqual(payload["source_type"], "pdf")
            self.assertTrue(str(payload["source_path"]).endswith(".pdf"))
            saved_path = Path(tmp_dir) / Path(payload["source_path"]).name
            self.assertTrue(saved_path.exists())
            self.assertTrue(saved_path.read_bytes().startswith(b"%PDF"))

    async def test_processor_extracts_html_bundle_without_pdf_steps(self) -> None:
        html = """
        <html>
          <head>
            <meta name="citation_title" content="HTML Paper" />
            <meta name="citation_abstract" content="A paper served as HTML." />
          </head>
          <body>
            <main>
              <h1>Introduction</h1>
              <p>This paper studies HTML-first delivery for academic reports.</p>
              <h2>Method</h2>
              <p>We extract structured text instead of depending on PDF parsing.</p>
            </main>
          </body>
        </html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            html_path = tmp_root / "paper.html"
            asset_dir = tmp_root / "assets"
            html_path.write_text(html, encoding="utf-8")

            parsed = await PaperProcessorAgent().run(
                {
                    "paper_id": "html-paper",
                    "source_path": str(html_path),
                    "source_type": "html",
                    "title": "",
                    "job_id": "job-html",
                    "job_assets_dir": str(asset_dir),
                }
            )

            self.assertEqual(parsed["source_type"], "html")
            self.assertEqual(parsed["pdf_path"], "")
            self.assertEqual(parsed["figures"], [])
            self.assertEqual(parsed["html_bundle"]["title"], "HTML Paper")
            self.assertIn("HTML-first delivery", parsed["html_bundle"]["plain_text"])

    async def test_build_paper_notes_uses_html_text_path(self) -> None:
        parsed_paper = {
            "paper_id": "html-paper",
            "source_type": "html",
            "source_path": "data/fetch/jobs/job-html/paper.html",
            "figure_index": {},
            "html_bundle": {
                "title": "HTML Paper",
                "abstract": "A paper served as HTML.",
                "source_url": "https://example.com/paper",
                "sections": [
                    {
                        "heading": "Method",
                        "content": "The method extracts sections from HTML and summarizes them.",
                    }
                ],
                "plain_text": "Method\nThe method extracts sections from HTML and summarizes them.",
            },
        }
        source_path = Path("/tmp/html-paper.html")
        fake_notes = json.dumps(
            {
                "metadata": {"title_en": "HTML Paper"},
                "paper_summary": "Summary",
                "problem": [],
                "method_steps": [],
                "main_results": [],
                "limitations": [],
                "glossary_seed": [],
                "figure_highlights": [],
            }
        )
        seen: dict[str, str] = {}

        async def fake_call(model_aliases, messages, **kwargs):
            seen["step_label"] = str(kwargs.get("step_label") or "")
            seen["html_context"] = str(messages[-1]["content"])
            return fake_notes

        with patch(
            "modules.paper_interpreter.task_runner.call_llm_fallback",
            side_effect=fake_call,
        ):
            notes = await build_paper_notes(source_path, parsed_paper)

        self.assertEqual(seen["step_label"], "shared paper_notes (html)")
        self.assertIn("HTML Paper", seen["html_context"])
        self.assertEqual(notes["metadata"]["title_en"], "HTML Paper")

    def test_report_auditor_does_not_treat_html_sections_as_pdf_pages(self) -> None:
        task_results = {
            "t2_background": "Background summary",
            "t2_background_structured": {
                "section": "background",
                "summary": "Background summary",
                "claims": [
                    {
                        "claim": "HTML source claims can still keep evidence anchors.",
                        "evidence": [{"label": "Table 1", "page": 99, "detail": "HTML excerpt"}],
                    }
                ],
                "risks": [],
            },
            "t3_method": "Method summary",
            "t3_method_structured": {
                "section": "method",
                "summary": "Method summary",
                "pipeline_overview": "Pipeline",
                "modules": [],
                "training_objectives": [],
                "claims": [],
                "risks": [],
            },
            "t4_experiments": "Experiment summary",
            "t4_experiments_structured": {
                "section": "experiments",
                "summary": "Experiment summary",
                "claims": [],
                "risks": [],
            },
            "t1_summary": "",
            "t5_ablation": "",
            "t6_limitations": "",
            "t7_conclusion": "",
        }

        updated_results, audit = audit_and_repair_report(
            {
                "source_type": "html",
                "html_bundle": {
                    "sections": [
                        {"heading": "Body", "content": "A long HTML body with evidence snippets."}
                    ]
                },
            },
            task_results,
        )

        self.assertFalse(bool(audit["repaired"]))
        self.assertEqual(
            updated_results["t2_background_structured"]["claims"][0]["claim"],
            "HTML source claims can still keep evidence anchors.",
        )


if __name__ == "__main__":
    unittest.main()
