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

    def test_actual_events_match_repeated_stops_in_remaining_sequence(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(9, 0, 0)}" />'))
        rows = ''.join(f'<gleis name="{name}" plan="{name}" an="10:00" ab="10:01" />'
                       for name in ("A", "B", "C", "B", "D"))
        collector.process(xml(f'<zugfahrplan zid="7">{rows}</zugfahrplan>'))
        first_remaining = ''.join(f'<gleis name="{name}" plan="{name}" an="10:00" ab="10:01" />'
                                  for name in ("B", "C", "B", "D"))
        collector.process(xml(f'<zugfahrplan zid="7">{first_remaining}</zugfahrplan>'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="B" />'))
        # Wiederholungen konsumieren nicht versehentlich den zweiten Besuch.
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="B" />'))
        second_remaining = ''.join(f'<gleis name="{name}" plan="{name}" an="10:00" ab="10:01" />'
                                   for name in ("B", "D"))
        collector.process(xml(f'<zugfahrplan zid="7">{second_remaining}</zugfahrplan>'))
        collector.process(xml(f'<simzeit zeit="{self.simtime(9, 30, 0)}" />'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'))
        timing = collector.services[7].actual_timing.rows
        self.assertEqual(sorted(timing), [1, 3])
        self.assertEqual(timing[1].actual_departure_minute, 9 * 60)
        self.assertEqual(timing[3].actual_arrival_minute, 9 * 60 + 30)

    def test_passage_events_do_not_create_actual_stop_times(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(10, 0, 0)}" />'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="X" plan="X" '
                              'an="10:00" ab="10:00" flags="D" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="X" />'))
        self.assertEqual(collector.services[7].actual_timing.rows, {})

    def test_departure_matches_event_track_before_already_shrunken_remaining_schedule(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(13, 30, 0)}" />'))
        collector.process(xml(
            '<zugdetails zid="7" name="RS 26369" gleis="TMU 1" />'))
        collector.process(xml(
            '<zugfahrplan zid="7">'
            '<gleis name="TMU 1" plan="TMU 1" an="13:25" ab="13:30" />'
            '<gleis name="TRR 1" plan="TRR 1" an="13:34" ab="13:34" />'
            '<gleis name="TEH 3" plan="TEH 3" an="13:40" ab="13:43" />'
            '</zugfahrplan>'))
        collector.process(xml(
            '<zugfahrplan zid="7">'
            '<gleis name="TRR 1" plan="TRR 1" an="13:34" ab="13:34" />'
            '<gleis name="TEH 3" plan="TEH 3" an="13:40" ab="13:43" />'
            '</zugfahrplan>'))

        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="TMU 1" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60)
        collector.advance_pending_departures(collector.pending_departures[7].deadline_monotonic)

        rows = collector.services[7].actual_timing.rows
        self.assertEqual(rows[0].actual_departure_minute, 13 * 60 + 30)
        self.assertNotIn(1, rows)
        diagnostic = collector.actual_timing_diagnostics[-1]
        self.assertEqual((diagnostic.selected_original_index,
                          diagnostic.selected_schedule_name), (0, "TMU 1"))
        self.assertEqual(diagnostic.match_reason, "pending_confirmed")

    def test_departure_prefers_last_arrival_despite_schedule_shrink(self):
        collector = STSLiveCollector()
        collector.process(xml(f'<simzeit zeit="{self.simtime(13, 20, 0)}" />'))
        collector.process(xml('<zugfahrplan zid="7">'
                              '<gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" />'
                              '<gleis name="C" plan="C" />'
                              '</zugfahrplan>'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" />'
                              '<gleis name="C" plan="C" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'),
                          event_simtime_seconds=13 * 3600 + 20 * 60)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="C" plan="C" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="B" />'),
                          event_simtime_seconds=13 * 3600 + 25 * 60)
        collector.advance_pending_departures(collector.pending_departures[7].deadline_monotonic)
        rows = collector.services[7].actual_timing.rows
        self.assertEqual((rows[1].actual_arrival_minute, rows[1].actual_departure_minute),
                         (13 * 60 + 20, 13 * 60 + 25))
        self.assertNotIn(2, rows)

    def test_actual_event_minutes_use_deterministic_half_up_rounding(self):
        for seconds in (13 * 3600 + 29 * 60 + 59.2, 13 * 3600 + 30 * 60 + 1):
            collector = STSLiveCollector()
            collector.process(xml('<zugfahrplan zid="7">'
                                  '<gleis name="A" plan="A" /></zugfahrplan>'))
            collector.process(xml('<ereignis art="ankunft" zid="7" gleis="A" />'),
                              event_simtime_seconds=seconds)
            self.assertEqual(collector.services[7].actual_timing.rows[0].actual_arrival_minute,
                             13 * 60 + 30)

    def test_arrival_uses_matching_stop_immediately_before_remaining_boundary(self):
        collector = STSLiveCollector()
        collector.process(xml('<zugfahrplan zid="7">'
                              '<gleis name="A" plan="A" /><gleis name="B" plan="B" />'
                              '<gleis name="C" plan="C" /><gleis name="B" plan="B" />'
                              '<gleis name="E" plan="E" /></zugfahrplan>'))
        collector.process(xml('<zugfahrplan zid="7"><gleis name="C" plan="C" />'
                              '<gleis name="B" plan="B" /><gleis name="E" plan="E" />'
                              '</zugfahrplan>'))
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'),
                          event_simtime_seconds=13 * 3600 + 20 * 60)
        self.assertIn(1, collector.services[7].actual_timing.rows)
        self.assertNotIn(3, collector.services[7].actual_timing.rows)

    def test_departure_is_pending_for_ten_realtime_seconds(self):
        now = [100.0]
        collector = STSLiveCollector(monotonic=lambda: now[0])
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" ab="13:30" />'
                              '<gleis name="B" plan="B" an="13:40" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60)
        self.assertNotIn(0, collector.services[7].actual_timing.rows)
        deadline = collector.pending_departures[7].deadline_monotonic
        self.assertEqual(deadline, 110.0)
        self.assertEqual(collector.advance_pending_departures(109.9), ())
        self.assertNotIn(0, collector.services[7].actual_timing.rows)
        self.assertEqual(collector.advance_pending_departures(110.0), (7,))
        self.assertEqual(collector.services[7].actual_timing.rows[0].actual_departure_minute,
                         13 * 60 + 30)

    def test_rothalt_cancels_only_same_train_pending_departure(self):
        now = [0.0]
        collector = STSLiveCollector(monotonic=lambda: now[0])
        for zid in (7, 8):
            collector.process(xml(f'<zugfahrplan zid="{zid}"><gleis name="A" plan="A" />'
                                  '<gleis name="B" plan="B" /></zugfahrplan>'))
            collector.process(xml(f'<ereignis art="abfahrt" zid="{zid}" gleis="A" />'),
                              event_simtime_seconds=13 * 3600 + 30 * 60)
        now[0] = 5.0
        collector.process(xml('<ereignis art="rothalt" zid="7" gleis="Signal" />'))
        self.assertNotIn(7, collector.pending_departures)
        self.assertIn(8, collector.pending_departures)
        collector.advance_pending_departures(10.0)
        self.assertNotIn(0, collector.services[7].actual_timing.rows)
        self.assertEqual(collector.services[8].actual_timing.rows[0].actual_departure_minute,
                         13 * 60 + 30)

    def test_late_rothalt_does_not_remove_confirmed_departure(self):
        collector = STSLiveCollector(monotonic=lambda: 0.0)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60,
                          received_monotonic=0.0)
        collector.advance_pending_departures(10.0)
        collector.process(xml('<ereignis art="rothalt" zid="7" />'),
                          received_monotonic=12.0)
        self.assertEqual(collector.services[7].actual_timing.rows[0].actual_departure_minute,
                         13 * 60 + 30)

    def test_duplicate_departure_keeps_original_pending_deadline_and_time(self):
        collector = STSLiveCollector(monotonic=lambda: 0.0)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60,
                          received_monotonic=0.0)
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 31 * 60,
                          received_monotonic=2.0)
        pending = collector.pending_departures[7]
        self.assertEqual((pending.deadline_monotonic, pending.actual_minute),
                         (10.0, 13 * 60 + 30))
        self.assertEqual(collector.actual_timing_diagnostics[-1].match_reason,
                         "pending_duplicate_ignored")

    def test_wurdegruen_does_not_create_departure_after_rothalt(self):
        collector = STSLiveCollector(monotonic=lambda: 0.0)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60,
                          received_monotonic=0.0)
        collector.process(xml('<ereignis art="rothalt" zid="7" />'), received_monotonic=5.0)
        collector.process(xml('<ereignis art="wurdegruen" zid="7" />'), received_monotonic=6.0)
        collector.advance_pending_departures(20.0)
        self.assertEqual(collector.services[7].actual_timing.rows, {})

    def test_next_arrival_confirms_pending_departure_with_original_event_time(self):
        collector = STSLiveCollector(monotonic=lambda: 0.0)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="A" plan="A" />'
                              '<gleis name="B" plan="B" /></zugfahrplan>'))
        collector.process(xml('<ereignis art="abfahrt" zid="7" gleis="A" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60,
                          received_monotonic=0.0)
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'),
                          event_simtime_seconds=13 * 3600 + 30 * 60 + 5,
                          received_monotonic=5.0)
        timing = collector.services[7].actual_timing.rows
        self.assertEqual(timing[0].actual_departure_minute, 13 * 60 + 30)
        self.assertEqual(timing[1].actual_arrival_minute, 13 * 60 + 30)
        self.assertNotIn(7, collector.pending_departures)
        self.assertEqual(collector.actual_timing_diagnostics[-1].match_reason,
                         "pending_confirmed_by_next_arrival")

    def test_ambiguous_repeated_stop_is_rejected_without_sequence_evidence(self):
        collector = STSLiveCollector(monotonic=lambda: 0.0)
        collector.process(xml('<zugfahrplan zid="7"><gleis name="B" plan="B" />'
                              '<gleis name="C" plan="C" /><gleis name="B" plan="B" />'
                              '</zugfahrplan>'))
        # Leerer Restfahrplan liefert bewusst keine Grenze zur Disambiguierung.
        collector.services[7].current_schedule = []
        collector.process(xml('<ereignis art="ankunft" zid="7" gleis="B" />'),
                          event_simtime_seconds=13 * 3600, received_monotonic=0.0)
        self.assertEqual(collector.services[7].actual_timing.rows, {})
        diagnostic = collector.actual_timing_diagnostics[-1]
        self.assertEqual(diagnostic.match_reason, "ambiguous")
        self.assertEqual(diagnostic.candidate_original_indices, (0, 2))


if __name__ == "__main__":
    unittest.main()
