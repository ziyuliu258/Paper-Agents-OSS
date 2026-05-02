import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import Database
from server.job_manager import JobManager
from server.routers import profiles as profiles_router
from utils.memory import MemoryManager


class MemoryDeleteByPaperTest(unittest.TestCase):
    def test_delete_paper_memories_prunes_orphaned_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                manager._localize_fields = (  # type: ignore[method-assign]
                    lambda _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                profile = manager.create_profile("vision", "Computer vision profile")
                profile_id = int(profile["id"])
                now = time.time()

                writeback_a = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                    (profile_id, "job-a", "paper-a", now),
                ).lastrowid
                writeback_b = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                    (profile_id, "job-b", "paper-b", now + 1),
                ).lastrowid

                entity_a = manager._conn.execute(
                    "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) "
                    "VALUES (?, 'Object Tokens', '', 'object tokens', 'concept', '', '', 0, 'active', ?, ?, NULL)",
                    (profile_id, now, now),
                ).lastrowid
                entity_b = manager._conn.execute(
                    "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) "
                    "VALUES (?, 'Temporal Fusion', '', 'temporal fusion', 'concept', '', '', 0, 'active', ?, ?, NULL)",
                    (profile_id, now + 1, now + 1),
                ).lastrowid

                claim_a = manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'claim-a', 'Paper A Claim', '', 'Paper A body', '', 'finding', 'support', 0.8, 'active', 'Paper A body', '', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_a, now, now),
                ).lastrowid
                claim_b = manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'claim-b', 'Paper B Claim', '', 'Paper B body', '', 'finding', 'support', 0.9, 'active', 'Paper B body', '', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_b, now + 1, now + 1),
                ).lastrowid

                manager._conn.execute(
                    "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, weight, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'method', 'Method', '', 'Snippet A', '', 'Summary A', '', 'p.3', 3, 3, 1.0, 0, ?, ?, NULL)",
                    (claim_a, writeback_a, now, now),
                )
                manager._conn.execute(
                    "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, weight, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'experiments', 'Experiments', '', 'Snippet B', '', 'Summary B', '', 'p.7', 7, 7, 1.0, 0, ?, ?, NULL)",
                    (claim_b, writeback_b, now + 1, now + 1),
                )
                manager._conn.execute(
                    "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                    (claim_a, entity_a, now),
                )
                manager._conn.execute(
                    "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                    (claim_b, entity_b, now + 1),
                )

                synthesis_a = manager._conn.execute(
                    "INSERT INTO memory_synthesis_items (profile_id, origin_writeback_id, synthesis_key, item_type, title, title_zh, summary, summary_zh, confidence, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'synth-a', 'consensus', 'Synthesis A', '', 'Summary A', '', 0.7, 'active', 'Summary A', '', 'none', 0, ?, ?, NULL)",
                    (profile_id, writeback_a, now, now),
                ).lastrowid
                manager._conn.execute(
                    "INSERT INTO memory_synthesis_claims (synthesis_id, claim_id, role, created_at) VALUES (?, ?, 'supports', ?)",
                    (synthesis_a, claim_a, now),
                )
                manager._conn.execute(
                    "INSERT INTO memory_graph_edges (profile_id, origin_writeback_id, source_kind, source_ref, target_kind, target_ref, relation_type, summary, summary_zh, weight, manual_locked, created_at, updated_at, deleted_at) "
                    "VALUES (?, ?, 'paper', 'paper-a', 'paper', 'paper-b', 'extends', 'Paper A extends Paper B', '', 1.0, 0, ?, ?, NULL)",
                    (profile_id, writeback_a, now, now),
                )

                manager._conn.execute(
                    "INSERT INTO memory_knowledge_events (writeback_id, category, content, relevance_score, created_at) VALUES (?, 'finding', 'A finding', 0.9, ?)",
                    (writeback_a, now),
                )
                manager._conn.execute(
                    "INSERT INTO memory_style_events (writeback_id, key, value, created_at) VALUES (?, 'detail_level', 'high', ?)",
                    (writeback_a, now),
                )
                manager._conn.execute(
                    "INSERT INTO memory_link_events (writeback_id, source_paper_id, target_paper_id, relation_type, summary, created_at) VALUES (?, 'paper-a', 'paper-b', 'extends', 'Paper A extends Paper B', ?)",
                    (writeback_a, now),
                )
                manager._conn.commit()
                manager.recompute_profile_paper_count(profile_id)

                result = manager.delete_paper_memories(profile_id, "paper-a")

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result["paper_id"], "paper-a")
                self.assertEqual(result["deleted_writeback_count"], 1)
                self.assertEqual(result["deleted_claims"], 1)
                self.assertEqual(result["deleted_synthesis"], 1)
                self.assertEqual(result["deleted_edges"], 1)
                self.assertEqual(result["deleted_orphaned_entities"], 1)

                active_writebacks = manager._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ? AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone()
                self.assertEqual(int(active_writebacks["cnt"]), 1)

                deleted_entity = manager._conn.execute(
                    "SELECT deleted_at FROM memory_entities WHERE id = ?",
                    (entity_a,),
                ).fetchone()
                surviving_entity = manager._conn.execute(
                    "SELECT deleted_at FROM memory_entities WHERE id = ?",
                    (entity_b,),
                ).fetchone()
                self.assertIsNotNone(deleted_entity["deleted_at"])
                self.assertIsNone(surviving_entity["deleted_at"])

                deleted_claim = manager._conn.execute(
                    "SELECT deleted_at FROM memory_claims WHERE id = ?",
                    (claim_a,),
                ).fetchone()
                surviving_claim = manager._conn.execute(
                    "SELECT deleted_at FROM memory_claims WHERE id = ?",
                    (claim_b,),
                ).fetchone()
                self.assertIsNotNone(deleted_claim["deleted_at"])
                self.assertIsNone(surviving_claim["deleted_at"])

                updated_profile = manager.get_profile_by_id(profile_id)
                self.assertIsNotNone(updated_profile)
                assert updated_profile is not None
                self.assertEqual(int(updated_profile["paper_count"]), 1)
            finally:
                manager.close()


