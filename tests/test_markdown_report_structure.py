import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.markdown import build_markdown_en


class MarkdownReportStructureTest(unittest.TestCase):
    def test_pmrc_markdown_uses_pmrc_headings_and_merges_sections(self) -> None:
        markdown = build_markdown_en(
            title_en="Smoke Test Paper",
            venue="Arxiv",
            pub_date="2026-01-01",
            institution="Test Lab",
            generated_at="2026-04-01 12:00",
            one_line_summary="A concise summary.",
            background="Background block.",
            method="Method block.",
            experiments="Experiment block.",
            ablation="Ablation block.",
            limitations="Limitation block.",
            conclusion="Conclusion block.",
            structure_mode="pmrc",
        )

        self.assertIn("## 1. Problem and Motivation", markdown)
        self.assertIn("## 2. Method and Key Mechanisms", markdown)
        self.assertIn("## 3. Results, Comparisons, and Ablations", markdown)
        self.assertIn("## 4. Conclusions, Limitations, and Takeaways", markdown)
        self.assertNotIn("## 5. Limitations and Future Directions", markdown)
        self.assertIn("Ablation block.", markdown)
        self.assertIn("### Limitations and Future Directions", markdown)
        self.assertIn("Limitation block.", markdown)


if __name__ == "__main__":
    unittest.main()
