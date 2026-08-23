import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from sts_collector import EVENT_TYPES, LocationResolver, STSLiveCollector


def xml(value):
    return ET.fromstring(value)


class CollectorTests(unittest.TestCase):
    def test_new_regular_train_is_initialized_once_and_missing_train_is_kept(self):
        collector = STSLiveCollector()
        collector.process(xml('<simzeit zeit="1000" />'))
        commands = collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        self.assertEqual(commands[:2], ['<zugdetails zid="7" />', '<zugfahrplan zid="7" />'])
        self.assertEqual(len(commands), 2 + len(EVENT_TYPES))
        self.assertEqual(collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>')), [])
        collector.process(xml("<zugliste />"))
        self.assertEqual(collector.services[7].status, "inactive_unknown")
        self.assertIn(7, collector.services)

    def test_temporary_locomotive_is_separate_and_not_initialized(self):
        collector = STSLiveCollector()
        commands = collector.process(xml('<zugliste><zug zid="-1" name="Lok IC 2085" /></zugliste>'))
        self.assertEqual(commands, [])
        self.assertTrue(collector.services[-1].temporary_locomotive)
        self.assertEqual(collector.services[-1].status, "temporary_locomotive")

    def test_original_schedule_never_changes_but_current_schedule_does(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        collector.process(xml(
            '<zugfahrplan zid="7"><gleis name="MBLH 1" plan="MBLH 1" an="15:00" ab="15:01" flags="D" /></zugfahrplan>'
        ))
        collector.process(xml(
            '<zugfahrplan zid="7"><gleis name="MBLH 2" plan="MBLH 1" an="15:00" ab="15:01" flags="D" /></zugfahrplan>'
        ))
        service = collector.services[7]
        self.assertEqual(service.original_schedule[0].current_name, "MBLH 1")
        self.assertEqual(service.current_schedule[0].current_name, "MBLH 2")
        self.assertEqual(service.current_schedule[0].planned_name, "MBLH 1")
        self.assertEqual(len(service.raw_schedules), 2)

    def test_location_mapping_is_explicit_and_raw_name_survives(self):
        resolver = LocationResolver({"3 N": {"operating_point": "Ulm Hbf", "physical_track": "3",
                                                "track_section": "N", "track_resolution": "section"}})
        collector = STSLiveCollector(resolver=resolver)
        collector.process(xml('<zugfahrplan zid="2"><gleis name="3 N" plan="3 N" /></zugfahrplan>'))
        point = collector.services[2].current_schedule[0]
        self.assertEqual((point.raw_name, point.physical_track, point.track_section), ("3 N", "3", "N"))
        collector.process(xml('<zugfahrplan zid="3"><gleis name="Martinszell" /></zugfahrplan>'))
        unknown = collector.services[3].current_schedule[0]
        self.assertEqual(unknown.raw_name, "Martinszell")
        self.assertEqual(unknown.track_resolution, "unknown")
        self.assertIsNone(unknown.physical_track)

    def test_raw_events_are_complete_while_interpreted_events_are_stateful(self):
        times = iter(datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc) for i in range(4))
        collector = STSLiveCollector(clock=lambda: next(times))
        for art in ("rothalt", "rothalt", "rothalt", "wurdegruen"):
            collector.process(xml(f'<ereignis art="{art}" zid="7" name="RE 7" gleis="A 1" />'))
        service = collector.services[7]
        self.assertEqual(len(service.raw_events), 4)
        self.assertEqual([event.art for event in service.interpreted_events], ["rothalt", "wurdegruen"])
        self.assertTrue(all(event.raw_xml for event in service.raw_events))

    def test_simtime_drives_train_list_refresh_including_midnight(self):
        collector = STSLiveCollector()
        self.assertEqual(collector.process(xml('<simzeit zeit="86340000" />')), ["<zugliste />"])
        self.assertEqual(collector.process(xml('<simzeit zeit="30000" />')), [])
        self.assertEqual(collector.process(xml('<simzeit zeit="60000" />')), ["<zugliste />"])

    def test_persistence_round_trip_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            collector = STSLiveCollector(path)
            raw = '<ereignis art="abfahrt" zid="7" name="RE 7" gleis="A 1" plangleis="A 2" />'
            collector.process(xml(raw), raw)
            restored = STSLiveCollector(path)
            self.assertEqual(restored.services[7].raw_events[0].planned_track, "A 2")
            self.assertIn(raw, restored.raw_xml)

    def test_details_preserve_plan_and_actual_track(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugdetails zid="7" name="RE 7" verspaetung="5" gleis="MBLH 2" '
                              'plangleis="MBLH 1" sichtbar="true" amgleis="false" von="A" nach="B" />'))
        service = collector.services[7]
        self.assertEqual((service.current_track, service.planned_track), ("MBLH 2", "MBLH 1"))
        self.assertEqual((service.visible, service.at_track), (True, False))


if __name__ == "__main__":
    unittest.main()
