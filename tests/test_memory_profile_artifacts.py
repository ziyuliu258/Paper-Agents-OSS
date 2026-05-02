import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.memory import MemoryManager


class MemoryProfileArtifactTest(unittest.TestCase):
    def test_schema_mismatch_preserves_existing_memory_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            manager = MemoryManager(db_path)
            try:
                profile = manager.create_profile("artifact-profile", "test profile")
                profile_id = int(profile["id"])
                now = time.time()
                writeback_id = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                    (profile_id, "job-1", "paper-1", now),
                ).lastrowid
                manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'claim-1', 'Claim 1', '', 'Body 1', '', 'finding', 'support', 0.8, 'active', 'Body 1', '', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_id, now, now),
                )
                manager._conn.execute(
                    "UPDATE memory_meta SET value = 'legacy-version' WHERE key = 'schema_version'"
                )
                manager._conn.commit()
            finally:
                manager.close()

            reopened = MemoryManager(db_path)
            try:
                claim_count = reopened._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM memory_claims WHERE profile_id = ? AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone()
                self.assertEqual(int(claim_count["cnt"]), 1)
                self.assertTrue(reopened._table_exists("memory_derived_artifacts"))
            finally:
                reopened.close()

    def test_theme_gap_and_survey_snapshots_are_built(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                profile = manager.create_profile("vision", "Computer vision profile")
                profile_id = int(profile["id"])
                now = time.time()
                writeback_id = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at, delta_json) VALUES (?, ?, ?, 'exact', ?, NULL, ?)",
                    (
                        profile_id,
                        "job-a",
                        "paper-a",
                        now,
                        '{"new_entities":[{"name":"Object Tokens","type":"concept"}],"reinforced_claims":[],"challenged_claims":[],"new_debates":[],"impact_score":0.4}',
                    ),
                ).lastrowid

                task_entity = manager._conn.execute(
                    "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) VALUES (?, 'Object Grounding', '目标对齐', 'object grounding', 'task', 'Ground visual objects.', '对视觉对象进行对齐。', 0, 'active', ?, ?, NULL)",
                    (profile_id, now, now),
                ).lastrowid
                method_entity = manager._conn.execute(
                    "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) VALUES (?, 'Temporal Fusion', '时序融合', 'temporal fusion', 'method', 'Fuse temporal signals.', '融合时序信号。', 0, 'active', ?, ?, NULL)",
                    (profile_id, now + 1, now + 1),
                ).lastrowid

                claim_id = manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'claim-a', 'Temporal fusion improves grounding', '时序融合提升对齐', 'Temporal fusion improves grounding accuracy.', '时序融合提升对齐准确率。', 'finding', 'support', 0.9, 'active', 'Temporal fusion improves grounding accuracy.', '时序融合提升对齐准确率。', 'pending', 0, ?, ?, NULL)",
                    (profile_id, writeback_id, now + 2, now + 2),
                ).lastrowid

                manager._conn.execute(
                    "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                    (claim_id, task_entity, now + 2),
                )
                manager._conn.execute(
                    "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                    (claim_id, method_entity, now + 2),
                )
                manager._conn.execute(
                    "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, weight, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'experiments', 'Experiments', '实验', 'Temporal fusion improves grounding accuracy by 3.1 points.', '时序融合提升对齐准确率 3.1 个点。', 'Result on the benchmark.', '基准结果。', 'p.7', 7, 7, 1.0, 0, ?, ?, NULL)",
                    (claim_id, writeback_id, now + 3, now + 3),
                )

                synthesis_id = manager._conn.execute(
                    "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'oq-a', 'open_question', 'How far does temporal fusion scale?', '时序融合能扩展到多大规模？', 'Scaling behavior remains unclear.', '扩展行为仍不清楚。', 0.65, 'active', 'Scaling behavior remains unclear.', '扩展行为仍不清楚。', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_id, now + 4, now + 4),
                ).lastrowid
                manager._conn.execute(
                    "INSERT INTO memory_synthesis_claims (synthesis_id, claim_id, role, created_at) VALUES (?, ?, 'supports', ?)",
                    (synthesis_id, claim_id, now + 4),
                )
                manager._conn.execute(
                    "INSERT INTO memory_review_items (profile_id, target_type, target_id, review_type, title, title_zh, description, description_zh, default_resolution, default_resolution_zh, suggested_payload, status, reminder_active, resolution_note, created_at, updated_at, resolved_at) VALUES (?, 'claim', ?, 'conflict', 'Temporal fusion debate', '时序融合争议', 'The current result is still disputed.', '当前结果仍有争议。', 'Keep the current result provisional.', '暂时保留当前结果。', '', 'pending', 1, '', ?, ?, NULL)",
                    (profile_id, claim_id, now + 5, now + 5),
                )
                manager._conn.commit()
                manager.rebuild_profile_memory(profile_id)
                manager.recompute_profile_paper_count(profile_id)
                manager._invalidate_profile_views(profile_id)

                theme_snapshot = manager.get_or_build_theme_snapshot(profile_id)
                gap_snapshot = manager.get_or_build_gap_snapshot(profile_id)
                survey = manager.get_or_build_living_survey(profile_id)

                self.assertGreaterEqual(theme_snapshot["item_count"], 1)
                self.assertEqual(
                    theme_snapshot["items"][0]["title"], "Object Grounding"
                )
                self.assertGreaterEqual(gap_snapshot["item_count"], 1)
                self.assertIn(
                    "gaps",
                    {section["section_key"] for section in survey["sections"]},
                )
                self.assertIn(
                    "themes",
                    {section["section_key"] for section in survey["sections"]},
                )
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
