import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import OperatingPointResolver, SchedulePointGraph, parse_bahnsteigliste, parse_wege


def service(zid, *names, kind="train"):
    points = [SimpleNamespace(planned_name=name, raw_name=name) for name in names]
    return SimpleNamespace(zid=zid, service_kind=kind, original_schedule=points, current_schedule=[])


class ScheduleGraphTests(unittest.TestCase):
    def test_original_train_schedules_are_primary_and_edges_are_directed(self):
        graph = SchedulePointGraph.from_services([
            service(1, "A", "B"), service(2, "A", "B"), service(3, "B", "A"),
            service(-1, "S119", "A", kind="locomotive_movement"),
        ])
        self.assertEqual(graph.edges[("A", "B")].observation_count, 2)
        self.assertEqual(graph.edges[("B", "A")].observation_count, 1)
        self.assertNotIn("S119", graph.nodes)

    def test_platform_evidence_groups_mims_and_avoids_self_loop(self):
        schedule = SchedulePointGraph.from_services([service(1, "A", "MIMS 1", "MIMS 3a", "B")])
        platforms = parse_bahnsteigliste(
            "<bahnsteigliste><bahnsteig name='MIMS 1'><n name='MIMS 3a'/></bahnsteig></bahnsteigliste>"
        )
        graph = OperatingPointResolver(platforms).resolve(schedule)
        self.assertEqual(graph.raw_to_operating_point["MIMS 1"], graph.raw_to_operating_point["MIMS 3a"])
        self.assertFalse(any(source == target for source, target in graph.edges))

    def test_manual_mapping_has_priority(self):
        schedule = SchedulePointGraph.from_services([service(1, "MIMS 1", "MIMS 2")])
        manual = {"operating_points": {"MIMS": {
            "display_name": "Immenstadt", "raw_names": ["MIMS 1", "MIMS 2"]}}}
        graph = OperatingPointResolver((), manual).resolve(schedule)
        self.assertTrue(graph.nodes["MIMS"].manual_confirmation)
        self.assertEqual(graph.nodes["MIMS"].display_name, "Immenstadt")

    def test_schedule_only_virtual_point_survives_without_wege_match(self):
        raw = parse_wege("<wege><e enr='119' name='S119' type='2'/></wege>")
        schedule = SchedulePointGraph.from_services([service(1, "Einfahrt Westallgäu", "Westallgäu")])
        graph = OperatingPointResolver().resolve(schedule).to_operational_graph()
        self.assertIn("schedule:Westallgäu", graph.nodes)
        self.assertNotIn("enr:119", graph.nodes)
        self.assertNotIn("S119", {node.label for node in graph.nodes.values()})
        self.assertIn("enr:119", raw.nodes)  # Raw-Infrastruktur bleibt erhalten.

    def test_aid_823_regression_branch_is_schedule_derived(self):
        platforms = parse_bahnsteigliste("""<bahnsteigliste>
          <bahnsteig name='MIMS 1'><n name='MIMS 2'/><n name='MIMS 3a'/></bahnsteig>
        </bahnsteigliste>""")
        manual = {"operating_points": {
            "MIMS": {"display_name": "Immenstadt", "raw_names": ["MIMS 1", "MIMS 2", "MIMS 3a"]},
            "MBLH": {"display_name": "Blaichach", "raw_names": ["MBLH 1"]},
            "MSF": {"display_name": "Sonthofen", "raw_names": ["MSF 1"]},
            "MATS": {"display_name": "Altstädten", "raw_names": ["MATS 1"]},
            "MFN": {"display_name": "Fischen", "raw_names": ["MFN 1"]},
            "MLNW": {"display_name": "Langenwang", "raw_names": ["MLNW 1"]},
            "MOF": {"display_name": "Oberstdorf", "raw_names": ["MOF 1"]},
        }}
        schedules = [
            service(1, "Einfahrt Kempten", "Martinszell", "MIMS 1", "Westallgäu", "Einfahrt Westallgäu"),
            service(2, "Einfahrt Kempten", "Martinszell", "MIMS 2", "MBLH 1", "MSF 1",
                    "MATS 1", "MFN 1", "MLNW 1", "MOF 1"),
        ]
        graph = OperatingPointResolver(platforms, manual).resolve(SchedulePointGraph.from_services(schedules))
        edges = set(graph.edges)
        west = graph.raw_to_operating_point["Westallgäu"]
        self.assertIn(("MIMS", west), edges)
        self.assertIn(("MIMS", "MBLH"), edges)
        for pair in zip(("MBLH", "MSF", "MATS", "MFN", "MLNW", "MOF"),
                        ("MSF", "MATS", "MFN", "MLNW", "MOF")):
            self.assertIn(pair, edges)
        self.assertIn("MIMS", graph.branch_nodes)
        self.assertNotIn("S119", graph.nodes)


if __name__ == "__main__":
    unittest.main()
