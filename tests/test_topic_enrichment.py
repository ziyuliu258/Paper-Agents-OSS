import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.topic_enrichment import enrich_topics_for_search
from utils.topic_enrichment import topic_needs_enrichment


class TopicEnrichmentTest(unittest.TestCase):
    def test_topic_needs_enrichment_for_chinese_only_topic(self) -> None:
        self.assertTrue(
            topic_needs_enrichment(
                {
                    "name": "时间序列预测",
                    "query": "时间序列预测",
                    "keywords": [],
                }
            )
        )

    def test_enrich_topics_adds_heuristic_english_hints(self) -> None:
        result = asyncio.run(
            enrich_topics_for_search(
                [
                    {
                        "name": "时间序列预测",
                        "query": "搜集与时间序列预测相关的论文，特别关注多变量时间序列和通道依赖方面的研究。",
                        "keywords": [],
                    }
                ]
            )
        )
        topic = result[0]
        self.assertIn("time series", topic["keywords"])
        self.assertIn("forecasting", topic["keywords"])
        self.assertIn("multivariate", topic["keywords"])
        self.assertIn("channel dependency", topic["keywords"])

    def test_enrich_topics_uses_llm_when_hints_are_still_weak(self) -> None:
        async def fake_call_llm_fallback(*_args, **_kwargs):
            return (
                '{"english_query":"causal invariant representation learning",'
                '"keywords":["invariant learning","causal representation","distribution shift robustness"]}'
            )

        with patch(
            "utils.topic_enrichment.call_llm_fallback",
            new=fake_call_llm_fallback,
        ):
            result = asyncio.run(
                enrich_topics_for_search(
                    [
                        {
                            "name": "因果不变性建模",
                            "query": "因果不变性建模",
                            "keywords": [],
                        }
                    ],
                    model_alias="gem_flash",
                )
            )
        topic = result[0]
        self.assertEqual(
            topic["query_en"],
            "causal invariant representation learning",
        )
        self.assertIn("invariant learning", topic["keywords"])
        self.assertIn("causal representation", topic["keywords"])
        self.assertEqual(topic["topic_enrichment_applied"], "llm")


if __name__ == "__main__":
    unittest.main()
