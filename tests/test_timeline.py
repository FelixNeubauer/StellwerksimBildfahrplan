import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "Schnittstellentest"))

from bildfahrplan.profile import OperatingPoint, RouteProfile
from bildfahrplan.timeline import (
    DISTANCE_AXIS, NOW_LINE_ANGLE, TIME_AXIS, BoundaryEndpoint, BoundaryRouteProjection,
    RouteInstanceProjection, RouteInstanceProjectionPoint, build_route_instance_train_segments,
    build_trace, extend_trace_to_boundaries, format_axis_time, is_renderable_service,
    parse_clock, schedule_to_points, unwrap_time,
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

    def test_exit_uses_adjacent_movement_slope_after_last_departure(self):
        profile = RouteProfile("Exit", (
            OperatingPoint("b", "B", 10, ("B",)),
            OperatingPoint("c", "C", 20, ("C",)),
        ))
        service = SimpleNamespace(
            zid=1, name="RE", service_kind="train", current_delay=2,
            origin="intern", destination="Exit", original_schedule=[
                point("B", departure="10:00"), point("C", "10:06", "10:08"),
            ],
        )
        trace = extend_trace_to_boundaries(
            service, build_trace(service, profile, parse_clock("10:00")),
            (BoundaryRouteProjection("route", (10, 20, 25), (
                BoundaryEndpoint("entry", 10, ("Entry",)),
                BoundaryEndpoint("exit", 25, ("Exit",)),
            )),),
        )
        self.assertEqual(trace.planned[-1].time_seconds, parse_clock("10:11"))
        self.assertEqual(trace.projected[-1].time_seconds, parse_clock("10:13"))
        self.assertEqual(trace.planned[-2].kind, "departure")
        self.assertEqual(trace.planned[-1].source, "extrapolated_from_adjacent_segment")
        self.assertEqual(trace.planned[-1].direction, "exit")

    def test_entry_reaches_arrival_and_preserves_halt(self):
        profile = RouteProfile("Entry", (
            OperatingPoint("a", "A", 5, ("A",)),
            OperatingPoint("b", "B", 15, ("B",)),
        ))
        service = SimpleNamespace(
            zid=2, name="RB", service_kind="train", current_delay=0,
            origin="Entry", destination="intern", original_schedule=[
                point("A", "10:03", "10:05"), point("B", "10:11"),
            ],
        )
        trace = extend_trace_to_boundaries(
            service, build_trace(service, profile, parse_clock("10:00")),
            (BoundaryRouteProjection("route", (0, 5, 15), (
                BoundaryEndpoint("entry", 0, ("Entry",)),
                BoundaryEndpoint("exit", 15, ("Exit",)),
            )),),
        )
        self.assertEqual(trace.planned[0].time_seconds, parse_clock("10:00"))
        self.assertEqual([item.kind for item in trace.planned[1:3]], ["arrival", "departure"])
        self.assertEqual([item.time_seconds for item in trace.planned[1:3]],
                         [parse_clock("10:03"), parse_clock("10:05")])

    def test_boundary_projection_is_independent_of_display_direction(self):
        service = SimpleNamespace(origin="Entry", destination="Exit")
        points = (
            # Fachliche Fahrtrichtung läuft hier von großem zu kleinem X.
            self._plot_point("10:03", 15, "A", "arrival"),
            self._plot_point("10:05", 15, "A", "departure"),
            self._plot_point("10:11", 5, "B", "arrival"),
            self._plot_point("10:12", 5, "B", "departure"),
        )
        from bildfahrplan.timeline import TrainTrace
        trace = extend_trace_to_boundaries(service, TrainTrace(3, "Zug", points, points), (
            BoundaryRouteProjection("reverse", (20, 15, 5, 0), (
                BoundaryEndpoint("entry", 20, ("Entry",)),
                BoundaryEndpoint("exit", 0, ("Exit",)),
            )),
        ))
        self.assertEqual((trace.planned[0].position, trace.planned[-1].position), (20, 0))
        self.assertEqual((trace.planned[0].time_seconds, trace.planned[-1].time_seconds),
                         (parse_clock("10:00"), parse_clock("10:15")))

    def test_one_inner_point_has_no_boundary_extrapolation(self):
        service = SimpleNamespace(origin="Entry", destination=None)
        from bildfahrplan.timeline import TrainTrace
        points = (self._plot_point("10:03", 5, "A", "arrival"),)
        trace = TrainTrace(4, "Zug", points, points)
        extended = extend_trace_to_boundaries(service, trace, (
            BoundaryRouteProjection("route", (0, 5), (
                BoundaryEndpoint("entry", 0, ("Entry",)),
                BoundaryEndpoint("exit", 5, ("Exit",)),
            )),
        ))
        self.assertEqual(extended, trace)

    def test_zero_distance_segment_uses_next_movement_segment(self):
        service = SimpleNamespace(origin="Entry", destination=None)
        from bildfahrplan.timeline import TrainTrace
        points = (
            self._plot_point("10:03", 5, "A", "arrival"),
            self._plot_point("10:04", 5, "A", "departure"),
            self._plot_point("10:05", 5, "B", "arrival"),
            self._plot_point("10:06", 5, "B", "departure"),
            self._plot_point("10:12", 15, "C", "arrival"),
        )
        trace = TrainTrace(5, "Zug", points, points)
        extended = extend_trace_to_boundaries(service, trace, (
            BoundaryRouteProjection("route", (0, 5, 15), (
                BoundaryEndpoint("entry", 0, ("Entry",)),
                BoundaryEndpoint("exit", 15, ("Exit",)),
            )),
        ))
        self.assertEqual(extended.planned[0].time_seconds, parse_clock("10:00"))

    def test_train_is_projected_per_instance_without_gap_connection(self):
        service = SimpleNamespace(
            zid=10, name="RE", service_kind="train", current_delay=0,
            origin=None, destination=None, original_schedule=[
                point("A", departure="10:00"), point("X", "10:05", "10:06"),
                point("B", "10:11"),
            ],
        )
        routes = (
            self._instance_route("first", "r1", (("A", 0.0), ("X", 0.45))),
            self._instance_route("second", "r2", (("X", 0.50), ("B", 1.0))),
        )
        segments = build_route_instance_train_segments(service, routes, parse_clock("10:00"))
        self.assertEqual([segment.instance_id for segment in segments], ["first", "second"])
        self.assertEqual([point.position for point in segments[0].planned], [0.0, 0.45, 0.45])
        self.assertEqual([point.position for point in segments[1].planned], [0.50, 0.50, 1.0])
        self.assertEqual(segments[0].planned[-2].time_seconds, segments[1].planned[0].time_seconds)
        for segment, (start, end) in zip(segments, ((0.0, 0.45), (0.50, 1.0))):
            for points in (segment.planned, segment.projected):
                self.assertGreaterEqual(min(point.position for point in points), start)
                self.assertLessEqual(max(point.position for point in points), end)
                self.assertTrue(all(point.instance_id == segment.instance_id for point in points))

    def test_shared_single_station_does_not_activate_unrelated_route(self):
        route_a = self._instance_route("a", "ra", (("P", 0), ("Y", 1), ("Q", 2)))
        route_b = self._instance_route("b", "rb", (("X", 3), ("Y", 4), ("Z", 5)))
        service_a = SimpleNamespace(
            zid=11, name="A", service_kind="train", current_delay=0,
            origin=None, destination=None,
            original_schedule=[point("P", departure="10:00"), point("Y", "10:05", "10:06"),
                               point("Q", "10:10")],
        )
        service_b = SimpleNamespace(
            zid=12, name="B", service_kind="train", current_delay=0,
            origin=None, destination=None,
            original_schedule=[point("X", departure="11:00"), point("Y", "11:05", "11:06"),
                               point("Z", "11:10")],
        )
        self.assertEqual(
            [item.instance_id for item in build_route_instance_train_segments(
                service_a, (route_a, route_b), parse_clock("10:00"))], ["a"])
        self.assertEqual(
            [item.instance_id for item in build_route_instance_train_segments(
                service_b, (route_a, route_b), parse_clock("11:00"))], ["b"])

    @staticmethod
    def _instance_route(instance_id, route_id, nodes):
        return RouteInstanceProjection(
            instance_id, route_id,
            tuple(RouteInstanceProjectionPoint(
                instance_id, route_id, name, x, name, (name,)) for name, x in nodes),
        )

    @staticmethod
    def _plot_point(clock, position, raw_name, kind):
        from bildfahrplan.timeline import PlotPoint
        return PlotPoint(parse_clock(clock), position, raw_name, kind)


if __name__ == "__main__":
    unittest.main()