class _FakeLogHandler:
    def publish_done(self, _job_id: str, _payload: object) -> None:
        return None


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.closed = False

    def delete_job_memories(self, profile_id: int, job_id: str) -> dict[str, object]:
        return {
            "profile_id": profile_id,
            "job_id": job_id,
            "paper_id": "paper-a",
            "deleted_writeback_count": 1,
            "deleted_knowledge_events": 0,
            "deleted_style_events": 0,
            "deleted_link_events": 0,
            "deleted_evidence": 0,
            "deleted_claims": 0,
            "deleted_synthesis": 0,
            "deleted_edges": 0,
            "deleted_orphaned_claims": 0,
            "deleted_orphaned_synthesis": 0,
            "deleted_orphaned_entities": 0,
            "provenance_mode": "exact",
            "provenance_modes": ["exact"],
            "approximate": False,
            "deleted_at": time.time(),
        }

    def close(self) -> None:
        self.closed = True


class JobForceStopPurgeTest(unittest.TestCase):
    def test_force_stop_and_purge_removes_job_dirs_and_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            db = Database(temp_root / "memory.db")
            try:
                manager = JobManager(db)
                job = db.create_job(mode="auto", profile_id=7, config_snapshot={})
                job_id = str(job["id"])
                db.update_job(
                    job_id,
                    status="processing",
                    progress=42,
                    current_step="Testing purge",
                )
                db.save_paper(
                    job_id,
                    {
                        "paper_id": "paper-a",
                        "title": "Paper A",
                        "pdf_path": "data/fetch/jobs/fake/paper-a.pdf",
                        "report_path": "results/jobs/fake/report.md",
                    },
                )

                results_dir = temp_root / "results" / job_id
                fetch_dir = temp_root / "fetch" / job_id
                cache_dir = temp_root / "cache" / job_id
                for path in (results_dir, fetch_dir, cache_dir):
                    path.mkdir(parents=True, exist_ok=True)
                    (path / "marker.txt").write_text("marker", encoding="utf-8")

                async def scenario() -> dict[str, object] | None:
                    manager._tasks[job_id] = asyncio.create_task(asyncio.sleep(3600))
                    return await manager.force_stop_and_purge(job_id)

                with (
                    patch(
                        "server.job_manager.get_job_results_dir",
                        return_value=results_dir,
                    ),
                    patch(
                        "server.job_manager.get_job_fetch_dir", return_value=fetch_dir
                    ),
                    patch(
                        "server.job_manager.get_job_cache_dir", return_value=cache_dir
                    ),
                    patch("server.job_manager.MemoryManager", _FakeMemoryManager),
                    patch(
                        "server.job_manager.get_log_handler",
                        return_value=_FakeLogHandler(),
                    ),
                ):
                    result = asyncio.run(scenario())

                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(bool(result["force_stopped"]))
                self.assertTrue(bool(result["task_cancel_requested"]))
                self.assertTrue(bool(result["job_deleted"]))
                self.assertTrue(bool(result["paper_record_deleted"]))
                self.assertTrue(bool(result["memory_deleted"]))
                self.assertTrue(bool(result["results_dir_removed"]))
                self.assertTrue(bool(result["fetch_dir_removed"]))
                self.assertTrue(bool(result["cache_dir_removed"]))

                self.assertFalse(results_dir.exists())
                self.assertFalse(fetch_dir.exists())
                self.assertFalse(cache_dir.exists())
                self.assertIsNone(db.get_job(job_id))
                self.assertIsNone(db.get_paper("paper-a"))
            finally:
                db.close()


