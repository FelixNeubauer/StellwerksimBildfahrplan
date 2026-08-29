import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bildfahrplan.decorations import build_route_plot_segments, build_station_label_placements
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

    def test_station_labels_are_anchored_above_box_without_moving_x(self):
        layout = build_bildfahrplan_x_axis(two_routes())
        labels = build_station_label_placements(layout, 100)
        nodes = [node for route in layout.routes for node in route.nodes]
        self.assertNotIn("Strecke (relative Position)", {item.text for item in labels})
        self.assertEqual([item.x for item in labels], [node.x for node in nodes])
        self.assertTrue(all(item.border_y == 100 and item.anchor_y == 1 for item in labels))
        self.assertTrue(all(item.gap_pixels > 0 for item in labels))
        self.assertEqual(labels[0].anchor_x, 0)
        self.assertEqual(labels[len(layout.routes[0].nodes) - 1].anchor_x, 1)


if __name__ == "__main__":
    unittest.main()
