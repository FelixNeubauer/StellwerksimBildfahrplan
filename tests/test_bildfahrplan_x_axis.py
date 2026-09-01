import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bildfahrplan.x_axis import (
    ROUTE_GAP_FRACTION, bildfahrplan_configuration_signature, build_bildfahrplan_x_axis,
)
from infrastructure.editable_topology import (
    BildfahrplanRouteInstance, DefinedRoute, EditableTopologyGraph, TopologyNode,
)


def graph_with_route(route_id="r", kilometres=(0, 5, 15), left="A"):
    graph = EditableTopologyGraph()
    ids = [chr(ord("A") + index) for index in range(len(kilometres))]
    for node_id in ids:
        graph.nodes[node_id] = TopologyNode(node_id, node_id, "line", "test")
    graph.defined_routes[route_id] = DefinedRoute(route_id, route_id, ids, ids[0], ids[-1])
    graph.bildfahrplan_routes.append(BildfahrplanRouteInstance(
        "i", route_id, left, 0, dict(zip(ids, kilometres))))
    return graph


class BildfahrplanXAxisTests(unittest.TestCase):
    def test_one_route_uses_full_width_and_segment_differences(self):
        layout = build_bildfahrplan_x_axis(graph_with_route())
        self.assertEqual((layout.routes[0].start_x, layout.routes[0].end_x), (0, 1))
        self.assertEqual([node.x for node in layout.routes[0].nodes], [0, 1 / 3, 1])

    def test_descending_kilometrage_has_positive_geometry(self):
        route = build_bildfahrplan_x_axis(graph_with_route(kilometres=(15, 10, 0))).routes[0]
        self.assertEqual(route.route_length, 15)
        self.assertEqual([node.x for node in route.nodes], [0, 1 / 3, 1])

    def test_left_endpoint_reverses_display_order_without_changing_values(self):
        graph = graph_with_route(left="C")
        original = dict(graph.bildfahrplan_routes[0].kilometrage)
        route = build_bildfahrplan_x_axis(graph).routes[0]
        self.assertEqual([node.node_id for node in route.nodes], ["C", "B", "A"])
        self.assertEqual([node.x for node in route.nodes], [0, 2 / 3, 1])
        self.assertEqual(graph.bildfahrplan_routes[0].kilometrage, original)

    def test_multiple_routes_are_proportional_and_separated(self):
        graph = graph_with_route("long", (0, 20))
        graph.defined_routes["short"] = DefinedRoute("short", "short", ["A", "B"], "A", "B")
        graph.bildfahrplan_routes.append(BildfahrplanRouteInstance(
            "second", "short", "A", 1, {"A": 0, "B": 10}))
        layout = build_bildfahrplan_x_axis(graph)
        widths = [route.end_x - route.start_x for route in layout.routes]
        self.assertEqual(len(layout.gaps), 1)
        self.assertAlmostEqual(layout.gaps[0].end_x - layout.gaps[0].start_x, ROUTE_GAP_FRACTION)
        self.assertAlmostEqual(widths[0], 2 * widths[1])
        self.assertEqual((layout.routes[0].start_x, layout.routes[-1].end_x), (0, 1))

    def test_three_instances_keep_order_and_have_two_gaps(self):
        graph = graph_with_route(kilometres=(0, 5))
        graph.bildfahrplan_routes.extend([
            BildfahrplanRouteInstance("i2", "r", "A", 1, {"A": 0, "B": 5}),
            BildfahrplanRouteInstance("i3", "r", "B", 2, {"A": 4, "B": 1}),
        ])
        layout = build_bildfahrplan_x_axis(graph)
        self.assertEqual([route.instance_id for route in layout.routes], ["i", "i2", "i3"])
        self.assertEqual(len(layout.gaps), 2)
        self.assertEqual(layout.routes[-1].end_x, 1)

    def test_duplicate_instances_use_independent_kilometrage_and_direction(self):
        graph = graph_with_route()
        graph.bildfahrplan_routes.append(BildfahrplanRouteInstance(
            "reverse", "r", "C", 1, {"A": 0, "B": 10, "C": 12}))
        layout = build_bildfahrplan_x_axis(graph)
        self.assertEqual([route.route_length for route in layout.routes], [15, 12])
        self.assertEqual(layout.routes[1].nodes[0].node_id, "C")
        self.assertTrue(all(node.instance_id == "i" and node.route_id == "r"
                            for node in layout.routes[0].nodes))
        self.assertTrue(all(node.instance_id == "reverse" and node.route_id == "r"
                            for node in layout.routes[1].nodes))

    def test_zero_length_is_deterministic_and_diagnostic(self):
        route = build_bildfahrplan_x_axis(graph_with_route(kilometres=(10, 10, 10))).routes[0]
        self.assertEqual(route.route_length, 0)
        self.assertEqual([node.x for node in route.nodes], [0, 0.5, 1])
        self.assertTrue(route.diagnostics)

    def test_equal_neighbours_keep_same_position_and_do_not_mutate(self):
        graph = graph_with_route(kilometres=(0, 0, 10))
        route = build_bildfahrplan_x_axis(graph).routes[0]
        self.assertEqual([node.x for node in route.nodes], [0, 0, 1])
        self.assertEqual(graph.bildfahrplan_routes[0].kilometrage["B"], 0)

    def test_missing_instance_values_fall_back_to_route_default(self):
        graph = graph_with_route()
        graph.defined_routes["r"].default_kilometrage = {"A": 3, "B": 4, "C": 9}
        graph.bildfahrplan_routes[0].kilometrage = {}
        route = build_bildfahrplan_x_axis(graph).routes[0]
        self.assertEqual(route.route_length, 6)
        self.assertFalse(route.diagnostics)

    def test_list_reorder_is_immediately_authoritative_and_changes_signature(self):
        graph = graph_with_route()
        graph.bildfahrplan_routes.append(BildfahrplanRouteInstance(
            "second", "r", "C", 1, {"A": 0, "B": 5, "C": 15}))
        before = bildfahrplan_configuration_signature(graph)
        graph.bildfahrplan_routes.reverse()
        # Simuliert den Moment des rowsMoved-Signals, bevor order synchronisiert ist.
        layout = build_bildfahrplan_x_axis(graph)
        after = bildfahrplan_configuration_signature(graph)
        self.assertEqual([item.instance_id for item in layout.routes], ["second", "i"])
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
