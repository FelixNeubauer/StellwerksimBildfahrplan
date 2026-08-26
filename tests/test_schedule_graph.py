import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import (
    CorridorGraph, CorridorGraphBuilder, OperatingPointResolver, SchedulePointGraph,
    TriangleResolutionEvidence,
    parse_bahnsteigliste, parse_wege, station_key,
)


def service(zid, *names, kind="train", origin=None, destination=None, **metadata):
    points = [SimpleNamespace(planned_name=name, raw_name=name) for name in names]
    return SimpleNamespace(zid=zid, service_kind=kind, original_schedule=points, current_schedule=[],
                           origin=origin, destination=destination, **metadata)


def timed_service(zid, *points, origin=None, destination=None, **metadata):
    schedule = [SimpleNamespace(
        planned_name=name, raw_name=name, planned_arrival=arrival, planned_departure=departure,
    ) for name, arrival, departure in points]
    return SimpleNamespace(zid=zid, service_kind="train", original_schedule=schedule, current_schedule=[],
                           origin=origin, destination=destination, **metadata)


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
        self.assertFalse(corridor.synthetic_junctions)

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
            name: {"display_name": name, "raw_names": [name]}
            for name in ("TWVH", "TSX", "TLW", "TER", "TLM")}}
        services = [
            timed_service(1, ("TSX", "08:00:00", "08:00:00"), ("TLW", "08:02:30", "08:02:30")),
            timed_service(2, ("TSX", "08:10:00", "08:10:00"), ("TLW", "08:12:30", "08:12:30")),
            timed_service(3, ("TSX", "09:00:00", "09:00:00"), ("TLM", "09:04:30", "09:10:30"),
                          ("TLW", "09:15:00", "09:15:00")),
            timed_service(4, ("TLW", "10:00:00", "10:00:00"), ("TLM", "10:03:30", "10:09:30"),
                          ("TLW", "10:13:00", "10:13:00")),
            service(5, "TWVH", "TSX"), service(6, "TSX", "TWVH"),
            service(7, "TLW", "TER"), service(8, "TER", "TLW"),
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
        junction = corridor.synthetic_junctions["synthetic:abzw_tlm"]
        self.assertEqual(junction.display_name, "Abzw TLM")
        self.assertEqual(junction.host_edge, ("TLW", "TSX"))
        self.assertAlmostEqual(junction.edge_fraction, .3)
        self.assertEqual(junction.position_source, "travel_time_triangulation")
        self.assertEqual(junction.display_fraction, junction.topological_fraction)
        self.assertEqual(junction.display_position_source, "topology")
        self.assertEqual(corridor.branch_attachments["TLM"].attachment_type, "edge")
        self.assertEqual(corridor.axis.nodes[junction.id].node_type, "synthetic_junction_node")
        self.assertEqual(corridor.expand_axis_path(("TSX", "TLW")),
                         ("TSX", junction.id, "TLW"))
        self.assertEqual(corridor.expand_axis_path(("TSX", "TLM")),
                         ("TSX", junction.id, "TLM"))
        self.assertEqual(tuple(point.planned_name for point in services[0].original_schedule),
                         ("TSX", "TLW"))
        operational = corridor.to_operational_graph()
        links = {frozenset((edge.source, edge.target)) for edge in operational.edges}
        self.assertNotIn(frozenset(("TSX", "TLW")), links)
        self.assertTrue({frozenset(("TSX", junction.id)), frozenset((junction.id, "TLW")),
                         frozenset((junction.id, "TLM"))} <= links)
        degree = sum(junction.id in (edge.source, edge.target) for edge in operational.edges)
        self.assertEqual(degree, 3)
        self.assertEqual(corridor.node_roles[junction.id], "branch_junction")
        self.assertEqual(corridor.node_roles["TLM"], "branch_terminal")
        self.assertEqual(corridor.node_roles["TSX"], "mainline")
        self.assertEqual(corridor.node_roles["TLW"], "mainline")

    def test_edge_endpoint_position_keeps_topology_and_offsets_only_layout(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "T")}}
        services = [
            timed_service(1, ("A", "08:00:00", "08:00:00"), ("B", "08:01:40", "08:01:40")),
            timed_service(2, ("A", "09:00:00", "09:00:00"), ("T", "09:00:50", "09:00:50"),
                          ("B", "09:03:20", "09:03:20")),
            timed_service(3, ("B", "10:00:00", "10:00:00"), ("T", "10:02:30", "10:02:30"),
                          ("B", "10:05:00", "10:05:00")),
        ]
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        junction = next(iter(corridor.synthetic_junctions.values()))
        self.assertEqual(junction.topological_fraction, 0.0)
        self.assertGreater(junction.display_fraction, 0.0)
        self.assertEqual(junction.display_position_source, "layout_offset")
        self.assertEqual(corridor.junction_fraction(junction.id), 0.0)
        self.assertEqual(corridor.junction_fraction(junction.id, for_display=True),
                         junction.display_fraction)
        self.assertEqual(corridor.branch_attachments["T"].attachment_type, "edge")
        opposite = SchedulePointGraph.from_services([
            timed_service(4, ("A", "11:00:00", "11:00:00"), ("B", "11:01:40", "11:01:40")),
            timed_service(5, ("A", "12:00:00", "12:00:00"), ("T", "12:02:30", "12:02:30"),
                          ("B", "12:03:20", "12:03:20")),
            timed_service(6, ("B", "13:00:00", "13:00:00"), ("T", "13:00:50", "13:00:50"),
                          ("B", "13:01:40", "13:01:40")),
        ])
        opposite_corridor = CorridorGraphBuilder(
            opposite, OperatingPointResolver((), manual).resolve(opposite)).build()
        opposite_junction = next(iter(opposite_corridor.synthetic_junctions.values()))
        self.assertEqual(opposite_junction.topological_fraction, 1.0)
        self.assertLess(opposite_junction.display_fraction, 1.0)
        self.assertEqual(opposite_junction.display_position_source, "layout_offset")
        manual_layout = replace(junction, display_fraction=.2,
                                display_position_source="manual_layout")
        self.assertEqual(manual_layout.topological_fraction, 0.0)
        self.assertEqual(manual_layout.display_fraction, .2)

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

    def test_heidenheim_high_between_is_applied_before_branch_for_three_triangles(self):
        triples = (("THMA", "TBER", "TSON"), ("TUE", "TOLC", "TTL"),
                   ("TKS", "TIT", "THDS"))
        names = {name for triple in triples for name in triple}
        extensions = {endpoint: f"EXT_{endpoint}" for a, _, c in triples for endpoint in (a, c)}
        names.update(extensions.values())
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in names}}
        services = []; zid = 100
        for a, middle, c in triples:
            services.extend([
                timed_service(zid, (a, "08:00", "08:00"), (middle, "08:02", "08:02"),
                              (c, "08:04", "08:04")),
                timed_service(zid + 1, (c, "09:00", "09:00"), (middle, "09:02", "09:02"),
                              (a, "09:04", "09:04")),
                service(zid + 2, extensions[a], a), service(zid + 3, a, extensions[a]),
                service(zid + 4, c, extensions[c]), service(zid + 5, extensions[c], c),
            ])
            zid += 6
            for minute in range(6):
                services.extend([
                    timed_service(zid, (a, f"10:{minute:02d}", f"10:{minute:02d}"),
                                  (c, f"10:{minute + 5:02d}", f"10:{minute + 5:02d}")),
                    timed_service(zid + 1, (c, f"11:{minute:02d}", f"11:{minute:02d}"),
                                  (a, f"11:{minute + 5:02d}", f"11:{minute + 5:02d}")),
                ])
                zid += 2
        schedule = SchedulePointGraph.from_services(services)
        operating = OperatingPointResolver((), manual).resolve(schedule)
        self.assertEqual(len(operating.branch_nodes), 6)
        corridor = CorridorGraphBuilder(schedule, operating).build()
        for a, middle, c in triples:
            path = corridor.applied_between_resolutions[frozenset((a, c))]
            self.assertEqual(path[1], middle)
            for pair in ((a, c), (c, a)):
                self.assertEqual(corridor.edges[pair].classification, "skip")
                self.assertEqual(corridor.edges[pair].covered_path[1], middle)
            self.assertNotIn(f"synthetic:abzw_{middle.casefold()}", corridor.synthetic_junctions)
            self.assertNotEqual(corridor.node_roles[a], "branch_junction")
            self.assertNotEqual(corridor.node_roles[c], "branch_junction")
        self.assertFalse(corridor.branch_nodes)

    def test_halt_aware_timing_keeps_stopped_intermediate_as_between(self):
        names = ("TUE", "TOLC", "TTL", "LEFT", "RIGHT")
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in names}}
        services = [
            timed_service(1, ("TUE", "08:00", "08:00"), ("TOLC", "08:02", "08:03"),
                          ("TTL", "08:06", "08:06")),
            timed_service(2, ("TTL", "09:00", "09:00"), ("TOLC", "09:03", "09:04"),
                          ("TUE", "09:06", "09:06")),
            timed_service(3, ("TUE", "10:00", "10:00"), ("TTL", "10:03", "10:03")),
            timed_service(4, ("TTL", "11:00", "11:00"), ("TUE", "11:03", "11:03")),
            service(5, "LEFT", "TUE"), service(6, "TUE", "LEFT"),
            service(7, "TTL", "RIGHT"), service(8, "RIGHT", "TTL"),
        ]
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        comparison_key = next(key for key in corridor.halt_aware_time_comparisons if key[1] == "TOLC")
        comparison = corridor.halt_aware_time_comparisons[comparison_key]
        self.assertEqual(comparison.direct_movement_median, 180)
        self.assertEqual(comparison.via_movement_sum, 300)
        self.assertTrue(comparison.intermediate_stop_observed)
        self.assertEqual(comparison.comparison_interpretation,
                         "consistent_with_intermediate_stop")
        self.assertEqual(corridor.between_evidence[comparison_key]["confidence"], "high")
        self.assertIn(frozenset(("TUE", "TTL")), corridor.applied_between_resolutions)
        self.assertEqual(corridor.edges[("TUE", "TTL")].classification, "skip")
        self.assertNotEqual(corridor.node_roles["TUE"], "branch_junction")
        self.assertNotEqual(corridor.node_roles["TTL"], "branch_junction")
        self.assertEqual(corridor.node_roles["TOLC"], "mainline")
        selected = next(item for item in corridor.triangle_hypotheses if item.selected)
        self.assertEqual(selected.middle, "TOLC")
        self.assertGreater(selected.same_service_forward_count + selected.same_service_reverse_count, 0)
        rejected = [item for item in corridor.triangle_hypotheses
                    if frozenset(item.path) == frozenset(("TUE", "TOLC", "TTL"))
                    and item.middle != "TOLC"]
        self.assertTrue(all(item.same_service_forward_count == item.same_service_reverse_count == 0
                            for item in rejected))

    def test_pairwise_edges_do_not_invent_same_service_triple(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "C")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "B"), service(2, "B", "C"), service(3, "A", "C")])
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertTrue(all(not item.total_services
                            for item in corridor.same_service_triple_evidence.values()))
        self.assertFalse(corridor.applied_between_resolutions)
        self.assertTrue(all(item.confidence != "high" for item in corridor.triangle_resolutions))

    def test_pairwise_plus_unambiguous_raw_corridor_can_resolve_between(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "C")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "B"), service(2, "B", "A"), service(3, "B", "C"),
            service(4, "C", "B"), service(5, "A", "C"), service(6, "C", "A")])
        raw = parse_wege("""<wege>
          <e enr='1' name='A'/><e enr='2'/><e enr='3' name='B'/><e enr='4'/><e enr='5' name='C'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='3'/>
          <connector enr1='3' enr2='4'/><connector enr1='4' enr2='5'/>
        </wege>""")
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule), raw).build()
        self.assertEqual(next(iter(corridor.triangle_resolutions)).between_candidate, "B")
        self.assertIn(frozenset(("A", "C")), corridor.applied_between_resolutions)

    def test_startup_truncation_preserves_internal_same_service_order(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("TUE", "TOLC", "TTL", "TUO")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "TUE", "TOLC", "TTL", discovery_source="initial_train_list",
                    schedule_start_completeness="possibly_truncated_at_startup",
                    schedule_end_completeness="likely_complete"),
            service(2, "TUE", "TTL"),
            service(3, "TOLC", "TTL", "TUO", discovery_source="initial_train_list",
                    schedule_start_completeness="possibly_truncated_at_startup"),
        ])
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        provenance = schedule.service_provenance[1]
        self.assertFalse(provenance.start_trusted)
        self.assertEqual(provenance.internal_order_trust, "reliable")
        triple = next(item for item in corridor.same_service_triple_evidence.values()
                      if item.middle == "TOLC")
        self.assertEqual(triple.truncated_start_services, (1,))
        self.assertIn(1, triple.total_services)
        self.assertTrue(any(item.nodes == ("TOLC", "TTL", "TUO")
                            and item.internal_order_trust == "reliable"
                            for item in corridor.ordered_schedule_sequences))
        self.assertEqual(next(iter(corridor.triangle_resolutions)).between_candidate, "TOLC")

    def test_conflicting_ordered_sequences_create_topology_question(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "C")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "B", "C"), service(2, "A", "C", "B")])
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertIsNone(next(iter(corridor.triangle_resolutions)).between_candidate)
        self.assertTrue(any(item.question_type == "conflicting_ordered_schedule_sequences"
                            for item in corridor.topology_questions.values()))

    def test_between_constraints_are_order_independent_and_conflicts_become_questions(self):
        schedule = SchedulePointGraph.from_services([service(1, "A", "B", "C")])
        operating = OperatingPointResolver().resolve(schedule)
        builder = CorridorGraphBuilder(schedule, operating)
        first = TriangleResolutionEvidence(
            ("A", "B", "C"), "B", "high", ("chain_schedule_support",), (),
            ("A", "C"), (("A", "B"), ("B", "C")))
        other = TriangleResolutionEvidence(
            ("D", "E", "F"), "E", "high", ("chain_schedule_support",), (),
            ("D", "F"), (("D", "E"), ("E", "F")))
        outcomes = []
        for resolutions in ((first, other), (other, first)):
            graph = CorridorGraph(operating.to_route_axis_graph())
            graph.triangle_resolutions.extend(resolutions)
            required, forbidden = builder._compile_between_constraints(graph)
            outcomes.append((required, forbidden, graph.applied_between_resolutions,
                             graph.between_constraints))
        self.assertEqual(outcomes[0], outcomes[1])

        conflict = TriangleResolutionEvidence(
            ("A", "B", "C"), "C", "high", ("competing_interpretation",), (),
            ("A", "B"), (("A", "C"), ("B", "C")))
        graph = CorridorGraph(operating.to_route_axis_graph())
        graph.triangle_resolutions.extend((first, conflict))
        required, forbidden = builder._compile_between_constraints(graph)
        self.assertFalse(required); self.assertFalse(forbidden)
        self.assertTrue(all(item.status == "conflicting" for item in graph.between_constraints.values()))
        self.assertTrue(all(question.question_type == "conflicting_between_constraints"
                            for question in graph.topology_questions.values()))

    def test_hidden_external_boundaries_use_service_endpoints_and_raw_connectors(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("TTL", "TUO")}}
        services = [
            service(1, "TTL", "TUO", destination="Ulm Hbf"),
            service(2, "TTL", "TUO", destination="Ulm Hbf"),
            service(3, "TUO", "TTL", origin="Ulm Hbf"),
            service(4, "TTL", "TUO", destination="Ulm Rbf"),
            service(5, "TTL", "TUO", destination="Ulm Rbf"),
            service(6, "TUO", "TTL", origin="Ulm Rbf"),
        ]
        raw = parse_wege("""<wege>
          <e enr='1' name='TTL'/><e enr='2'/><e enr='3' name='TUO'/><e enr='4'/>
          <e enr='5' name='Ulm Hbf'/><e enr='6' name='Ulm Rbf'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='3'/>
          <connector enr1='3' enr2='4'/><connector enr1='4' enr2='5'/>
          <connector enr1='4' enr2='6'/>
        </wege>""")
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule), raw).build()
        self.assertEqual(corridor.node_roles["TUO"], "boundary_adjacent")
        self.assertNotEqual(corridor.node_roles["TUO"], "terminal")
        self.assertEqual({item.external_name for item in corridor.synthetic_external_boundaries.values()},
                         {"Ulm Hbf", "Ulm Rbf"})
        self.assertTrue(all(item.directionality == "bidirectional"
                            for item in corridor.synthetic_external_boundaries.values()))
        operational = corridor.to_operational_graph()
        self.assertTrue(all(operational.nodes[item.id].node_type == "synthetic_external_boundary"
                            for item in corridor.synthetic_external_boundaries.values()))

    def test_internal_endpoint_targets_are_resolved_before_boundary_questions(self):
        manual = {"operating_points": {
            "THD": {"display_name": "THD", "raw_names": ["THD1", "THD2"]},
            "TNS": {"display_name": "TNS", "raw_names": ["TNS1", "TNS2"]},
            "A": {"display_name": "A", "raw_names": ["A"]},
        }}
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "THD2", destination="Gleis THD1"),
            service(2, "A", "TNS2", destination="Gleis TNS1"),
            service(3, "THD1", "A"), service(4, "TNS1", "A"),
        ])
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        resolutions = list(corridor.external_target_resolutions.values())
        self.assertEqual({item.classification for item in resolutions},
                         {"same_operating_point_internal"})
        self.assertEqual({item.original_target for item in resolutions},
                         {"Gleis THD1", "Gleis TNS1"})
        self.assertFalse(corridor.hidden_boundary_evidence)
        self.assertFalse(corridor.synthetic_external_boundaries)
        self.assertFalse(corridor.topology_questions)

    def test_ambiguous_single_external_endpoint_prepares_topology_question(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "X")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "A", "X", destination="External Unknown")])
        corridor = CorridorGraphBuilder(schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertFalse(corridor.synthetic_external_boundaries)
        question = next(iter(corridor.topology_questions.values()))
        self.assertEqual(question.question_type, "terminal_or_external_boundary")
        self.assertEqual(question.status, "needs_user_confirmation")

    def test_untrusted_start_origin_is_deferred_but_destination_remains_usable(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "THD", "TUO")}}
        schedule = SchedulePointGraph.from_services([
            service(1, "THD", "A", origin="Aalen",
                    discovery_source="initial_train_list",
                    schedule_start_completeness="possibly_truncated_at_startup",
                    schedule_end_completeness="likely_complete"),
            service(2, "A", "TUO", destination="External Unknown",
                    discovery_source="initial_train_list",
                    schedule_start_completeness="possibly_truncated_at_startup",
                    schedule_end_completeness="likely_complete"),
        ])
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertEqual(len(corridor.ignored_endpoint_observations), 1)
        self.assertEqual(corridor.ignored_endpoint_observations[0]["external_name"], "Aalen")
        self.assertEqual(len(corridor.deferred_questions), 1)
        self.assertFalse(any("Aalen" in question.question_text
                             for question in corridor.topology_questions.values()))
        self.assertTrue(any("External Unknown" in question.question_text
                            for question in corridor.topology_questions.values()))

    def test_raw_continuation_rejects_observed_terminal_but_mof_remains_terminal(self):
        manual = {"operating_points": {
            "TAU": {"display_name": "TAU", "raw_names": ["TAU"]},
            "TAT": {"display_name": "TAT", "raw_names": ["TAT 1", "TAT 2"]},
        }}
        schedule = SchedulePointGraph.from_services([
            service(1, "TAU", "TAT 1"), service(2, "TAU", "TAT 2"),
            service(3, "TAT 1", "TAU"), service(4, "TAT 2", "TAU"),
        ])
        raw = parse_wege("""<wege>
          <e enr='1' name='TAU'/><e enr='2'/><e enr='3' name='TAT 1'/>
          <e enr='4' name='TAT 2'/><e enr='5'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='3'/>
          <connector enr1='3' enr2='4'/><connector enr1='4' enr2='5'/>
        </wege>""")
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule), raw).build()
        self.assertEqual(corridor.node_roles["TAT"], "observed_schedule_boundary")
        self.assertIn("raw_external_continuation",
                      corridor.terminal_evidence["TAT"].contradicting_terminal_evidence)
        self.assertEqual(corridor.terminal_evidence["TAT"].raw_outgoing_corridors, 2)

    def test_raw_junction_projection_precedes_travel_time_position(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "T")}}
        services = [
            timed_service(1, ("A", "08:00", "08:00"), ("B", "08:03", "08:03")),
            timed_service(2, ("A", "09:00", "09:00"), ("T", "09:04", "09:04"),
                          ("B", "09:08", "09:08")),
            timed_service(3, ("B", "10:00", "10:00"), ("T", "10:04", "10:04"),
                          ("B", "10:08", "10:08")),
        ]
        raw = parse_wege("""<wege>
          <e enr='1' name='A'/><e enr='2'/><e enr='3' name='B'/><e enr='4' name='T'/>
          <connector enr1='1' enr2='2'/><connector enr1='2' enr2='3'/>
          <connector enr1='2' enr2='4'/>
        </wege>""")
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule), raw).build()
        junction = next(iter(corridor.synthetic_junctions.values()))
        self.assertEqual(junction.position_source, "raw_and_travel_time")
        self.assertEqual(junction.raw_junction_node, "enr:2")
        self.assertEqual(junction.edge_fraction, .5)

    def test_implausible_time_triangulation_stays_unresolved_without_clamping(self):
        manual = {"operating_points": {
            name: {"display_name": name, "raw_names": [name]} for name in ("A", "B", "T")}}
        services = [
            timed_service(1, ("A", "08:00:00", "08:00:00"), ("B", "08:02:30", "08:02:30")),
            timed_service(2, ("A", "09:00", "09:00"), ("T", "09:01", "09:01"),
                          ("B", "09:06", "09:06")),
            timed_service(3, ("B", "10:00", "10:00"), ("T", "10:05", "10:05"),
                          ("B", "10:10", "10:10")),
        ]
        schedule = SchedulePointGraph.from_services(services)
        corridor = CorridorGraphBuilder(
            schedule, OperatingPointResolver((), manual).resolve(schedule)).build()
        self.assertFalse(corridor.synthetic_junctions)
        self.assertEqual(corridor.branch_attachments["T"].attachment_type, "unresolved")
        estimate = corridor.junction_position_estimates["T"]
        self.assertIsNone(estimate.edge_fraction)
        self.assertEqual(estimate.confidence, "unresolved")

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
