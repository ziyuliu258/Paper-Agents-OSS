import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import Database
from utils.memory import MemoryManager
from utils.profile_assignment import assign_profile_for_paper
from utils.profile_assignment import suggest_profile_for_topics


class ProfileAssignmentTest(unittest.TestCase):
    def test_suggest_profile_for_topics_matches_existing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            db = Database(db_path)
            mm = MemoryManager(db_path)
            original_localize = MemoryManager._localize_fields
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                matched = mm.create_profile(
                    "Time Series Forecasting",
                    "Temporal forecasting, multivariate time series prediction, and long-horizon forecasting.",
                )
                mm.create_profile(
                    "Agents",
                    "Tool-using agent workflows, planning, reflection, and orchestration.",
                )
                result = suggest_profile_for_topics(
                    mm,
                    db,
                    [
                        {
                            "name": "Time Series Forecasting",
                            "query": "long-term multivariate temporal forecasting",
                            "keywords": [
                                "time series forecasting",
                                "temporal prediction",
                            ],
                        }
                    ],
                )
                self.assertIsNotNone(result["matched_profile"])
                assert result["matched_profile"] is not None
                self.assertEqual(
                    int(result["matched_profile"]["profile_id"]),
                    int(matched["id"]),
                )
            finally:
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                mm.close()
                db.close()

    def test_suggest_profile_for_topics_matches_english_profile_from_chinese_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            db = Database(db_path)
            mm = MemoryManager(db_path)
            original_localize = MemoryManager._localize_fields
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                matched = mm.create_profile(
                    "Time Series Forecasting",
                    "Temporal forecasting, multivariate time series prediction, and long-horizon forecasting.",
                )
                result = suggest_profile_for_topics(
                    mm,
                    db,
                    [
                        {
                            "name": "时间序列预测",
                            "query": "多变量时间序列长期预测",
                            "keywords": [
                                "time series forecasting",
                                "multivariate forecasting",
                            ],
                        }
                    ],
                )
                self.assertIsNotNone(result["matched_profile"])
                assert result["matched_profile"] is not None
                self.assertEqual(
                    int(result["matched_profile"]["profile_id"]),
                    int(matched["id"]),
                )
            finally:
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                mm.close()
                db.close()

    def test_assign_profile_for_paper_creates_new_profile_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            db = Database(db_path)
            mm = MemoryManager(db_path)
            original_localize = MemoryManager._localize_fields
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                mm.create_profile(
                    "Agents",
                    "Tool-using agent workflows, planning, reflection, and orchestration.",
                )
                before = len(mm.list_profiles())
                result = assign_profile_for_paper(
                    mm,
                    db,
                    {
                        "metadata": {
                            "title_en": "UNet Variants for Medical Image Segmentation",
                            "venue": "MICCAI",
                        },
                        "paper_summary": (
                            "The paper studies medical image segmentation with a "
                            "UNet-style architecture and pixel-level supervision."
                        ),
                        "problem": ["Accurate organ boundary delineation in volumetric scans."],
                        "method_steps": ["Encode scans", "Decode masks", "Refine segmentation boundaries"],
                    },
                )
                after = len(mm.list_profiles())
                self.assertEqual(result["status"], "created")
                self.assertEqual(after, before + 1)
                self.assertIn("Segmentation", result["profile_name"])
            finally:
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                mm.close()
                db.close()

    def test_assign_profile_prefers_existing_chinese_time_series_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory.db"
            db = Database(db_path)
            mm = MemoryManager(db_path)
            original_localize = MemoryManager._localize_fields
            try:
                MemoryManager._localize_fields = (  # type: ignore[method-assign]
                    lambda self, _label, fields, **_kwargs: {
                        key: str(value) for key, value in dict(fields).items()
                    }
                )
                matched = mm.create_profile(
                    "时间序列",
                    "时间序列预测",
                )
                result = assign_profile_for_paper(
                    mm,
                    db,
                    {
                        "metadata": {
                            "title_en": "Adaptive Multi-Scale Decomposition Framework for Time Series Forecasting",
                            "venue": "AAAI",
                        },
                        "paper_summary": (
                            "This paper proposes an adaptive multi-scale decomposition framework "
                            "for time series forecasting."
                        ),
                        "problem": [
                            "Long-horizon time series forecasting",
                            "Multi-scale temporal pattern modeling",
                        ],
                        "method_steps": [
                            "Decompose time series into multiple temporal scales",
                            "Forecast future values with the refined representation",
                        ],
                    },
                    topics=[
                        {
                            "name": "时间序列预测",
                            "query": "时间序列预测",
                            "keywords": ["time series", "forecasting", "prediction"],
                        }
                    ],
                )
                self.assertEqual(result["status"], "matched")
                self.assertEqual(int(result["profile_id"]), int(matched["id"]))
            finally:
                MemoryManager._localize_fields = original_localize  # type: ignore[method-assign]
                mm.close()
                db.close()


if __name__ == "__main__":
    unittest.main()
