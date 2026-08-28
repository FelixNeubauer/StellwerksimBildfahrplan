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

    def test_supplement_nodes_are_unconnected_parked_and_not_repositioned(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "entry"))
        graph.nodes["A"].layout_x = 100; graph.nodes["B"].layout_x = 270
        manual = graph.ensure_supplement_node("manual:X", "ManualX", "junction", "operating_point_config")
        entry = graph.ensure_supplement_node("entry:Y", "Y", "entry", "entry_point_config")
        self.assertFalse(graph.neighbours(manual.id)); self.assertFalse(graph.neighbours(entry.id))
        self.assertNotEqual((manual.layout_x, manual.layout_y), (entry.layout_x, entry.layout_y))
        position = (manual.layout_x, manual.layout_y)
        graph.ensure_supplement_node("manual:X", "Umbenannt", "junction", "operating_point_config")
        self.assertEqual((manual.layout_x, manual.layout_y), position)

    def test_supplements_are_parked_below_main_component_in_rows(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "entry"))
        graph.nodes["A"].layout_x = 0; graph.nodes["B"].layout_x = 500
        graph.nodes["A"].layout_y = graph.nodes["B"].layout_y = 20
        graph.add_edge("A", "B")
        parked = [graph.ensure_supplement_node(f"entry:{index}", str(index), "entry",
                                               "entry_point_config") for index in range(8)]
        self.assertTrue(all(node.layout_y > 20 for node in parked))
        self.assertGreater(len({node.layout_x for node in parked}), 1)
        self.assertGreater(len({node.layout_y for node in parked}), 1)

    def test_connected_entry_prevents_and_cleans_automatic_supplement_duplicate(self):
        graph = graph_with_nodes(("automatic-aalen", "entry"), ("X", "line"))
        graph.nodes["automatic-aalen"].display_name = "Aalen"
        graph.nodes["automatic-aalen"].source = "automatic"
        graph.add_edge("automatic-aalen", "X")
        self.assertEqual(graph.represented_node("Aalen").id, "automatic-aalen")
        duplicate = graph.ensure_supplement_node("entry:aalen", "Aalen", "entry",
                                                 "entry_point_config")
        self.assertIn(duplicate.id, graph.nodes)
        self.assertEqual(graph.remove_redundant_entry_supplements(), 1)
        self.assertNotIn(duplicate.id, graph.nodes)

    def test_manual_equal_name_is_not_removed_as_entry_duplicate(self):
        graph = graph_with_nodes(("manual-aalen", "entry"))
        graph.nodes["manual-aalen"].display_name = "Aalen"
        supplement = graph.ensure_supplement_node("entry:aalen", "Aalen", "entry",
                                                  "entry_point_config")
        self.assertEqual(graph.remove_redundant_entry_supplements(), 0)
        self.assertIn(supplement.id, graph.nodes)

    def test_simple_path_enumeration_is_complete_sorted_and_bounded(self):
        graph = graph_with_nodes(("A", "entry"), ("X", "junction"), ("B", "line"),
                                 ("C", "line"), ("E", "line"), ("F", "line"), ("D", "entry"))
        for edge in (("A", "X"), ("X", "B"), ("B", "C"), ("C", "D"),
                     ("X", "E"), ("E", "F"), ("F", "D")):
            graph.add_edge(*edge)
        result = graph.enumerate_simple_paths("A", "D", limit=50)
        self.assertEqual(result.paths, (("A", "X", "B", "C", "D"),
                                        ("A", "X", "E", "F", "D")))
        limited = graph.enumerate_simple_paths("A", "D", limit=1)
        self.assertEqual(len(limited.paths), 1); self.assertTrue(limited.truncated)

    def test_fresh_import_restores_graph_after_editable_copy_was_emptied(self):
        source = OperationalRouteGraph(
            nodes={name: OperationalRouteNode(name, name, (), (name,), "inferred")
                   for name in ("A", "B", "C")},
            edges=[OperationalRouteEdge(a, b, 1, {}, "inferred", (a, b))
                   for a, b in (("A", "B"), ("B", "C"))])
        editable = EditableTopologyGraph.from_operational_graph(source)
        editable.nodes.clear(); editable.edges.clear()
        regenerated = EditableTopologyGraph.from_operational_graph(source)
        self.assertEqual(set(regenerated.nodes), {"A", "B", "C"})
        self.assertEqual({frozenset((edge.node_a, edge.node_b)) for edge in regenerated.edges.values()},
                         {frozenset(("A", "B")), frozenset(("B", "C"))})
        with tempfile.TemporaryDirectory() as directory:
            store = EditableTopologyGraphStore(directory)
            store.save(7, "Test", editable)
            store.save(7, "Test", regenerated)
            reloaded = EditableTopologyGraph.from_dict(store.load_path(store.path_for(7)))
            self.assertEqual(set(reloaded.nodes), {"A", "B", "C"})


if __name__ == "__main__":
    unittest.main()
