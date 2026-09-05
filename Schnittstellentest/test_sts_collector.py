import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from sts_collector import EVENT_TYPES, LocationResolver, STSLiveCollector


def xml(value):
    return ET.fromstring(value)


class CollectorTests(unittest.TestCase):
    @staticmethod
    def simtime(hour, minute, second):
        return ((hour * 60 + minute) * 60 + second) * 1000

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

    def test_train_list_discovery_records_endpoint_provenance(self):
        collector = STSLiveCollector()
        collector.startup_commands()
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        initial = collector.services[7]
        self.assertEqual(initial.discovery_source, "initial_train_list")
        self.assertEqual(initial.schedule_start_completeness,
                         "possibly_truncated_at_startup")
        self.assertEqual(initial.schedule_end_completeness, "likely_complete")

        collector.process(xml('<zugliste><zug zid="7" name="RE 7" />'
                              '<zug zid="8" name="RE 8" /></zugliste>'))
        periodic = collector.services[8]
        self.assertEqual(periodic.discovery_source, "periodic_train_list")
        self.assertEqual(periodic.schedule_start_completeness, "likely_complete")

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

    def test_schedule_refresh_uses_unique_10_30_50_slots(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /><zug zid="8" name="Wagen IC 8" />'
                              '<zug zid="-1" name="Lok RE 7" /></zugliste>'))
        cases = (
            ((12, 0, 9), []),
            ((12, 0, 10), ['<zugfahrplan zid="7" />', '<zugfahrplan zid="8" />']),
            ((12, 0, 10), []),
            ((12, 0, 29), []),
            ((12, 0, 30), ['<zugfahrplan zid="7" />', '<zugfahrplan zid="8" />']),
            ((12, 0, 50), ['<zugfahrplan zid="7" />', '<zugfahrplan zid="8" />']),
            ((12, 1, 10), ['<zugfahrplan zid="7" />', '<zugfahrplan zid="8" />']),
        )
        for parts, expected_schedules in cases:
            commands = collector.process(xml(f'<simzeit zeit="{self.simtime(*parts)}" />'))
            schedules = [command for command in commands if command.startswith("<zugfahrplan")]
            self.assertEqual(schedules, expected_schedules, parts)

    def test_schedule_slots_survive_midnight(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        before = collector.process(xml(f'<simzeit zeit="{self.simtime(23, 59, 50)}" />'))
        after = collector.process(xml(f'<simzeit zeit="{self.simtime(0, 0, 10)}" />'))
        self.assertIn('<zugfahrplan zid="7" />', before)
        self.assertIn('<zugfahrplan zid="7" />', after)

    def test_schedule_refresh_does_not_change_train_list_cadence(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(12, 0, 0)}" />'))
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        commands = collector.process(xml(f'<simzeit zeit="{self.simtime(12, 0, 10)}" />'))
        self.assertNotIn("<zugliste />", commands)
        commands = collector.process(xml(f'<simzeit zeit="{self.simtime(12, 2, 0)}" />'))
        self.assertIn("<zugliste />", commands)

    def test_departure_is_one_stateful_operation(self):
        collector = STSLiveCollector()
        for at_track, delay in (("true", 0), ("true", 0), ("true", 1), ("false", 1)):
            collector.process(xml(
                f'<ereignis art="abfahrt" zid="7" name="RE 7" gleis="A 1" '
                f'amgleis="{at_track}" verspaetung="{delay}" />'
            ))
        service = collector.services[7]
        self.assertEqual(len(service.raw_events), 4)
        departures = [event for event in service.interpreted_events if event.art == "abfahrt"]
        self.assertEqual(len(departures), 1)
        self.assertFalse(departures[0].at_track)
        state = service.departure_states["A 1"]
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.started_event.delay, 0)
        self.assertEqual(state.completed_event.delay, 1)

    def test_service_kinds_are_conservative(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugliste><zug zid="1" name="RE 32926" />'
                              '<zug zid="-1" name="Lok ALX 39953" />'
                              '<zug zid="2" name="Wagen IC 2084" /></zugliste>'))
        self.assertEqual(collector.services[1].service_kind, "train")
        self.assertEqual(collector.services[-1].service_kind, "locomotive_movement")
        self.assertEqual(collector.services[2].service_kind, "wagon_set")

    def test_startup_has_one_train_list_and_facility_request(self):
        collector = STSLiveCollector()
        commands = collector.startup_commands()
        self.assertEqual(commands.count("<zugliste />"), 1)
        self.assertIn("<anlageninfo />", commands)
        follow_up = collector.process(xml('<simzeit zeit="1000" />'))
        self.assertNotIn("<zugliste />", follow_up)

    def test_persistence_round_trip_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            collector = STSLiveCollector(path)
            raw = '<ereignis art="abfahrt" zid="7" name="RE 7" gleis="A 1" plangleis="A 2" />'
            collector.process(xml(raw), raw)
            restored = STSLiveCollector(path)
            self.assertEqual(restored.services[7].raw_events[0].planned_track, "A 2")
            self.assertIn(raw, restored.raw_xml)

    def test_hint_text_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            collector = STSLiveCollector(path)
            collector.process(xml('<zugfahrplan zid="7"><gleis name="A 1" plan="A 1" '
                                  'hinweistext="Lok von rechts nach links umsetzen" /></zugfahrplan>'))
            restored = STSLiveCollector(path)
            self.assertEqual(
                restored.services[7].current_schedule[0].hint_text,
                "Lok von rechts nach links umsetzen",
            )

    def test_different_aid_archives_and_starts_clean_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            collector = STSLiveCollector(path)
            collector.process(xml('<anlageninfo aid="823" name="Immenstadt" region="S" simbuild="1" online="true" />'))
            collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
            restored = STSLiveCollector(path)
            restored.process(xml('<anlageninfo aid="999" name="Andere Anlage" region="N" simbuild="2" online="false" />'))
            self.assertEqual(restored.session.aid, 999)
            self.assertEqual(restored.services, {})
            self.assertTrue(list(Path(directory).glob("collector.aid-823.*.json")))

    def test_track_change_creates_message_without_overwriting_original(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugliste><zug zid="7" name="RE 7" /></zugliste>'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="MBLH 1" plan="MBLH 1" an="12:00" /></zugfahrplan>'))
        collector.drain_messages()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="MBLH 2" plan="MBLH 1" an="12:00" /></zugfahrplan>'))
        self.assertEqual(collector.services[7].original_schedule[0].current_name, "MBLH 1")
        self.assertEqual(collector.services[7].current_schedule[0].current_name, "MBLH 2")
        self.assertTrue(any("MBLH 1 → MBLH 2" in message for message in collector.drain_messages()))

    def test_details_preserve_plan_and_actual_track(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugdetails zid="7" name="RE 7" verspaetung="5" gleis="MBLH 2" '
                              'plangleis="MBLH 1" sichtbar="true" amgleis="false" von="A" nach="B" />'))
        service = collector.services[7]
        self.assertEqual((service.current_track, service.planned_track), ("MBLH 2", "MBLH 1"))
        self.assertEqual((service.visible, service.at_track), (True, False))

    def test_observed_arrival_and_departure_match_original_planned_track(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 20, 0)}" />'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A1" plan="A1" />'
                              '<gleis name="B9" plan="B2" /><gleis name="C1" plan="C1" />'
                              '</zugfahrplan>'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B9" plangleis="B2" amgleis="true" />'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 24, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="B9" plangleis="B2" amgleis="true" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="C1" plangleis="C1" amgleis="false" />'))
        row = collector.observed_train_times[7].rows[1]
        self.assertEqual((row.actual_arrival_minute, row.actual_departure_minute), (620, 624))

    def test_observed_departure_without_arrival_and_duplicates_first_win(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B2" plan="B2" /></zugfahrplan>'))
        for minute in (24, 24, 25):
            collector.process(xml(f'<simzeit zeit="{self.simtime(10, minute, 0)}" />'))
            collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="other" plangleis="B2" amgleis="true" />'))
        self.assertEqual(collector.observed_train_times[7].rows, {})
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 26, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="next" plangleis="next" amgleis="false" />'))
        row = collector.observed_train_times[7].rows[0]
        self.assertIsNone(row.actual_arrival_minute)
        self.assertEqual(row.actual_departure_minute, 626)

    def test_observed_repeated_track_uses_remaining_schedule_sequence(self):
        collector = STSLiveCollector()
        full = ('<zugfahrplan zid="7"><gleis name="A" plan="A" an="10:00" />'
                '<gleis name="B" plan="B" an="10:10" /><gleis name="C" plan="C" an="10:20" />'
                '<gleis name="B" plan="B" an="10:30" /><gleis name="D" plan="D" an="10:40" />'
                '</zugfahrplan>')
        collector.process(xml(full))
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 11, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="B" amgleis="true" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="C" amgleis="false" />'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" an="10:30" />'
                              '<gleis name="D" plan="D" an="10:40" /></zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 31, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="B" amgleis="true" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="D" amgleis="false" />'))
        rows = collector.observed_train_times[7].rows
        self.assertEqual(set(rows), {1, 3})
        self.assertEqual(rows[1].actual_arrival_minute, 611)
        self.assertEqual(rows[3].actual_arrival_minute, 631)

    def test_observed_ambiguous_d_point_and_missing_plangleis_are_ignored(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" />'
                              '<gleis name="X" plan="X" flags="D" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.services[7].current_schedule = []
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 0, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="X" amgleis="true" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" amgleis="true" />'))
        self.assertEqual(collector.observed_train_times[7].rows, {})

    def test_not_at_track_departure_for_next_stop_is_ignored_before_matching(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="TMU 1" plan="TMU 1" />'
                              '<gleis name="TRR 1" plan="TRR 1" />'
                              '<gleis name="TEH 1" plan="TEH 1" /></zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 10, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="TMU 1" '
                              'plangleis="TMU 1" amgleis="true" />'))
        with self.assertLogs("sts_collector", level="DEBUG") as logs:
            collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="TRR 1" '
                                  'plangleis="TRR 1" amgleis="false" />'))
        state = collector.observed_train_times[7]
        self.assertEqual(state.rows[0].actual_departure_minute, 310)
        self.assertNotIn(1, state.rows)
        self.assertEqual(state.last_observed_original_index, 0)
        self.assertIn("reason=departure_confirmed_by_false", "\n".join(logs.output))

    def test_observed_event_minutes_use_half_up_rounding(self):
        cases = (
            ((6 * 60 + 5) * 60 + 29, 365),
            ((6 * 60 + 5) * 60 + 30, 366),
            ((6 * 60 + 5) * 60 + 59.2, 366),
            ((6 * 60 + 6) * 60 + 1, 366),
            ((6 * 60 + 6) * 60 + 30, 367),
        )
        for event_seconds, expected_minute in cases:
            with self.subTest(event_seconds=event_seconds):
                collector = STSLiveCollector()
                collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'))
                collector.process(
                    xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'),
                    event_simtime_seconds=event_seconds,
                )
                self.assertEqual(
                    collector.observed_train_times[7].rows[0].actual_arrival_minute,
                    expected_minute,
                )

    def test_observed_event_rounding_preserves_next_simulation_day(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(
            xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'),
            event_simtime_seconds=24 * 3600 - 0.8,
        )
        self.assertEqual(collector.observed_train_times[7].rows[0].actual_arrival_minute, 1440)

    def test_false_arrival_does_not_block_later_true_arrival(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="TGEH" plan="TGEH" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 5, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="TGEH" amgleis="false" />'))
        self.assertNotIn(7, collector.observed_train_times)
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 8, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="TGEH" amgleis="true" />'))
        self.assertEqual(collector.observed_train_times[7].rows[1].actual_arrival_minute, 308)

    def test_false_departure_does_not_block_later_real_stop_events(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="TBL 2" plan="TBL 2" /></zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 8, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="TBL 2" amgleis="false" />'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 9, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="TBL 2" amgleis="true" />'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 10, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="TBL 2" amgleis="true" />'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 11, 0)}" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="next" amgleis="false" />'))
        row = collector.observed_train_times[7].rows[0]
        self.assertEqual((row.actual_arrival_minute, row.actual_departure_minute), (309, 311))

    def test_departure_uses_true_row_and_first_false_time_only(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="TEH 1" plan="TEH 1" />'
                              '<gleis name="TALL 1" plan="TALL 1" /></zugfahrplan>'))
        true_time = (6 * 60 + 5) * 60 + 58
        collector.process(
            xml('<ereignis art="abfahrt" zid="7" gleis="TEH 1" '
                'plangleis="TEH 1" amgleis="true" />'),
            event_simtime_seconds=true_time,
        )
        self.assertEqual(collector.observed_train_times[7].rows, {})
        self.assertEqual(collector.pending_observed_departures[7].original_schedule_index, 0)
        false_time = (6 * 60 + 6) * 60 + 11
        collector.process(
            xml('<ereignis art="abfahrt" zid="7" gleis="TALL 1" '
                'plangleis="TALL 1" amgleis="false" />'),
            event_simtime_seconds=false_time,
        )
        rows = collector.observed_train_times[7].rows
        self.assertEqual(rows[0].actual_departure_minute, 366)
        self.assertNotIn(1, rows)
        self.assertNotIn(7, collector.pending_observed_departures)
        for seconds in (false_time + 1, false_time + 4):
            collector.process(
                xml('<ereignis art="abfahrt" zid="7" plangleis="TALL 1" amgleis="false" />'),
                event_simtime_seconds=seconds,
            )
        self.assertEqual(rows[0].actual_departure_minute, 366)

    def test_conflicting_true_departure_keeps_first_pending_without_advancing(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="A" amgleis="true" />'),
                          event_simtime_seconds=360)
        with self.assertLogs("sts_collector", level="DEBUG") as logs:
            collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="B" amgleis="true" />'),
                              event_simtime_seconds=361)
        pending = collector.pending_observed_departures[7]
        self.assertEqual((pending.original_schedule_index, pending.planned_name), (0, "A"))
        self.assertEqual(collector.observed_train_times[7].last_observed_original_index, 0)
        self.assertIn("reason=departure_true_conflict", "\n".join(logs.output))

    def test_departure_false_without_true_does_not_match_its_plangleis(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'))
        with self.assertLogs("sts_collector", level="DEBUG") as logs:
            collector.process(xml('<ereignis art="abfahrt" zid="7" plangleis="B" amgleis="false" />'),
                              event_simtime_seconds=360)
        self.assertNotIn(7, collector.observed_train_times)
        self.assertIn("reason=departure_false_without_pending", "\n".join(logs.output))

    def test_missing_amgleis_is_ignored_and_xml_boole_are_not_string_truthy(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(5, 0, 0)}" />'))
        with self.assertLogs("sts_collector", level="DEBUG") as logs:
            collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" />'))
            collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="false" />'))
        self.assertNotIn(7, collector.observed_train_times)
        self.assertIsNone(collector.services[7].raw_events[-2].at_track)
        self.assertIs(collector.services[7].raw_events[-1].at_track, False)
        diagnostic = "\n".join(logs.output)
        self.assertIn("reason=missing_amgleis", diagnostic)
        self.assertIn("reason=ignored_not_at_track", diagnostic)
        collector.process(xml('<ereignis art="ankunft" zid="7" plangleis="B" amgleis="true" />'))
        self.assertIs(collector.services[7].raw_events[-1].at_track, True)
        self.assertEqual(collector.observed_train_times[7].rows[0].actual_arrival_minute, 300)


if __name__ == "__main__":
    unittest.main()
