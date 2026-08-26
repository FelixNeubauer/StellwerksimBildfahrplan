import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import InfrastructureGraphBuilder, parse_bahnsteigliste, parse_wege, save_generated_graph


LINEAR = """<wege>
  <element enr="1" name="A" type="bahnsteig" shape="0" mystery="kept" />
  <element enr="2" type="signal" />
  <element enr="3" name="B" type="unknown-future-type" />
  <connector enr1="1" enr2="2" connector="x" />
  <connector enr1="2" enr2="3" />
</wege>"""

BRANCH = """<wege>
  <e enr="1" name="A"/><e enr="2" name="C"/><e enr="3" name="B"/>
  <e enr="4" name="D"/><e enr="5" name="E"/>
  <connector enr1="1" enr2="2"/><connector enr1="2" enr2="3"/>
  <connector enr1="2" enr2="4"/><connector enr1="2" enr2="5"/>
</wege>"""


class InfrastructureTests(unittest.TestCase):
    def test_linear_raw_graph_is_literal_undirected_and_lossless(self):
        graph = parse_wege(LINEAR)
        self.assertEqual((len(graph.nodes), len(graph.edges)), (3, 2))
        self.assertFalse(graph.edges[0].directed)
        self.assertEqual(graph.nodes["enr:1"].metadata["mystery"], "kept")
        self.assertEqual(graph.nodes["enr:3"].element_type, "unknown-future-type")

    def test_branch_is_preserved_in_operational_graph(self):
        builder = InfrastructureGraphBuilder(parse_wege(BRANCH))
        for schedule in (("A", "C", "B"), ("A", "C", "D"), ("A", "C", "E")):
            builder.observe_schedule(schedule)
        graph = builder.build_operational_graph()
        self.assertEqual(len(graph.edges), 4)
        self.assertIn("enr:2", graph.nodes)

    def test_exact_ambiguous_and_unresolved_anchors_do_not_guess(self):
        graph = parse_wege("<wege><e enr='1' name='5a'/><e enr='2' name='5a'/><e enr='3' name='5b'/><e enr='4' name='TU 1'/><e enr='5' name='TU 1G'/></wege>")
        builder = InfrastructureGraphBuilder(graph)
        self.assertEqual(builder.resolve_anchor("5a").resolution, "ambiguous")
        self.assertEqual(len(builder.anchors["5a"].graph_nodes), 2)
        self.assertEqual(builder.resolve_anchor("unknown").resolution, "unresolved")
        self.assertNotEqual(builder.resolve_anchor("5b").graph_nodes, builder.resolve_anchor("5a").graph_nodes)
        self.assertNotEqual(builder.resolve_anchor("TU 1").graph_nodes, builder.resolve_anchor("TU 1G").graph_nodes)

    def test_repeated_schedules_increase_path_evidence_and_compress_degree_two(self):
        builder = InfrastructureGraphBuilder(parse_wege(LINEAR))
        builder.observe_schedule(("A", "B")); builder.observe_schedule(("A", "B"))
        graph = builder.build_operational_graph()
        self.assertEqual(set(graph.nodes), {"enr:1", "enr:3"})
        self.assertEqual(graph.edges[0].evidence["schedule"], 2)
        self.assertIn(graph.edges[0].source_path, {
            ("enr:1", "enr:2", "enr:3"), ("enr:3", "enr:2", "enr:1"),
        })

    def test_equal_alternative_paths_are_not_chosen_arbitrarily(self):
        graph = parse_wege("""<wege>
          <e enr='1' name='A'/><e enr='2'/><e enr='3'/><e enr='4' name='B'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='4'/>
          <connector enr1='1' enr2='3'/><connector enr1='3' enr2='4'/>
        </wege>""")
        builder = InfrastructureGraphBuilder(graph)
        builder.observe_schedule(("A", "B"))
        self.assertEqual(builder.path_evidence, {})

    def test_route_path_validates_branch_and_uses_relative_positions(self):
        builder = InfrastructureGraphBuilder(parse_wege(BRANCH))
        builder.observe_schedule(("A", "C", "D"))
        graph = builder.build_operational_graph()
        path = builder.make_route_path(graph, "main", "A–D", ("enr:1", "enr:2", "enr:4"))
        self.assertEqual(path.positions, (0.0, 1.0, 2.0))
        self.assertEqual(path.axis_unit, "relative")
        with self.assertRaises(ValueError):
            builder.make_route_path(graph, "bad", "bad", ("enr:1", "enr:4"))

    def test_platform_neighbours_are_evidence_not_raw_edges(self):
        evidence = parse_bahnsteigliste("<bahnsteigliste><bahnsteig name='A'><n name='B'/></bahnsteig></bahnsteigliste>")
        self.assertEqual(evidence[0].related_names, ("B",))
        self.assertEqual(evidence[0].evidence, "bahnsteigliste")

    def test_generated_persistence_separates_raw_and_derived(self):
        raw = parse_wege(LINEAR)
        builder = InfrastructureGraphBuilder(raw)
        builder.observe_schedule(("A", "B"))
        with tempfile.TemporaryDirectory() as directory:
            path = save_generated_graph(directory, 1778, "Teststellwerk", raw, builder.anchors,
                                        builder.build_operational_graph())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(path.name, "1778_graph.json")
            self.assertEqual(data["schema_version"], 14)
            self.assertEqual(data["aid"], 1778); self.assertEqual(data["stellwerk_name"], "Teststellwerk")
            self.assertIn("raw", data); self.assertIn("derived", data)
            self.assertIn("schedule", data); self.assertIn("operating_point_clustering", data)
            self.assertIn("route_axis", data)
            self.assertIn("synthetic_junctions", data["corridor"])
            self.assertIn("branch_attachments", data["corridor"])
            self.assertIn("halt_aware_travel_time_comparisons", data["corridor"])
            self.assertIn("same_service_triple_evidence", data["corridor"])
            self.assertIn("topology_roles", data["corridor"])
            self.assertIn("deferred_external_boundary_candidates", data["corridor"])
            self.assertIn("ignored_endpoint_observations", data["corridor"])
            self.assertIn("service_provenance", data["schedule"])
            self.assertIn("junction_position_estimates", data["corridor"])
            self.assertIn("final_node_roles", data["corridor"])
            self.assertIn("applied_between_resolutions", data["corridor"])
            self.assertIn("synthetic_external_boundaries", data["corridor"])
            self.assertIn("topology_questions", data["corridor"])
            self.assertIn("corridor", data)


if __name__ == "__main__":
    unittest.main()
