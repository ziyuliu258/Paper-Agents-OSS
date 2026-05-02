import sys
import tempfile
import unittest
import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_interpreter.agent import (
    PaperInterpreterAgent,
    _build_memory_extraction_request,
)
from modules.paper_interpreter.distillation import (
    build_distillation_candidates,
    build_distilled_memory_summary,
)
from modules.paper_interpreter.task_runner import _claim_evidence_refs
from modules.paper_interpreter.working_memory import WorkingMemory
from utils.job_paths import get_job_results_dir
from utils.memory import (
    MemoryManager,
    build_memory_extraction_prompt,
    validate_memory_extraction_for_writeback,
)
from utils.working_memory_localization import (
    ensure_localized_distilled_summary_artifact,
    get_distilled_summary_localized_cache_path,
)


class WorkingMemoryDistillationTest(unittest.TestCase):
    def test_distillation_accepts_evidence_backed_claim(self) -> None:
        wm = WorkingMemory(job_id="job-1", profile_id=1, paper_id="paper-a")
        wm.add_draft_claim(
            section_key="method",
            claim="The paper introduces a two-stage retriever-generator pipeline.",
            evidence_refs=["Figure 1 | p.3", "Table 2 | p.8"],
            importance="high",
            confidence=0.9,
        )

        buckets = build_distillation_candidates(
            wm,
            {"t1_summary": "", "t7_conclusion": ""},
        )

        self.assertEqual(len(buckets["accepted"]), 1)
        self.assertEqual(buckets["accepted"][0].status, "accepted")
        self.assertEqual(len(buckets["review_required"]), 0)

    def test_distillation_downgrades_when_conflict_context_overlaps(self) -> None:
        wm = WorkingMemory(job_id="job-2", profile_id=1, paper_id="paper-b")
        wm.set_retrieved_context(
            "interpreter_bundle",
            {
                "active_conflicts": [
                    {
                        "title": "two-stage retriever-generator pipeline",
                        "description": "Existing memory still treats the pipeline design as disputed.",
                        "default_resolution": "keep existing version",
                    }
                ]
            },
        )
        wm.add_draft_claim(
            section_key="method",
            claim="The paper introduces a two-stage retriever-generator pipeline.",
            evidence_refs=["Figure 1 | p.3"],
            importance="high",
            confidence=0.92,
        )

        buckets = build_distillation_candidates(
            wm,
            {"t1_summary": "", "t7_conclusion": ""},
        )

        self.assertEqual(len(buckets["accepted"]), 0)
        self.assertEqual(len(buckets["review_required"]), 1)
        self.assertEqual(buckets["review_required"][0].status, "review_required")

    def test_distilled_summary_registers_candidate_statuses(self) -> None:
        wm = WorkingMemory(job_id="job-3", profile_id=1, paper_id="paper-c")
        wm.add_draft_claim(
            section_key="experiments",
            claim="The method outperforms baselines by 4.2 points on the main benchmark.",
            evidence_refs=["Table 1 | p.6"],
            importance="high",
            confidence=0.88,
        )

        summary, metrics = build_distilled_memory_summary(
            wm,
            {
                "t1_summary": "A benchmark-oriented retrieval paper.",
                "t7_conclusion": "The empirical gains look meaningful but need replication.",
            },
        )

        self.assertIn("Experiments distilled claims:", summary)
        self.assertEqual(metrics["accepted_count"], 1)
        self.assertGreaterEqual(len(wm.promotion_candidates), 2)
        self.assertIn(
            "accepted",
            {item.status for item in wm.promotion_candidates},
        )

    def test_distilled_summary_keeps_full_evidence_detail(self) -> None:
        wm = WorkingMemory(job_id="job-4", profile_id=1, paper_id="paper-d")
        wm.add_draft_claim(
            section_key="method",
            claim="The staged memory pipeline keeps citation-level grounding through every reasoning hop. UNIQUE-CLAIM-TAIL",
            evidence_refs=[
                "Figure 3 | p.5 | The appendix traces the grounding path across retrieval, consolidation, and synthesis. UNIQUE-EVIDENCE-TAIL"
            ],
            importance="high",
            confidence=0.91,
        )

        summary, _ = build_distilled_memory_summary(
            wm,
            {"t1_summary": "", "t7_conclusion": ""},
        )

        self.assertIn("UNIQUE-CLAIM-TAIL", summary)
        self.assertIn("UNIQUE-EVIDENCE-TAIL", summary)

    def test_claim_evidence_refs_do_not_clip_detail(self) -> None:
        refs = _claim_evidence_refs(
            {
                "evidence": [
                    {
                        "label": "Table 4",
                        "page": 9,
                        "detail": (
                            "This long evidence sentence is preserved for downstream memory display "
                            "without being truncated at serialization time. UNIQUE-DETAIL-TAIL"
                        ),
                    }
                ]
            }
        )

        self.assertEqual(len(refs), 1)
        self.assertIn("UNIQUE-DETAIL-TAIL", refs[0])


