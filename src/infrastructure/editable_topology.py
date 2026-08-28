"""Autoritativer, manuell editierbarer Betriebsstellen-Graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from uuid import uuid4

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


@dataclass
class EditableTopologyGraph:
    nodes: dict[str, TopologyNode] = field(default_factory=dict)
    edges: dict[str, TopologyEdge] = field(default_factory=dict)
    defined_routes: dict[str, DefinedRoute] = field(default_factory=dict)
    bildfahrplan_routes: list[BildfahrplanRouteInstance] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_operational_graph(cls, source: OperationalRouteGraph) -> "EditableTopologyGraph":
        graph = cls(metadata={"initial_source": "generated_graph"})
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in source.nodes}
        for edge in source.edges:
            if edge.source in adjacency and edge.target in adjacency and edge.source != edge.target:
                adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        for node_id, node in source.nodes.items():
            degree = len(adjacency[node_id])
            is_boundary = node.node_type == "synthetic_external_boundary"
            node_type = "entry" if degree == 1 or is_boundary else "line" if degree == 2 else "junction"
            operating_id = node.source_nodes[0] if len(node.source_nodes) == 1 else None
            graph.nodes[node_id] = TopologyNode(
                node_id, node.label, node_type, "automatic", operating_point_id=operating_id,
                metadata={"operating_point_ids": list(node.source_nodes), "automatic_node_type": node.node_type},
            )
        for edge in source.edges:
            graph.add_edge(edge.source, edge.target, source="automatic")
        graph.auto_layout()
        return graph

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

    def auto_layout(self) -> None:
        y_offset = 0.0
        for component in sorted(self.connected_components(), key=lambda value: min(value)):
            roots = sorted((node for node in component if self.degree(node) <= 1)) or sorted(component)
            levels: dict[str, int] = {roots[0]: 0}; queue = [roots[0]]
            while queue:
                current = queue.pop(0)
                for neighbour in sorted(self.neighbours(current)):
                    if neighbour not in levels:
                        levels[neighbour] = levels[current] + 1; queue.append(neighbour)
            rows: dict[int, list[str]] = {}
            for node_id in sorted(component): rows.setdefault(levels.get(node_id, 0), []).append(node_id)
            component_height = max((len(row) for row in rows.values()), default=1) * 100.0
            for level, node_ids in rows.items():
                for row, node_id in enumerate(node_ids):
                    self.nodes[node_id].layout_x = level * 170.0
                    self.nodes[node_id].layout_y = y_offset + row * 100.0
            y_offset += component_height + 130.0
