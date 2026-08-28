import json
import tempfile
import unittest
from pathlib import Path

from infrastructure import (
    EditableTopologyGraph, EditableTopologyGraphStore, OperationalRouteEdge,
    OperationalRouteGraph, OperationalRouteNode,
)


def graph_with_nodes(*values):
    graph = EditableTopologyGraph()
    for node_id, node_type in values:
        graph.nodes[node_id] = __import__("infrastructure").TopologyNode(
            node_id, node_id, node_type, "manual")
    return graph


class EditableTopologyTests(unittest.TestCase):
    def test_node_degree_validation_does_not_block_intermediate_states(self):
        graph = graph_with_nodes(("A", "entry"), ("X", "junction"), ("B", "line"))
        graph.add_edge("A", "X")
        self.assertIsNone(graph.node_validation("A"))
        self.assertIsNone(graph.node_validation("X"))
        self.assertIn("genau 2", graph.node_validation("B"))
        graph.add_edge("X", "B")
        self.assertEqual(graph.degree("X"), 2)
        self.assertIsNone(graph.node_validation("X"), "Junction mit Grad 2 muss gültig sein")

    def test_junction_may_be_inside_overlapping_manual_routes(self):
        graph = graph_with_nodes(("A", "entry"), ("X", "junction"),
                                 ("B", "entry"), ("C", "entry"))
        for pair in (("A", "X"), ("X", "B"), ("X", "C")): graph.add_edge(*pair)
        route_one = graph.add_route("A – B", ["A", "X", "B"])
        route_two = graph.add_route("X – C", ["X", "C"])
        self.assertFalse(graph.route_validation(route_one))
        self.assertFalse(graph.route_validation(route_two))
        self.assertNotEqual(route_one.route_id, route_two.route_id)

    def test_route_rejects_jump_and_line_endpoint(self):
        graph = graph_with_nodes(("A", "entry"), ("X", "line"), ("B", "entry"))
        graph.add_edge("A", "X"); graph.add_edge("X", "B")
        with self.assertRaisesRegex(ValueError, "Keine Verbindung"):
            graph.add_route("Sprung", ["A", "B"])
        with self.assertRaisesRegex(ValueError, "Endpunkt"):
            graph.add_route("Linienende", ["X", "B"])

    def test_disconnected_components_and_duplicate_instances(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "entry"),
                                 ("D", "entry"), ("E", "entry"))
        graph.add_edge("A", "B"); graph.add_edge("D", "E")
        self.assertEqual({frozenset(item) for item in graph.connected_components()},
                         {frozenset(("A", "B")), frozenset(("D", "E"))})
        first = graph.add_route("A – B", ["A", "B"]); graph.add_route("D – E", ["D", "E"])
        one = graph.add_bildfahrplan_instance(first.route_id)
        two = graph.add_bildfahrplan_instance(first.route_id)
        self.assertEqual(one.route_id, two.route_id)
        self.assertNotEqual(one.instance_id, two.instance_id)

    def test_undirected_duplicate_edge_is_not_created(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "entry"))
        first = graph.add_edge("A", "B"); second = graph.add_edge("B", "A")
        self.assertIs(first, second); self.assertEqual(len(graph.edges), 1)

    def test_operational_import_proposes_types_and_layout(self):
        source = OperationalRouteGraph(
            nodes={name: OperationalRouteNode(name, name, (), (name,), "inferred")
                   for name in ("A", "X", "B", "C")},
            edges=[OperationalRouteEdge(a, b, 1, {}, "inferred", (a, b))
                   for a, b in (("A", "X"), ("X", "B"), ("X", "C"))],
        )
        graph = EditableTopologyGraph.from_operational_graph(source)
        self.assertEqual(graph.nodes["A"].node_type, "entry")
        self.assertEqual(graph.nodes["X"].node_type, "junction")
        self.assertTrue(all(node.source == "automatic" for node in graph.nodes.values()))

    def test_persistence_round_trip_includes_identity_layout_routes_and_order(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "entry"))
        graph.nodes["A"].layout_x = 42.5; graph.add_edge("A", "B")
        route = graph.add_route("A – B", ["A", "B"])
        graph.add_bildfahrplan_instance(route.route_id); graph.add_bildfahrplan_instance(route.route_id)
        with tempfile.TemporaryDirectory() as directory:
            store = EditableTopologyGraphStore(directory); path = store.save(17, "Test", graph)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((payload["aid"], payload["stellwerk_name"], payload["artifact_type"]),
                             (17, "Test", "editable_topology_graph"))
            restored = EditableTopologyGraph.from_dict(payload)
            self.assertEqual(restored.nodes["A"].layout_x, 42.5)
            self.assertEqual(len(restored.bildfahrplan_routes), 2)
            self.assertEqual([item.order for item in restored.bildfahrplan_routes], [0, 1])


if __name__ == "__main__":
    unittest.main()
