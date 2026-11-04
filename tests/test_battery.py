import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor
import sources_monitor as sources
from topic_config import output_paths

class BatteryTest(unittest.TestCase):
    def test_filters(self):
        for title in ["EV battery recycling", "Second-life lithium-ion batteries", "Battery passport regulation"]:
            self.assertEqual(monitor.prefilter(title, ""), "pass")
        self.assertEqual(monitor.prefilter("Hydropower reservoir operations", ""), "off-topic")
        self.assertEqual(monitor.prefilter("A battery of tests", ""), "excluded")

    def test_paths(self):
        self.assertEqual(monitor.DATA, sources.DATA)
        self.assertEqual(monitor.DATA.parts[-2:], ("data", "battery"))
        with self.assertRaises(ValueError):
            output_paths("hydro")

    def test_preprints_and_sources(self):
        self.assertEqual(monitor.preprint_platform({"doi": "10.26434/chemrxiv-123"}, {}), "ChemRxiv")
        self.assertIn("Recycling", sources.classify("Battery recycling"))
        self.assertIn("battery", sources.EXT["rss_query"])

if __name__ == "__main__":
    unittest.main()
