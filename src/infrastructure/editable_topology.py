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
    target_id: str | None = None
    target_kind: str | None = None
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


@dataclass(frozen=True)
class TopologyTarget:
    target_id: str
    target_kind: str
    display_name: str
    raw_members: tuple[str, ...] = ()
    station_key: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass
class TopologyTargetRegistry:
    targets: dict[str, TopologyTarget] = field(default_factory=dict)
    raw_to_target: dict[str, str] = field(default_factory=dict)
    evidence_to_target: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_assignments(cls, assignments, operating=None) -> "TopologyTargetRegistry":
        registry = cls()
        members: dict[str, list[str]] = {}
        for raw_name, target_id in assignments.assignments.items():
            members.setdefault(target_id, []).append(raw_name)
        for point_id, point in assignments.points.items():
            eligibility = assignments.topology_eligibility(point_id)
            if not eligibility.eligible:
                continue
            registry.targets[point_id] = TopologyTarget(
                point_id, "operating_point", point.display_name,
                tuple(sorted(members.get(point_id, ()))), point.station_key,
                ("operating_point_assignment", eligibility.reason,
                 *(f"relevant_member:{name}" for name in eligibility.relevant_members)))
            registry.evidence_to_target[point_id] = point_id
        for point_id, point in assignments.entry_points.items():
            registry.targets[point_id] = TopologyTarget(
                point_id, "entry_point", point.display_name,
                tuple(sorted(members.get(point_id, ()))), None, tuple(point.evidence))
            registry.evidence_to_target[point_id] = point_id
            for element in point.infrastructure_elements:
                registry.evidence_to_target[element.node_id] = point_id
                registry.raw_to_target.setdefault(element.raw_name, point_id)
        for raw_name, target_id in assignments.assignments.items():
            if target_id in registry.targets:
                registry.raw_to_target[raw_name] = target_id
        if operating is not None:
            for point_id, point in operating.nodes.items():
                owners = {registry.raw_to_target[name] for name in point.raw_names
                          if name in registry.raw_to_target}
                if len(owners) == 1:
                    registry.evidence_to_target[point_id] = next(iter(owners))
        return registry

    def resolve_operational_node(self, node) -> str | None:
        candidates = {self.raw_to_target[name] for name in node.raw_names
                      if name in self.raw_to_target}
        candidates.update(self.evidence_to_target[value] for value in node.source_nodes
                          if value in self.evidence_to_target)
        if node.id in self.evidence_to_target:
            candidates.add(self.evidence_to_target[node.id])
        if not candidates:
            by_name = {target.target_id for target in self.targets.values()
                       if EditableTopologyGraph.logical_name(target.display_name)
                       == EditableTopologyGraph.logical_name(node.label)}
            candidates.update(by_name)
        return next(iter(candidates)) if len(candidates) == 1 else None


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

    @classmethod
    def from_registry_projection(cls, source: OperationalRouteGraph,
                                 registry: TopologyTargetRegistry) -> "EditableTopologyGraph":
        """Builds the visible node set exclusively from authoritative assignment targets."""
        graph = cls(metadata={"initial_source": "assignment_registry_projection"})
        projected = {node_id: registry.resolve_operational_node(node)
                     for node_id, node in source.nodes.items()}
        evidence: dict[str, dict[str, set[str]]] = {}
        for node_id, target_id in projected.items():
            if target_id is None:
                continue
            node = source.nodes[node_id]
            values = evidence.setdefault(target_id, {"automatic_node_ids": set(), "raw_names": set(),
                                                       "source_nodes": set()})
            values["automatic_node_ids"].add(node_id)
            values["raw_names"].update(node.raw_names)
            values["source_nodes"].update(node.source_nodes)
        connected_targets: set[str] = set()
        projected_edges: list[tuple[str, str]] = []
        for edge in source.edges:
            left, right = projected.get(edge.source), projected.get(edge.target)
            if left and right and left != right:
                connected_targets.update((left, right)); projected_edges.append((left, right))
        for target_id in sorted(connected_targets):
            target = registry.targets[target_id]; item_evidence = evidence.get(target_id, {})
            graph.nodes[target_id] = TopologyNode(
                target_id, target.display_name, "entry" if target.target_kind == "entry_point" else "junction",
                "automatic", operating_point_id=target_id if target.target_kind == "operating_point" else None,
                target_id=target_id, target_kind=target.target_kind,
                metadata={**{key: sorted(values) for key, values in item_evidence.items()},
                          "target_raw_members": list(target.raw_members),
                          "target_evidence": list(target.evidence)})
        for left, right in projected_edges:
            graph.add_edge(left, right, source="automatic")
        for node in graph.nodes.values():
            if node.target_kind == "entry_point": node.node_type = "entry"
            elif graph.degree(node.id) == 2: node.node_type = "line"
            elif graph.degree(node.id) == 1: node.node_type = "entry"
            else: node.node_type = "junction"
        graph.auto_layout()
        for target_id, target in sorted(registry.targets.items()):
            if target_id not in graph.nodes:
                node = graph.ensure_supplement_node(
                    target_id, target.display_name,
                    "entry" if target.target_kind == "entry_point" else "junction",
                    "registry_supplement", canonical_target_id=target_id,
                    target_kind=target.target_kind)
                node.metadata.update({key: sorted(values) for key, values in evidence.get(target_id, {}).items()})
                node.metadata["target_raw_members"] = list(target.raw_members)
                node.metadata["target_evidence"] = list(target.evidence)
        graph.metadata["unresolved_automatic_nodes"] = sorted(
            node_id for node_id, target_id in projected.items() if target_id is None)
        return graph

    def migrate_to_registry(self, registry: TopologyTargetRegistry) -> int:
        """Projects persisted nodes, edges and routes onto one node per registry target."""
        mapping: dict[str, str] = {}
        unresolved: list[dict[str, Any]] = []
        names: dict[str, set[str]] = {}
        for target in registry.targets.values():
            names.setdefault(self.logical_name(target.display_name), set()).add(target.target_id)
        for node_id, node in self.nodes.items():
            candidates: set[str] = set()
            if node_id in registry.targets: candidates.add(node_id)
            if node_id in registry.evidence_to_target:
                candidates.add(registry.evidence_to_target[node_id])
            for value in (node.target_id, node.operating_point_id,
                          node.metadata.get("canonical_target_id")):
                if value in registry.targets: candidates.add(value)
                elif value in registry.evidence_to_target: candidates.add(registry.evidence_to_target[value])
            for key in ("raw_names", "source_nodes", "automatic_node_ids", "operating_point_ids"):
                for value in node.metadata.get(key, ()):
                    if value in registry.raw_to_target: candidates.add(registry.raw_to_target[value])
                    if value in registry.evidence_to_target: candidates.add(registry.evidence_to_target[value])
            if not candidates:
                candidates.update(names.get(self.logical_name(node.display_name), ()))
            if len(candidates) == 1:
                mapping[node_id] = next(iter(candidates))
            else:
                unresolved.append(asdict(node))

        migrated = EditableTopologyGraph(metadata=dict(self.metadata))
        groups: dict[str, list[TopologyNode]] = {}
        for node_id, target_id in mapping.items():
            groups.setdefault(target_id, []).append(self.nodes[node_id])
        for target_id, nodes in groups.items():
            target = registry.targets[target_id]
            winner = max(nodes, key=lambda node: (self.degree(node.id), node.source != "manual", node.id))
            metadata: dict[str, Any] = dict(winner.metadata)
            metadata["legacy_node_ids"] = sorted(node.id for node in nodes)
            metadata["target_raw_members"] = list(target.raw_members)
            metadata["target_evidence"] = list(target.evidence)
            migrated.nodes[target_id] = TopologyNode(
                target_id, target.display_name, winner.node_type, winner.source,
                winner.layout_x, winner.layout_y,
                target_id if target.target_kind == "operating_point" else None,
                target_id, target.target_kind, metadata)
        for edge in self.edges.values():
            left, right = mapping.get(edge.node_a), mapping.get(edge.node_b)
            if left and right and left != right and left in migrated.nodes and right in migrated.nodes:
                migrated.add_edge(left, right, source=edge.source)
        for route_id, route in self.defined_routes.items():
            redirected = [mapping[item] for item in route.ordered_node_ids if item in mapping]
            redirected = [item for index, item in enumerate(redirected)
                          if index == 0 or item != redirected[index - 1]]
            migrated.defined_routes[route_id] = DefinedRoute(
                route_id, route.display_name, redirected,
                redirected[0] if redirected else "", redirected[-1] if redirected else "")
        migrated.bildfahrplan_routes = list(self.bildfahrplan_routes)
        for target_id, target in sorted(registry.targets.items()):
            if target_id not in migrated.nodes:
                node = migrated.ensure_supplement_node(
                    target_id, target.display_name,
                    "entry" if target.target_kind == "entry_point" else "junction",
                    "registry_supplement", canonical_target_id=target_id,
                    target_kind=target.target_kind)
                node.metadata["target_raw_members"] = list(target.raw_members)
                node.metadata["target_evidence"] = list(target.evidence)
        migrated.metadata["unmapped_legacy_nodes"] = unresolved
        changed = int(migrated.to_dict() != self.to_dict())
        self.nodes, self.edges = migrated.nodes, migrated.edges
        self.defined_routes, self.bildfahrplan_routes = migrated.defined_routes, migrated.bildfahrplan_routes
        self.metadata = migrated.metadata
        return changed

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
                               canonical_target_id: str | None = None,
                               target_kind: str | None = None) -> TopologyNode:
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
        node.target_id = canonical_target_id
        node.target_kind = target_kind
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

    def duplicate_target_ids(self) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            if node.target_id:
                counts[node.target_id] = counts.get(node.target_id, 0) + 1
        return tuple(sorted(target_id for target_id, count in counts.items() if count > 1))

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
