import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bildfahrplan.decorations import build_route_plot_segments, build_station_header_layout
from bildfahrplan.x_axis import build_bildfahrplan_x_axis
from infrastructure.editable_topology import (
    BildfahrplanRouteInstance, DefinedRoute, EditableTopologyGraph, TopologyNode,
)


def two_routes():
    graph = EditableTopologyGraph()
    for node_id in ("entry-a", "A", "exit-a", "entry-b", "B", "exit-b"):
        graph.nodes[node_id] = TopologyNode(node_id, node_id, "entry" if "entry" in node_id or "exit" in node_id else "line", "test")
    graph.defined_routes["route-a"] = DefinedRoute(
        "route-a", "A", ["entry-a", "A", "exit-a"], "entry-a", "exit-a")
    graph.defined_routes["route-b"] = DefinedRoute(
        "route-b", "B", ["entry-b", "B", "exit-b"], "entry-b", "exit-b")
    graph.bildfahrplan_routes = [
        BildfahrplanRouteInstance("instance-a", "route-a", "entry-a", 0,
                                  {"entry-a": 0, "A": 5, "exit-a": 10}),
        BildfahrplanRouteInstance("instance-b", "route-b", "entry-b", 1,
                                  {"entry-b": 0, "B": 10, "exit-b": 20}),
    ]
    return graph


class DecorationTests(unittest.TestCase):
    def test_boxes_endpoints_and_gap_are_authoritative(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        self.assertEqual(len(layout.routes), 2)
        self.assertEqual(layout.routes[0].nodes[0].x, layout.routes[0].start_x)
        self.assertEqual(layout.routes[0].nodes[-1].x, layout.routes[0].end_x)
        self.assertEqual(layout.routes[1].nodes[0].x, layout.routes[1].start_x)
        self.assertEqual(layout.routes[1].nodes[-1].x, layout.routes[1].end_x)
        self.assertLess(layout.routes[0].end_x, layout.gaps[0].end_x)
        self.assertEqual(layout.gaps[0].start_x, layout.routes[0].end_x)
        self.assertEqual(layout.gaps[0].end_x, layout.routes[1].start_x)

    def test_grid_segments_never_cross_a_gap(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        segments = build_route_plot_segments(layout, 100, 200, (125, 175))
        self.assertEqual(len([item for item in segments if item.kind == "frame"]), 8)
        for segment in segments:
            route = next(item for item in layout.routes if item.instance_id == segment.instance_id)
            self.assertGreaterEqual(min(segment.x1, segment.x2), route.start_x)
            self.assertLessEqual(max(segment.x1, segment.x2), route.end_x)

    @staticmethod
    def _label_layout(layout, width, size=lambda text: (len(text) * 10, 14)):
        return build_station_header_layout(layout, lambda x: x * width, size)

    def test_enough_pixel_space_uses_horizontal_labels(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        header = self._label_layout(layout, 1200)
        self.assertTrue(all(route.orientation == "horizontal" for route in header.routes))

    def test_collisions_use_vertical_labels(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        header = self._label_layout(layout, 120)
        self.assertTrue(all(route.orientation == "vertical" for route in header.routes))
        self.assertTrue(all(label.rotation == -90 for route in header.routes for label in route.labels))

    def test_resize_changes_orientation_both_ways_without_moving_x(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        wide = self._label_layout(layout, 1200)
        narrow = self._label_layout(layout, 120)
        wide_again = self._label_layout(layout, 1200)
        self.assertEqual(wide.routes[0].orientation, "horizontal")
        self.assertEqual(narrow.routes[0].orientation, "vertical")
        self.assertEqual(wide_again.routes[0].orientation, "horizontal")
        nodes = [node for route in layout.routes for node in route.nodes]
        for header in (wide, narrow, wide_again):
            labels = [label for route in header.routes for label in route.labels]
            self.assertEqual([item.x for item in labels], [node.x for node in nodes])

    def test_mixed_routes_use_largest_required_header_height(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        def size(text):
            return (10 if text in {"entry-a", "A", "exit-a"} else 400, 14)
        header = self._label_layout(layout, 1000, size)
        self.assertEqual([route.orientation for route in header.routes], ["horizontal", "vertical"])
        self.assertEqual(header.global_header_height,
                         max(route.required_header_height for route in header.routes))
        self.assertGreater(header.routes[1].required_header_height,
                           header.routes[0].required_header_height)

    def test_long_edge_labels_are_anchored_inward(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        header = self._label_layout(layout, 1200, lambda _text: (120, 14))
        first_route = header.routes[0]
        self.assertEqual(first_route.labels[0].anchor_x, 0)
        self.assertEqual(first_route.labels[-1].anchor_x, 1)


if __name__ == "__main__":
    unittest.main()
