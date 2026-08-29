"""Autoritativer, manuell editierbarer Betriebsstellen-Graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from uuid import uuid4
import unicodedata

from .model import OperationalRouteGraph

NODE_TYPES = ("line", "entry", "junction")


def _identifier(prefix: str) -> str:
    return f"{prefix}:{uuid4()}"


@dataclass
class TopologyNode:
    id: str
    display_name: str
    node_type: str
    source: str
    layout_x: float = 0.0
    layout_y: float = 0.0
    operating_point_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopologyEdge:
    id: str
    node_a: str
    node_b: str
    source: str = "manual"


@dataclass
class DefinedRoute:
    route_id: str
    display_name: str
    ordered_node_ids: list[str]
    endpoint_a: str
    endpoint_b: str


@dataclass
class BildfahrplanRouteInstance:
    instance_id: str
    route_id: str
    left_endpoint: str
    order: int


@dataclass(frozen=True)
class PathEnumerationResult:
    paths: tuple[tuple[str, ...], ...]
    truncated: bool = False


@dataclass(frozen=True)
class TopologySupplementCandidate:
    node_id: str
    display_name: str
    node_type: str
    source: str
    anchor_id: str | None = None
    canonical_target_id: str | None = None


@dataclass
class EditableTopologyGraph:
    nodes: dict[str, TopologyNode] = field(default_factory=dict)
    edges: dict[str, TopologyEdge] = field(default_factory=dict)
    defined_routes: dict[str, DefinedRoute] = field(default_factory=dict)
    bildfahrplan_routes: list[BildfahrplanRouteInstance] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_operational_graph(cls, source: OperationalRouteGraph,
                               canonical_operating_points: dict[str, str] | None = None) -> "EditableTopologyGraph":
        graph = cls(metadata={"initial_source": "generated_graph"})
        canonical_operating_points = canonical_operating_points or {}
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in source.nodes}
        for edge in source.edges:
            if edge.source in adjacency and edge.target in adjacency and edge.source != edge.target:
                adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        aliases = {node_id: canonical_operating_points.get(node_id, node_id) for node_id in source.nodes}
        grouped: dict[str, list] = {}
        for node_id, node in source.nodes.items():
            grouped.setdefault(aliases[node_id], []).append(node)
        canonical_adjacency = {node_id: set() for node_id in grouped}
        for edge in source.edges:
            left, right = aliases[edge.source], aliases[edge.target]
            if left != right:
                canonical_adjacency[left].add(right); canonical_adjacency[right].add(left)
        for node_id, grouped_nodes in grouped.items():
            node = grouped_nodes[0]
            degree = len(canonical_adjacency[node_id])
            is_boundary = any(item.node_type == "synthetic_external_boundary" for item in grouped_nodes)
            node_type = "entry" if degree == 1 or is_boundary else "line" if degree == 2 else "junction"
            source_nodes = tuple(sorted({value for item in grouped_nodes for value in item.source_nodes}))
            raw_names = tuple(sorted({value for item in grouped_nodes for value in item.raw_names}))
            operating_id = node_id if (len(grouped_nodes) > 1 or any(
                item.id in canonical_operating_points for item in grouped_nodes)) else (
                source_nodes[0] if len(source_nodes) == 1 else None)
            graph.nodes[node_id] = TopologyNode(
                node_id, node.label, node_type, "automatic", operating_point_id=operating_id,
                metadata={"operating_point_ids": list(source_nodes), "raw_names": list(raw_names),
                          "automatic_node_ids": [item.id for item in grouped_nodes],
                          "automatic_node_type": node.node_type},
            )
        for edge in source.edges:
            left, right = aliases[edge.source], aliases[edge.target]
            if left != right:
                graph.add_edge(left, right, source="automatic")
        graph.auto_layout()
        return graph

    def canonicalize_automatic_nodes(self, canonical_by_node: dict[str, str]) -> int:
        """Unifies automatic representations without touching authoritative manual nodes."""
        groups: dict[str, list[str]] = {}
        for node_id, canonical in canonical_by_node.items():
            if node_id in self.nodes and self.nodes[node_id].source != "manual":
                groups.setdefault(canonical, []).append(node_id)
        changed = 0
        for canonical, identifiers in groups.items():
            if len(identifiers) < 2:
                if identifiers:
                    self.nodes[identifiers[0]].operating_point_id = canonical
                continue
            winner_id = canonical if canonical in identifiers else max(
                identifiers, key=lambda value: (self.degree(value), value))
            winner = self.nodes[winner_id]; winner.operating_point_id = canonical
            for key in ("operating_point_ids", "raw_names", "automatic_node_ids"):
                winner.metadata[key] = sorted({value for node_id in identifiers
                                               for value in self.nodes[node_id].metadata.get(key, ())})
            winner.metadata["automatic_node_ids"] = sorted(set(
                winner.metadata["automatic_node_ids"]) | set(identifiers))
            aliases = {item: winner_id for item in identifiers}
            edge_pairs = []
            for edge in self.edges.values():
                left, right = aliases.get(edge.node_a, edge.node_a), aliases.get(edge.node_b, edge.node_b)
                if left != right: edge_pairs.append((left, right, edge.source))
            for route in self.defined_routes.values():
                redirected = [aliases.get(item, item) for item in route.ordered_node_ids]
                route.ordered_node_ids = [item for index, item in enumerate(redirected)
                                          if index == 0 or item != redirected[index - 1]]
                route.endpoint_a, route.endpoint_b = route.ordered_node_ids[0], route.ordered_node_ids[-1]
            for old_id in identifiers:
                if old_id != winner_id: self.delete_node(old_id); changed += 1
            self.edges.clear()
            for left, right, source in edge_pairs:
                self.add_edge(left, right, source=source)
        return changed

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditableTopologyGraph":
        graph_data = data.get("graph", data)
        graph = cls(metadata=dict(data.get("metadata", {})))
        for item in graph_data.get("nodes", []):
            node = TopologyNode(**item); graph.nodes[node.id] = node
        for item in graph_data.get("edges", []):
            edge = TopologyEdge(**item); graph.edges[edge.id] = edge
        for item in data.get("defined_routes", []):
            route = DefinedRoute(**item); graph.defined_routes[route.route_id] = route
        graph.bildfahrplan_routes = [BildfahrplanRouteInstance(**item)
                                     for item in data.get("bildfahrplan_routes", [])]
        graph.normalize_instance_order()
        return graph

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph": {
                "nodes": [asdict(item) for item in self.nodes.values()],
                "edges": [asdict(item) for item in self.edges.values()],
            },
            "defined_routes": [asdict(item) for item in self.defined_routes.values()],
            "bildfahrplan_routes": [asdict(item) for item in self.bildfahrplan_routes],
            "metadata": self.metadata,
        }

    def neighbours(self, node_id: str) -> set[str]:
        result: set[str] = set()
        for edge in self.edges.values():
            if edge.node_a == node_id: result.add(edge.node_b)
            elif edge.node_b == node_id: result.add(edge.node_a)
        return result

    def degree(self, node_id: str) -> int:
        return len(self.neighbours(node_id))

    def edge_between(self, node_a: str, node_b: str) -> TopologyEdge | None:
        pair = frozenset((node_a, node_b))
        return next((edge for edge in self.edges.values()
                     if frozenset((edge.node_a, edge.node_b)) == pair), None)

    def add_node(self, display_name: str, node_type: str = "junction", *,
                 position: tuple[float, float] = (0.0, 0.0)) -> TopologyNode:
        if node_type not in NODE_TYPES: raise ValueError(f"Unbekannter Knotentyp: {node_type}")
        node = TopologyNode(_identifier("manual-node"), display_name, node_type, "manual", *position)
        self.nodes[node.id] = node
        return node

    def ensure_supplement_node(self, node_id: str, display_name: str, node_type: str,
                               source: str, *, anchor_id: str | None = None,
                               canonical_target_id: str | None = None) -> TopologyNode:
        """Ergänzt nur neue bekannte Ziele und schützt bestehende Layoutdaten."""
        if node_id in self.nodes:
            return self.nodes[node_id]
        core_ids = {item.id for item in self.nodes.values() if not item.metadata.get("parking_area")
                    and not item.metadata.get("parking_anchor")}
        components = [component & core_ids for component in self.connected_components()]
        main = max((component for component in components if component), key=lambda value: (len(value), -len(min(value))),
                   default=core_ids)
        core = [self.nodes[item] for item in main] or list(self.nodes.values())
        min_x = min((item.layout_x for item in core), default=0.0)
        max_x = max((item.layout_x for item in core), default=min_x + 900.0)
        bottom = max((item.layout_y for item in core), default=0.0) + 140.0
        parked = sum(bool(item.metadata.get("parking_area") or item.metadata.get("parking_anchor"))
                     for item in self.nodes.values())
        columns = max(1, min(6, int(max(600.0, max_x - min_x) // 170.0) + 1))
        row, column = divmod(parked, columns)
        x = min_x + column * max(170.0, max(600.0, max_x - min_x) / max(1, columns - 1))
        if anchor_id in self.nodes:
            x = self.nodes[anchor_id].layout_x
        position = (x, bottom + row * 95.0)
        node = TopologyNode(node_id, display_name, node_type, source, *position,
                            metadata={"parking_area": anchor_id is None, "parking_anchor": anchor_id,
                                      "automatic_supplement": True, "supplement_source": source,
                                      "canonical_target_id": canonical_target_id})
        if source in {"operating_point", "operating_point_config"}:
            node.operating_point_id = canonical_target_id
        self.nodes[node_id] = node
        return node

    @staticmethod
    def deduplicate_supplement_candidates(
            candidates: Iterable[TopologySupplementCandidate]) -> tuple[TopologySupplementCandidate, ...]:
        """Returns at most one candidate for each canonical logical target."""
        result: dict[tuple[str, str], TopologySupplementCandidate] = {}
        for candidate in candidates:
            identity = (("target", candidate.canonical_target_id) if candidate.canonical_target_id
                        else ("node", candidate.node_id))
            current = result.get(identity)
            if current is None or (candidate.source, candidate.node_id) < (current.source, current.node_id):
                result[identity] = candidate
        return tuple(result[key] for key in sorted(result))

    def remove_redundant_operating_supplements(
            self, canonical_by_node: dict[str, str], canonical_names: dict[str, set[str]]) -> int:
        """Migrates only unconnected automatic supplements with an unambiguous identity."""
        identities = dict(canonical_by_node)
        for node_id, node in self.nodes.items():
            if node.source == "manual" or node_id in identities:
                continue
            candidates: set[str] = set()
            if node.operating_point_id:
                candidates.add(node.operating_point_id)
            target = node.metadata.get("canonical_target_id")
            if target:
                candidates.add(target)
            for value in node.metadata.get("operating_point_ids", ()):
                candidates.add(value)
            if not candidates:
                candidates.update(canonical_names.get(self.logical_name(node.display_name), ()))
            if len(candidates) == 1:
                identities[node_id] = next(iter(candidates))

        groups: dict[str, list[str]] = {}
        for node_id, identity in identities.items():
            if node_id in self.nodes:
                groups.setdefault(identity, []).append(node_id)
        removed = 0
        supplement_sources = {"operating_point", "operating_point_config"}
        for identifiers in groups.values():
            if len(identifiers) < 2:
                continue
            established = [node_id for node_id in identifiers
                           if self.nodes[node_id].source not in supplement_sources
                           or self.degree(node_id) > 0]
            winner = max(established or identifiers, key=lambda value: (
                self.degree(value), self.nodes[value].source not in supplement_sources, value))
            for node_id in identifiers:
                node = self.nodes[node_id]
                legacy_supplement = (node.source in supplement_sources
                                     or node.metadata.get("automatic_supplement") is True)
                if node_id != winner and legacy_supplement and self.degree(node_id) == 0:
                    self.delete_node(node_id); removed += 1
        return removed

    def missing_supplement_candidates(
            self, candidates: Iterable[TopologySupplementCandidate],
            canonical_by_node: dict[str, str],
            canonical_names: dict[str, set[str]]) -> tuple[TopologySupplementCandidate, ...]:
        """Filters operating-point supplements already represented by a non-manual graph node."""
        represented = set(canonical_by_node.values())
        for node_id, node in self.nodes.items():
            if node.source == "manual":
                continue
            identities: set[str] = set()
            if node_id in canonical_by_node:
                identities.add(canonical_by_node[node_id])
            if node.operating_point_id:
                identities.add(node.operating_point_id)
            target = node.metadata.get("canonical_target_id")
            if target:
                identities.add(target)
            identities.update(canonical_names.get(self.logical_name(node.display_name), ()))
            if len(identities) == 1:
                represented.update(identities)
        return tuple(candidate for candidate in self.deduplicate_supplement_candidates(candidates)
                     if candidate.source not in {"operating_point", "operating_point_config"}
                     or candidate.canonical_target_id not in represented)

    @staticmethod
    def logical_name(value: str) -> str:
        return unicodedata.normalize("NFC", value).strip().casefold()

    def represented_node(self, display_name: str, *, exclude_id: str | None = None) -> TopologyNode | None:
        """Returns an established automatic node for the same conservative external identity."""
        key = self.logical_name(display_name)
        candidates = [node for node in self.nodes.values() if node.id != exclude_id
                      and self.logical_name(node.display_name) == key
                      and node.source != "manual" and node.source != "entry_point_config"]
        return max(candidates, key=lambda node: (self.degree(node.id) > 0, node.source == "automatic", node.id),
                   default=None)

    def remove_redundant_entry_supplements(self) -> int:
        removed = 0
        for node in tuple(self.nodes.values()):
            if node.source != "entry_point_config":
                continue
            winner = self.represented_node(node.display_name, exclude_id=node.id)
            if winner is None:
                continue
            for route in self.defined_routes.values():
                route.ordered_node_ids = [winner.id if item == node.id else item for item in route.ordered_node_ids]
                if route.endpoint_a == node.id: route.endpoint_a = winner.id
                if route.endpoint_b == node.id: route.endpoint_b = winner.id
            self.delete_node(node.id); removed += 1
        return removed

    def add_edge(self, node_a: str, node_b: str, *, source: str = "manual") -> TopologyEdge:
        if node_a == node_b: raise ValueError("Eine Betriebsstelle kann nicht mit sich selbst verbunden werden.")
        if node_a not in self.nodes or node_b not in self.nodes: raise KeyError("Unbekannter Knoten")
        existing = self.edge_between(node_a, node_b)
        if existing: return existing
        edge = TopologyEdge(_identifier("edge"), node_a, node_b, source)
        self.edges[edge.id] = edge
        return edge

    def delete_edge(self, edge_id: str) -> None:
        self.edges.pop(edge_id)

    def routes_using_node(self, node_id: str) -> list[DefinedRoute]:
        return [route for route in self.defined_routes.values() if node_id in route.ordered_node_ids]

    def delete_node(self, node_id: str) -> None:
        self.nodes.pop(node_id)
        self.edges = {key: edge for key, edge in self.edges.items()
                      if node_id not in {edge.node_a, edge.node_b}}

    def node_validation(self, node_id: str) -> str | None:
        node, degree = self.nodes[node_id], self.degree(node_id)
        if node.node_type == "line" and degree != 2:
            return "Streckenbetriebsstelle muss genau 2 Verbindungen besitzen."
        if node.node_type == "entry" and degree != 1:
            return "Einfahrt muss genau eine Verbindung besitzen."
        return None

    def route_validation(self, route: DefinedRoute) -> tuple[str, ...]:
        errors: list[str] = []
        path = route.ordered_node_ids
        if len(path) < 2: errors.append("Eine Strecke benötigt mindestens zwei Betriebsstellen.")
        missing = [node_id for node_id in path if node_id not in self.nodes]
        if missing: errors.append("Betriebsstellen fehlen im Graph: " + ", ".join(missing))
        if path and (route.endpoint_a != path[0] or route.endpoint_b != path[-1]):
            errors.append("Endpunkte entsprechen nicht dem geordneten Pfad.")
        for left, right in zip(path, path[1:]):
            if left in self.nodes and right in self.nodes and self.edge_between(left, right) is None:
                errors.append(f"Keine Verbindung zwischen {self.nodes[left].display_name} und "
                              f"{self.nodes[right].display_name}.")
        for endpoint in (route.endpoint_a, route.endpoint_b):
            if endpoint in self.nodes and self.nodes[endpoint].node_type not in {"entry", "junction"}:
                errors.append(f"Endpunkt {self.nodes[endpoint].display_name} muss Einfahrt oder Abzweig sein.")
        return tuple(errors)

    def add_route(self, display_name: str, ordered_node_ids: Iterable[str]) -> DefinedRoute:
        path = list(ordered_node_ids)
        if len(path) < 2: raise ValueError("Eine Strecke benötigt mindestens zwei Betriebsstellen.")
        route = DefinedRoute(_identifier("route"), display_name, path, path[0], path[-1])
        errors = self.route_validation(route)
        if errors: raise ValueError("\n".join(errors))
        self.defined_routes[route.route_id] = route
        return route

    def delete_route(self, route_id: str) -> None:
        self.defined_routes.pop(route_id)
        self.bildfahrplan_routes = [item for item in self.bildfahrplan_routes if item.route_id != route_id]
        self.normalize_instance_order()

    def add_bildfahrplan_instance(self, route_id: str) -> BildfahrplanRouteInstance:
        route = self.defined_routes[route_id]
        item = BildfahrplanRouteInstance(_identifier("bf"), route_id, route.endpoint_a,
                                         len(self.bildfahrplan_routes))
        self.bildfahrplan_routes.append(item)
        return item

    def normalize_instance_order(self) -> None:
        self.bildfahrplan_routes.sort(key=lambda item: item.order)
        for order, item in enumerate(self.bildfahrplan_routes): item.order = order

    def connected_components(self) -> tuple[set[str], ...]:
        unseen, components = set(self.nodes), []
        while unseen:
            start = min(unseen); stack = [start]; component: set[str] = set()
            while stack:
                node_id = stack.pop()
                if node_id in component: continue
                component.add(node_id); unseen.discard(node_id)
                stack.extend(self.neighbours(node_id) - component)
            components.append(component)
        return tuple(components)

    def enumerate_simple_paths(self, start_id: str, end_id: str, *, limit: int = 50) -> PathEnumerationResult:
        """Enumeriert deterministisch und begrenzt nur echte einfache Graphpfade."""
        if start_id not in self.nodes or end_id not in self.nodes or start_id == end_id or limit < 1:
            return PathEnumerationResult(())
        found: list[tuple[str, ...]] = []
        stack: list[tuple[str, tuple[str, ...]]] = [(start_id, (start_id,))]
        while stack and len(found) <= limit:
            current, path = stack.pop()
            if current == end_id:
                found.append(path); continue
            neighbours = sorted(
                (node for node in self.neighbours(current) if node not in path),
                key=lambda node: (self.nodes[node].display_name.casefold(), node), reverse=True)
            stack.extend((node, (*path, node)) for node in neighbours)
        found.sort(key=lambda path: (
            len(path), tuple(self.nodes[node].display_name.casefold() for node in path), path))
        return PathEnumerationResult(tuple(found[:limit]), len(found) > limit or bool(stack))

    def auto_layout(self) -> None:
        y_offset = 0.0
        components = [item for item in self.connected_components()
                      if any(self.degree(node) for node in item)]
        for component in sorted(components, key=lambda value: (-len(value), min(value))):
            axis = self._layout_backbone(component)
            axis_index = {node: index for index, node in enumerate(axis)}
            for index, node_id in enumerate(axis):
                self.nodes[node_id].layout_x = index * 170.0
                self.nodes[node_id].layout_y = y_offset
            remaining = component - set(axis); queue = list(axis); parent: dict[str, str] = {}
            while queue:
                current = queue.pop(0)
                for neighbour in sorted(self.neighbours(current) & remaining):
                    remaining.remove(neighbour); parent[neighbour] = current; queue.append(neighbour)
            branch_rows: dict[int, int] = {}
            for node_id in sorted(parent, key=lambda value: (self._branch_depth(value, parent), value)):
                root = node_id
                while parent.get(root) not in axis_index: root = parent[root]
                anchor = parent[root]; column = axis_index[anchor]
                branch_rows[column] = branch_rows.get(column, 0) + 1
                sign = -1 if branch_rows[column] % 2 == 0 else 1
                depth = self._branch_depth(node_id, parent)
                self.nodes[node_id].layout_x = column * 170.0 + (depth - 1) * 120.0
                self.nodes[node_id].layout_y = y_offset + sign * branch_rows[column] * 100.0
            height = max(1, max(branch_rows.values(), default=0)) * 100.0
            y_offset += height * 2 + 130.0

    def _layout_backbone(self, component: set[str]) -> tuple[str, ...]:
        endpoints = sorted(node for node in component if self.degree(node) <= 1) or sorted(component)
        candidates: list[tuple[str, ...]] = []
        for start in endpoints:
            paths = {start: (start,)}; queue = [start]
            while queue:
                current = queue.pop(0)
                for neighbour in sorted(self.neighbours(current) & component):
                    if neighbour not in paths:
                        paths[neighbour] = (*paths[current], neighbour); queue.append(neighbour)
            candidates.extend(paths[end] for end in endpoints if end in paths and start < end)
        if not candidates:
            return (min(component),)
        longest = max(map(len, candidates))
        normalized = [min(path, tuple(reversed(path))) for path in candidates if len(path) == longest]
        return min(normalized)

    @staticmethod
    def _branch_depth(node_id: str, parent: dict[str, str]) -> int:
        depth = 1
        while parent.get(node_id) in parent:
            node_id = parent[node_id]; depth += 1
        return depth
