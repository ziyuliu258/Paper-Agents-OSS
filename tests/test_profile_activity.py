import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import Database


class ProfileActivityTest(unittest.TestCase):
    def test_list_profile_activity_normalizes_missing_paper_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "memory.db")
            try:
                job = db.create_job(mode="auto", profile_id=2, config_snapshot={})

                activity = db.list_profile_activity(2, limit=10)

                self.assertEqual(len(activity), 1)
                item = activity[0]
                self.assertEqual(item["job_id"], job["id"])
                self.assertIsNone(item["paper_row_id"])
                self.assertEqual(item["paper_id"], "")
                self.assertEqual(item["paper_title"], "")
                self.assertEqual(item["paper_venue"], "")
                self.assertEqual(item["paper_pub_date"], "")
                self.assertEqual(item["paper_pdf_path"], "")
                self.assertEqual(item["paper_report_path"], "")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
