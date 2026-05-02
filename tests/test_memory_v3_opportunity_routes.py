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
from server.routers import memory_workspace as memory_workspace_router
from server.routers import profiles as profiles_router
from utils.memory import MemoryManager


class MemoryV3OpportunityRoutesTest(unittest.TestCase):
    def test_workspace_snapshot_and_routes_expose_opportunities(self) -> None:
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
                    profile = seed_manager.create_profile("opportunity-route", "route test")
                    profile_id = int(profile["id"])
                    now = time.time()
                    writeback_id = seed_manager._conn.execute(
                        "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, ?, ?, 'exact', ?, NULL)",
                        (profile_id, "job-opportunity", "paper-opportunity", now),
                    ).lastrowid
                    task_entity = seed_manager._conn.execute(
                        "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) VALUES (?, 'Object Grounding', '目标对齐', 'object grounding', 'task', 'Ground visual objects.', '对视觉对象进行对齐。', 0, 'active', ?, ?, NULL)",
                        (profile_id, now + 1, now + 1),
                    ).lastrowid
                    method_entity = seed_manager._conn.execute(
                        "INSERT INTO memory_entities (profile_id, canonical_name, canonical_name_zh, normalized_name, entity_type, summary, summary_zh, manual_locked, status, created_at, updated_at, deleted_at) VALUES (?, 'Temporal Fusion', '时序融合', 'temporal fusion', 'method', 'Fuse temporal signals.', '融合时序信号。', 0, 'active', ?, ?, NULL)",
                        (profile_id, now + 2, now + 2),
                    ).lastrowid

                    claim_ids: list[int] = []
                    claim_specs = [
                        (
                            "claim-a",
                            "Temporal fusion improves grounding",
                            "时序融合提升对齐",
                            "Temporal fusion improves grounding accuracy.",
                            "时序融合提升对齐准确率。",
                            "support",
                            0.92,
                            now + 3,
                        ),
                        (
                            "claim-b",
                            "Temporal fusion fails in saturated settings",
                            "时序融合在饱和设定下失效",
                            "Temporal fusion does not improve grounding in saturated settings.",
                            "时序融合在饱和设定下无法提升对齐。",
                            "oppose",
                            0.9,
                            now + 4,
                        ),
                    ]
                    for claim_key, title, title_zh, body, body_zh, stance, importance, timestamp in claim_specs:
                        claim_id = seed_manager._conn.execute(
                            "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'finding', ?, ?, 'active', ?, ?, 'pending', 0, ?, ?, NULL)",
                            (
                                profile_id,
                                writeback_id,
                                claim_key,
                                title,
                                title_zh,
                                body,
                                body_zh,
                                stance,
                                importance,
                                body,
                                body_zh,
                                timestamp,
                                timestamp,
                            ),
                        ).lastrowid
                        claim_ids.append(int(claim_id))
                        seed_manager._conn.execute(
                            "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                            (claim_id, task_entity, timestamp),
                        )
                        seed_manager._conn.execute(
                            "INSERT INTO memory_claim_entities (claim_id, entity_id, role, created_at) VALUES (?, ?, 'mentions', ?)",
                            (claim_id, method_entity, timestamp),
                        )

                    seed_manager._conn.execute(
                        "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, section_title, section_title_zh, snippet, snippet_zh, evidence_summary, evidence_summary_zh, page_label, page_start, page_end, weight, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'experiments', 'Experiments', '实验', 'Temporal fusion improves grounding accuracy by 3.1 points.', '时序融合提升对齐准确率 3.1 个点。', 'Benchmark result.', '基准结果。', 'p.7', 7, 7, 1.0, 0, ?, ?, NULL)",
                        (claim_ids[0], writeback_id, now + 5, now + 5),
                    )
                    seed_manager._conn.execute(
                        "INSERT INTO memory_review_items (profile_id, target_type, target_id, review_type, title, title_zh, description, description_zh, default_resolution, default_resolution_zh, suggested_payload, status, reminder_active, resolution_note, created_at, updated_at, resolved_at) VALUES (?, 'claim', ?, 'conflict', 'Grounding contradiction', '对齐矛盾', 'The disagreement is still unresolved.', '该分歧仍未解决。', 'Keep the disagreement open.', '暂时保持争议状态。', '', 'pending', 1, '', ?, ?, NULL)",
                        (profile_id, claim_ids[1], now + 6, now + 6),
                    )
                    seed_manager._conn.commit()
                    seed_manager.rebuild_profile_memory(profile_id)
                    seed_manager._invalidate_profile_views(profile_id)

                    app = FastAPI()
                    app.include_router(memory_workspace_router.router, prefix="/api")
                    app.include_router(profiles_router.router, prefix="/api")
                    with TestClient(app) as client:
                        with (
                            patch(
                                "server.routers.memory_workspace._get_mm",
                                side_effect=_ScopedMemoryManager,
                            ),
                            patch(
                                "server.routers.profiles._get_mm",
                                side_effect=_ScopedMemoryManager,
                            ),
                            patch("server.routers.profiles.get_db", return_value=db),
                        ):
                            workspace_response = client.get(
                                f"/api/profiles/{profile_id}/workspace"
                            )
                            opportunities_response = client.get(
                                f"/api/profiles/{profile_id}/workspace/opportunities"
                            )
                            detail_response = client.get(
                                f"/api/profiles/{profile_id}/detail"
                            )
                            health_response = client.get(
                                f"/api/profiles/{profile_id}/workspace/health"
                            )
                            field_map_response = client.get(
                                f"/api/profiles/{profile_id}/workspace/field-map"
                            )
                            matrix_response = client.get(
                                f"/api/profiles/{profile_id}/workspace/evidence-matrix"
                            )
                            create_evidence_response = client.post(
                                f"/api/profiles/{profile_id}/workspace/evidence",
                                json={
                                    "claim_id": claim_ids[0],
                                    "section_key": "experiments",
                                    "section_title": "Experiment details",
                                    "snippet": "Table 2 reports a 4.2 point accuracy gain on Bench-B.",
                                    "evidence_summary": "Manual metric anchor.",
                                    "page_label": "p.8",
                                    "anchor_kind": "metric",
                                    "context_before": "Bench-B setup.",
                                    "context_after": "The effect is stable.",
                                    "structured_signal": {
                                        "task": "grounding",
                                        "method": "temporal fusion",
                                        "dataset": "Bench-B",
                                        "metric": "accuracy",
                                        "value": "+4.2",
                                        "baseline": "single frame",
                                        "setting": "standard split",
                                        "scope_note": "Bench-B only",
                                    },
                                },
                            )
                            created_evidence_id = create_evidence_response.json().get("id")
                            update_evidence_response = client.put(
                                f"/api/profiles/{profile_id}/workspace/evidence/{created_evidence_id}",
                                json={
                                    "claim_id": claim_ids[0],
                                    "section_key": "experiments",
                                    "section_title": "Updated experiment details",
                                    "snippet": "Table 3 reports a 5.0 point gain with a constrained protocol.",
                                    "evidence_summary": "Updated manual metric anchor.",
                                    "page_label": "p.9",
                                    "anchor_kind": "table",
                                    "context_before": "Updated setup.",
                                    "context_after": "Updated effect remains stable.",
                                    "structured_signal": {
                                        "task": "grounding",
                                        "method": "temporal fusion v2",
                                        "dataset": "Bench-C",
                                        "metric": "accuracy",
                                        "value": "+5.0",
                                        "baseline": "single frame",
                                        "comparator": "temporal average",
                                        "setting": "constrained protocol",
                                        "limitation": "small validation split",
                                        "scope_note": "Bench-C only",
                                    },
                                },
                            )
                            list_evidence_response = client.get(
                                f"/api/profiles/{profile_id}/workspace/evidence"
                            )

                    self.assertEqual(workspace_response.status_code, 200, workspace_response.text)
                    self.assertEqual(opportunities_response.status_code, 200, opportunities_response.text)
                    self.assertEqual(detail_response.status_code, 200, detail_response.text)
                    self.assertEqual(health_response.status_code, 200, health_response.text)
                    self.assertEqual(field_map_response.status_code, 200, field_map_response.text)
                    self.assertEqual(matrix_response.status_code, 200, matrix_response.text)
                    self.assertEqual(create_evidence_response.status_code, 200, create_evidence_response.text)
                    self.assertEqual(update_evidence_response.status_code, 200, update_evidence_response.text)
                    self.assertEqual(list_evidence_response.status_code, 200, list_evidence_response.text)

                    workspace_payload = workspace_response.json()
                    opportunity_payload = opportunities_response.json()
                    detail_payload = detail_response.json()
                    health_payload = health_response.json()
                    field_map_payload = field_map_response.json()
                    matrix_payload = matrix_response.json()
                    created_evidence_payload = create_evidence_response.json()
                    updated_evidence_payload = update_evidence_response.json()
                    listed_evidence_payload = list_evidence_response.json()

                    self.assertIn("opportunities", workspace_payload)
                    self.assertIn("health", workspace_payload)
                    self.assertIn("field_map", workspace_payload)
                    self.assertIn("evidence_matrix", workspace_payload)
                    self.assertGreaterEqual(
                        int(workspace_payload["overview"]["opportunity_count"]), 1
                    )
                    self.assertGreaterEqual(
                        int(workspace_payload["overview"]["high_priority_opportunity_count"]),
                        1,
                    )
                    self.assertGreaterEqual(int(opportunity_payload["item_count"]), 1)
                    self.assertIn(
                        "persistent_contradiction",
                        {
                            item["opportunity_type"]
                            for item in opportunity_payload["items"]
                        },
                    )
                    self.assertGreaterEqual(len(detail_payload["opportunity_preview"]), 1)
                    self.assertIsNotNone(detail_payload["health"])
                    self.assertGreaterEqual(len(detail_payload["field_map_preview"]), 1)
                    self.assertGreaterEqual(
                        int(health_payload["summary"]["pending_review_count"]), 1
                    )
                    self.assertGreaterEqual(int(field_map_payload["cluster_count"]), 1)
                    self.assertGreaterEqual(int(matrix_payload["evidence_count"]), 1)
                    self.assertEqual(created_evidence_payload["anchor_kind"], "metric")
                    self.assertEqual(created_evidence_payload["context_before"], "Bench-B setup.")
                    self.assertEqual(
                        created_evidence_payload["structured_signal"]["task"],
                        "grounding",
                    )
                    self.assertEqual(
                        created_evidence_payload["structured_signal"]["value"],
                        "+4.2",
                    )
                    self.assertEqual(updated_evidence_payload["anchor_kind"], "table")
                    self.assertEqual(
                        updated_evidence_payload["structured_signal"]["method"],
                        "temporal fusion v2",
                    )
                    self.assertEqual(
                        updated_evidence_payload["structured_signal"]["limitation"],
                        "small validation split",
                    )
                    listed_updated_evidence = next(
                        item
                        for item in listed_evidence_payload
                        if item["id"] == updated_evidence_payload["id"]
                    )
                    self.assertEqual(
                        listed_updated_evidence["structured_signal"]["scope_note"],
                        "Bench-C only",
                    )
                    self.assertIn(
                        "persistent_contradiction",
                        {
                            item["opportunity_type"]
                            for item in detail_payload["opportunity_preview"]
                        },
                    )
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