class ProfilePaperDeleteRouteTest(unittest.TestCase):
    def test_delete_route_accepts_doi_with_slash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            seed_manager = MemoryManager(db_path)
            original_localize = MemoryManager._localize_fields
            doi = "10.1609/aaai.v38i17.29946"
            spawned_managers: list[MemoryManager] = []
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                profile = seed_manager.create_profile("doi-route", "Route coverage")
                profile_id = int(profile["id"])
                seed_manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                    (profile_id, "job-doi", doi, time.time()),
                )
                seed_manager._conn.commit()

                app = FastAPI()
                app.include_router(profiles_router.router, prefix="/api")
                with TestClient(app) as client:

                    def _make_manager() -> MemoryManager:
                        manager = MemoryManager(db_path)
                        spawned_managers.append(manager)
                        return manager

                    with patch(
                        "server.routers.profiles._get_mm",
                        side_effect=_make_manager,
                    ):
                        response = client.delete(
                            f"/api/profiles/{profile_id}/papers/{quote(doi, safe='')}/memory"
                        )

                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["paper_id"], doi)
                self.assertEqual(payload["deleted_writeback_count"], 1)
            finally:
                for manager in spawned_managers:
                    try:
                        manager.close()
                    except Exception:
                        pass
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                seed_manager.close()


