import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.paper_selector.fetcher import _build_queries
from modules.paper_selector.fetcher import fetch_candidates
from modules.paper_selector.agent import PaperSelectorAgent
from modules.paper_selector.agent import SelectionFailure
from modules.paper_selector.reranker import _keyword_overlap_score
from modules.paper_selector.selector import select_top_paper
from utils.memory import MemoryManager


class PaperSelectorRegressionTest(unittest.TestCase):
    def test_empty_selection_memory_renders_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mm = MemoryManager(Path(tmp_dir) / "memory.db")
            try:
                rendered = mm.render_selection_context(
                    {
                        "high_level_digest": [],
                        "priority_claims": [],
                        "related_papers": [],
                    }
                )
            finally:
                mm.close()
        self.assertEqual(rendered, "")

    def test_build_queries_includes_topic_name_query_and_keywords(self) -> None:
        queries = _build_queries(
            [
                {
                    "name": "计算机视觉",
                    "query": "computer vision",
                    "keywords": ["Pose estimation", "Pose estimation", "Visual tracking"],
                    "query_en": "visual recognition",
                    "auto_keywords": ["multimodal vision"],
                    "heuristic_keywords": ["image understanding"],
                }
            ]
        )
        self.assertEqual(
            queries,
            [
                "计算机视觉",
                "computer vision",
                "visual recognition",
                "Pose estimation",
                "Visual tracking",
                "multimodal vision",
                "image understanding",
            ],
        )

    def test_keyword_overlap_ignores_placeholder_memory_context(self) -> None:
        score = _keyword_overlap_score(
            "MemoryBank: Enhancing Large Language Models with Long-Term Memory",
            [{"name": "计算机视觉", "query": "计算机视觉", "keywords": ["Pose estimation"]}],
            "[Profile Research Memory for Paper Selection]",
        )
        self.assertEqual(score, 0.0)

    def test_select_top_paper_fallback_prefers_selectable_candidate(self) -> None:
        candidates = [
            {
                "paper_id": "video-chatgpt",
                "title": "Video-ChatGPT",
                "abstract": "Video understanding with large vision-language models.",
                "pdf_url": "",
                "semantic_score": 0.95,
            },
            {
                "paper_id": "hybrid-sort",
                "title": "Hybrid-SORT",
                "abstract": "Online multi-object tracking for computer vision.",
                "pdf_url": "https://example.com/hybrid-sort.pdf",
                "semantic_score": 0.72,
            },
        ]

        async def _run() -> tuple[dict, dict]:
            with patch(
                "modules.paper_selector.selector.call_llm",
                new=AsyncMock(side_effect=RuntimeError("selector unavailable")),
            ):
                return await select_top_paper(
                    candidates,
                    [{"name": "计算机视觉", "query": "computer vision", "keywords": ["Pose estimation"]}],
                    memory_context="",
                )

        selected, selector_meta = asyncio.run(_run())
        self.assertEqual(selected["paper_id"], "hybrid-sort")
        self.assertIn("可下载候选", selected["selection_reason"])
        self.assertEqual(selector_meta["method"], "fallback")
        self.assertEqual(selector_meta["eligible_candidate_count"], 1)
        self.assertEqual(
            selector_meta["fallback_basis"],
            "highest_topic_fit_score_then_semantic_score_among_selectable_candidates",
        )

    def test_topic_fit_gate_rejects_keyword_hit_but_wrong_task_domain(self) -> None:
        config = {
            "topics": [
                {
                    "name": "时序预测",
                    "query": "long-term time series forecasting",
                    "keywords": ["time series forecasting", "temporal prediction"],
                }
            ],
            "selection": {
                "candidate_pool_size": 4,
                "semantic_top_k": 2,
                "min_semantic_score": 0.0,
            },
            "storage": {
                "cache_dir": tempfile.mkdtemp(),
                "fetch_dir": tempfile.mkdtemp(),
                "keep_cache": True,
            },
        }
        candidate = {
            "paper_id": "echomimic",
            "title": "EchoMimic",
            "abstract": "Audio-driven portrait generation with expressive talking-face synthesis.",
            "pdf_url": "https://example.com/echomimic.pdf",
            "semantic_score": 0.93,
            "source": "openreview",
        }

        async def _run() -> None:
            agent = PaperSelectorAgent(config)
            with (
                patch(
                    "modules.paper_selector.agent.fetch_candidates",
                    new=AsyncMock(return_value=[candidate]),
                ),
                patch(
                    "modules.paper_selector.agent.rerank_candidates",
                    new=AsyncMock(return_value=[candidate]),
                ),
                patch(
                    "modules.paper_selector.agent.enrich_candidates_with_pdf_urls",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "modules.paper_selector.agent.enrich_candidate_with_aic",
                    new=AsyncMock(
                        return_value={
                            **candidate,
                            "aic_text": (
                                "This paper studies expressive talking-head generation "
                                "and portrait animation from audio."
                            ),
                        }
                    ),
                ),
                patch(
                    "modules.paper_selector.agent._load_processed_dedupe_keys",
                    return_value=set(),
                ),
            ):
                await agent.run()

        with self.assertRaises(SelectionFailure) as ctx:
            asyncio.run(_run())

        diagnostics = ctx.exception.diagnostics
        self.assertEqual(
            diagnostics["failure_reason"],
            "topic_fit_gate_rejected_all_candidates",
        )
        self.assertEqual(len(diagnostics["fit_judgments"]), 1)
        self.assertEqual(len(diagnostics["rejected_candidates"]), 1)
        self.assertEqual(
            diagnostics["fit_judgments"][0]["fit_label"],
            "mismatch",
        )
        self.assertGreaterEqual(
            len(diagnostics["fit_judgments"][0]["mismatch_reasons"]),
            1,
        )

    def test_fetch_candidates_keeps_all_filtered_candidates_before_semantic_trim(self) -> None:
        candidates = [
            {
                "paper_id": f"paper-{idx}",
                "title": f"Candidate {idx}",
                "abstract": "Computer vision candidate",
                "pdf_url": f"https://example.com/{idx}.pdf",
                "venue": "AAAI",
                "date": "2024-03-24",
                "citations": 100 + idx,
                "institutions": [],
                "source": "openalex",
            }
            for idx in range(5)
        ]

        config = {
            "topics": [{"name": "计算机视觉", "query": "computer vision", "keywords": ["Pose estimation"]}],
            "selection": {
                "track": "classic",
                "candidate_pool_size": 2,
                "classic_min_citations": 0,
                "preferred_venues": ["AAAI"],
            },
            "storage": {"cache_dir": tempfile.mkdtemp()},
        }

        async def _run() -> list[dict]:
            with patch(
                "modules.paper_selector.fetcher._fetch_venue_first",
                new=AsyncMock(return_value=candidates),
            ):
                return await fetch_candidates(config)

        result = asyncio.run(_run())
        self.assertEqual(len(result), 5)


if __name__ == "__main__":
    unittest.main()
