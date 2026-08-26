"""Fahrplanbasierte Stations- und Achsentopologie, unabhaengig von ``wege``."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode, PlatformEvidence

_STATION_KEY = re.compile(r"^(?:([A-ZÄÖÜ]{2,8})\s+\S|([A-ZÄÖÜ]{2,8}?)(?=\d))")


@dataclass
class SchedulePointNode:
    raw_name: str
    occurrence_count: int = 0
    services: set[int] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduleEdge:
    source: str
    target: str
    observation_count: int = 0
    services: set[int] = field(default_factory=set)
    evidence: str = "schedule"


@dataclass
class SchedulePointGraph:
    nodes: dict[str, SchedulePointNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], ScheduleEdge] = field(default_factory=dict)
    service_paths: dict[int, tuple[str, ...]] = field(default_factory=dict)
    service_schedules: dict[int, tuple[object, ...]] = field(default_factory=dict)

    def observe(self, zid: int, raw_names: Iterable[str]) -> None:
        names = tuple(name for name in raw_names if name)
        self.service_paths[zid] = names
        for name in names:
            node = self.nodes.setdefault(name, SchedulePointNode(name))
            node.occurrence_count += 1
            node.services.add(zid)
        for source, target in zip(names, names[1:]):
            edge = self.edges.setdefault((source, target), ScheduleEdge(source, target))
            edge.observation_count += 1
            edge.services.add(zid)

    @classmethod
    def from_services(cls, services: Iterable[object]) -> "SchedulePointGraph":
        graph = cls()
        for service in services:
            if getattr(service, "service_kind", "unknown") != "train":
                continue
            schedule = getattr(service, "original_schedule", ())
            zid = getattr(service, "zid")
            graph.observe(zid, (p.planned_name or p.raw_name for p in schedule))
            graph.service_schedules[zid] = tuple(schedule)
        return graph


@dataclass(frozen=True)
class PlatformRelationGraph:
    adjacency: dict[str, frozenset[str]]
    haltepunkt_names: frozenset[str]
    station_key_support: frozenset[str]

    @classmethod
    def build(cls, platforms: Iterable[PlatformEvidence], schedule_names: set[str]) -> "PlatformRelationGraph":
        links: dict[str, set[str]] = defaultdict(set)
        haltepunkte: set[str] = set()
        supported_keys: set[str] = set()
        for item in platforms:
            if item.raw_name in schedule_names and item.metadata.get("haltepunkt", "false").lower() == "true":
                haltepunkte.add(item.raw_name)
            if item.raw_name not in schedule_names:
                continue
            for related in item.related_names:
                key = station_key(item.raw_name)
                if key and key == station_key(related):
                    supported_keys.add(key)
                if related in schedule_names:
                    links[item.raw_name].add(related); links[related].add(item.raw_name)
        return cls(
            {name: frozenset(values) for name, values in links.items()},
            frozenset(haltepunkte), frozenset(supported_keys),
        )

    def components(self) -> tuple[frozenset[str], ...]:
        result: list[frozenset[str]] = []
        seen: set[str] = set()
        for start in sorted(self.adjacency):
            if start in seen:
                continue
            stack, component = [start], set()
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name); component.add(name); stack.extend(self.adjacency.get(name, ()))
            result.append(frozenset(component))
        return tuple(result)


@dataclass(frozen=True)
class OperatingPoint:
    id: str
    display_name: str
    raw_names: tuple[str, ...]
    point_type: str
    resolution: str
    evidence: dict[str, int]
    manual_confirmation: bool = False


@dataclass(frozen=True)
class RouteAxisNode:
    id: str
    display_name: str
    operating_points: tuple[str, ...]
    raw_names: tuple[str, ...]
    x_position: float | None
    evidence: dict[str, int]
    node_type: str = "schedule_axis_node"


@dataclass
class RouteAxisGraph:
    nodes: dict[str, RouteAxisNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], ScheduleEdge] = field(default_factory=dict)
    operating_to_axis: dict[str, str] = field(default_factory=dict)

    @property
    def branch_nodes(self) -> set[str]:
        neighbours: dict[str, set[str]] = defaultdict(set)
        for source, target in self.edges:
            neighbours[source].add(target); neighbours[target].add(source)
        return {node for node, linked in neighbours.items() if len(linked) > 2}

    def to_operational_graph(self) -> OperationalRouteGraph:
        result = OperationalRouteGraph()
        for node in self.nodes.values():
            result.nodes[node.id] = OperationalRouteNode(
                node.id, node.display_name, node.raw_names, node.operating_points, "inferred",
            )
        for edge in self.edges.values():
            result.edges.append(OperationalRouteEdge(
                edge.source, edge.target, 1.0, {"schedule": edge.observation_count},
                "exact", (edge.source, edge.target),
            ))
        return result


@dataclass
class OperatingPointGraph:
    nodes: dict[str, OperatingPoint] = field(default_factory=dict)
    edges: dict[tuple[str, str], ScheduleEdge] = field(default_factory=dict)
    raw_to_operating_point: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=dict)

    @property
    def branch_nodes(self) -> set[str]:
        return self.to_route_axis_graph().branch_nodes

    def to_route_axis_graph(self) -> RouteAxisGraph:
        """Kollabiert Ein-/Ausfahrt-Aliasse, ohne OperatingPoints zu loeschen."""
        alias_groups: dict[str, list[OperatingPoint]] = defaultdict(list)
        plain_labels = {point.display_name for point in self.nodes.values()}
        entry_labels: dict[str, set[str]] = defaultdict(set)
        for point in self.nodes.values():
            for prefix in ("Einfahrt ", "Ausfahrt "):
                if point.display_name.startswith(prefix):
                    entry_labels[point.display_name[len(prefix):]].add(prefix.strip())
        for point in self.nodes.values():
            base = point.display_name
            for prefix in ("Einfahrt ", "Ausfahrt "):
                if base.startswith(prefix):
                    candidate = base[len(prefix):]
                    if candidate in plain_labels or len(entry_labels[candidate]) > 1:
                        base = candidate
            alias_groups[base].append(point)
        axis = RouteAxisGraph()
        for label, points in sorted(alias_groups.items()):
            point_ids = tuple(sorted(p.id for p in points))
            node_id = point_ids[0] if len(points) == 1 else "axis:" + label
            raw_names = tuple(sorted({name for point in points for name in point.raw_names}))
            evidence = {"operating_point": len(points)}
            if len(points) > 1:
                evidence["entry_exit_alias"] = len(points)
            # Eine X-Position entsteht erst durch einen gewaehlt linearen RoutePath.
            axis.nodes[node_id] = RouteAxisNode(node_id, label, point_ids, raw_names, None, evidence)
            for point in points:
                axis.operating_to_axis[point.id] = node_id
        for edge in self.edges.values():
            source = axis.operating_to_axis[edge.source]; target = axis.operating_to_axis[edge.target]
            if source == target:
                continue
            result = axis.edges.setdefault((source, target), ScheduleEdge(source, target))
            result.observation_count += edge.observation_count; result.services.update(edge.services)
        return axis

    def to_operational_graph(self) -> OperationalRouteGraph:
        return self.to_route_axis_graph().to_operational_graph()


def station_key(raw_name: str) -> str | None:
    """Liefert nur syntaktisch plausible Grossbuchstaben-Kuerzel als Kandidat."""
    match = _STATION_KEY.match(raw_name)
    return (match.group(1) or match.group(2)) if match else None


class OperatingPointResolver:
    """Nachvollziehbares Clustering: manual > platform/key/context > schedule-only."""

    def __init__(self, platforms: Iterable[PlatformEvidence] = (), manual: dict[str, Any] | None = None,
                 aid: int | None = None) -> None:
        self.platforms = tuple(platforms)
        self.manual = (manual or {}).get("operating_points", {})
        self.aid = aid

    def resolve(self, schedule: SchedulePointGraph) -> OperatingPointGraph:
        platform_graph = PlatformRelationGraph.build(self.platforms, set(schedule.nodes))
        groups: list[tuple[set[str], str, dict[str, int], bool]] = []
        assigned: set[str] = set()

        for point_id, values in self.manual.items():
            names = {name for name in values.get("raw_names", ()) if name in schedule.nodes}
            if names:
                groups.append((names, point_id, {"manual": 100}, True)); assigned.update(names)
        manual_groups = {point_id: (names, evidence) for names, point_id, evidence, manual in groups if manual}

        components = [set(component) - assigned for component in platform_graph.components()]
        components = [component for component in components if component]
        key_names: dict[str, set[str]] = defaultdict(set)
        for name in schedule.nodes:
            key = station_key(name)
            if key:
                key_names[key].add(name)

        # Key-Merge nur mit zusaetzlicher Evidenz: Plattformbezug oder interne Fahrplanfolge.
        internal_pairs = {(a, b) for a, b in schedule.edges if station_key(a) == station_key(b) and station_key(a)}
        schedule_neighbours: dict[str, set[str]] = defaultdict(set)
        for left, right in schedule.edges:
            schedule_neighbours[left].add(right); schedule_neighbours[right].add(left)
        shared_neighbour_keys = {
            key for key, names in key_names.items() if len(names) > 1 and any(
                schedule_neighbours[left] & schedule_neighbours[right]
                for index, left in enumerate(sorted(names)) for right in sorted(names)[index + 1:]
            )
        }
        reliable_keys = {
            key for key, names in key_names.items() if key in platform_graph.station_key_support or (
                len(names) > 1 and (
                    any(len(component & names) > 1 for component in components)
                    or any(a in names and b in names for a, b in internal_pairs)
                    or key in shared_neighbour_keys
                )
            )
        }
        for key in sorted(reliable_keys):
            names = key_names[key] - assigned
            if names:
                if key in manual_groups:
                    manual_names, manual_evidence = manual_groups[key]
                    manual_names.update(names)
                    manual_evidence["same_station_key"] = len(names)
                    assigned.update(names)
                    continue
                evidence = {"same_station_key": len(names)}
                if key in shared_neighbour_keys:
                    evidence["shared_external_neighbors"] = 1
                if any(len(component & names) > 1 for component in components):
                    evidence["platform_relation"] = sum(len(component & names) for component in components)
                groups.append((names, key, evidence, False)); assigned.update(names)

        anonymous_index = 0
        for component in components:
            names = component - assigned
            if not names:
                continue
            keys = {station_key(name) for name in names} - {None}
            point_id = next(iter(keys)) if len(keys) == 1 else f"anonymous:{self.aid or 'unknown'}:cluster_{anonymous_index}"
            anonymous_index += 1
            groups.append((names, point_id, {"platform_relation": len(names)}, False)); assigned.update(names)

        # Sandwich und geschlossene Exkursion duerfen nur bestehende, sichere
        # Cluster erweitern und niemals zwei widerspruechliche Cluster verbinden.
        owner = {name: point_id for names, point_id, _, _ in groups for name in names}
        sandwich_merges = closed_merges = 0
        changed = True
        while changed:
            changed = False
            for path in schedule.service_paths.values():
                for left, middle, right in zip(path, path[1:], path[2:]):
                    target = owner.get(left)
                    if target and target == owner.get(right) and middle not in owner:
                        key = station_key(middle)
                        repeated = len(schedule.nodes[middle].services) > 1
                        if key == target or station_key(left) == key or repeated:
                            owner[middle] = target; sandwich_merges += 1; changed = True
                for index in range(len(path) - 4):
                    a, x, y, x2, a2 = path[index:index + 5]
                    target = owner.get(a)
                    if target and a == a2 and x == x2 and x not in owner and y not in owner:
                        keys = {station_key(value) for value in (a, x, y)} - {None}
                        if len(keys) <= 1:
                            owner[x] = owner[y] = target; closed_merges += 2; changed = True
        for names, point_id, evidence, manual in groups:
            additions = {name for name, owner_id in owner.items() if owner_id == point_id} - names
            if additions:
                evidence["schedule_sandwich"] = len(additions)
                names.update(additions); assigned.update(additions)

        evidence_by_id = {point_id: evidence for _, point_id, evidence, _ in groups}
        sandwich_patterns = closed_patterns = 0
        for path in schedule.service_paths.values():
            for left, middle, right in zip(path, path[1:], path[2:]):
                target = owner.get(left)
                if target and target == owner.get(middle) == owner.get(right):
                    sandwich_patterns += 1
                    evidence_by_id[target]["schedule_sandwich"] = evidence_by_id[target].get(
                        "schedule_sandwich", 0) + 1
            for index in range(len(path) - 4):
                a, x, y, x2, a2 = path[index:index + 5]
                target = owner.get(a)
                if a == a2 and x == x2 and target and all(owner.get(item) == target for item in (x, y)):
                    closed_patterns += 1
                    evidence_by_id[target]["closed_excursion"] = evidence_by_id[target].get(
                        "closed_excursion", 0) + 1

        result = OperatingPointGraph(diagnostics={
            "station_key_clusters": len(reliable_keys),
            "platform_relation_clusters": len(components),
            "sandwich_merges": sandwich_merges + sandwich_patterns,
            "closed_excursion_merges": closed_merges + closed_patterns,
            "unprefixed_platform_clusters": sum(not any(station_key(n) for n in c) for c in components),
            "conflicting_candidates": 0,
        })
        manual_values = self.manual
        for names, point_id, evidence, manual in groups:
            values = manual_values.get(point_id, {})
            point_type = values.get("point_type", "operating_point")
            if len(names) == 1 and next(iter(names)) in platform_graph.haltepunkt_names:
                point_type = "haltpunkt"
            result.nodes[point_id] = OperatingPoint(
                point_id, values.get("display_name", point_id), tuple(sorted(names)),
                point_type, "manual" if manual else "inferred", dict(evidence), manual,
            )
            for name in names:
                result.raw_to_operating_point[name] = point_id

        for name in sorted(schedule.nodes):
            if name in result.raw_to_operating_point:
                continue
            point_type = "haltpunkt" if name in platform_graph.haltepunkt_names else (
                "entry_exit" if name.startswith(("EA ", "Einfahrt ", "Ausfahrt ")) else "virtual_schedule_point")
            point_id = "schedule:" + name
            result.nodes[point_id] = OperatingPoint(
                point_id, name, (name,), point_type, "schedule_only", {"schedule": 1}, False,
            )
            result.raw_to_operating_point[name] = point_id

        for raw_edge in schedule.edges.values():
            source = result.raw_to_operating_point[raw_edge.source]
            target = result.raw_to_operating_point[raw_edge.target]
            if source == target:
                continue
            edge = result.edges.setdefault((source, target), ScheduleEdge(source, target))
            edge.observation_count += raw_edge.observation_count; edge.services.update(raw_edge.services)
        result.diagnostics["internal_points_merged"] = sum(max(0, len(p.raw_names) - 1) for p in result.nodes.values())
        return result
