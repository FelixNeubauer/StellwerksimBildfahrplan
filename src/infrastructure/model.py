"""Verlustfreie Modelle fuer explizite und abgeleitete Infrastruktur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InfrastructureNode:
    id: str
    raw_name: str | None
    element_type: str
    enr: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InfrastructureEdge:
    id: str
    source: str
    target: str
    directed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ("wege",)


@dataclass
class RawInfrastructureGraph:
    nodes: dict[str, InfrastructureNode] = field(default_factory=dict)
    edges: list[InfrastructureEdge] = field(default_factory=list)

    def neighbours(self, node_id: str) -> set[str]:
        result: set[str] = set()
        for edge in self.edges:
            if edge.source == node_id:
                result.add(edge.target)
            if not edge.directed and edge.target == node_id:
                result.add(edge.source)
        return result


@dataclass(frozen=True)
class PlatformEvidence:
    raw_name: str
    related_names: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: str = "bahnsteigliste"


@dataclass(frozen=True)
class RouteAnchor:
    raw_name: str
    graph_nodes: tuple[str, ...]
    resolution: str
    confidence: str
    evidence: tuple[str, ...] = ("exact_name",)


@dataclass(frozen=True)
class OperationalRouteNode:
    id: str
    label: str
    raw_names: tuple[str, ...]
    source_nodes: tuple[str, ...]
    confidence: str
    node_type: str = "schedule_axis_node"


@dataclass
class OperationalRouteEdge:
    source: str
    target: str
    weight: float
    evidence: dict[str, int]
    confidence: str
    source_path: tuple[str, ...]


@dataclass
class OperationalRouteGraph:
    nodes: dict[str, OperationalRouteNode] = field(default_factory=dict)
    edges: list[OperationalRouteEdge] = field(default_factory=list)


@dataclass(frozen=True)
class RoutePath:
    id: str
    name: str
    nodes: tuple[str, ...]
    positions: tuple[float, ...]
    axis_unit: str = "relative"

    @classmethod
    def from_nodes(cls, path_id: str, name: str, nodes: tuple[str, ...]) -> "RoutePath":
        return cls(path_id, name, nodes, tuple(float(index) for index in range(len(nodes))))

    def position_for(self, node_id: str) -> float | None:
        try:
            return self.positions[self.nodes.index(node_id)]
        except ValueError:
            return None
