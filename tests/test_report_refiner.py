import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_interpreter.report_refiner import (
    list_report_variants,
    load_report_variant,
    refine_report_variant,
)


class ReportRefinerTest(unittest.TestCase):
    def test_refine_report_variant_creates_file_backed_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "report.md"
            report_path.write_text(
                "# Smoke Test Paper\n\n"
                "> **One-line summary:** Original summary.\n\n"
                "## 一、研究背景与动机\n\n"
                "背景部分。\n\n"
                "## 二、核心方法详解\n\n"
                "方法部分。\n\n"
                "## 三、实验与结果分析\n\n"
                "实验部分。\n",
                encoding="utf-8",
            )
            report_path.with_name("report.en.md").write_text(
                "# Smoke Test Paper\n\n"
                "## 1. Research Background and Motivation\n\n"
                "Background section.\n",
                encoding="utf-8",
            )
            report_path.with_name("distilled_memory_summary.md").write_text(
                "One-line summary: grounded summary.\n\nMethod distilled claims:\n- The method has two stages.",
                encoding="utf-8",
            )
            report_path.with_name("working_memory.json").write_text(
                json.dumps(
                    {
                        "metrics": {"retrieved_claim_count": 2, "retrieved_evidence_count": 3},
                        "observations": [
                            {"section_key": "method", "kind": "task_output", "summary": "The method uses a two-stage pipeline."}
                        ],
                        "draft_claims": [
                            {"section_key": "method", "claim": "Two-stage design improves robustness."}
                        ],
                        "open_questions": [],
                        "promotion_candidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            async def fake_call_llm_fallback(*args, **kwargs):
                step_label = kwargs.get("step_label", "")
                if step_label == "report refinement planning":
                    return json.dumps(
                        {
                            "label": "Method Focus",
                            "reasoning_summary": "Expand the method section and keep the report grounded.",
                            "actions": [
                                {"type": "locate", "description": "Find the method-related sections."},
                                {"type": "ground", "description": "Use memory artifacts to ground the rewrite."},
                                {"type": "expand", "description": "Add more method detail."},
                                {"type": "polish", "description": "Polish the final narrative."},
                            ],
                        }
                    )
                if step_label == "report refinement rewrite":
                    return (
                        "# Smoke Test Paper\n\n"
                        "> **One-line summary:** Refined summary.\n\n"
                        "## 一、研究背景与动机\n\n"
                        "背景部分。\n\n"
                        "## 二、核心方法详解\n\n"
                        "方法部分，补充了两阶段流程和关键机制。\n\n"
                        "## 三、实验与结果分析\n\n"
                        "实验部分。\n"
                    )
                raise AssertionError(f"Unexpected step_label: {step_label}")

            try:
                with patch(
                    "modules.paper_interpreter.report_refiner.call_llm_fallback",
                    fake_call_llm_fallback,
                ):
                    created = asyncio.run(
                        refine_report_variant(
                            job_id="smoke-job",
                            original_report_path=report_path,
                            original_structure_mode="classic",
                            instruction="把方法部分展开一点，但不要改事实。",
                            target_structure_mode="preserve",
                            detail_level="detailed",
                            base_variant_id="original",
                        )
                    )

                self.assertEqual(created["kind"], "refined")
                self.assertEqual(created["label"], "Method Focus")
                self.assertEqual(created["structure_mode"], "classic")
                self.assertTrue(str(created["variant_id"]).startswith("variant-"))
                self.assertTrue(Path(created["path"]).exists())

                variants = list_report_variants(
                    job_id="smoke-job",
                    original_report_path=report_path,
                    original_structure_mode="classic",
                )
                self.assertEqual(len(variants), 2)
                self.assertEqual(variants[0]["variant_id"], "original")
                self.assertEqual(variants[1]["label"], "Method Focus")

                loaded = load_report_variant(
                    job_id="smoke-job",
                    original_report_path=report_path,
                    original_structure_mode="classic",
                    variant_id=created["variant_id"],
                )
                self.assertIn("两阶段流程", loaded["content"])
            finally:
                shutil.rmtree(report_path.parent / "variants", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
