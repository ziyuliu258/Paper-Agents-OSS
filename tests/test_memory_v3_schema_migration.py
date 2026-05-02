import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.memory import MemoryManager


class MemoryV3SchemaMigrationTest(unittest.TestCase):
    def test_schema_v4_soft_migration_preserves_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            manager = MemoryManager(db_path)
            try:
                profile = manager.create_profile("migration-v4", "migration test")
                profile_id = int(profile["id"])
                now = time.time()
                writeback_id = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                    (profile_id, "job-migrate", "paper-migrate", now),
                ).lastrowid
                manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'claim-migrate', 'Migrated claim', '', 'Migrated body', '', 'finding', 'support', 0.9, 'active', 'Migrated body', '', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_id, now, now),
                )
                manager._conn.execute(
                    "UPDATE memory_meta SET value = '3' WHERE key = 'schema_version'"
                )
                manager._conn.commit()
            finally:
                manager.close()

            reopened = MemoryManager(db_path)
            try:
                version_row = reopened._conn.execute(
                    "SELECT value FROM memory_meta WHERE key = 'schema_version'"
                ).fetchone()
                self.assertEqual(str(version_row["value"]), "4")

                claim_count_row = reopened._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM memory_claims WHERE profile_id = ? AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone()
                self.assertEqual(int(claim_count_row["cnt"]), 1)

                relation_table = reopened._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_claim_relations'"
                ).fetchone()
                self.assertIsNotNone(relation_table)

                profile_state = reopened._conn.execute(
                    "SELECT claim_relations_stale, claim_relations_updated_at FROM memory_profile_state WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                self.assertIsNotNone(profile_state)
                assert profile_state is not None
                self.assertEqual(int(profile_state["claim_relations_stale"]), 1)
                self.assertEqual(float(profile_state["claim_relations_updated_at"]), 0.0)

                claim_row = reopened._conn.execute(
                    "SELECT scope_json, stability_score, last_supported_at, last_challenged_at, lifecycle_state, lifecycle_reason_json, superseded_by_claim_id, last_lifecycle_update_at FROM memory_claims WHERE profile_id = ? LIMIT 1",
                    (profile_id,),
                ).fetchone()
                self.assertIsNotNone(claim_row)
                assert claim_row is not None
                self.assertEqual(str(claim_row["scope_json"]), "{}")
                self.assertEqual(float(claim_row["stability_score"]), 0.5)
                self.assertIsNone(claim_row["last_supported_at"])
                self.assertIsNone(claim_row["last_challenged_at"])
                self.assertEqual(str(claim_row["lifecycle_state"]), "emerging")
                self.assertEqual(str(claim_row["lifecycle_reason_json"]), "{}")
                self.assertIsNone(claim_row["superseded_by_claim_id"])
                self.assertIsNone(claim_row["last_lifecycle_update_at"])
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
