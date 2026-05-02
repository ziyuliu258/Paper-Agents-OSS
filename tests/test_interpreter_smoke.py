import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_interpreter.agent import PaperInterpreterAgent
from utils.job_paths import get_job_results_dir
from utils.memory import MemoryManager


def _paper_notes_payload() -> str:
    return json.dumps(
        {
            "metadata": {
                "title_en": "Smoke Test Paper",
                "title_cn": "",
                "venue": "Arxiv",
                "pub_date": "2026-01-01",
                "institution": "Test Lab",
                "code_repository_url": "",
            },
            "paper_summary": "This paper studies a retrieval-augmented generation workflow.",
            "problem": ["Long-context reasoning is brittle."],
            "method_steps": ["Retrieve evidence", "Fuse context", "Generate answer"],
            "main_results": [
                {
                    "metric": "Accuracy",
                    "value": "+4.2",
                    "page": 6,
                    "evidence": "Table 1 shows the main gain.",
                }
            ],
            "limitations": ["The benchmark coverage is narrow."],
            "glossary_seed": ["Retrieval-Augmented Generation", "Dual Encoder"],
            "figure_highlights": [],
        }
    )


def _structured_section(section: str, summary: str, claim: str) -> str:
    payload = {
        "section": section,
        "summary": summary,
        "pipeline_overview": "Input documents are retrieved, fused, and passed to the generator."
        if section == "method"
        else "",
        "modules": [
            {
                "name": "Retriever",
                "order": 1,
                "what_it_is": "A dense retrieval module.",
                "what_it_does": "Fetches relevant evidence from a corpus.",
                "why_it_exists": "Grounds the generator on external evidence.",
                "inputs": "Query",
                "outputs": "Retrieved documents",
            }
        ]
        if section == "method"
        else [],
        "training_objectives": [
            {
                "name": "Ranking loss",
                "what_it_optimizes": "Better retrieval ordering",
                "why_it_matters": "Supports more faithful downstream generation",
                "when_it_is_applied": "Retriever training",
            }
        ]
        if section == "method"
        else [],
        "claims": [
            {
                "claim": claim,
                "evidence": [
                    {
                        "type": "figure",
                        "label": "Figure 1",
                        "page": 3,
                        "detail": "Shows the end-to-end workflow.",
                    },
                    {
                        "type": "number",
                        "label": "+4.2",
                        "page": 6,
                        "detail": "Main benchmark improvement.",
                    },
                ],
                "importance": "high",
            }
        ],
        "risks": [],
    }
    return json.dumps(payload)


def _memory_extraction_payload() -> str:
    return json.dumps(
        {
            "entities": [
                {
                    "name": "Retrieval-Augmented Generation",
                    "type": "method",
                    "summary": "Generation grounded on retrieved evidence.",
                }
            ],
            "claims": [
                {
                    "claim_key": "rag-main-gain",
                    "title": "RAG improves benchmark accuracy",
                    "body": "The paper reports a measurable improvement on the main benchmark.",
                    "claim_type": "finding",
                    "stance": "support",
                    "importance": 0.9,
                    "entity_names": ["Retrieval-Augmented Generation"],
                    "evidence": [
                        {
                            "section_key": "experiments",
                            "section_title": "Experiments and Results",
                            "snippet": "Table 1 reports a +4.2 gain.",
                            "summary": "Main quantitative gain.",
                            "page_label": "p.6",
                        }
                    ],
                }
            ],
            "synthesis_items": [
                {
                    "item_type": "consensus",
                    "title": "Evidence-grounded generation is effective",
                    "summary": "The run suggests retrieval improves factual grounding.",
                    "claim_keys": ["rag-main-gain"],
                    "entity_names": ["Retrieval-Augmented Generation"],
                    "confidence": 0.7,
                }
            ],
            "paper_relations": [],
            "style_observations": [
                {"key": "tone", "value": "precise"},
            ],
        }
    )


