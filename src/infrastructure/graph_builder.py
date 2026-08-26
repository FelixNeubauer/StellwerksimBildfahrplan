"""Exakte Anchor-Aufloesung, Fahrplan-Evidenz und konservative Ableitung."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable, Sequence

from .model import (
    OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode,
    RawInfrastructureGraph, RouteAnchor, RoutePath,
)


class InfrastructureGraphBuilder:
    def __init__(self, raw_graph: RawInfrastructureGraph) -> None:
        self.raw_graph = raw_graph
        self.anchors: dict[str, RouteAnchor] = {}
        self.path_evidence: Counter[tuple[str, ...]] = Counter()

    def resolve_anchor(self, raw_name: str) -> RouteAnchor:
        candidates = tuple(sorted(node.id for node in self.raw_graph.nodes.values() if node.raw_name == raw_name))
        if not candidates:
            anchor = RouteAnchor(raw_name, (), "unresolved", "unresolved")
        elif len(candidates) > 1:
            anchor = RouteAnchor(raw_name, candidates, "ambiguous", "ambiguous")
        else:
            anchor = RouteAnchor(raw_name, candidates, "exact", "exact")
        self.anchors[raw_name] = anchor
        return anchor

    def resolve_names(self, raw_names: Iterable[str]) -> dict[str, RouteAnchor]:
        for name in raw_names:
            self.resolve_anchor(name)
        return self.anchors

    def _shortest_path(self, start: str, end: str) -> tuple[str, ...] | None:
        queue = deque([(start, (start,))])
        shortest_length: int | None = None
        solutions: list[tuple[str, ...]] = []
        best_depth = {start: 0}
        while queue:
            node, path = queue.popleft()
            depth = len(path) - 1
            if shortest_length is not None and depth > shortest_length:
                break
            if node == end:
                shortest_length = depth
                solutions.append(path)
                continue
            for neighbour in sorted(self.raw_graph.neighbours(node)):
                if neighbour in path:
                    continue
                next_depth = depth + 1
                if next_depth <= best_depth.get(neighbour, next_depth):
                    best_depth[neighbour] = next_depth
                    queue.append((neighbour, (*path, neighbour)))
        # Gleich kurze Alternativen werden nicht willkuerlich entschieden.
        return solutions[0] if len(solutions) == 1 else None

    def observe_schedule(self, raw_names: Sequence[str]) -> None:
        """Zaehlt nur eindeutig aufloesbare, kuerzeste Raw-Pfade als Evidenz."""
        for left_name, right_name in zip(raw_names, raw_names[1:]):
            left = self.anchors.get(left_name) or self.resolve_anchor(left_name)
            right = self.anchors.get(right_name) or self.resolve_anchor(right_name)
            if left.resolution != "exact" or right.resolution != "exact":
                continue
            path = self._shortest_path(left.graph_nodes[0], right.graph_nodes[0])
            if path:
                self.path_evidence[path] += 1

    def build_operational_graph(self) -> OperationalRouteGraph:
        """Komprimiert Evidenzpfade; Anchor- und Verzweigungsknoten bleiben erhalten."""
        used_edges: Counter[frozenset[str]] = Counter()
        used_nodes: set[str] = set()
        for path, count in self.path_evidence.items():
            used_nodes.update(path)
            for source, target in zip(path, path[1:]):
                used_edges[frozenset((source, target))] += count
        anchor_nodes = {node for anchor in self.anchors.values() for node in anchor.graph_nodes}
        degree = Counter(node for edge in used_edges for node in edge)
        retained = {node for node in used_nodes if node in anchor_nodes or degree[node] != 2}
        result = OperationalRouteGraph()
        for node_id in retained:
            raw = self.raw_graph.nodes[node_id]
            names = tuple(sorted({a.raw_name for a in self.anchors.values() if node_id in a.graph_nodes}))
            result.nodes[node_id] = OperationalRouteNode(
                node_id, names[0] if names else raw.raw_name or node_id, names, (node_id,),
                "exact" if names else "inferred",
            )
        visited: set[tuple[str, str]] = set()
        adjacency = {node: set() for node in used_nodes}
        for edge in used_edges:
            source, target = tuple(edge)
            adjacency[source].add(target)
            adjacency[target].add(source)
        for start in retained:
            for neighbour in adjacency.get(start, ()):
                if (start, neighbour) in visited:
                    continue
                path = [start, neighbour]
                previous, current = start, neighbour
                while current not in retained:
                    following = next(node for node in adjacency[current] if node != previous)
                    previous, current = current, following
                    path.append(current)
                for source, target in zip(path, path[1:]):
                    visited.add((source, target)); visited.add((target, source))
                count = min(used_edges[frozenset((source, target))] for source, target in zip(path, path[1:]))
                result.edges.append(OperationalRouteEdge(
                    start, current, float(len(path) - 1), {"schedule": count}, "inferred", tuple(path),
                ))
        return result

    @staticmethod
    def make_route_path(graph: OperationalRouteGraph, path_id: str, name: str,
                        nodes: Sequence[str]) -> RoutePath:
        edges = {frozenset((edge.source, edge.target)) for edge in graph.edges}
        if any(frozenset(pair) not in edges for pair in zip(nodes, nodes[1:])):
            raise ValueError("RoutePath enthaelt eine nicht vorhandene OperationalRouteGraph-Kante")
        return RoutePath.from_nodes(path_id, name, tuple(nodes))
