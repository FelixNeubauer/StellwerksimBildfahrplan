import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import (OperatingPoint, OperatingPointGraph, OperatingPointResolver,
                            SchedulePointGraph, parse_bahnsteigliste, parse_wege)
from infrastructure.operating_point_assignments import (
    InvalidAssignment, OperatingPointAssignments, OperatingPointConfigStore, can_assign_kind,
    entry_points_from_raw_graph, natural_sort_key, related_selection,
)


class Point:
    def __init__(self, name): self.planned_name = name; self.raw_name = name


class Service:
    service_kind = "train"
    origin = destination = None
    discovery_source = "test"
    discovered_simtime = None
    schedule_start_completeness = schedule_end_completeness = "likely_complete"
    provenance_evidence = ()

    def __init__(self, zid, *names): self.zid = zid; self.original_schedule = tuple(Point(n) for n in names)


def automatic(*names, platforms=()):
    schedule = SchedulePointGraph.from_services((Service(1, *names),))
    return OperatingPointResolver(platforms).resolve(schedule)


class AssignmentLogicTests(unittest.TestCase):
    def test_topology_eligibility_filters_empty_and_automatic_schedule_only_targets(self):
        empty_graph = OperatingPointGraph(nodes={
            "empty": OperatingPoint("empty", "Empty", (), "station", "inferred", {})})
        empty = OperatingPointAssignments(); empty.rebuild(empty_graph, (), ())
        self.assertFalse(empty.topology_eligibility("empty").eligible)

        schedule = OperatingPointAssignments(); schedule.rebuild(automatic("Optional"), ("Optional",), ())
        target = schedule.assignments["Optional"]
        self.assertEqual(schedule.sources["Optional"], "automatic")
        self.assertFalse(schedule.topology_eligibility(target).eligible)

    def test_topology_eligibility_accepts_platform_self_haltpunkt_and_manual_schedule(self):
        platform = OperatingPointAssignments()
        platform.rebuild(automatic("TRM 1"), ("TRM 1",), (),
                         raw_item_kinds={"TRM 1": "platform_or_haltpunkt"})
        self.assertTrue(platform.topology_eligibility(platform.assignments["TRM 1"]).eligible)

        halt = OperatingPointAssignments(); halt.rebuild(
            automatic("Martinszell"), ("Martinszell",), ("Martinszell",),
            raw_item_kinds={"Martinszell": "platform_or_haltpunkt"})
        self.assertEqual(halt.sources["Martinszell"], "self_haltpunkt")
        self.assertTrue(halt.topology_eligibility(halt.assignments["Martinszell"]).eligible)

        manual_schedule = OperatingPointAssignments()
        manual_schedule.rebuild(automatic("Optional"), ("Optional",), ())
        destination = next(iter(manual_schedule.points))
        manual_schedule.assign(("Optional",), destination)
        self.assertTrue(manual_schedule.topology_eligibility(destination).eligible)

    def test_topology_eligibility_keeps_empty_manual_point_and_active_entry(self):
        model = OperatingPointAssignments(); model.rebuild(automatic("X"), ("X",), ())
        manual = model.add_point("ManualX")
        self.assertTrue(model.topology_eligibility(manual).eligible)
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Aalen' enr='1'/></wege>"))
        entry_id = next(iter(entries)); entry_model = OperatingPointAssignments()
        entry_model.rebuild(automatic("Aalen"), ("Aalen",), (), entry_points=entries,
                            raw_item_kinds={"Aalen": "entry"},
                            automatic_entry_assignments={"Aalen": entry_id})
        self.assertTrue(entry_model.topology_eligibility(entry_id).eligible)

    def test_equal_automatic_targets_merge_but_manual_names_remain_distinct(self):
        graph = OperatingPointGraph(
            nodes={
                "TKS": OperatingPoint("TKS", "TKS", ("TKS 1",), "station", "exact", {}),
                "schedule:TKS": OperatingPoint("schedule:TKS", "TKS", ("TKS 2",), "station", "exact", {}),
            }, raw_to_operating_point={"TKS 1": "TKS", "TKS 2": "schedule:TKS"})
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TKS 1", "TKS 2"), ())
        self.assertEqual(list(model.points), ["TKS"])
        self.assertEqual(set(model.assignments.values()), {"TKS"})
        one = model.add_point("TKS"); two = model.add_point("TKS")
        self.assertNotEqual(one, two)

    def test_target_tombstones_survive_refresh_and_explicit_restore(self):
        graph = automatic("TKS 1", "TKS 2")
        model = OperatingPointAssignments(); model.rebuild(graph, ("TKS 1", "TKS 2"), ())
        target = next(iter(model.points)); model.delete_point(target)
        config = model.to_config()
        refreshed = OperatingPointAssignments(); refreshed.rebuild(graph, ("TKS 1", "TKS 2"), (), config)
        self.assertFalse(refreshed.points)
        config["deleted_automatic_point_ids"] = []
        config["deleted_automatic_identities"] = []
        restored = OperatingPointAssignments(); restored.rebuild(graph, ("TKS 1", "TKS 2"), (), config)
        self.assertTrue(restored.points)

    def test_bulk_delete_preserves_hidden_manual_definition(self):
        model = OperatingPointAssignments(); model.rebuild(automatic("TKS 1"), ("TKS 1",), ())
        manual = model.add_point("Nicht rekonstruierbar")
        model.delete_all_points()
        self.assertFalse(model.points)
        config = model.to_config()
        self.assertEqual(config["operating_points"][manual]["display_name"], "Nicht rekonstruierbar")
        self.assertIn(manual, config["hidden_manual_point_ids"])

    def test_completion_only_blocks_required_raw_kinds_and_empty_entries(self):
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Aalen' enr='1'/></wege>"))
        entry_id = next(iter(entries)); graph = automatic("TKS 1", "Optional", "Aalen")
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TKS 1", "Optional", "Aalen"), (), entry_points=entries,
                      raw_item_kinds={"TKS 1": "platform_or_haltpunkt",
                                      "Optional": "schedule_point", "Aalen": "entry"})
        state = model.completeness()
        self.assertEqual((state.unassigned_entry_count, state.empty_entry_point_count), (1, 1))
        self.assertFalse(state.is_complete)
        model.assign(("Aalen",), entry_id)
        self.assertTrue(model.completeness().is_complete)

    def test_type_six_and_seven_create_one_lossless_entry_point(self):
        raw = parse_wege("""<wege>
            <shape type='6' name='Friedrichshafen' enr='101' extra='in'/>
            <shape type='6' name='Friedrichshafen' enr='103' extra='second'/>
            <shape type='7' name='Friedrichshafen' enr='102' extra='out'/>
        </wege>""")
        entries = entry_points_from_raw_graph(raw)
        self.assertEqual(len(entries), 1)
        point = next(iter(entries.values()))
        self.assertEqual(point.display_name, "Friedrichshafen")
        self.assertEqual({item.enr for item in point.infrastructure_elements}, {"101", "102", "103"})
        self.assertEqual({item.element_type for item in point.infrastructure_elements}, {"6", "7"})
        self.assertEqual({item.metadata["extra"] for item in point.infrastructure_elements},
                         {"in", "second", "out"})

    def test_different_external_names_create_different_entry_points(self):
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Friedrichshafen' enr='1'/>"
            "<shape type='7' name='Ulm Hbf' enr='2'/></wege>"))
        self.assertEqual({item.display_name for item in entries.values()}, {"Friedrichshafen", "Ulm Hbf"})

    def test_assignment_kind_matrix(self):
        self.assertTrue(can_assign_kind("platform_or_haltpunkt", "operating_point"))
        self.assertTrue(can_assign_kind("schedule_point", "operating_point"))
        self.assertFalse(can_assign_kind("entry", "operating_point"))
        for kind in ("platform_or_haltpunkt", "schedule_point", "entry"):
            self.assertTrue(can_assign_kind(kind, "entry_point"))

    def test_entry_raw_is_not_operating_point_and_self_entry_is_created(self):
        graph = automatic("Einfahrt Friedrichshafen", "TAT 1")
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Friedrichshafen' enr='1'/></wege>"))
        entry_id = next(iter(entries))
        model = OperatingPointAssignments()
        model.rebuild(
            graph, ("Einfahrt Friedrichshafen", "TAT 1"), (), entry_points=entries,
            raw_item_kinds={"Einfahrt Friedrichshafen": "entry", "TAT 1": "schedule_point"},
            automatic_entry_assignments={"Einfahrt Friedrichshafen": entry_id})
        self.assertNotIn("schedule:Einfahrt Friedrichshafen", model.points)
        self.assertEqual(model.assignments["Einfahrt Friedrichshafen"], entry_id)
        self.assertEqual(model.sources["Einfahrt Friedrichshafen"], "self_entry")

    def test_stale_automatic_snapshot_does_not_restore_empty_station_shadow(self):
        graph = automatic("TKS 1", "TKS 2", "TKS 3", "TKS 4")
        config = {"editor_snapshot": {
            "raw_names": ["TKS 1", "TKS 2", "TKS 3", "TKS 4"],
            "operating_points": {
                "schedule:TKS": {"display_name": "TKS", "station_key": "TKS", "source": "automatic"}},
            "assignments": {}}}
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TKS 1", "TKS 2", "TKS 3", "TKS 4"), (), config)
        self.assertEqual([(point.id, point.display_name) for point in model.points.values()], [("TKS", "TKS")])
        self.assertEqual(sum(owner == "TKS" for owner in model.assignments.values()), 4)

    def test_schema_operating_point_shadow_is_merged_after_config_overlay(self):
        graph = automatic("TRM", "TRM1", "TRM2")
        config = {
            "manual_point_ids": [],
            "operating_points": {
                "schedule:TRM": {"display_name": "TRM", "station_key": "TRM", "removable": False}},
            "editor_snapshot": {
                "raw_names": ["TRM", "TRM1", "TRM2"],
                "operating_points": {
                    "schedule:TRM": {"display_name": "TRM", "station_key": "TRM",
                                     "source": "automatic"}},
                "assignments": {"TRM": {"target": "schedule:TRM", "source": "automatic"}},
            },
        }
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TRM", "TRM1", "TRM2"), (), config,
                      raw_item_kinds={name: "platform_or_haltpunkt"
                                      for name in ("TRM", "TRM1", "TRM2")})
        matching = [point for point in model.points.values() if point.display_name == "TRM"]
        self.assertEqual(len(matching), 1)
        self.assertNotIn("schedule:TRM", model.points)
        self.assertEqual({model.assignments[name] for name in ("TRM", "TRM1", "TRM2")},
                         {matching[0].id})

    def test_manual_points_with_equal_display_name_are_preserved(self):
        graph = automatic("TKS 1", "TKS 2")
        config = {"editor_snapshot": {"raw_names": ["TKS 1", "TKS 2"], "operating_points": {
            "manual:one": {"display_name": "TKS", "source": "manual"},
            "manual:two": {"display_name": "TKS", "source": "manual"}}, "assignments": {}},
            "manual_point_ids": ["manual:one", "manual:two"], "operating_points": {
                "manual:one": {"display_name": "TKS", "removable": True},
                "manual:two": {"display_name": "TKS", "removable": True}}}
        model = OperatingPointAssignments(); model.rebuild(graph, ("TKS 1", "TKS 2"), (), config)
        self.assertTrue({"manual:one", "manual:two"}.issubset(model.points))

    def test_type_six_seven_seed_is_one_self_entry_raw_member(self):
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Aalen' enr='101'/>"
            "<shape type='7' name='Aalen' enr='102'/></wege>"))
        entry_id = next(iter(entries)); graph = automatic("Aalen")
        model = OperatingPointAssignments()
        model.rebuild(graph, ("Aalen",), (), entry_points=entries,
                      raw_item_kinds={"Aalen": "entry"},
                      automatic_entry_assignments={"Aalen": entry_id})
        self.assertEqual(len(model.entry_points), 1)
        self.assertEqual([name for name, item in model.raw_items.items() if item.kind == "entry"], ["Aalen"])
        self.assertEqual((model.assignments["Aalen"], model.sources["Aalen"]), (entry_id, "self_entry"))
        self.assertEqual(sum(owner == entry_id for owner in model.assignments.values()), 1)
        self.assertEqual(len(model.entry_points[entry_id].infrastructure_elements), 2)

    def test_assignment_is_atomic_when_entry_is_dropped_on_operating_point(self):
        graph = automatic("TAT 1", "Einfahrt Friedrichshafen")
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='Friedrichshafen' enr='1'/></wege>"))
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TAT 1", "Einfahrt Friedrichshafen"), (), entry_points=entries,
                      raw_item_kinds={"TAT 1": "platform_or_haltpunkt",
                                      "Einfahrt Friedrichshafen": "entry"})
        target = model.add_point("TAU")
        previous = dict(model.assignments)
        with self.assertRaises(InvalidAssignment):
            model.assign(("TAT 1", "Einfahrt Friedrichshafen"), target)
        self.assertEqual(model.assignments, previous)

    def test_entry_target_accepts_entry_schedule_and_platform(self):
        names = ("Einfahrt Friedrichshafen", "Grenzpunkt", "TAT 1")
        graph = automatic(*names)
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='7' name='Friedrichshafen' enr='1'/></wege>"))
        entry_id = next(iter(entries)); model = OperatingPointAssignments()
        model.rebuild(graph, names, (), entry_points=entries,
                      raw_item_kinds={names[0]: "entry", names[1]: "schedule_point",
                                      names[2]: "platform_or_haltpunkt"})
        model.assign(names, entry_id)
        self.assertEqual({model.assignments[name] for name in names}, {entry_id})
        self.assertEqual({model.sources[name] for name in names}, {"manual_entry"})
        config = model.to_config()
        self.assertEqual(set(config["assignments"].values()), {entry_id})
        self.assertEqual(set(config["assignment_sources"].values()), {"manual_entry"})

    def test_clear_keeps_evidence_based_self_assignments(self):
        graph = automatic("Martinszell", "Einfahrt F")
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='F' enr='1'/></wege>")); entry_id = next(iter(entries))
        model = OperatingPointAssignments()
        model.rebuild(graph, ("Martinszell", "Einfahrt F"), ("Martinszell",), entry_points=entries,
                      raw_item_kinds={"Martinszell": "platform_or_haltpunkt", "Einfahrt F": "entry"},
                      automatic_entry_assignments={"Einfahrt F": entry_id})
        model.clear_editable_assignments()
        self.assertEqual(model.sources["Martinszell"], "self_haltpunkt")
        self.assertEqual(model.sources["Einfahrt F"], "self_entry")

    def test_schema_three_roundtrip_preserves_entries_kinds_and_sources(self):
        graph = automatic("Einfahrt F")
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='F' enr='1' custom='raw'/></wege>")); entry_id = next(iter(entries))
        model = OperatingPointAssignments()
        model.rebuild(graph, ("Einfahrt F",), (), entry_points=entries,
                      raw_item_kinds={"Einfahrt F": "entry"},
                      automatic_entry_assignments={"Einfahrt F": entry_id})
        config = model.to_config()
        self.assertEqual(config["schema_version"], 3)
        restored = OperatingPointAssignments()
        restored.rebuild(graph, (), (), config)
        self.assertEqual(restored.raw_items["Einfahrt F"].kind, "entry")
        self.assertEqual(restored.sources["Einfahrt F"], "self_entry")
        self.assertEqual(next(iter(restored.entry_points.values())).infrastructure_elements[0].metadata["custom"],
                         "raw")

    def test_legacy_manual_operating_point_is_not_migrated_by_name(self):
        graph = automatic("Einfahrt F")
        legacy = {"schema_version": 2, "manual_point_ids": ["manual:einfahrt-f"],
                  "operating_points": {"manual:einfahrt-f": {
                      "display_name": "Einfahrt F", "raw_names": [], "removable": True}},
                  "assignments": {}, "unassigned": []}
        entries = entry_points_from_raw_graph(parse_wege(
            "<wege><shape type='6' name='F' enr='1'/></wege>"))
        model = OperatingPointAssignments()
        model.rebuild(graph, ("Einfahrt F",), (), legacy, entry_points=entries,
                      raw_item_kinds={"Einfahrt F": "entry"})
        self.assertIn("manual:einfahrt-f", model.points)
        self.assertIn("manual:einfahrt-f", model.manual_point_ids)

    def test_natural_sort(self):
        self.assertEqual(sorted(("TBL 10", "TBL 2", "TBL 1"), key=natural_sort_key),
                         ["TBL 1", "TBL 2", "TBL 10"])

    def test_prefix_selection(self):
        names = ("TBL 1", "TBL 2", "TBL 4N", "TBL Wende", "MIMS 1", "3", "3N")
        self.assertEqual(related_selection(names, ("TBL 1",)), set(names[:4]))

    def test_unprefixed_selection_has_no_prefixed_members(self):
        names = ("1", "2", "3", "3N", "3S", "5a", "5b", "TBL 1", "TU 3")
        self.assertEqual(related_selection(names, ("3",)), set(names[:7]))

    def test_haltpunkt_self_assignment_without_invented_track(self):
        platforms = parse_bahnsteigliste(
            "<bahnsteigliste><bahnsteig name='Martinszell' haltepunkt='true'/></bahnsteigliste>")
        graph = automatic("Martinszell", platforms=platforms)
        model = OperatingPointAssignments()
        model.rebuild(graph, ("Martinszell",), ("Martinszell",))
        self.assertEqual(model.assignments["Martinszell"], "schedule:Martinszell")
        self.assertEqual(model.sources["Martinszell"], "self_haltpunkt")
        self.assertEqual(graph.nodes["schedule:Martinszell"].raw_names, ("Martinszell",))

    def test_manual_override_survives_auto_rebuild_and_remove(self):
        graph = automatic("X")
        model = OperatingPointAssignments(); model.rebuild(graph, ("X",), ())
        target = model.add_point("B"); model.assign(("X",), target)
        config = model.to_config()
        rebuilt = OperatingPointAssignments(); rebuilt.rebuild(graph, ("X",), (), config)
        self.assertEqual((rebuilt.assignments["X"], rebuilt.sources["X"]), (target, "manual"))
        rebuilt.remove_assignments(("X",))
        self.assertIn("X", rebuilt.unassigned)
        again = OperatingPointAssignments(); again.rebuild(graph, ("X",), (), rebuilt.to_config())
        self.assertIn("X", again.unassigned)

    def test_existing_config_does_not_replace_live_auto_assignments(self):
        graph = automatic("TBL 1", "TBL 2", "TBL 3")
        config = {"schema_version": 1, "manual_point_ids": [], "operating_points": {},
                  "assignments": {}, "unassigned": []}
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), (), config)
        self.assertEqual({model.assignments[name] for name in model.all_raw_names}, {"TBL"})

    def test_station_key_extends_strong_point_to_platform_only_names(self):
        graph = automatic("TBL 1", "TBL 2", "MIMS 1")
        names = ("TBL 1", "TBL 2", "TBL 3", "TBL 9", "MIMS 1")
        model = OperatingPointAssignments(); model.rebuild(graph, names, ())
        self.assertEqual({model.assignments[name] for name in names[:4]}, {"TBL"})
        self.assertNotEqual(model.assignments["MIMS 1"], "TBL")
        self.assertEqual(model.sources["TBL 9"], "automatic_station_key")

    def test_explicit_auto_clears_tombstone_but_keeps_positive_override(self):
        graph = automatic("TBL 1", "TBL 2", "TBL 3")
        model = OperatingPointAssignments(); model.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), ())
        special = model.add_point("Sonderbetriebsstelle")
        model.assign(("TBL 2",), special)
        model.remove_assignments(("TBL 3",))
        config = model.to_config()
        normal = OperatingPointAssignments()
        normal.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), (), config)
        self.assertIn("TBL 3", normal.unassigned)
        explicit = OperatingPointAssignments()
        explicit.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), (), config,
                         respect_unassigned=False)
        self.assertEqual(explicit.assignments["TBL 3"], "TBL")
        self.assertEqual(explicit.assignments["TBL 2"], special)

    def test_auto_after_clear_restores_assignments(self):
        graph = automatic("TBL 1", "TBL 2", "TBL 3")
        model = OperatingPointAssignments(); model.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), ())
        model.clear_editable_assignments()
        config = model.to_config()
        self.assertEqual(set(config["unassigned"]), {"TBL 1", "TBL 2", "TBL 3"})
        model.rebuild(graph, ("TBL 1", "TBL 2", "TBL 3"), (), config,
                      respect_unassigned=False)
        self.assertEqual({model.assignments[name] for name in model.all_raw_names}, {"TBL"})

    def test_multiselect_assign_and_reassign_use_one_manual_path(self):
        graph = automatic("X", "Y")
        model = OperatingPointAssignments(); model.rebuild(graph, ("X", "Y"), ())
        a = model.add_point("A"); b = model.add_point("B")
        model.assign(("X", "Y"), a); model.assign(("X", "Y"), b)
        self.assertEqual({model.assignments[name] for name in ("X", "Y")}, {b})
        self.assertEqual({model.sources[name] for name in ("X", "Y")}, {"manual"})

    def test_automatic_source_in_legacy_config_is_not_manual_override(self):
        graph = automatic("TBL 1", "TBL 2")
        config = {"operating_points": {"old": {
            "display_name": "old", "raw_names": ["TBL 1"], "assignment_source": "automatic"}}}
        model = OperatingPointAssignments(); model.rebuild(graph, ("TBL 1", "TBL 2"), (), config)
        self.assertEqual(model.assignments["TBL 1"], "TBL")
        self.assertNotIn("old", model.manual_point_ids)

    def test_aid_specific_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OperatingPointConfigStore(directory)
            graph = automatic("X")
            seven = OperatingPointAssignments(); seven.rebuild(graph, ("X",), ())
            a = seven.add_point("A"); seven.assign(("X",), a); store.save(7, "Sieben", seven)
            other = OperatingPointAssignments(); other.rebuild(graph, ("X",), ())
            b = other.add_point("B"); other.assign(("X",), b); store.save(823, "Achthundert", other)
            self.assertNotEqual(store.load(7)["assignments"]["X"], store.load(823)["assignments"]["X"])
            saved = json.loads(store.path_for(7).read_text())
            self.assertEqual(saved["assignments"]["X"], a)
            self.assertEqual((saved["aid"], saved["stellwerk_name"], saved["artifact_type"]),
                             (7, "Sieben", "operating_points"))

    def test_full_snapshot_roundtrip_preserves_sources(self):
        graph = automatic("TBL 1", "TBL 2", "Martinszell")
        model = OperatingPointAssignments()
        model.rebuild(graph, ("TBL 1", "TBL 2", "TBL 9", "Martinszell", "OFFEN"), ("Martinszell",))
        special = model.add_point("Sonderanschluss"); model.assign(("TBL 9",), special)
        model.remove_assignments(("OFFEN",))
        config = model.to_config()
        restored = OperatingPointAssignments(); restored.rebuild(graph, (), ("Martinszell",), config)
        self.assertEqual(restored.sources["TBL 1"], model.sources["TBL 1"])
        self.assertEqual(restored.sources["Martinszell"], "self_haltpunkt")
        self.assertEqual(restored.sources["TBL 9"], "manual")
        self.assertIn("OFFEN", restored.unassigned); self.assertIn(special, restored.points)


if __name__ == "__main__":
    unittest.main()