class InterpreterSmokeTest(unittest.TestCase):
    def test_interpreter_run_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            pdf_path = tmp_root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n% smoke\n")
            report_path = tmp_root / "report.md"
            results_dir = tmp_root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            db_path = tmp_root / "memory.db"

            memory_manager = MemoryManager(db_path)
            profile = memory_manager.get_profile_by_name("default")
            assert profile is not None
            profile_id = int(profile["id"])

            job_id = "interpreter-smoke-test"
            artifact_dir = get_job_results_dir(job_id)
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir, ignore_errors=True)

            parsed_paper = {
                "job_id": job_id,
                "precompute_localized_working_memory": False,
                "paper_id": "smoke-paper",
                "pdf_path": str(pdf_path),
                "job_mode": "manual",
                "selection": {
                    "post_download_topic_fit_threshold": 0.55,
                },
                "job_results_dir": str(results_dir),
                "job_report_path": str(report_path),
                "figures": [],
                "figure_index": {},
            }

            async def fake_pdf_fallback(*args, **kwargs):
                return _paper_notes_payload()

            async def fake_task_llm(*args, **kwargs):
                step_label = kwargs.get("step_label", "")
                if step_label == "T1 one-line summary":
                    return "Smoke Test Paper uses retrieval-augmented generation to improve grounded long-context reasoning."
                if step_label == "T5 ablation studies":
                    return "Removing retrieval hurts grounded generation quality."
                if step_label == "T6 limitations and future directions":
                    return "The benchmark scope is limited and broader evaluation is needed."
                if step_label == "T7 overall assessment and glossary":
                    return (
                        "The paper is practically useful and methodologically clear.\n"
                        "---GLOSSARY---\n"
                        '[{"term":"Retrieval-Augmented Generation","explanation":"Generation conditioned on retrieved evidence."}]'
                    )
                if step_label.endswith("adjudication"):
                    if "Background" in step_label:
                        return _structured_section(
                            "background",
                            "The paper targets brittle long-context reasoning.",
                            "Prior methods struggle to ground answers reliably over long context windows.",
                        )
                    if "Core Method" in step_label:
                        return _structured_section(
                            "method",
                            "The method couples retrieval with a grounded generator.",
                            "The paper introduces a retrieval-then-generation pipeline for grounded answers.",
                        )
                    if "Experiments" in step_label:
                        return _structured_section(
                            "experiments",
                            "The method improves benchmark accuracy and robustness.",
                            "The method outperforms strong baselines by 4.2 points on the main benchmark.",
                        )
                if step_label == "memory writeback extraction":
                    return _memory_extraction_payload()
                if step_label == "working memory distilled summary compression":
                    return (
                        "One-line summary: RAG improves grounded long-context reasoning.\n\n"
                        "Background distilled claims:\n"
                        "- Prior methods struggle to keep long-context answers reliably grounded [evidence: Figure 1 | p.3 | Shows the end-to-end workflow.]\n\n"
                        "Method distilled claims:\n"
                        "- The paper uses a retrieval-then-generation pipeline for grounded answers [evidence: Figure 1 | p.3 | Shows the end-to-end workflow.]\n\n"
                        "Experiments distilled claims:\n"
                        "- The method beats strong baselines by 4.2 points on the main benchmark [evidence: +4.2 | p.6 | Main benchmark improvement.]\n\n"
                        "Overall assessment:\n"
                        "The method is clear and practically useful, but broader evaluation is still needed."
                    )
                raise AssertionError(f"Unexpected step_label: {step_label}")

            async def fake_dual(*args, **kwargs):
                context_label = args[1]
                if context_label == "Research Background and Motivation":
                    payload = _structured_section(
                        "background",
                        "The paper targets brittle long-context reasoning.",
                        "Prior methods struggle to ground answers reliably over long context windows.",
                    )
                elif context_label == "Core Method":
                    payload = _structured_section(
                        "method",
                        "The method couples retrieval with a grounded generator.",
                        "The paper introduces a retrieval-then-generation pipeline for grounded answers.",
                    )
                elif context_label == "Experiments and Results":
                    payload = _structured_section(
                        "experiments",
                        "The method improves benchmark accuracy and robustness.",
                        "The method outperforms strong baselines by 4.2 points on the main benchmark.",
                    )
                else:
                    raise AssertionError(f"Unexpected context_label: {context_label}")
                return [("gpt_pro", payload), ("gem_pro", payload)], []

            async def fake_translate(english_md, **kwargs):
                return english_md

            async def fake_review(chinese_md, english_md):
                return chinese_md

            async def fake_resolve_repo(meta, parsed_paper, task_results):
                return meta

            def fake_translate_memory_batch_sync(items, **kwargs):
                return [
                    {key: "" for key in (item.get("fields") or {}).keys()}
                    for item in items
                ]

            def fake_translate_memory_item_sync(item_type, fields, **kwargs):
                return {key: "" for key in fields.keys()}

            async def fake_to_thread(func, *args, **kwargs):
                return func(*args, **kwargs)

            agent = PaperInterpreterAgent()
            try:
                with (
                    patch(
                        "modules.paper_interpreter.task_runner.call_llm_with_pdf_fallback",
                        fake_pdf_fallback,
                    ),
                    patch(
                        "modules.paper_interpreter.task_runner.call_llm_fallback",
                        fake_task_llm,
                    ),
                    patch(
                        "modules.paper_interpreter.task_runner.collect_dual_model_responses",
                        fake_dual,
                    ),
                    patch(
                        "modules.paper_interpreter.agent.call_llm_fallback",
                        fake_task_llm,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler.translate_to_chinese",
                        fake_translate,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler.review_translation",
                        fake_review,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler._resolve_code_repository",
                        fake_resolve_repo,
                    ),
                    patch(
                        "utils.memory.translate_memory_batch_sync",
                        fake_translate_memory_batch_sync,
                    ),
                    patch(
                        "utils.memory.translate_memory_item_sync",
                        fake_translate_memory_item_sync,
                    ),
                    patch(
                        "modules.paper_interpreter.agent.asyncio.to_thread",
                        fake_to_thread,
                    ),
                ):
                    output_path = asyncio.run(
                        asyncio.wait_for(
                            agent.run(
                                parsed_paper,
                                profile_id=profile_id,
                                memory_manager=memory_manager,
                            ),
                            timeout=5.0,
                        )
                    )
            finally:
                memory_manager.close()

            self.assertEqual(output_path, report_path)
            self.assertTrue(report_path.exists())
            self.assertTrue(report_path.with_name("report.en.md").exists())
            self.assertTrue((artifact_dir / "working_memory.json").exists())
            self.assertTrue((artifact_dir / "distilled_memory_summary.md").exists())
            self.assertTrue((artifact_dir / "report_audit.json").exists())

    def test_manual_explicit_profile_skips_post_download_topic_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            pdf_path = tmp_root / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n% smoke\n")
            report_path = tmp_root / "report.md"
            results_dir = tmp_root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            db_path = tmp_root / "memory.db"

            memory_manager = MemoryManager(db_path)
            profile = memory_manager.get_profile_by_name("default")
            assert profile is not None
            profile_id = int(profile["id"])

            job_id = "interpreter-manual-explicit-skip"
            artifact_dir = get_job_results_dir(job_id)
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir, ignore_errors=True)

            parsed_paper = {
                "job_id": job_id,
                "job_mode": "manual",
                "precompute_localized_working_memory": False,
                "paper_id": "manual-skip-paper",
                "pdf_path": str(pdf_path),
                "job_results_dir": str(results_dir),
                "job_report_path": str(report_path),
                "figures": [],
                "figure_index": {},
                "topics": [{"name": "time series", "query": "time series forecasting", "keywords": ["forecasting"]}],
                "selection": {"post_download_topic_fit_threshold": 0.61},
            }

            async def fake_pdf_fallback(*args, **kwargs):
                return _paper_notes_payload()

            async def fake_task_llm(*args, **kwargs):
                step_label = kwargs.get("step_label", "")
                if step_label == "T1 one-line summary":
                    return "Smoke Test Paper uses retrieval-augmented generation to improve grounded long-context reasoning."
                if step_label == "T5 ablation studies":
                    return "Removing retrieval hurts grounded generation quality."
                if step_label == "T6 limitations and future directions":
                    return "The benchmark scope is limited and broader evaluation is needed."
                if step_label == "T7 overall assessment and glossary":
                    return (
                        "The paper is practically useful and methodologically clear.\n"
                        "---GLOSSARY---\n"
                        '[{"term":"Retrieval-Augmented Generation","explanation":"Generation conditioned on retrieved evidence."}]'
                    )
                if step_label.endswith("adjudication"):
                    if "Background" in step_label:
                        return _structured_section(
                            "background",
                            "The paper targets brittle long-context reasoning.",
                            "Prior methods struggle to ground answers reliably over long context windows.",
                        )
                    if "Core Method" in step_label:
                        return _structured_section(
                            "method",
                            "The method couples retrieval with a grounded generator.",
                            "The paper introduces a retrieval-then-generation pipeline for grounded answers.",
                        )
                    if "Experiments" in step_label:
                        return _structured_section(
                            "experiments",
                            "The method improves benchmark accuracy and robustness.",
                            "The method outperforms strong baselines by 4.2 points on the main benchmark.",
                        )
                if step_label == "memory writeback extraction":
                    return _memory_extraction_payload()
                if step_label == "working memory distilled summary compression":
                    return (
                        "One-line summary: RAG improves grounded long-context reasoning.\n\n"
                        "Background distilled claims:\n"
                        "- Prior methods struggle to keep long-context answers reliably grounded [evidence: Figure 1 | p.3 | Shows the end-to-end workflow.]\n\n"
                        "Method distilled claims:\n"
                        "- The paper uses a retrieval-then-generation pipeline for grounded answers [evidence: Figure 1 | p.3 | Shows the end-to-end workflow.]\n\n"
                        "Experiments distilled claims:\n"
                        "- The method beats strong baselines by 4.2 points on the main benchmark [evidence: +4.2 | p.6 | Main benchmark improvement.]\n\n"
                        "Overall assessment:\n"
                        "The method is clear and practically useful, but broader evaluation is still needed."
                    )
                raise AssertionError(f"Unexpected step_label: {step_label}")

            async def fake_dual(*args, **kwargs):
                context_label = args[1]
                if context_label == "Research Background and Motivation":
                    payload = _structured_section(
                        "background",
                        "The paper targets brittle long-context reasoning.",
                        "Prior methods struggle to ground answers reliably over long context windows.",
                    )
                elif context_label == "Core Method":
                    payload = _structured_section(
                        "method",
                        "The method couples retrieval with a grounded generator.",
                        "The paper introduces a retrieval-then-generation pipeline for grounded answers.",
                    )
                elif context_label == "Experiments and Results":
                    payload = _structured_section(
                        "experiments",
                        "The method improves benchmark accuracy and robustness.",
                        "The method outperforms strong baselines by 4.2 points on the main benchmark.",
                    )
                else:
                    raise AssertionError(f"Unexpected context_label: {context_label}")
                return [("gpt_pro", payload), ("gem_pro", payload)], []

            async def fake_translate(english_md, **kwargs):
                return english_md

            async def fake_review(chinese_md, english_md):
                return chinese_md

            async def fake_resolve_repo(meta, parsed_paper, task_results):
                return meta

            def fake_translate_memory_batch_sync(items, **kwargs):
                return [
                    {key: "" for key in (item.get("fields") or {}).keys()}
                    for item in items
                ]

            def fake_translate_memory_item_sync(item_type, fields, **kwargs):
                return {key: "" for key in fields.keys()}

            async def fake_to_thread(func, *args, **kwargs):
                return func(*args, **kwargs)

            agent = PaperInterpreterAgent()
            try:
                with (
                    patch(
                        "modules.paper_interpreter.task_runner.call_llm_with_pdf_fallback",
                        fake_pdf_fallback,
                    ),
                    patch(
                        "modules.paper_interpreter.task_runner.call_llm_fallback",
                        fake_task_llm,
                    ),
                    patch(
                        "modules.paper_interpreter.task_runner.collect_dual_model_responses",
                        fake_dual,
                    ),
                    patch(
                        "modules.paper_interpreter.agent.call_llm_fallback",
                        fake_task_llm,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler.translate_to_chinese",
                        fake_translate,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler.review_translation",
                        fake_review,
                    ),
                    patch(
                        "modules.paper_interpreter.assembler._resolve_code_repository",
                        fake_resolve_repo,
                    ),
                    patch(
                        "utils.memory.translate_memory_batch_sync",
                        fake_translate_memory_batch_sync,
                    ),
                    patch(
                        "utils.memory.translate_memory_item_sync",
                        fake_translate_memory_item_sync,
                    ),
                    patch(
                        "modules.paper_interpreter.agent.asyncio.to_thread",
                        fake_to_thread,
                    ),
                ):
                    asyncio.run(
                        asyncio.wait_for(
                            agent.run(
                                parsed_paper,
                                profile_id=profile_id,
                                profile_mode="explicit",
                                memory_manager=memory_manager,
                            ),
                            timeout=5.0,
                        )
                    )
            finally:
                memory_manager.close()

            selector_diagnostics = json.loads(
                (artifact_dir / "selector_diagnostics.json").read_text(encoding="utf-8")
            )
            topic_audit = selector_diagnostics.get("selected_paper_topic_audit") or {}
            self.assertTrue(topic_audit.get("skipped"))
            self.assertEqual(
                topic_audit.get("reason"), "manual_upload_with_explicit_profile"
            )
            self.assertEqual(topic_audit.get("threshold"), 0.61)

            report_audit = json.loads(
                (artifact_dir / "report_audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_audit["status"], "pass")
            self.assertFalse(bool(report_audit["warning"]))
            self.assertEqual(int(report_audit["repair_passes"]), 1)
            self.assertGreaterEqual(len(report_audit["issues"]), 1)

            reloaded = MemoryManager(db_path)
            try:
                claims = reloaded.list_claims(profile_id, limit=20)
                synthesis_items = reloaded.list_synthesis_items(profile_id, limit=20)
                self.assertGreaterEqual(len(claims), 1)
                self.assertGreaterEqual(len(synthesis_items), 1)
            finally:
                reloaded.close()
                if artifact_dir.exists():
                    shutil.rmtree(artifact_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
