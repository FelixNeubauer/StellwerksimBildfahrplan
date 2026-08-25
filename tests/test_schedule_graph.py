import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import OperatingPointResolver, SchedulePointGraph, parse_bahnsteigliste, parse_wege, station_key


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

    def test_station_key_requires_support_and_merges_internal_points(self):
        self.assertEqual(station_key("MIMS Wende West"), "MIMS")
        self.assertIsNone(station_key("Gleis 1"))
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "MIMS 1", "MIMS Wende West", "MIMS 4", "B")])
        graph = OperatingPointResolver().resolve(schedule)
        self.assertEqual({graph.raw_to_operating_point[name] for name in
                          ("MIMS 1", "MIMS Wende West", "MIMS 4")}, {"MIMS"})
        self.assertIn("same_station_key", graph.nodes["MIMS"].evidence)
        self.assertIn("schedule_sandwich", graph.nodes["MIMS"].evidence)

    def test_single_unrelated_sandwich_does_not_blindly_merge(self):
        schedule = SchedulePointGraph.from_services([service(1, "MIMS 1", "Foreign", "MIMS 2")])
        platforms = parse_bahnsteigliste(
            "<bahnsteigliste><bahnsteig name='MIMS 1'><n name='MIMS 2'/></bahnsteig></bahnsteigliste>")
        graph = OperatingPointResolver(platforms).resolve(schedule)
        self.assertNotEqual(graph.raw_to_operating_point["Foreign"], graph.raw_to_operating_point["MIMS 1"])

    def test_separate_mof_platform_components_merge_by_supported_key(self):
        schedule = SchedulePointGraph.from_services([
            service(1, "MIMS 1", "MOF 1", "MOF 2", "MOF 22", "MOF 31", "MOF 51")])
        platforms = parse_bahnsteigliste("""<bahnsteigliste>
          <bahnsteig name='MOF 1'><n name='MOF 2'/></bahnsteig>
          <bahnsteig name='MOF 22'><n name='MOF 31'/><n name='MOF 51'/></bahnsteig>
        </bahnsteigliste>""")
        graph = OperatingPointResolver(platforms).resolve(schedule)
        self.assertEqual({graph.raw_to_operating_point[name] for name in
                          ("MOF 1", "MOF 2", "MOF 22", "MOF 31", "MOF 51")}, {"MOF"})

    def test_closed_excursion_is_internal_and_recorded(self):
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "MSF 2", "MSF AUSZ", "MSF 12", "MSF AUSZ", "MSF 2", "B")])
        graph = OperatingPointResolver().resolve(schedule)
        self.assertEqual({graph.raw_to_operating_point[name] for name in
                          ("MSF 2", "MSF AUSZ", "MSF 12")}, {"MSF"})
        self.assertIn("closed_excursion", graph.nodes["MSF"].evidence)

    def test_unprefixed_platform_components_stay_separate(self):
        schedule = SchedulePointGraph.from_services([service(1, "1", "2", "3N", "3S", "5", "6")])
        platforms = parse_bahnsteigliste("""<bahnsteigliste>
          <bahnsteig name='1'><n name='2'/><n name='3N'/><n name='3S'/></bahnsteig>
          <bahnsteig name='5'><n name='6'/></bahnsteig>
        </bahnsteigliste>""")
        graph = OperatingPointResolver(platforms, aid=77).resolve(schedule)
        first = {graph.raw_to_operating_point[name] for name in ("1", "2", "3N", "3S")}
        second = {graph.raw_to_operating_point[name] for name in ("5", "6")}
        self.assertEqual(len(first), 1); self.assertEqual(len(second), 1)
        self.assertNotEqual(first, second)
        self.assertTrue(next(iter(first)).startswith("anonymous:77:"))

    def test_platform_cluster_does_not_claim_physical_track_identity(self):
        schedule = SchedulePointGraph.from_services([service(1, "5a", "5b")])
        platforms = parse_bahnsteigliste(
            "<bahnsteigliste><bahnsteig name='5a'><n name='5b'/></bahnsteig></bahnsteigliste>")
        point = OperatingPointResolver(platforms).resolve(schedule).nodes["anonymous:unknown:cluster_0"]
        self.assertEqual(point.raw_names, ("5a", "5b"))
        self.assertNotIn("physical_track", point.evidence)

    def test_haltepunkt_and_entry_exit_axis_alias(self):
        schedule = SchedulePointGraph.from_services([
            service(1, "Martinszell", "Einfahrt Westallgäu", "Westallgäu", "Ausfahrt Westallgäu")])
        platforms = parse_bahnsteigliste(
            "<bahnsteigliste><bahnsteig name='Martinszell' haltepunkt='true'/></bahnsteigliste>")
        graph = OperatingPointResolver(platforms).resolve(schedule)
        self.assertEqual(graph.nodes[graph.raw_to_operating_point["Martinszell"]].point_type, "haltpunkt")
        axis = graph.to_route_axis_graph()
        aliases = {axis.operating_to_axis[graph.raw_to_operating_point[name]] for name in
                   ("Einfahrt Westallgäu", "Westallgäu", "Ausfahrt Westallgäu")}
        self.assertEqual(len(aliases), 1)

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
        self.assertIn("Westallgäu", {node.label for node in graph.nodes.values()})
        self.assertNotIn("enr:119", graph.nodes)
        self.assertNotIn("S119", {node.label for node in graph.nodes.values()})
        self.assertIn("enr:119", raw.nodes)  # Raw-Infrastruktur bleibt erhalten.

    def test_aid_823_regression_branch_is_schedule_derived(self):
        platforms = parse_bahnsteigliste("""<bahnsteigliste>
          <bahnsteig name='MIMS 1'><n name='MIMS 2'/><n name='MIMS 3a'/></bahnsteig>
          <bahnsteig name='MBLH 1'><n name='MBLH 2'/></bahnsteig>
          <bahnsteig name='MSF 1'><n name='MSF 2'/><n name='MSF 3'/></bahnsteig>
          <bahnsteig name='MATS 1'><n name='MATS 2'/></bahnsteig>
          <bahnsteig name='MFN 1'><n name='MFN 2'/></bahnsteig>
          <bahnsteig name='MLNW 1'><n name='MLNW 2'/></bahnsteig>
          <bahnsteig name='MOF 1'><n name='MOF 2'/></bahnsteig>
          <bahnsteig name='MOF 22'><n name='MOF 31'/><n name='MOF 51'/></bahnsteig>
          <bahnsteig name='Martinszell' haltepunkt='true'/>
        </bahnsteigliste>""")
        schedules = [
            service(1, "EA Kempten", "Martinszell", "MIMS 1", "MIMS Wende West", "MIMS 2",
                    "Westallgäu", "Einfahrt Westallgäu"),
            service(2, "EA Kempten", "Martinszell", "MIMS 3a", "MBLH 1", "MSF 2",
                    "MATS 1", "MFN 1", "MLNW 1", "MOF 1", "MOF 2", "MOF 22", "MOF 31"),
            service(3, "MSF 2", "MSF AUSZ", "MSF 12", "MSF AUSZ", "MSF 2"),
        ]
        graph = OperatingPointResolver(platforms, aid=823).resolve(SchedulePointGraph.from_services(schedules))
        edges = set(graph.edges)
        west = graph.raw_to_operating_point["Westallgäu"]
        self.assertIn(("MIMS", west), edges)
        self.assertIn(("MIMS", "MBLH"), edges)
        for pair in zip(("MBLH", "MSF", "MATS", "MFN", "MLNW", "MOF"),
                        ("MSF", "MATS", "MFN", "MLNW", "MOF")):
            self.assertIn(pair, edges)
        self.assertIn("MIMS", graph.branch_nodes)
        self.assertNotIn("S119", graph.nodes)
        axis = graph.to_route_axis_graph()
        self.assertEqual({node.display_name for node in axis.nodes.values()}, {
            "EA Kempten", "Martinszell", "MIMS", "Westallgäu", "MBLH", "MSF",
            "MATS", "MFN", "MLNW", "MOF",
        })
        self.assertEqual(axis.branch_nodes, {"MIMS"})
        self.assertNotIn("MSF AUSZ", {node.display_name for node in axis.nodes.values()})


if __name__ == "__main__":
    unittest.main()
