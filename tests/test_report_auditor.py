import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_interpreter.report_auditor import audit_and_repair_report


def _make_pdf(path: Path, *, pages: int) -> None:
    doc = pymupdf.open()
    for idx in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {idx + 1} content")
    doc.save(path)
    doc.close()


class ReportAuditorTest(unittest.TestCase):
    def test_audit_repairs_invalid_grounding_and_rechecks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "paper.pdf"
            _make_pdf(pdf_path, pages=2)

            task_results = {
                "t2_background": "Background summary",
                "t2_background_structured": {
                    "section": "background",
                    "summary": "Background summary",
                    "claims": [
                        {
                            "claim": "A claim tied to a non-existent page.",
                            "evidence": [{"label": "Table 9", "page": 9, "detail": "Missing"}],
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
                {"pdf_path": str(pdf_path)},
                task_results,
            )

            self.assertTrue(bool(audit["repaired"]))
            self.assertTrue(bool(audit["repair_attempted"]))
            self.assertEqual(int(audit["repair_passes"]), 2)
            self.assertEqual(audit["status"], "pass")
            self.assertFalse(bool(audit["warning"]))
            self.assertIn("background", audit["removed_claims_by_section"])
            self.assertEqual(
                audit["removed_claims_by_section"]["background"],
                ["A claim tied to a non-existent page."],
            )
            self.assertEqual(updated_results["t2_background_structured"]["claims"], [])
            self.assertNotIn(
                "A claim tied to a non-existent page.",
                updated_results["t2_background"],
            )

    def test_audit_flags_numeric_consistency_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "paper.pdf"
            _make_pdf(pdf_path, pages=2)

            task_results = {
                "t2_background": "Background summary",
                "t2_background_structured": {
                    "section": "background",
                    "summary": "Background summary",
                    "claims": [],
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
                "t4_experiments": "The model improves accuracy by 4.2 points.",
                "t4_experiments_structured": {
                    "section": "experiments",
                    "summary": "The model improves accuracy by 4.2 points.",
                    "claims": [
                        {
                            "claim": "The model improves accuracy by 4.2 points.",
                            "evidence": [{"label": "+4.2", "page": 2, "detail": "Main gain"}],
                        }
                    ],
                    "risks": [],
                },
                "t1_summary": "Summary",
                "t5_ablation": "",
                "t6_limitations": "",
                "t7_conclusion": "The paper claims a 12 point gain overall.",
            }

            _updated_results, audit = audit_and_repair_report(
                {"pdf_path": str(pdf_path)},
                task_results,
            )

            self.assertFalse(bool(audit["repaired"]))
            self.assertFalse(bool(audit["repair_attempted"]))
            self.assertEqual(audit["status"], "warning")
            self.assertTrue(bool(audit["warning"]))
            self.assertEqual(int(audit["severity_counts"]["medium"]), 1)
            self.assertEqual(
                audit["issues"][0]["issue_type"],
                "consistency",
            )


if __name__ == "__main__":
    unittest.main()