class MemoryPromptAndRetrievalTest(unittest.TestCase):
    def test_writeback_validation_drops_ungrounded_and_audited_claims(self) -> None:
        extraction = {
            "entities": [{"name": "Memory", "type": "concept"}],
            "claims": [
                {
                    "claim_key": "kept",
                    "title": "Grounded claim",
                    "body": "Grounded claim body.",
                    "evidence": [{"snippet": "Table 1 shows the grounded result."}],
                },
                {
                    "claim_key": "missing",
                    "title": "Missing evidence claim",
                    "body": "No evidence should drop this claim.",
                    "evidence": [],
                },
                {
                    "claim_key": "audited",
                    "title": "Unsupported method claim",
                    "body": "Unsupported method claim should not re-enter memory.",
                    "evidence": [{"snippet": "A snippet exists but report audit removed it."}],
                },
            ],
            "synthesis_items": [
                {
                    "synthesis_key": "syn",
                    "title": "Synthesis",
                    "summary": "Uses claim links.",
                    "claim_keys": ["kept", "missing", "audited"],
                }
            ],
        }

        validated, report = validate_memory_extraction_for_writeback(
            extraction,
            report_audit={
                "removed_claims_by_section": {"method": ["Unsupported method claim"]}
            },
        )

        self.assertEqual([claim["claim_key"] for claim in validated["claims"]], ["kept"])
        self.assertEqual(report["dropped_claim_count"], 2)
        self.assertEqual(validated["synthesis_items"][0]["claim_keys"], ["kept"])

    def test_memory_manager_health_field_map_and_matrix_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                profile = manager.create_profile("derived-memory", "derived test")
                profile_id = int(profile["id"])
                extraction = {
                    "entities": [
                        {"name": "Grounding", "type": "task"},
                        {"name": "Memory Agent", "type": "method"},
                    ],
                    "claims": [
                        {
                            "claim_key": "grounding-gain",
                            "title": "Memory agents improve grounding",
                            "body": "Memory agents improve grounding on the benchmark.",
                            "claim_type": "finding",
                            "stance": "support",
                            "importance": 0.9,
                            "scope": {"conditions": ["benchmark setting"]},
                            "entity_names": ["Grounding", "Memory Agent"],
                            "evidence": [
                                {
                                    "snippet": "Table 1 reports a higher grounding score.",
                                    "section_key": "experiments",
                                    "structured_signal": {
                                        "task": "grounding",
                                        "method": "memory agent",
                                        "dataset": "Bench-A",
                                        "metric": "accuracy",
                                        "value": "82.0",
                                        "baseline": "retrieval baseline",
                                        "setting": "standard split",
                                        "scope_note": "benchmark setting",
                                    },
                                }
                            ],
                        }
                    ],
                    "synthesis_items": [],
                }
                manager.write_memories(
                    profile_id,
                    "paper-derived",
                    extraction,
                    job_id="job-derived",
                    paper_title="Derived Paper",
                )
                manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, scope_json, review_status, manual_locked, lifecycle_state, lifecycle_reason_json, created_at, updated_at, deleted_at) VALUES (?, NULL, 'manual-unsupported', 'Manual unsupported', '', 'Manual unsupported body', '', 'finding', 'support', 0.8, 'active', 'Manual unsupported body', '', '{}', 'none', 1, 'needs_review', '{}', 1, 1, NULL)",
                    (profile_id,),
                )
                manager._conn.execute(
                    "INSERT INTO memory_derived_artifacts (profile_id, artifact_key, artifact_version, payload_json, stale, updated_at) VALUES (?, 'memory_health_snapshot', 'test', '{}', 1, 1)",
                    (profile_id,),
                )
                manager._conn.commit()
                manager._invalidate_profile_views(profile_id)

                health = manager.get_or_build_memory_health(profile_id)
                field_map = manager.get_or_build_field_map(profile_id)
                matrix = manager.get_or_build_evidence_matrix(profile_id)

                self.assertEqual(health["summary"]["unsupported_claim_count"], 1)
                self.assertEqual(health["summary"]["stale_artifact_count"], 0)
                self.assertGreaterEqual(field_map["cluster_count"], 1)
                self.assertEqual(matrix["row_count"], 1)
                self.assertEqual(matrix["rows"][0]["task"], "grounding")
                self.assertEqual(matrix["rows"][0]["cells"][0]["value"], "82.0")
            finally:
                manager.close()

    def test_memory_health_detects_orphan_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                profile = manager.create_profile("orphan-health", "orphan test")
                profile_id = int(profile["id"])
                writeback_id = manager._conn.execute(
                    "INSERT INTO memory_writebacks (profile_id, job_id, paper_id, provenance_mode, created_at, deleted_at) VALUES (?, 'job-orphan', 'paper-orphan', 'exact', 1, NULL)",
                    (profile_id,),
                ).lastrowid
                claim_id = manager._conn.execute(
                    "INSERT INTO memory_claims (profile_id, origin_writeback_id, claim_key, title, title_zh, body, body_zh, claim_type, stance, importance, status, default_resolution, default_resolution_zh, scope_json, review_status, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'deleted-claim', 'Deleted claim', '', 'Deleted body', '', 'finding', 'support', 0.5, 'active', 'Deleted body', '', '{}', 'none', 0, 1, 1, 2)",
                    (profile_id, writeback_id),
                ).lastrowid
                manager._conn.execute(
                    "INSERT INTO memory_claim_evidence (claim_id, writeback_id, section_key, snippet, weight, manual_locked, created_at, updated_at, deleted_at) VALUES (?, ?, 'experiments', 'Evidence attached to deleted claim.', 1.0, 0, 1, 1, NULL)",
                    (claim_id, writeback_id),
                )
                manager._conn.commit()
                manager._invalidate_profile_views(profile_id)

                health = manager.get_or_build_memory_health(profile_id)

                self.assertEqual(health["summary"]["orphan_evidence_count"], 1)
                self.assertIn(
                    "orphan_evidence",
                    {issue["issue_type"] for issue in health["issues"]},
                )
            finally:
                manager.close()

    def test_memory_extraction_prompt_includes_distilled_and_review_context(self) -> None:
        prompt = build_memory_extraction_prompt(
            {"metadata": {"title_en": "Test Paper"}},
            "Interpretation summary body",
            promotion_candidates=[
                {
                    "candidate_type": "claim",
                    "payload": {"title": "Candidate", "body": "Candidate body"},
                    "status": "accepted",
                }
            ],
            review_context="[Pending Conflict Queue]\n- Existing debate remains unresolved",
        )

        self.assertIn("Distilled promotion candidates", prompt)
        self.assertIn("Nearby long-term conflicts and review context", prompt)
        self.assertIn("Candidate body", prompt)
        self.assertIn("Existing debate remains unresolved", prompt)

    def test_translation_style_retrieval_collects_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                manager.get_style_preferences = lambda profile_id: {  # type: ignore[method-assign]
                    "tone": "precise",
                    "term_policy": "prefer bilingual first mention",
                }
                manager.list_synthesis_items = lambda profile_id, limit=30: [  # type: ignore[method-assign]
                    {
                        "title": "Retrieval-Augmented Generation",
                        "summary": "Combine retrieval with generation.",
                        "default_resolution": "Use retrieved evidence before generation.",
                    }
                ]
                manager.list_claims = lambda profile_id, limit=40: [  # type: ignore[method-assign]
                    {
                        "title": "Dual-graph fusion",
                        "body": "Fuse semantic and structural graphs.",
                        "default_resolution": "Fuse semantic and structural graphs.",
                        "entity_names": ["Graph Attention", "Temporal Encoder"],
                    }
                ]

                bundle = manager.retrieve_for_translation_style(
                    1,
                    keywords=["retrieval", "graph"],
                )
                rendered = manager.render_translation_style_context(bundle)

                self.assertIn("tone", bundle["style_preferences"])
                self.assertIn("Retrieval-Augmented Generation", bundle["terminology_hints"])
                self.assertIn("[Translation Style Preferences]", rendered)
                self.assertIn("[Terminology Hints]", rendered)
            finally:
                manager.close()

    def test_working_memory_artifacts_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_cwd = Path.cwd()
            try:
                # Persist to the repo results directory through normal job paths.
                agent = PaperInterpreterAgent()
                job_id = "test-working-memory-artifacts"
                wm = WorkingMemory(
                    job_id=job_id,
                    profile_id=7,
                    paper_id="paper-smoke",
                    paper_title="Smoke Paper",
                )
                wm.set_retrieved_context("interpreter_bundle", {"keywords": ["memory"]})
                wm.add_draft_claim(
                    section_key="method",
                    claim="Smoke artifact persistence claim.",
                    evidence_refs=["Figure 1 | p.3"],
                    importance="high",
                    confidence=0.9,
                )
                wm.add_open_question(
                    question="What is the failure mode on out-of-distribution data?",
                    section_key="limitations",
                    reason="Not yet grounded by the current section outputs.",
                )

                asyncio.run(
                    asyncio.wait_for(
                        agent._persist_working_memory_artifacts(
                            parsed_paper={"job_id": job_id},
                            working_memory=wm,
                            distilled_summary="Smoke distilled summary.",
                            artifact_stage="tasks_complete",
                        ),
                        timeout=5.0,
                    )
                )

                results_dir = Path("results") / "jobs" / job_id
                self.assertTrue((results_dir / "working_memory.json").exists())
                self.assertTrue((results_dir / "distilled_memory_summary.md").exists())
                payload = (results_dir / "working_memory.json").read_text(encoding="utf-8")
                self.assertIn('"artifact_stage": "tasks_complete"', payload)
                self.assertIn('"open_questions"', payload)
            finally:
                pass

    def test_memory_extraction_request_trims_large_payload_and_reorders_models(self) -> None:
        wm = WorkingMemory(
            job_id="budget-job",
            profile_id=7,
            paper_id="paper-budget",
            paper_title="Budget Paper",
        )
        for index in range(12):
            wm.register_promotion_candidate(
                candidate_type="claim",
                payload={
                    "title": f"Candidate {index}",
                    "body": ("Very long body " * 80) + str(index),
                    "default_resolution": "Resolution " * 50,
                },
                source_section="method",
                evidence_refs=[f"Figure {index} | p.{index + 1}"],
                confidence=0.9 - index * 0.01,
                status="accepted" if index < 6 else "review_required",
            )

        prompt, models, metrics = _build_memory_extraction_request(
            paper_notes={"metadata": {"title_en": "Budget Paper"}},
            summary="Summary " * 2000,
            working_memory=wm,
            review_context="Conflict " * 1000,
        )

        self.assertLessEqual(metrics["memory_extraction_candidate_count"], 8)
        self.assertEqual(metrics["memory_extraction_original_candidate_count"], 12)
        self.assertLessEqual(metrics["memory_extraction_review_context_chars"], 1800)
        self.assertLessEqual(metrics["memory_extraction_summary_chars"], 7000)
        self.assertIn(models[0], {"gem_pro", "gpt_pro"})
        self.assertLessEqual(len(prompt), 18000)
        if len(prompt) > 14000:
            self.assertEqual(models, ["gem_pro", "gpt_pro"])

    def test_localized_distilled_summary_is_cached(self) -> None:
        job_id = "localized-distilled-summary-test"
        results_dir = get_job_results_dir(job_id)
        source_path = results_dir / "distilled_memory_summary.md"
        cache_path = get_distilled_summary_localized_cache_path(job_id, "zh")

        try:
            results_dir.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                "One-line summary: Memory reasoning remains grounded.\n\n- Evidence chain stays explicit.",
                encoding="utf-8",
            )

            with patch(
                "utils.working_memory_localization.translate_brief_markdown_to_chinese",
                new=AsyncMock(return_value="一句话总结：记忆推理保持有据可依。\n\n- 证据链保持显式。"),
            ):
                localized = asyncio.run(
                    ensure_localized_distilled_summary_artifact(job_id, language="zh")
                )

            self.assertIn("一句话总结", localized)
            self.assertTrue(cache_path.exists())
            self.assertEqual(cache_path.read_text(encoding="utf-8"), localized)
        finally:
            shutil.rmtree(results_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
