"""Evidenzbasierte Klassifikation roher Fahrplanfolgen zu Streckenkorridoren."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .model import OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode, RawInfrastructureGraph
from .schedule_graph import OperatingPointGraph, RouteAxisGraph, ScheduleEdge, SchedulePointGraph

EDGE_CLASSES = {"neighbour", "skip", "alternative_route", "branch", "local_internal", "unresolved"}
NODE_ROLES = {"mainline", "branch_junction", "branch_terminal", "branch_intermediate",
              "local_industrial", "external_boundary", "unresolved"}


@dataclass(frozen=True)
class DirectionChangeEvidence:
    terminal: str
    approach: str
    observations: int
    services: tuple[int, ...]
    evidence: tuple[str, ...] = ("schedule_reversal", "return_same_corridor")


@dataclass
class DerivedRouteEdge:
    source: str
    target: str
    classification: str
    evidence: dict[str, Any]
    covered_path: tuple[str, ...]
    confidence: str
    observations: int
    services: tuple[int, ...] = ()


@dataclass
class CorridorGraph:
    axis: RouteAxisGraph
    edges: dict[tuple[str, str], DerivedRouteEdge] = field(default_factory=dict)
    node_roles: dict[str, str] = field(default_factory=dict)
    direction_changes: list[DirectionChangeEvidence] = field(default_factory=list)
    component_roles: dict[str, str] = field(default_factory=dict)

    @property
    def visible_edges(self) -> tuple[DerivedRouteEdge, ...]:
        return tuple(edge for edge in self.edges.values() if edge.classification not in {"skip", "local_internal"})

    @property
    def branch_nodes(self) -> set[str]:
        return {node for node, role in self.node_roles.items() if role == "branch_junction"}

    def to_operational_graph(self) -> OperationalRouteGraph:
        graph = OperationalRouteGraph()
        visible_nodes = {node for edge in self.visible_edges for node in (edge.source, edge.target)}
        for node_id in visible_nodes:
            node = self.axis.nodes[node_id]
            graph.nodes[node_id] = OperationalRouteNode(
                node_id, node.display_name, node.raw_names, node.operating_points,
                "inferred" if self.node_roles.get(node_id) != "unresolved" else "unresolved",
            )
        for edge in self.visible_edges:
            graph.edges.append(OperationalRouteEdge(
                edge.source, edge.target, 1.0,
                {edge.classification: edge.observations}, edge.confidence, edge.covered_path,
            ))
        return graph


class CorridorGraphBuilder:
    """Klassifiziert ScheduleEdges; ``wege`` darf nur Evidenz beisteuern."""

    def __init__(self, schedule: SchedulePointGraph, operating: OperatingPointGraph,
                 raw_graph: RawInfrastructureGraph | None = None) -> None:
        self.schedule = schedule
        self.operating = operating
        self.axis = operating.to_route_axis_graph()
        self.raw_graph = raw_graph

    def _axis_paths(self) -> dict[int, tuple[str, ...]]:
        result: dict[int, tuple[str, ...]] = {}
        for zid, raw_path in self.schedule.service_paths.items():
            path: list[str] = []
            for raw_name in raw_path:
                operating = self.operating.raw_to_operating_point.get(raw_name)
                if operating is None:
                    continue
                axis = self.axis.operating_to_axis[operating]
                if not path or path[-1] != axis:
                    path.append(axis)
            result[zid] = tuple(path)
        return result

    @staticmethod
    def _support(edges: dict[tuple[str, str], ScheduleEdge], source: str, target: str) -> int:
        return sum(edges[pair].observation_count for pair in ((source, target), (target, source)) if pair in edges)

    def _alternative_path(self, source: str, target: str) -> tuple[str, ...] | None:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for left, right in self.axis.edges:
            if {left, right} == {source, target}:
                continue
            adjacency[left].add(right); adjacency[right].add(left)
        queue = deque([(source, (source,))])
        while queue:
            node, path = queue.popleft()
            if len(path) > 6:
                continue
            for neighbour in sorted(adjacency[node]):
                if neighbour in path:
                    continue
                candidate = (*path, neighbour)
                if neighbour == target:
                    return candidate
                queue.append((neighbour, candidate))
        return None

    def _reversals(self) -> list[DirectionChangeEvidence]:
        found: dict[tuple[str, str], tuple[int, set[int]]] = {}
        for zid, path in self._axis_paths().items():
            for left, terminal, right in zip(path, path[1:], path[2:]):
                if left == right and terminal != left:
                    count, services = found.get((terminal, left), (0, set()))
                    services.add(zid); found[(terminal, left)] = (count + 1, services)
            for index in range(len(path) - 4):
                a, b, terminal, b2, a2 = path[index:index + 5]
                if a == a2 and b == b2 and terminal != b:
                    count, services = found.get((terminal, b), (0, set()))
                    services.add(zid); found[(terminal, b)] = (count + 1, services)
        return [DirectionChangeEvidence(t, a, count, tuple(sorted(services)))
                for (t, a), (count, services) in found.items()]

    def build(self) -> CorridorGraph:
        result = CorridorGraph(self.axis)
        reversals = self._reversals()
        result.direction_changes = reversals
        terminal_approach: dict[str, DirectionChangeEvidence] = {}
        for item in reversals:
            current = terminal_approach.get(item.terminal)
            if current is None or item.observations > current.observations:
                terminal_approach[item.terminal] = item

        for pair, raw_edge in self.axis.edges.items():
            source, target = pair
            alternative = self._alternative_path(source, target)
            classification, covered, confidence = "neighbour", (source, target), "inferred"
            evidence: dict[str, Any] = {"schedule_observations": raw_edge.observation_count}
            if alternative and len(alternative) > 2:
                supports = [self._support(self.axis.edges, a, b) for a, b in zip(alternative, alternative[1:])]
                reverse_supported = all((b, a) in self.axis.edges for a, b in zip(alternative, alternative[1:]))
                if min(supports) >= 2 and raw_edge.observation_count < min(supports):
                    classification, covered, confidence = "skip", alternative, "inferred"
                    evidence.update({"stable_covered_path": min(supports),
                                     "reverse_path_observed": reverse_supported,
                                     "triangle_removed": len(alternative) == 3})
                    if self.raw_graph is not None:
                        evidence["raw_infrastructure"] = "available_secondary_evidence"
                elif min(supports) >= 2 and raw_edge.observation_count >= min(supports):
                    classification = "alternative_route"
                    evidence["alternative_path_candidate"] = alternative
                else:
                    evidence["weak_alternative_path_ignored"] = alternative
            result.edges[pair] = DerivedRouteEdge(
                source, target, classification, evidence, covered, confidence,
                raw_edge.observation_count, tuple(sorted(raw_edge.services)),
            )

        # Reversal-Terminal: nur der wiederholt befahrene Rueckweg ist der Ast;
        # schwache scheinbare Durchfahrten am Terminal bleiben als lokale Rohbeobachtung.
        for terminal, reversal in terminal_approach.items():
            result.node_roles[terminal] = "branch_terminal"
            for pair, edge in result.edges.items():
                if terminal not in pair or edge.classification == "skip":
                    continue
                other = pair[1] if pair[0] == terminal else pair[0]
                if other == reversal.approach:
                    edge.classification = "branch"
                    edge.evidence["direction_change"] = reversal.observations
                    edge.confidence = "exact" if reversal.observations > 1 else "inferred"
                else:
                    edge.classification = "local_internal"
                    edge.evidence["terminal_has_no_confirmed_through_route"] = True

        # Komponenten werden nach sichtbaren Kanten ermittelt. Die groesste ist
        # lediglich Hauptkorridor-Kandidat; kleinere Daten bleiben als local erhalten.
        components = self._components(result.visible_edges)
        largest = max(components, key=len, default=set())
        for index, component in enumerate(components):
            role = "main_component" if component == largest else "secondary_component"
            result.component_roles[f"component_{index}"] = role
            if role == "secondary_component":
                for node in component:
                    result.node_roles.setdefault(node, "local_industrial")
                for edge in result.edges.values():
                    if edge.source in component and edge.target in component and edge.classification == "neighbour":
                        edge.classification = "local_internal"
                        edge.evidence["secondary_component"] = True

        neighbours: dict[str, set[str]] = defaultdict(set)
        for edge in result.visible_edges:
            neighbours[edge.source].add(edge.target); neighbours[edge.target].add(edge.source)
        for node in self.axis.nodes:
            if node in result.node_roles:
                continue
            degree = len(neighbours[node])
            result.node_roles[node] = "branch_junction" if degree > 2 else (
                "external_boundary" if degree == 1 else "mainline" if degree == 2 else "unresolved")
        # Kanten an echten Junctions als branch markieren, ohne Hauptkorridor zu erfinden.
        for edge in result.edges.values():
            if edge.classification == "neighbour" and (
                    result.node_roles.get(edge.source) == "branch_junction"
                    or result.node_roles.get(edge.target) in {"branch_terminal", "branch_intermediate"}):
                edge.classification = "branch"
                edge.evidence["stable_branch_junction"] = True
        return result

    @staticmethod
    def _components(edges: tuple[DerivedRouteEdge, ...]) -> list[set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        result: list[set[str]] = []
        unseen = set(adjacency)
        while unseen:
            start = unseen.pop(); component = {start}; stack = [start]
            while stack:
                node = stack.pop()
                for neighbour in adjacency[node] - component:
                    component.add(neighbour); unseen.discard(neighbour); stack.append(neighbour)
            result.append(component)
        return result
