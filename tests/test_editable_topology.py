import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from infrastructure import (
    EditableTopologyGraph, EditableTopologyGraphStore, OperationalRouteEdge,
    OperationalRouteGraph, OperationalRouteNode, TopologySupplementCandidate,
    TopologyTarget, TopologyTargetRegistry,
    AssignableRawItem, EditableOperatingPoint, EntryPoint, OperatingPointAssignments,
    KM_PER_MINUTE, estimate_kilometrage, validate_kilometrage,
)


def graph_with_nodes(*values):
    graph = EditableTopologyGraph()
    for node_id, node_type in values:
        graph.nodes[node_id] = __import__("infrastructure").TopologyNode(
            node_id, node_id, node_type, "manual")
    return graph


class EditableTopologyTests(unittest.TestCase):
    @staticmethod
    def _timed_service(*points):
        schedule = [SimpleNamespace(planned_name=name, raw_name=name,
                                    planned_arrival=arrival, planned_departure=departure)
                    for name, arrival, departure in points]
        return SimpleNamespace(service_kind="train", original_schedule=schedule)

    def test_kilometrage_estimate_uses_median_planned_travel_time(self):
        services = [
            self._timed_service(("A", "10:00", "10:00"), ("B", "10:06", "10:06")),
            self._timed_service(("A", "11:00", "11:00"), ("B", "11:04", "11:04")),
            self._timed_service(("A", "12:00", "12:00"), ("B", "12:05", "12:05")),
        ]
        estimate = estimate_kilometrage(["A", "B"], {}, services, {"A": "A", "B": "B"})
        self.assertEqual(estimate.segment_minutes, (5.0,))
        self.assertAlmostEqual(estimate.kilometres[1], 5 * KM_PER_MINUTE)

    def test_kilometrage_interpolates_one_unknown_point(self):
        service = self._timed_service(("A", "10:00", "10:00"), ("C", "10:08", "10:08"))
        estimate = estimate_kilometrage(["A", "B", "C"], {}, [service], {"A": "A", "C": "C"})
        self.assertEqual(estimate.segment_minutes, (4.0, 4.0))

    def test_kilometrage_interpolates_multiple_unknown_points(self):
        service = self._timed_service(("A", "10:00", "10:00"), ("D", "10:09", "10:09"))
        estimate = estimate_kilometrage(["A", "B", "C", "D"], {}, [service], {"A": "A", "D": "D"})
        self.assertEqual(estimate.segment_minutes, (3.0, 3.0, 3.0))

    def test_kilometrage_uses_three_minutes_for_unobserved_edge_entry(self):
        estimate = estimate_kilometrage(["entry", "B"], {"entry": "entry"}, [], {})
        self.assertEqual(estimate.segment_minutes, (3.0,))
        self.assertEqual(estimate.kilometres, (0.0, 5.0))

    def test_kilometrage_validation_accepts_both_directions_and_ties(self):
        self.assertTrue(validate_kilometrage([0, 2.4, 6.1], 3).valid)
        self.assertTrue(validate_kilometrage([42, 39.6, 35.9], 3).valid)
        self.assertTrue(validate_kilometrage([10, 10, 12.5], 3).valid)

    def test_kilometrage_validation_rejects_direction_change_and_incomplete_values(self):
        self.assertFalse(validate_kilometrage([0, 3, 2.5, 4], 4).valid)
        self.assertFalse(validate_kilometrage([0, "", 4], 3).valid)
        self.assertFalse(validate_kilometrage([0, "x", 4], 3).valid)

    def test_route_default_is_stored_and_copied_independently_to_instances(self):
        graph = graph_with_nodes(("A", "entry"), ("B", "line"), ("C", "entry"))
        graph.add_edge("A", "B"); graph.add_edge("B", "C")
        estimated = {"A": 0.0, "B": 4.0, "C": 9.0}
        route = graph.add_route("A – C", ["A", "B", "C"], default_kilometrage=estimated)
        first = graph.add_bildfahrplan_instance(route.route_id)
        second = graph.add_bildfahrplan_instance(route.route_id)
        self.assertEqual(route.default_kilometrage, estimated)
        self.assertEqual(first.kilometrage, estimated)
        self.assertIsNot(first.kilometrage, route.default_kilometrage)
        self.assertIsNot(first.kilometrage, second.kilometrage)
        first.kilometrage["B"] = 5.0
        self.assertEqual(second.kilometrage["B"], 4.0)
        self.assertEqual(route.default_kilometrage["B"], 4.0)
        first.left_endpoint = route.endpoint_b
        self.assertEqual([first.kilometrage[node] for node in reversed(route.ordered_node_ids)],
                         [9.0, 5.0, 0.0])

    def test_legacy_route_and_instance_load_with_empty_kilometrage(self):
        payload = {
            "graph": {"nodes": [], "edges": []},
            "defined_routes": [{"route_id": "route", "display_name": "Legacy",
                                "ordered_node_ids": ["A", "B"], "endpoint_a": "A", "endpoint_b": "B"}],
            "bildfahrplan_routes": [{"instance_id": "bf", "route_id": "route",
                                      "left_endpoint": "A", "order": 0}],
        }
        restored = EditableTopologyGraph.from_dict(payload)
        self.assertEqual(restored.defined_routes["route"].default_kilometrage, {})
        self.assertEqual(restored.bildfahrplan_routes[0].kilometrage, {})
        self.assertFalse(restored.bildfahrplan_routes[0].kilometrage_stale)
        changed = restored.initialize_missing_kilometrages(
            lambda route: dict(zip(route.ordered_node_ids, (0.0, 5.0))))
        self.assertEqual(changed, 2)
        self.assertEqual(restored.defined_routes["route"].default_kilometrage,
                         {"A": 0.0, "B": 5.0})
        self.assertEqual(restored.bildfahrplan_routes[0].kilometrage,
                         {"A": 0.0, "B": 5.0})
        self.assertIsNot(restored.bildfahrplan_routes[0].kilometrage,
                         restored.defined_routes["route"].default_kilometrage)

    def test_registry_filters_ineligible_automatic_targets_but_keeps_manual_and_entry(self):
        assignments = OperatingPointAssignments(
            points={
                "empty": EditableOperatingPoint("empty", "Empty"),
                "optional": EditableOperatingPoint("optional", "Optional"),
                "platform": EditableOperatingPoint("platform", "Platform"),
                "manual:X": EditableOperatingPoint("manual:X", "ManualX", removable=True),
            },
            assignments={"Optional": "optional", "Platform 1": "platform", "Aalen": "entry:Aalen"},
            sources={"Optional": "automatic", "Platform 1": "automatic", "Aalen": "self_entry"},
            manual_point_ids={"manual:X"},
            raw_items={
                "Optional": AssignableRawItem("Optional", "schedule_point"),
                "Platform 1": AssignableRawItem("Platform 1", "platform_or_haltpunkt"),
                "Aalen": AssignableRawItem("Aalen", "entry"),
            },
            entry_points={"entry:Aalen": EntryPoint("entry:Aalen", "Aalen")})
        registry = TopologyTargetRegistry.from_assignments(assignments)
        self.assertEqual(set(registry.targets), {"platform", "manual:X", "entry:Aalen"})
        self.assertNotIn("Optional", registry.raw_to_target)
        self.assertEqual(registry.raw_to_target["Platform 1"], "platform")

    def test_filtered_target_creates_no_parking_node_and_legacy_node_is_removed(self):
        assignments = OperatingPointAssignments(
            points={"shadow": EditableOperatingPoint("shadow", "Shadow")},
            assignments={"Shadow": "shadow"}, sources={"Shadow": "automatic"},
            raw_items={"Shadow": AssignableRawItem("Shadow", "schedule_point")})
        registry = TopologyTargetRegistry.from_assignments(assignments)
        source = OperationalRouteGraph(nodes={
            "schedule:Shadow": OperationalRouteNode(
                "schedule:Shadow", "Shadow", ("Shadow",), (), "inferred")})
        projected = EditableTopologyGraph.from_registry_projection(source, registry)
        self.assertFalse(projected.nodes)
        legacy = graph_with_nodes(("schedule:Shadow", "junction"))
        legacy.nodes["schedule:Shadow"].source = "automatic"
        legacy.migrate_to_registry(registry)
        self.assertFalse(legacy.nodes)
        self.assertEqual(len(legacy.metadata["unmapped_legacy_nodes"]), 1)

    def test_registry_projection_uses_only_targets_and_unions_edges(self):
        registry = TopologyTargetRegistry(
            targets={name: TopologyTarget(name, "operating_point", name) for name in ("THDM", "X", "Y", "Z")},
            raw_to_target={"THDM": "THDM", "THDM 1": "THDM", "THDM 2": "THDM",
                           "X": "X", "Y": "Y", "Z": "Z"},
            evidence_to_target={"op:THDM": "THDM", "X": "X", "Y": "Y", "Z": "Z"})
        nodes = {
            "schedule:THDM": OperationalRouteNode("schedule:THDM", "THDM", ("THDM",), (), "inferred"),
            "axis:THDM": OperationalRouteNode("axis:THDM", "THDM", ("THDM 1",), ("op:THDM",), "inferred"),
            "resolver:THDM": OperationalRouteNode("resolver:THDM", "THDM", ("THDM 2",), (), "inferred"),
            **{name: OperationalRouteNode(name, name, (name,), (name,), "inferred") for name in "XYZ"},
            "schedule:UNKNOWN": OperationalRouteNode("schedule:UNKNOWN", "UNKNOWN", ("UNKNOWN",), (), "inferred"),
        }
        source = OperationalRouteGraph(nodes=nodes, edges=[
            OperationalRouteEdge("X", "schedule:THDM", 1, {}, "inferred", ()),
            OperationalRouteEdge("axis:THDM", "Y", 1, {}, "inferred", ()),
            OperationalRouteEdge("resolver:THDM", "Z", 1, {}, "inferred", ()),
        ])
        graph = EditableTopologyGraph.from_registry_projection(source, registry)
        self.assertEqual(set(graph.nodes), {"THDM", "X", "Y", "Z"})
        self.assertEqual(graph.neighbours("THDM"), {"X", "Y", "Z"})
        self.assertNotIn("schedule:UNKNOWN", graph.nodes)
        self.assertEqual(graph.nodes["THDM"].metadata["automatic_node_ids"],
                         ["axis:THDM", "resolver:THDM", "schedule:THDM"])

    def test_registry_projection_adds_one_missing_target_and_one_entry(self):
        registry = TopologyTargetRegistry(targets={
            "manual:X": TopologyTarget("manual:X", "operating_point", "ManualX"),
            "entry:Aalen": TopologyTarget("entry:Aalen", "entry_point", "Aalen"),
        }, raw_to_target={"Aalen": "entry:Aalen"})
        source = OperationalRouteGraph(nodes={
            "type6:Aalen": OperationalRouteNode("type6:Aalen", "Aalen", ("Aalen",), (), "inferred"),
            "type7:Aalen": OperationalRouteNode("type7:Aalen", "Aalen", ("Aalen",), (), "inferred"),
        })
        graph = EditableTopologyGraph.from_registry_projection(source, registry)
        self.assertEqual(set(graph.nodes), {"manual:X", "entry:Aalen"})
        self.assertEqual(graph.nodes["entry:Aalen"].target_kind, "entry_point")
        self.assertEqual(graph.degree("manual:X"), 0)
        self.assertEqual(graph.degree("entry:Aalen"), 0)

    def test_registry_migration_collapses_old_nodes_and_records_unmapped_manual(self):
        registry = TopologyTargetRegistry(
            targets={"TRM": TopologyTarget("TRM", "operating_point", "TRM")},
            raw_to_target={"TRM": "TRM"},
            evidence_to_target={"schedule:TRM": "TRM", "axis:TRM": "TRM"})
        graph = graph_with_nodes(("schedule:TRM", "junction"), ("axis:TRM", "junction"),
                                 ("supplement:TRM", "junction"), ("manual:orphan", "junction"))
        graph.nodes["schedule:TRM"].source = graph.nodes["axis:TRM"].source = "automatic"
        graph.nodes["supplement:TRM"].source = "operating_point"
        graph.nodes["supplement:TRM"].display_name = "TRM"
        graph.nodes["manual:orphan"].display_name = "Orphan"
        graph.migrate_to_registry(registry)
        self.assertEqual(set(graph.nodes), {"TRM"})
        self.assertEqual(graph.nodes["TRM"].target_id, "TRM")
        self.assertEqual(len(graph.metadata["unmapped_legacy_nodes"]), 1)
        self.assertFalse(graph.duplicate_target_ids())

    def test_duplicate_target_validation_reports_target(self):
        graph = graph_with_nodes(("one", "junction"), ("two", "junction"))
        graph.nodes["one"].target_id = graph.nodes["two"].target_id = "THDM"
        self.assertEqual(graph.duplicate_target_ids(), ("THDM",))

    def test_supplement_candidates_deduplicate_by_canonical_target(self):
        candidates = (
            TopologySupplementCandidate("schedule:THDM", "THDM", "junction",
                                        "operating_point", canonical_target_id="THDM"),
            TopologySupplementCandidate("station:THDM", "THDM", "junction",
                                        "operating_point_config", canonical_target_id="THDM"),
        )
        result = EditableTopologyGraph.deduplicate_supplement_candidates(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].canonical_target_id, "THDM")

    def test_represented_operating_point_filters_supplement_candidate(self):
        graph = graph_with_nodes(("THDM", "junction"), ("A", "entry"), ("B", "entry"))
        graph.nodes["THDM"].source = graph.nodes["A"].source = graph.nodes["B"].source = "automatic"
        graph.nodes["THDM"].operating_point_id = "THDM"
        graph.add_edge("A", "THDM"); graph.add_edge("THDM", "B")
        candidate = TopologySupplementCandidate(
            "schedule:THDM", "THDM", "junction", "operating_point",
            canonical_target_id="THDM")
        self.assertFalse(graph.missing_supplement_candidates(
            (candidate,), {"THDM": "THDM"}, {"thdm": {"THDM"}}))

    def test_legacy_isolated_supplements_are_removed_but_manual_name_is_preserved(self):
        graph = graph_with_nodes(("TRM", "junction"), ("A", "entry"), ("B", "entry"),
                                 ("legacy-1", "junction"), ("legacy-2", "junction"),
                                 ("manual", "junction"))
        for node_id in ("TRM", "A", "B"):
            graph.nodes[node_id].source = "automatic"
        graph.nodes["TRM"].operating_point_id = "TRM"
        graph.add_edge("A", "TRM"); graph.add_edge("TRM", "B")
        for node_id in ("legacy-1", "legacy-2"):
            graph.nodes[node_id].display_name = "TRM"
            graph.nodes[node_id].source = "operating_point"
        graph.nodes["manual"].display_name = "TRM"
        removed = graph.remove_redundant_operating_supplements(
            {"TRM": "TRM"}, {"trm": {"TRM"}})
        self.assertEqual(removed, 2)
        self.assertEqual(set(graph.nodes), {"TRM", "A", "B", "manual"})
        with tempfile.TemporaryDirectory() as directory:
            store = EditableTopologyGraphStore(directory)
            store.save(9, "Test", graph)
            reloaded = EditableTopologyGraph.from_dict(store.load_path(store.path_for(9)))
            self.assertEqual(set(reloaded.nodes), {"TRM", "A", "B", "manual"})

    def test_operational_representations_collapse_to_canonical_operating_point(self):
        source = OperationalRouteGraph(
            nodes={
                "schedule:THDM": OperationalRouteNode("schedule:THDM", "THDM", ("THDM",),
                                                       ("schedule:THDM",), "inferred"),
                "axis:THDM": OperationalRouteNode("axis:THDM", "THDM", ("THDM 1",),
                                                   ("station:THDM",), "inferred"),
                "X": OperationalRouteNode("X", "X", (), ("X",), "inferred"),
                "Y": OperationalRouteNode("Y", "Y", (), ("Y",), "inferred"),
            }, edges=[OperationalRouteEdge("schedule:THDM", "X", 1, {}, "inferred", ()),
                      OperationalRouteEdge("axis:THDM", "Y", 1, {}, "inferred", ()),
                      OperationalRouteEdge("schedule:THDM", "axis:THDM", 1, {}, "inferred", ())])
        graph = EditableTopologyGraph.from_operational_graph(
            source, {"schedule:THDM": "THDM", "axis:THDM": "THDM"})
        self.assertEqual(set(graph.nodes), {"THDM", "X", "Y"})
        self.assertEqual(graph.neighbours("THDM"), {"X", "Y"})
        self.assertIsNone(graph.edge_between("THDM", "THDM"))
        self.assertEqual(len(graph.edges), 2)

    def test_saved_automatic_duplicates_merge_edges_and_route_references(self):
        graph = graph_with_nodes(("old:TRM", "junction"), ("axis:TRM", "junction"),
                                 ("A", "entry"), ("B", "entry"), ("manual", "junction"))
        for node_id in ("old:TRM", "axis:TRM", "A", "B"):
            graph.nodes[node_id].source = "automatic"
        graph.nodes["old:TRM"].display_name = graph.nodes["axis:TRM"].display_name = "TRM"
        graph.nodes["manual"].display_name = "TRM"
        graph.add_edge("A", "old:TRM"); graph.add_edge("old:TRM", "axis:TRM"); graph.add_edge("axis:TRM", "B")
        route = graph.add_route("A-B", ["A", "old:TRM", "axis:TRM", "B"])
        changed = graph.canonicalize_automatic_nodes({"old:TRM": "TRM", "axis:TRM": "TRM"})
        self.assertEqual(changed, 1)
        canonical = next(node for node in graph.nodes.values() if node.operating_point_id == "TRM")
        self.assertEqual(graph.neighbours(canonical.id), {"A", "B"})
        self.assertEqual(route.ordered_node_ids, ["A", canonical.id, "B"])
        self.assertIn("manual", graph.nodes)

    def test_auto_layout_uses_horizontal_backbone_and_offsets_real_branch(self):
        graph = graph_with_nodes(*((name, "line") for name in "ABCDE"))
        for left, right in zip("ABCDE", "BCDE"): graph.add_edge(left, right)
        graph.auto_layout()
        self.assertEqual(len({graph.nodes[name].layout_y for name in "ABCDE"}), 1)
        self.assertEqual([graph.nodes[name].layout_x for name in "ABCDE"], sorted(
            graph.nodes[name].layout_x for name in "ABCDE"))
        branch = graph_with_nodes(("A", "entry"), ("B", "junction"), ("C", "entry"), ("X", "entry"))
        branch.add_edge("A", "B"); branch.add_edge("B", "C"); branch.add_edge("B", "X")
        branch.auto_layout()
        self.assertEqual(branch.nodes["A"].layout_y, branch.nodes["B"].layout_y)
        self.assertEqual(branch.nodes["B"].layout_y, branch.nodes["C"].layout_y)
        self.assertNotEqual(branch.nodes["X"].layout_y, branch.nodes["B"].layout_y)

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
        route.default_kilometrage = {"A": 0.0, "B": 12.5}
        first = graph.add_bildfahrplan_instance(route.route_id)
        second = graph.add_bildfahrplan_instance(route.route_id)
        first.kilometrage = {"A": 0.0, "B": 12.5}
        second.kilometrage = {"A": 42.0, "B": 39.0}
        with tempfile.TemporaryDirectory() as directory:
            store = EditableTopologyGraphStore(directory); path = store.save(17, "Test", graph)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((payload["aid"], payload["stellwerk_name"], payload["artifact_type"]),
                             (17, "Test", "editable_topology_graph"))
            restored = EditableTopologyGraph.from_dict(payload)
            self.assertEqual(restored.nodes["A"].layout_x, 42.5)
            self.assertEqual(restored.defined_routes[route.route_id].default_kilometrage,
                             {"A": 0.0, "B": 12.5})
            self.assertEqual(len(restored.bildfahrplan_routes), 2)
            self.assertEqual([item.order for item in restored.bildfahrplan_routes], [0, 1])
            self.assertEqual(restored.bildfahrplan_routes[0].kilometrage, {"A": 0.0, "B": 12.5})
            self.assertEqual(restored.bildfahrplan_routes[1].kilometrage, {"A": 42.0, "B": 39.0})
            self.assertNotEqual(restored.bildfahrplan_routes[0].kilometrage,
                                restored.bildfahrplan_routes[1].kilometrage)

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
