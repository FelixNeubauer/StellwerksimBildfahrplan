"""Fahrplanbasierte betriebliche Topologie, unabhaengig von ``wege``."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode, PlatformEvidence


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

    def observe(self, zid: int, raw_names: Iterable[str]) -> None:
        names = [name for name in raw_names if name]
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
            graph.observe(getattr(service, "zid"), (p.planned_name or p.raw_name for p in schedule))
        return graph


@dataclass(frozen=True)
class OperatingPoint:
    id: str
    display_name: str
    raw_names: tuple[str, ...]
    point_type: str
    resolution: str
    evidence: tuple[str, ...]
    manual_confirmation: bool = False


@dataclass
class OperatingPointGraph:
    nodes: dict[str, OperatingPoint] = field(default_factory=dict)
    edges: dict[tuple[str, str], ScheduleEdge] = field(default_factory=dict)
    raw_to_operating_point: dict[str, str] = field(default_factory=dict)

    @property
    def branch_nodes(self) -> set[str]:
        neighbours: dict[str, set[str]] = defaultdict(set)
        for source, target in self.edges:
            neighbours[source].add(target); neighbours[target].add(source)
        return {node for node, linked in neighbours.items() if len(linked) > 2}

    def to_operational_graph(self) -> OperationalRouteGraph:
        result = OperationalRouteGraph()
        for point in self.nodes.values():
            result.nodes[point.id] = OperationalRouteNode(
                point.id, point.display_name, point.raw_names, point.raw_names,
                "exact" if point.manual_confirmation else point.resolution,
            )
        for edge in self.edges.values():
            result.edges.append(OperationalRouteEdge(
                edge.source, edge.target, 1.0,
                {"schedule": edge.observation_count}, "exact", (edge.source, edge.target),
            ))
        return result


class OperatingPointResolver:
    """Priorisiert manuelle Config, dann explizite Bahnsteig-Verwandtschaft.

    Nicht verbundene Namen bleiben eigenstaendige, gueltige Fahrplanpunkte.
    Es gibt bewusst keine Praefix- oder Suffixheuristik.
    """

    def __init__(self, platforms: Iterable[PlatformEvidence] = (), manual: dict[str, Any] | None = None) -> None:
        self.platforms = tuple(platforms)
        self.manual = (manual or {}).get("operating_points", {})

    def resolve(self, schedule: SchedulePointGraph) -> OperatingPointGraph:
        result = OperatingPointGraph()
        assigned: set[str] = set()
        for point_id, values in self.manual.items():
            names = tuple(name for name in values.get("raw_names", ()) if name in schedule.nodes)
            if not names:
                continue
            result.nodes[point_id] = OperatingPoint(
                point_id, values.get("display_name", point_id), names,
                values.get("point_type", "operating_point"), "manual", ("manual",), True,
            )
            for name in names:
                result.raw_to_operating_point[name] = point_id
            assigned.update(names)

        # Bahnsteiglisten-Komponenten sind starke, explizite Verwandtschaftsevidenz.
        adjacency: dict[str, set[str]] = defaultdict(set)
        for item in self.platforms:
            if item.raw_name not in schedule.nodes:
                continue
            for related in item.related_names:
                if related in schedule.nodes:
                    adjacency[item.raw_name].add(related); adjacency[related].add(item.raw_name)
        seen: set[str] = set()
        for start in sorted(adjacency):
            if start in seen or start in assigned:
                continue
            stack, component = [start], set()
            while stack:
                name = stack.pop()
                if name in seen or name in assigned:
                    continue
                seen.add(name); component.add(name); stack.extend(adjacency[name])
            if len(component) < 2:
                continue
            point_id = "auto:" + min(component)
            names = tuple(sorted(component))
            result.nodes[point_id] = OperatingPoint(
                point_id, min(component), names, "operating_point", "inferred", ("bahnsteigliste",), False,
            )
            for name in names:
                result.raw_to_operating_point[name] = point_id
            assigned.update(names)

        for name in sorted(schedule.nodes):
            if name in assigned:
                continue
            point_type = "entry_exit" if name.startswith(("EA ", "Einfahrt ", "Ausfahrt ")) else "virtual_schedule_point"
            point_id = "schedule:" + name
            result.nodes[point_id] = OperatingPoint(
                point_id, name, (name,), point_type, "schedule_only", ("schedule",), False,
            )
            result.raw_to_operating_point[name] = point_id

        for raw_edge in schedule.edges.values():
            source = result.raw_to_operating_point[raw_edge.source]
            target = result.raw_to_operating_point[raw_edge.target]
            if source == target:
                continue
            edge = result.edges.setdefault((source, target), ScheduleEdge(source, target))
            edge.observation_count += raw_edge.observation_count
            edge.services.update(raw_edge.services)
        return result
