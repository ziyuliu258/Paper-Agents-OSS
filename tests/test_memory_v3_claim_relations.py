import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.memory_claim_relations import build_claim_relations


class MemoryV3ClaimRelationsTest(unittest.TestCase):
    def test_build_claim_relations_returns_reinforce_extend_and_contradict(self) -> None:
        claims = [
            {
                "id": 1,
                "title": "Temporal fusion improves grounding",
                "body": "Temporal fusion improves grounding accuracy on the main benchmark.",
                "stance": "support",
                "importance": 0.9,
                "entity_names": ["Object Grounding", "Temporal Fusion"],
                "updated_at": 10.0,
                "review_status": "none",
                "status": "active",
            },
            {
                "id": 2,
                "title": "Temporal fusion improves grounding under sparse labels",
                "body": "Temporal fusion improves grounding accuracy under sparse-label settings.",
                "stance": "support",
                "importance": 0.82,
                "entity_names": [
                    "Object Grounding",
                    "Temporal Fusion",
                    "Sparse Labels",
                ],
                "updated_at": 12.0,
                "review_status": "none",
                "status": "active",
            },
            {
                "id": 3,
                "title": "Temporal fusion saturates on grounding",
                "body": "Temporal fusion does not improve grounding once the data regime is saturated.",
                "stance": "oppose",
                "importance": 0.88,
                "entity_names": ["Object Grounding", "Temporal Fusion"],
                "updated_at": 15.0,
                "review_status": "pending",
                "status": "conflicted",
            },
            {
                "id": 4,
                "title": "Object-centric memory stabilizes retrieval",
                "body": "Object-centric memory stabilizes retrieval quality across runs.",
                "stance": "support",
                "importance": 0.7,
                "entity_names": ["Retrieval Memory", "Object Grounding"],
                "updated_at": 20.0,
                "review_status": "none",
                "status": "active",
            },
        ]
        evidence_fragments = [
            {"claim_id": 1},
            {"claim_id": 1},
            {"claim_id": 2},
            {"claim_id": 3},
        ]
        reviews = [
            {"id": 9, "status": "pending", "target_type": "claim", "target_id": 3}
        ]

        rows, stats = build_claim_relations(claims, evidence_fragments, reviews)

        relation_types = {(row["source_claim_id"], row["target_claim_id"], row["relation_type"]) for row in rows}
        self.assertIn((2, 1, "extends"), relation_types)
        self.assertIn((1, 3, "contradicts"), relation_types)
        self.assertIn((1, 4, "reinforces"), relation_types)

        self.assertGreater(float(stats[1]["stability_score"] or 0.0), 0.5)
        self.assertIsNotNone(stats[1]["last_supported_at"])
        self.assertIsNotNone(stats[1]["last_challenged_at"])
        self.assertEqual(stats[1]["lifecycle_state"], "contested")
        self.assertEqual(stats[2]["lifecycle_state"], "contested")
        self.assertEqual(stats[3]["lifecycle_state"], "contested")
        self.assertEqual(stats[4]["lifecycle_state"], "contested")
        self.assertLess(float(stats[3]["stability_score"] or 1.0), float(stats[1]["stability_score"] or 0.0))

    def test_lifecycle_marks_unsupported_claims_for_review(self) -> None:
        claims = [
            {
                "id": 1,
                "title": "Ungrounded claim",
                "body": "This claim has no evidence in old memory.",
                "stance": "support",
                "importance": 0.6,
                "entity_names": ["Memory"],
                "updated_at": 1.0,
                "review_status": "none",
                "status": "active",
            }
        ]

        rows, stats = build_claim_relations(claims, [], [])

        self.assertEqual(rows, [])
        self.assertEqual(stats[1]["lifecycle_state"], "needs_review")
        self.assertEqual(stats[1]["lifecycle_reason"]["evidence_count"], 0)


if __name__ == "__main__":
    unittest.main()
