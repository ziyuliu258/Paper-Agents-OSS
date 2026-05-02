import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.job_summaries import build_job_report_summary, get_job_memory_artifact_paths
from utils.job_paths import get_job_report_path, get_job_results_dir
from utils.repo_paths import to_repo_relative_path


class ReportMemoryArtifactSummaryTest(unittest.TestCase):
    def test_job_report_summary_detects_memory_artifacts(self) -> None:
        job_id = "report-memory-artifact-test"
        results_dir = get_job_results_dir(job_id)
        artifact_paths = get_job_memory_artifact_paths(job_id)
        report_path = get_job_report_path(job_id)
        try:
            results_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text("# Smoke Report\n\nbody", encoding="utf-8")
            artifact_paths["selector_diagnostics"].write_text(
                '{"candidate_count": 18, "ranked_count": 6, "selection_memory": "abc", "selection_memory_bundle": {"high_level_digest": [1,2], "priority_claims": [1], "related_papers": [1,2,3] }, "selected": {"paper_id": "smoke"}}',
                encoding="utf-8",
            )
            artifact_paths["working_memory"].write_text(
                '{"job_id": "smoke", "metrics": {"memory_extraction_prompt_chars": 17030, "memory_extraction_candidate_count": 8, "memory_extraction_original_candidate_count": 12}, "promotion_candidates": [{"status": "accepted"}, {"status": "review_required"}, {"status": "accepted"}]}',
                encoding="utf-8",
            )
            artifact_paths["distilled_memory_summary"].write_text(
                "Smoke distilled summary.",
                encoding="utf-8",
            )
            artifact_paths["report_audit"].write_text(
                '{"warning": true, "issues": [{"severity": "high"}, {"severity": "medium"}]}',
                encoding="utf-8",
            )

            summary = build_job_report_summary(
                {
                    "id": job_id,
                    "status": "completed",
                    "mode": "auto",
                    "profile_id": 3,
                    "profile_mode": "auto",
                    "profile_assignment_status": "matched",
                    "profile_assignment_note": "Auto matched to Test Profile",
                    "report_path": to_repo_relative_path(report_path),
                    "paper_title": "Smoke Paper",
                    "config_snapshot": {},
                },
                {3: "Test Profile"},
                default_profile_id=None,
            )

            self.assertTrue(summary["has_report"])
            self.assertTrue(summary["has_selector_diagnostics"])
            self.assertTrue(summary["has_working_memory"])
            self.assertTrue(summary["has_distilled_memory_summary"])
            self.assertTrue(summary["has_report_audit"])
            self.assertEqual(summary["title"], "Smoke Report")
            self.assertEqual(summary["diagnostic_snapshot"]["selector_candidate_count"], 18)
            self.assertEqual(summary["diagnostic_snapshot"]["selector_ranked_count"], 6)
            self.assertEqual(summary["diagnostic_snapshot"]["memory_extraction_prompt_chars"], 17030)
            self.assertEqual(summary["diagnostic_snapshot"]["promotion_counts"]["accepted"], 2)
            self.assertEqual(summary["diagnostic_snapshot"]["report_audit_issue_count"], 2)
            self.assertTrue(bool(summary["diagnostic_snapshot"]["report_audit_warning"]))
        finally:
            shutil.rmtree(results_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
