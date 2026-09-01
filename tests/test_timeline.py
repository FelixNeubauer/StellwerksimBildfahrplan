import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "Schnittstellentest"))

from bildfahrplan.profile import OperatingPoint, RouteProfile
from bildfahrplan.timeline import (
    DISTANCE_AXIS, NOW_LINE_ANGLE, TIME_AXIS, build_trace, format_axis_time,
    is_renderable_service, parse_clock, schedule_to_points, unwrap_time,
)
from sts_collector import SchedulePoint


def point(name, arrival=None, departure=None):
    return SchedulePoint(
        raw_name=name, current_name=name, planned_name=name,
        planned_arrival=arrival, planned_departure=departure,
    )


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.profile = RouteProfile("Test", (
            OperatingPoint("a", "A", 0, ("A 1",)),
            OperatingPoint("b", "B", 10, ("B",)),
        ))

    def test_time_conversion_and_format(self):
        self.assertEqual(parse_clock("12:34"), 45240)
        self.assertEqual(parse_clock("12:34:56"), 45296)
        self.assertEqual(format_axis_time(86461, True), "00:01:01")
        with self.assertRaises(ValueError):
            parse_clock("24:00")

    def test_midnight_is_unwrapped_forward(self):
        points = schedule_to_points(
            [point("A 1", "23:59", "23:59"), point("B", "00:01", "00:01")],
            self.profile, parse_clock("23:58"),
        )
        self.assertEqual([item.time_seconds for item in points], [86340, 86460])
        self.assertEqual(unwrap_time(parse_clock("00:01"), parse_clock("23:59")), 86460)

    def test_halt_has_arrival_and_departure(self):
        points = schedule_to_points([point("A 1", "12:00", "12:05")], self.profile, parse_clock("12:00"))
        self.assertEqual([item.kind for item in points], ["arrival", "departure"])
        self.assertEqual([item.position for item in points], [0, 0])
        self.assertNotEqual(points[0].time_seconds, points[1].time_seconds)

    def test_classical_axis_orientation_contract(self):
        self.assertEqual((DISTANCE_AXIS, TIME_AXIS), ("x", "y"))
        self.assertEqual(NOW_LINE_ANGLE, 0)  # horizontale Linie y = Simulationszeit

    def test_passage_has_one_point(self):
        points = schedule_to_points([point("B", "12:05", "12:05")], self.profile, parse_clock("12:00"))
        self.assertEqual(len(points), 1)

    def test_missing_mapping_is_omitted_without_guessing(self):
        schedule = [point("5a", "12:00", "12:01")]
        self.assertEqual(schedule_to_points(schedule, self.profile, parse_clock("12:00")), ())
        self.assertEqual(schedule[0].raw_name, "5a")

    def test_service_filter_is_strict(self):
        self.assertTrue(is_renderable_service(SimpleNamespace(service_kind="train")))
        self.assertFalse(is_renderable_service(SimpleNamespace(service_kind="locomotive_movement")))
        self.assertFalse(is_renderable_service(SimpleNamespace(service_kind="wagon_set")))
        self.assertFalse(is_renderable_service(SimpleNamespace(service_kind="unknown")))

    def test_projection_uses_delay_and_original_schedule(self):
        service = SimpleNamespace(
            zid=7, name="RE 7", service_kind="train", current_delay=5,
            original_schedule=[point("A 1", "12:00", "12:02")],
            current_schedule=[point("B", "13:00", "13:00")],
        )
        trace = build_trace(service, self.profile, parse_clock("12:00"))
        self.assertIsNotNone(trace)
        self.assertEqual([item.raw_name for item in trace.planned], ["A 1", "A 1"])
        self.assertEqual(trace.projected[0].time_seconds - trace.planned[0].time_seconds, 300)
        self.assertIn("+5", trace.label)


if __name__ == "__main__":
    unittest.main()
