import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import (
    CorridorGraphBuilder, OperatingPointResolver, SchedulePointGraph,
    parse_bahnsteigliste, parse_wege, station_key,
)


def service(zid, *names, kind="train"):
    points = [SimpleNamespace(planned_name=name, raw_name=name) for name in names]
    return SimpleNamespace(zid=zid, service_kind=kind, original_schedule=points, current_schedule=[])


def timed_service(zid, *points):
    schedule = [SimpleNamespace(
        planned_name=name, raw_name=name, planned_arrival=arrival, planned_departure=departure,
    ) for name, arrival, departure in points]
    return SimpleNamespace(zid=zid, service_kind="train", original_schedule=schedule, current_schedule=[])


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

    def test_heidenheim_station_keys_without_spaces(self):
        self.assertEqual({name: station_key(name) for name in ("TGB1", "THD1", "THDM1", "TNS1")}, {
            "TGB1": "TGB", "THD1": "THD", "THDM1": "THDM", "TNS1": "TNS",
        })

    def test_same_key_points_with_shared_external_neighbor_cluster(self):
        schedule = SchedulePointGraph.from_services([
            service(1, "TLW", "TLM 401"), service(2, "TLW", "TLM 402")])
        graph = OperatingPointResolver().resolve(schedule)
        self.assertEqual({graph.raw_to_operating_point[name] for name in ("TLM 401", "TLM 402")}, {"TLM"})
        self.assertIn("shared_external_neighbors", graph.nodes["TLM"].evidence)

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
        corridor = CorridorGraphBuilder(SchedulePointGraph.from_services(schedules), graph).build()
        self.assertIn("MIMS", corridor.branch_nodes)

    def test_laupheim_skip_reversal_branch_and_local_component(self):
        names = ("EAF", "TAU", "TBSC", "TBIB", "TBI", "TWVH", "TSX", "TLW", "TER", "TEIN", "TUDT",
                 "TAT", "TLM", "U1", "U4", "U5")
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]}
            for name in names if name not in {"TLM"}
        }}
        manual["operating_points"]["TLM"] = {
            "display_name": "TLM", "raw_names": ["TLM 401", "TLM 402"],
        }
        main = ("TAU", "TBSC", "TBIB", "TBI", "TWVH", "TSX", "TLW", "TER", "TEIN", "TUDT")
        services = [
            service(1, *main), service(2, *main), service(3, *reversed(main)), service(4, *reversed(main)),
            service(13, "EAF", "TAU", "TBSC"), service(14, "TBSC", "TAU", "EAF"),
            service(5, "TBI", "TBSC"), service(6, "TER", "TUDT"),
            service(7, "TAU", "TAT"), service(8, "TAT", "TAU"),
            service(9, "TLW", "TLM 401", "TLW"), service(10, "TLW", "TLM 402", "TLW"),
            service(11, "TLW", "TLM 401", "TSX"),
            service(12, "U1", "U4", "U5"),
        ]
        schedule = SchedulePointGraph.from_services(services)
        operating = OperatingPointResolver((), manual, 1728).resolve(schedule)
        self.assertEqual({operating.raw_to_operating_point[name] for name in ("TLM 401", "TLM 402")}, {"TLM"})
        corridor = CorridorGraphBuilder(schedule, operating).build()
        skips = {(edge.source, edge.target): edge for edge in corridor.edges.values()
                 if edge.classification == "skip"}
        self.assertEqual(skips[("TBI", "TBSC")].covered_path, ("TBI", "TBIB", "TBSC"))
        self.assertEqual(skips[("TER", "TUDT")].covered_path, ("TER", "TEIN", "TUDT"))
        self.assertEqual(corridor.node_roles["TLM"], "branch_terminal")
        self.assertEqual(corridor.edges[("TLW", "TLM")].classification, "branch")
        self.assertEqual(corridor.edges[("TLM", "TSX")].classification, "skip")
        self.assertEqual(corridor.edges[("TLM", "TSX")].covered_path, ("TLM", "TLW", "TSX"))
        self.assertNotIn(("TBI", "TBSC"), {(e.source, e.target) for e in corridor.visible_edges})
        self.assertEqual(corridor.edges[("TAU", "TAT")].classification, "branch")
        self.assertTrue(all(corridor.node_roles[name] == "local_industrial" for name in ("U1", "U4", "U5")))
        self.assertEqual(corridor.node_roles["TLW"], "branch_junction")
        self.assertNotIn("branch_junction", {corridor.node_roles[name] for name in ("TER", "TSX", "TUDT")})
        self.assertGreaterEqual(sum(edge.classification == "skip" for edge in corridor.edges.values()), 2)

    def test_recursive_skip_and_strong_triangle_is_not_blindly_reduced(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "C", "D")}}
        services = [
            service(1, "A", "B", "C", "D"), service(2, "A", "B", "C", "D"),
            service(3, "D", "C", "B", "A"), service(4, "D", "C", "B", "A"),
            service(5, "A", "D"),
        ]
        schedule = SchedulePointGraph.from_services(services)
        operating = OperatingPointResolver((), manual).resolve(schedule)
        corridor = CorridorGraphBuilder(schedule, operating).build()
        self.assertEqual(corridor.edges[("A", "D")].classification, "skip")
        self.assertEqual(corridor.edges[("A", "D")].covered_path, ("A", "B", "C", "D"))

        strong = SchedulePointGraph.from_services(services + [
            service(6, "A", "D"), service(7, "A", "D"), service(8, "A", "D"),
        ])
        alternative = CorridorGraphBuilder(strong, OperatingPointResolver((), manual).resolve(strong)).build()
        self.assertEqual(alternative.edges[("A", "D")].classification, "alternative_route")

    def test_travel_times_separate_movement_dwell_and_protect_direct_backbone(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("TSX", "TLW", "TLM")}}
        services = [
            timed_service(1, ("TSX", "08:00:00", "08:00:00"), ("TLW", "08:02:30", "08:02:30")),
            timed_service(2, ("TSX", "08:10:00", "08:10:00"), ("TLW", "08:12:30", "08:12:30")),
            timed_service(3, ("TSX", "09:00:00", "09:00:00"), ("TLM", "09:04:30", "09:10:30"),
                          ("TLW", "09:15:00", "09:15:00")),
            timed_service(4, ("TLW", "10:00", "10:00"), ("TLM", "10:08", "10:18"),
                          ("TLW", "10:26", "10:26")),
        ]
        schedule = SchedulePointGraph.from_services(services)
        operating = OperatingPointResolver((), manual).resolve(schedule)
        corridor = CorridorGraphBuilder(schedule, operating).build()
        via = corridor.travel_time_stats[("TSX", "TLM", "TLW")]
        self.assertEqual(via.movement.median, 9 * 60)
        self.assertEqual(via.dwell.median, 6 * 60)
        self.assertEqual(via.total_elapsed.median, 15 * 60)
        direct = corridor.travel_time_stats[("TSX", "TLW")]
        self.assertEqual(direct.movement.median, 150)
        self.assertIn(frozenset(("TSX", "TLW")), corridor.backbone_edges)
        self.assertEqual(corridor.node_roles["TLM"], "branch_terminal")
        rejected = corridor.between_evidence[("TSX", "TLM", "TLW")]
        self.assertEqual(rejected["confidence"], "rejected")
        self.assertGreater(
            corridor.backbone_scores[frozenset(("TSX", "TLW"))].travel_time_support, 0)

    def test_laupheim_timed_chain_wins_before_forest_and_direct_becomes_skip(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("TER", "TEIN", "TUDT")}}
        services = [
            timed_service(1, ("TER", "08:00", "08:00"), ("TEIN", "08:02", "08:02"),
                          ("TUDT", "08:04", "08:04")),
            timed_service(2, ("TUDT", "09:00", "09:00"), ("TEIN", "09:02", "09:02"),
                          ("TER", "09:04", "09:04")),
            timed_service(3, ("TER", "10:00", "10:00"), ("TUDT", "10:05", "10:05")),
        ]
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertIn(frozenset(("TER", "TEIN")), corridor.backbone_edges)
        self.assertIn(frozenset(("TEIN", "TUDT")), corridor.backbone_edges)
        self.assertEqual(corridor.edges[("TER", "TUDT")].classification, "skip")
        self.assertEqual(corridor.edges[("TER", "TUDT")].covered_path, ("TER", "TEIN", "TUDT"))
        self.assertEqual(corridor.between_evidence[("TER", "TEIN", "TUDT")]["confidence"], "high")

    def test_raw_continuation_rejects_observed_terminal_but_mof_remains_terminal(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("TAU", "TAT")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "TAU", "TAT"), service(2, "TAU", "TAT"),
            service(3, "TAT", "TAU"), service(4, "TAT", "TAU"),
        ])
        raw = parse_wege("""<wege>
          <e enr='1' name='TAU'/><e enr='2'/><e enr='3' name='TAT'/><e enr='4'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='3'/><connector enr1='3' enr2='4'/>
        </wege>""")
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule), raw).build()
        self.assertEqual(corridor.node_roles["TAT"], "observed_schedule_boundary")
        self.assertIn("raw_external_continuation",
                      corridor.terminal_evidence["TAT"].contradicting_terminal_evidence)

    def test_travel_time_normalizes_midnight(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B")}}
        schedule = SchedulePointGraph.from_services([
            timed_service(1, ("A", "23:58", "23:58"), ("B", "00:01", "00:01"))])
        corridor = CorridorGraphBuilder(schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertEqual(corridor.travel_time_stats[("A", "B")].movement.median, 3 * 60)

    def test_schedule_end_terminal_differs_from_external_boundary(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("EA Kempten", "MIMS", "MOF")}}
        services = [
            service(1, "EA Kempten", "MIMS", "MOF"), service(2, "EA Kempten", "MIMS", "MOF"),
            service(3, "MOF", "MIMS", "EA Kempten"), service(4, "MOF", "MIMS", "EA Kempten"),
        ]
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertEqual(corridor.node_roles["MOF"], "terminal")
        self.assertEqual(corridor.node_roles["EA Kempten"], "external_boundary")
        self.assertEqual(corridor.terminal_evidence["MOF"].schedule_end_count, 2)
        self.assertEqual(corridor.terminal_evidence["MOF"].schedule_start_count, 2)


if __name__ == "__main__":
    unittest.main()
