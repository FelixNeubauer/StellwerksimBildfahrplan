import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.collector_adapter import CollectorAdapter


class CollectorStartupTests(unittest.TestCase):
    def test_normal_start_ignores_persisted_volatile_collector_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old-state.json"
            path.write_text(json.dumps({
                "schema_version": 2, "simtime": 43_200_000, "sim_day": 4,
                "session": {"aid": 123, "name": "Alt"}, "services": [{
                    "zid": 7, "name": "Alter Zug", "service_kind": "train",
                    "original_schedule": [], "current_schedule": [], "raw_events": [],
                    "interpreted_events": [], "relations": [], "departure_states": {},
                }],
                "families": [], "raw_xml": ["<zugliste />"],
            }), encoding="utf-8")
            live = CollectorAdapter(path, offline=False)
            self.assertIsNone(live.collector.simtime)
            self.assertEqual(live.collector.services, {})
            self.assertIsNone(live.collector.session.aid)
            self.assertEqual(live.collector.raw_xml, [])
            self.assertIsNone(live.collector.storage_path)

            offline = CollectorAdapter(path, offline=True)
            self.assertEqual(offline.collector.simtime, 43_200_000)
            self.assertEqual(offline.collector.session.aid, 123)
            self.assertIn(7, offline.collector.services)

    def test_empty_live_start_has_no_train_lines_until_current_data_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CollectorAdapter(Path(directory) / "missing.json", offline=False)
            self.assertEqual(adapter.snapshot().services, ())


if __name__ == "__main__":
    unittest.main()