class ProfileDeleteRouteTest(unittest.TestCase):
    def test_delete_profile_cascades_jobs_artifacts_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            db_path = temp_root / "memory.db"
            db = Database(db_path)
            original_localize = MemoryManager._localize_fields
            spawned_managers: list[MemoryManager] = []

            class _ScopedMemoryManager(MemoryManager):
                def __init__(self) -> None:
                    super().__init__(db_path)
                    spawned_managers.append(self)

            manager = JobManager(db)
            results_dir = temp_root / "results" / "job-delete-profile"
            fetch_dir = temp_root / "fetch" / "job-delete-profile"
            cache_dir = temp_root / "cache" / "job-delete-profile"
            for path in (results_dir, fetch_dir, cache_dir):
                path.mkdir(parents=True, exist_ok=True)
                (path / "marker.txt").write_text("marker", encoding="utf-8")

            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                seed_manager = MemoryManager(db_path)
                try:
                    profile = seed_manager.create_profile("vision-delete", "Delete coverage")
                    profile_id = int(profile["id"])
                    job = db.create_job(mode="auto", profile_id=profile_id, config_snapshot={})
                    job_id = str(job["id"])
                    db.update_job(
                        job_id,
                        status="completed",
                        report_path="results/jobs/job-delete-profile/report.md",
                    )
                    db.save_paper(
                        job_id,
                        {
                            "paper_id": "paper-delete",
                            "title": "Delete Me",
                            "pdf_path": "data/fetch/jobs/job-delete-profile/paper-delete.pdf",
                            "report_path": "results/jobs/job-delete-profile/report.md",
                        },
                    )
                    seed_manager._conn.execute(
                        "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                        (profile_id, job_id, "paper-delete", time.time()),
                    )
                    seed_manager._conn.commit()

                    with (
                        patch("server.routers.profiles._get_mm", side_effect=_ScopedMemoryManager),
                        patch("server.routers.profiles.get_db", return_value=db),
                        patch("server.routers.profiles._get_manager", return_value=manager),
                        patch("server.job_manager.MemoryManager", _ScopedMemoryManager),
                        patch("server.job_manager.get_job_results_dir", return_value=results_dir),
                        patch("server.job_manager.get_job_fetch_dir", return_value=fetch_dir),
                        patch("server.job_manager.get_job_cache_dir", return_value=cache_dir),
                    ):
                        payload = asyncio.run(profiles_router.delete_profile(profile_id))

                    self.assertTrue(bool(payload["deleted_profile"]))
                    self.assertEqual(int(payload["purged_job_count"]), 1)
                    self.assertEqual(int(payload["deleted_paper_record_count"]), 1)
                    self.assertEqual(int(payload["deleted_writeback_count"]), 1)
                    self.assertEqual(int(payload["results_dirs_removed"]), 1)
                    self.assertEqual(int(payload["fetch_dirs_removed"]), 1)
                    self.assertEqual(int(payload["cache_dirs_removed"]), 1)
                    self.assertIsNone(db.get_job(job_id))
                    self.assertIsNone(db.get_paper("paper-delete"))
                    self.assertFalse(results_dir.exists())
                    self.assertFalse(fetch_dir.exists())
                    self.assertFalse(cache_dir.exists())
                    self.assertIsNone(seed_manager.get_profile_by_id(profile_id))
                    remaining_writebacks = seed_manager._conn.execute(
                        "SELECT COUNT(*) AS cnt FROM memory_writebacks WHERE profile_id = ?",
                        (profile_id,),
                    ).fetchone()
                    self.assertEqual(int(remaining_writebacks["cnt"]), 0)
                finally:
                    seed_manager.close()
            finally:
                for item in spawned_managers:
                    try:
                        item.close()
                    except Exception:
                        pass
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                db.close()

    def test_delete_profile_blocks_active_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            db = Database(db_path)
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
                seed_manager = MemoryManager(db_path)
                try:
                    profile = seed_manager.create_profile("vision-busy", "Busy profile")
                    profile_id = int(profile["id"])
                    job = db.create_job(mode="auto", profile_id=profile_id, config_snapshot={})
                    db.update_job(str(job["id"]), status="processing")

                    with (
                        patch("server.routers.profiles._get_mm", side_effect=_ScopedMemoryManager),
                        patch("server.routers.profiles.get_db", return_value=db),
                        patch("server.routers.profiles._get_manager", return_value=JobManager(db)),
                    ):
                        with self.assertRaises(HTTPException) as ctx:
                            asyncio.run(profiles_router.delete_profile(profile_id))

                    self.assertEqual(ctx.exception.status_code, 409)
                    self.assertIn("still active", str(ctx.exception.detail))
                finally:
                    seed_manager.close()
            finally:
                for item in spawned_managers:
                    try:
                        item.close()
                    except Exception:
                        pass
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                db.close()


if __name__ == "__main__":
    unittest.main()
