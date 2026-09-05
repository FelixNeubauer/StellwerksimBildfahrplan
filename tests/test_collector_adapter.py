import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.collector_adapter import CollectorAdapter
from app.simtime import SimTimeInterpolator


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

    def test_event_uses_interpolated_simtime_at_protocol_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [100.0]
            adapter = CollectorAdapter(Path(directory) / "missing.json", offline=False)
            adapter._display_clock = SimTimeInterpolator(
                lambda: now[0], max_extrapolation=30)
            adapter._client = SimpleNamespace(connected=True)
            simtime = (6 * 3600 + 5 * 60 + 40) * 1000
            adapter._process_protocol_element(
                ET.fromstring(f'<simzeit zeit="{simtime}" />'), "")
            adapter._process_protocol_element(
                ET.fromstring('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'), "")
            now[0] += 20
            adapter._process_protocol_element(
                ET.fromstring('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'), "")
            event = adapter.collector.services[7].raw_events[-1]
            self.assertEqual(event.simtime, simtime)
            self.assertEqual(event.event_simtime_seconds, 6 * 3600 + 6 * 60)
            self.assertEqual(
                adapter.collector.observed_train_times[7].rows[0].actual_arrival_minute,
                366,
            )

    def test_schedule_disappearance_uses_interpolated_receipt_time(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [100.0]
            adapter = CollectorAdapter(Path(directory) / "missing.json", offline=False)
            adapter._display_clock = SimTimeInterpolator(
                lambda: now[0], max_extrapolation=60)
            adapter._client = SimpleNamespace(connected=True)
            simtime = (10 * 3600 + 10) * 1000
            adapter._process_protocol_element(
                ET.fromstring(f'<simzeit zeit="{simtime}" />'), "")
            adapter._process_protocol_element(
                ET.fromstring('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'), "")
            now[0] += 35
            adapter._process_protocol_element(
                ET.fromstring('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'), "")
            self.assertEqual(
                adapter.collector.observed_train_times[7].rows[0].actual_departure_minute,
                601,
            )


if __name__ == "__main__":
    unittest.main()
