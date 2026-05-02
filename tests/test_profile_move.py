import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import Database
from server.routers import profiles as profiles_router
from utils.memory import MemoryManager


class ProfileMoveMemoryTest(unittest.TestCase):
    def test_move_job_memories_transfers_bundle_to_target_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            original_localize = MemoryManager._localize_fields
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                db = Database(db_path)
                manager = MemoryManager(db_path)
                try:
                    source = manager.create_profile("source-lab", "Source profile")
                    target = manager.create_profile("target-lab", "Target profile")
                    source_profile_id = int(source["id"])
                    target_profile_id = int(target["id"])

                    job = db.create_job(
                        mode="manual",
                        profile_id=source_profile_id,
                        profile_mode="explicit",
                        config_snapshot={},
                    )
                    job_id = str(job["id"])
                    db.update_job(job_id, status="completed", paper_title="Paper A")
                    db.save_paper(
                        job_id,
                        {
                            "paper_id": "paper-a",
                            "title": "Paper A",
                            "pdf_path": "data/fetch/jobs/job-a/paper-a.pdf",
                            "report_path": "results/jobs/job-a/report.md",
                        },
                    )

                    now = time.time()
                    writeback_id = manager._conn.execute(
                        "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) "
                        "VALUES (?, ?, ?, 'exact', ?, NULL)",
                        (source_profile_id, job_id, "paper-a", now),
                    ).lastrowid
                    entity_id = manager._conn.execute(
                        "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) "
                        "VALUES (?, 'Temporal Fusion', '时序融合', 'temporal fusion', 'concept', 'Fusion block', '融合模块', 0, 'active', ?, ?, NULL)",
                        (source_profile_id, now, now),
                    ).lastrowid
                    manager._conn.execute(
                        "INSERT INTO memory_entity_aliases (entity_id, alias, normalized_alias, created_at) "
                        "VALUES (?, 'Temporal Fusion', 'temporal fusion', ?)",
                        (entity_id, now),
                    )
                    claim_id = manager._conn.execute(
                        "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                        "VALUES (?, ?, 'claim-a', 'Accurate forecasting', '预测更准', 'Improves forecasting accuracy', '提升预测精度', 'finding', 'support', 0.9, 'active', 'Improves forecasting accuracy', '提升预测精度', 'pending', 0, ?, ?, NULL)",
                        (source_profile_id, writeback_id, now, now),
                    ).lastrowid
                    manager._conn.execute(
                        "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, weight, manual_locked, created_at, updated_at, deleted_at) "
                        "VALUES (?, ?, 'results', 'Results', '结果', 'RMSE drops by 8%', 'RMSE 降低 8%', 'Metric improvement', '指标提升', 'p.6', 6, 6, 1.0, 0, ?, ?, NULL)",
                        (claim_id, writeback_id, now, now),
                    )
                    manager._conn.execute(
                        "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'uses', ?)",
                        (claim_id, entity_id, now),
                    )
                    synthesis_id = manager._conn.execute(
                        "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                        "VALUES (?, ?, 'synth-a', 'consensus', 'Forecasting gain', '预测收益', 'Consistent gain', '稳定收益', 0.7, 'active', 'Consistent gain', '稳定收益', 'pending', 0, ?, ?, NULL)",
                        (source_profile_id, writeback_id, now, now),
                    ).lastrowid
                    manager._conn.execute(
                        "INSERT INTO memory_synthesis_claims (synthesis_id, claim_id, role, created_at) VALUES (?, ?, 'supports', ?)",
                        (synthesis_id, claim_id, now),
                    )
                    edge_id = manager._conn.execute(
                        "INSERT INTO memory_graph_edges (profile_id, origin_writeback_id, source_kind, source_ref, target_kind, target_ref, relation_type, summary, summary_zh, weight, manual_locked, created_at, updated_at, deleted_at) "
                        "VALUES (?, ?, 'paper', 'paper-a', 'paper', 'paper-b', 'extends', 'Paper A extends Paper B', 'A 扩展 B', 1.0, 0, ?, ?, NULL)",
                        (source_profile_id, writeback_id, now, now),
                    ).lastrowid
                    manager._conn.execute(
                        "INSERT INTO memory_review_items (profile_id, target_type, target_id, review_type, title, title_zh, description, description_zh, default_resolution, default_resolution_zh, suggested_payload, status, reminder_active, resolution_note, created_at, updated_at, resolved_at) "
                        "VALUES (?, 'claim', ?, 'candidate_update', 'Review claim', '审查 claim', 'Check this claim', '检查 claim', '', '', '', 'pending', 1, '', ?, ?, NULL)",
                        (source_profile_id, claim_id, now, now),
                    )
                    manager._conn.execute(
                        "INSERT INTO memory_revisions (profile_id, target_type, target_id, action, actor_type, summary, summary_zh, before_json, after_json, writeback_id, created_at) "
                        "VALUES (?, 'claim', ?, 'create', 'ai', 'Created claim', '创建 claim', '', '', ?, ?)",
                        (source_profile_id, str(claim_id), writeback_id, now),
                    )
                    manager._conn.commit()
                    manager.rebuild_profile_memory(source_profile_id)
                    manager.recompute_profile_paper_count(source_profile_id)

                    result = manager.move_job_memories(
                        source_profile_id,
                        target_profile_id,
                        [job_id],
                    )

                    self.assertEqual(result["moved_job_ids"], [job_id])
                    self.assertEqual(result["moved_paper_ids"], ["paper-a"])
                    self.assertEqual(int(result["moved_writeback_count"]), 1)
                    self.assertEqual(int(result["moved_claim_count"]), 1)
                    self.assertEqual(int(result["moved_synthesis_count"]), 1)
                    self.assertEqual(int(result["moved_edge_count"]), 1)
                    self.assertEqual(int(result["source_active_writeback_count"]), 0)
                    self.assertEqual(int(result["target_active_writeback_count"]), 1)

                    moved_writeback = manager._conn.execute(
                        "SELECT profile_id FROM memory_writebacks WHERE id = ?",
                        (writeback_id,),
                    ).fetchone()
                    moved_claim = manager._conn.execute(
                        "SELECT profile_id FROM memory_claims WHERE id = ?",
                        (claim_id,),
                    ).fetchone()
                    moved_synthesis = manager._conn.execute(
                        "SELECT profile_id FROM memory_synthesis_items WHERE id = ?",
                        (synthesis_id,),
                    ).fetchone()
                    moved_edge = manager._conn.execute(
                        "SELECT profile_id, deleted_at FROM memory_graph_edges WHERE id = ?",
                        (edge_id,),
                    ).fetchone()
                    moved_entity = manager._conn.execute(
                        "SELECT profile_id, deleted_at FROM memory_entities WHERE id = ?",
                        (entity_id,),
                    ).fetchone()
                    moved_review = manager._conn.execute(
                        "SELECT profile_id FROM memory_review_items WHERE target_type = 'claim' AND target_id = ?",
                        (claim_id,),
                    ).fetchone()
                    moved_revision = manager._conn.execute(
                        "SELECT profile_id FROM memory_revisions WHERE target_type = 'claim' AND target_id = ?",
                        (str(claim_id),),
                    ).fetchone()

                    self.assertEqual(int(moved_writeback["profile_id"]), target_profile_id)
                    self.assertEqual(int(moved_claim["profile_id"]), target_profile_id)
                    self.assertEqual(int(moved_synthesis["profile_id"]), target_profile_id)
                    self.assertEqual(int(moved_edge["profile_id"]), target_profile_id)
                    self.assertIsNone(moved_edge["deleted_at"])
                    self.assertEqual(int(moved_entity["profile_id"]), target_profile_id)
                    self.assertIsNone(moved_entity["deleted_at"])
                    self.assertEqual(int(moved_review["profile_id"]), target_profile_id)
                    self.assertEqual(int(moved_revision["profile_id"]), target_profile_id)

                    source_profile = manager.get_profile_by_id(source_profile_id)
                    target_profile = manager.get_profile_by_id(target_profile_id)
                    self.assertIsNotNone(source_profile)
                    self.assertIsNotNone(target_profile)
                    assert source_profile is not None
                    assert target_profile is not None
                    self.assertEqual(int(source_profile["paper_count"]), 0)
                    self.assertEqual(int(target_profile["paper_count"]), 1)
                finally:
                    manager.close()
                    db.close()
            finally:
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]


