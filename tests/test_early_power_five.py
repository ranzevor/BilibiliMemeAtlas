"""Regression checks for the documented 战五渣 evidence boundary."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_catalogue


class EarlyPowerFiveEvidenceTests(unittest.TestCase):
    def test_official_chapter_and_2013_bilibili_title_are_separate_claims(self):
        entries, sources, _ = load_catalogue()
        item = next(entry for entry in entries if entry["id"] == "early-power-five")
        claims = {ref["source_id"]: ref["claims"] for ref in item["evidence"]}
        official = sources["early-src-powerfive-dragonball-official-2021"]
        bilibili = sources["early-src-powerfive-bili-2013"]
        self.assertEqual(claims[official["id"]], ["origin"])
        self.assertEqual(official["access_level"], "full")
        self.assertIn("第195回", official["supports"])
        self.assertEqual(claims[bilibili["id"]], ["platform_usage"])
        self.assertEqual(bilibili["access_level"], "metadata")
        self.assertIn("2013年7月14日", bilibili["supports"])
        self.assertEqual(item["evidence_status"], "partial")


if __name__ == "__main__":
    unittest.main()
