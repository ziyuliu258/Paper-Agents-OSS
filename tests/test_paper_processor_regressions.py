import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_processor.agent import PaperProcessorAgent


class PaperProcessorRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_processor_continues_when_figure_identification_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            pdf_path = tmp_root / "paper.pdf"
            asset_dir = tmp_root / "assets"
            pdf_path.write_bytes(b"%PDF-1.4\n% mock pdf for processor regression\n")

            with patch(
                "modules.paper_processor.agent.get_page_count",
                return_value=1,
            ), patch(
                "modules.paper_processor.agent._identify_figures",
                side_effect=RuntimeError("mock 403 from upstream pdf tool"),
            ):
                parsed = await PaperProcessorAgent().run(
                    {
                        "paper_id": "pdf-paper",
                        "source_path": str(pdf_path),
                        "source_type": "pdf",
                        "title": "PDF Paper",
                        "job_id": "job-pdf",
                        "job_assets_dir": str(asset_dir),
                    }
                )

        self.assertEqual(parsed["source_type"], "pdf")
        self.assertEqual(parsed["figures"], [])
        self.assertEqual(parsed["figure_index"], {})
        self.assertIn("mock 403", parsed["figure_identification_warning"])


if __name__ == "__main__":
    unittest.main()