class ProfileMoveRouteTest(unittest.TestCase):
    def test_move_route_updates_job_profile_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            original_localize = MemoryManager._localize_fields
            spawned_managers: list[MemoryManager] = []

            class _ScopedMemoryManager(MemoryManager):
                def __init__(self) -> None:
                    super().__init__(db_path)
                    spawned_managers.append(self)

            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                db = Database(db_path)
                seed_manager = MemoryManager(db_path)
                try:
                    source = seed_manager.create_profile("source-route", "Source route")
                    target = seed_manager.create_profile("target-route", "Target route")
                    source_profile_id = int(source["id"])
                    target_profile_id = int(target["id"])
                    job = db.create_job(
                        mode="manual",
                        profile_id=source_profile_id,
                        profile_mode="explicit",
                        config_snapshot={},
                    )
                    job_id = str(job["id"])
                    db.update_job(job_id, status="completed", paper_title="Paper Route")
                    seed_manager._conn.execute(
                        "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                        (source_profile_id, job_id, "paper-route", time.time()),
                    )
                    seed_manager._conn.commit()

                    app = FastAPI()
                    app.include_router(profiles_router.router, prefix="/api")
                    with TestClient(app) as client:
                        with (
                            patch("server.routers.profiles._get_mm", side_effect=_ScopedMemoryManager),
                            patch("server.routers.profiles.get_db", return_value=db),
                        ):
                            response = client.post(
                                f"/api/profiles/{source_profile_id}/move-papers",
                                json={
                                    "target_profile_id": target_profile_id,
                                    "job_ids": [job_id],
                                },
                            )

                    self.assertEqual(response.status_code, 200, response.text)
                    payload = response.json()
                    self.assertEqual(payload["moved_job_ids"], [job_id])

                    moved_job = db.get_job(job_id)
                    self.assertIsNotNone(moved_job)
                    assert moved_job is not None
                    self.assertEqual(int(moved_job["profile_id"]), target_profile_id)
                    self.assertEqual(str(moved_job["profile_mode"]), "explicit")
                    self.assertEqual(str(moved_job["profile_assignment_status"]), "manual")
                    self.assertIn("Manually moved", str(moved_job["profile_assignment_note"]))
                finally:
                    seed_manager.close()
                    db.close()
            finally:
                for manager in spawned_managers:
                    try:
                        manager.close()
                    except Exception:
                        pass
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]


if __name__ == "__main__":
    unittest.main()
