import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure import OperatingPointResolver, SchedulePointGraph, parse_bahnsteigliste
from infrastructure.operating_point_assignments import (
    OperatingPointAssignments, OperatingPointConfigStore, natural_sort_key, related_selection,
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

    def test_aid_specific_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OperatingPointConfigStore(directory)
            graph = automatic("X")
            seven = OperatingPointAssignments(); seven.rebuild(graph, ("X",), ())
            a = seven.add_point("A"); seven.assign(("X",), a); store.save(7, seven)
            other = OperatingPointAssignments(); other.rebuild(graph, ("X",), ())
            b = other.add_point("B"); other.assign(("X",), b); store.save(823, other)
            self.assertNotEqual(store.load(7)["assignments"]["X"], store.load(823)["assignments"]["X"])
            self.assertEqual(json.loads(store.path_for(7).read_text())["assignments"]["X"], a)


if __name__ == "__main__":
    unittest.main()
