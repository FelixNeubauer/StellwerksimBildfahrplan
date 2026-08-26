"""Backbone-first reconstruction of operational corridors from schedule evidence."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from statistics import median
import re
from typing import Any, Iterable

from .model import OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode, RawInfrastructureGraph
from .schedule_graph import (
    OperatingPointGraph, RouteAxisGraph, RouteAxisNode, ScheduleEdge, SchedulePointGraph,
)

DAY_SECONDS = 86400
MINIMUM_DISPLAY_OFFSET_FRACTION = 0.12


@dataclass(frozen=True)
class TravelTimeStats:
    observations: int
    minimum: float
    median: float
    lower_percentile: float
    maximum: float
    services: tuple[int, ...]


@dataclass(frozen=True)
class PathTimeStats:
    path: tuple[str, ...]
    movement: TravelTimeStats
    total_elapsed: TravelTimeStats
    dwell: TravelTimeStats


@dataclass(frozen=True)
class HaltAwareTravelTimeComparison:
    direct_movement_median: float | None
    via_leg_1_movement_median: float | None
    via_leg_2_movement_median: float | None
    via_movement_sum: float | None
    intermediate_dwell_median: float | None
    intermediate_stop_observed: bool
    intermediate_stop_frequency: float
    movement_excess: float | None
    total_elapsed_excess: float | None
    estimated_stop_penalty: float | None
    adjusted_movement_excess: float | None
    comparison_interpretation: str


@dataclass(frozen=True)
class IntermediateStopOrSkippedPointEvidence:
    intermediate: str
    outer_nodes: tuple[str, str]
    chain_observations: int
    direct_observations: int
    forward_chain_complete: bool
    reverse_chain_complete: bool
    chain_services: tuple[int, ...]
    direct_services: tuple[int, ...]
    confidence: str


@dataclass(frozen=True)
class OrderedScheduleSequenceEvidence:
    nodes: tuple[str, str, str]
    service_zid: int
    direction: str
    is_consecutive: bool
    capture_start_completeness: str
    capture_end_completeness: str
    internal_order_trust: str
    source: str = "original_schedule"


@dataclass(frozen=True)
class SameServiceTripleEvidence:
    outer_a: str
    middle: str
    outer_c: str
    forward_services: tuple[int, ...]
    reverse_services: tuple[int, ...]
    forward_count: int
    reverse_count: int
    total_services: tuple[int, ...]
    consecutive_forward_count: int
    consecutive_reverse_count: int
    truncated_start_services: tuple[int, ...]
    evidence_strength: str


@dataclass(frozen=True)
class TriangleHypothesisEvidence:
    path: tuple[str, str, str]
    middle: str
    same_service_forward_count: int
    same_service_reverse_count: int
    pairwise_support: int
    pairwise_only_support: bool
    travel_time_interpretation: str
    raw_between_support: str
    branch_terminal_contradiction: bool
    reversal_contradiction: bool
    final_score: float
    selected: bool = False


@dataclass(frozen=True)
class DirectionChangeEvidence:
    terminal: str
    approach: str
    observations: int
    services: tuple[int, ...]
    evidence: tuple[str, ...] = ("schedule_reversal", "return_same_corridor")


@dataclass(frozen=True)
class TerminalEvidence:
    node: str
    schedule_start_count: int
    schedule_end_count: int
    through_count: int
    reversal_count: int
    stable_external_neighbors: tuple[str, ...]
    external_continuation_evidence: tuple[str, ...]
    raw_outgoing_corridors: int
    boundary_connections: tuple[str, ...]
    contradicting_terminal_evidence: tuple[str, ...]
    classification: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RawAdjacencyEvidence:
    source: str
    target: str
    connected: bool
    path: tuple[str, ...]
    intermediate_operating_points: tuple[str, ...]
    confidence: str


@dataclass(frozen=True)
class TriangleResolutionEvidence:
    nodes: tuple[str, str, str]
    between_candidate: str | None
    confidence: str
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    direct_edge: tuple[str, str] | None = None
    chain_edges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BetweenConstraint:
    id: str
    path: tuple[str, str, str]
    required_edges: tuple[tuple[str, str], ...]
    forbidden_transitive_edge: tuple[str, str]
    status: str
    confidence: str
    evidence: tuple[str, ...]
    conflict_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackboneScore:
    schedule_support: float
    reverse_support: float
    infrastructure_support: float
    travel_time_support: float
    between_support: float
    mainline_support: float
    terminal_penalty: float
    branch_penalty: float
    contradiction_penalty: float
    final_score: float


@dataclass(frozen=True)
class JunctionPositionEstimate:
    topological_fraction: float | None
    observations: int
    median_fraction: float | None
    spread: float | None
    residual: float | None
    confidence: str
    source: str
    position_unit: str = "relative"

    @property
    def edge_fraction(self) -> float | None:
        """Schema-7 compatibility alias; new data uses topological_fraction."""
        return self.topological_fraction


@dataclass(frozen=True)
class SyntheticJunctionNode:
    id: str
    display_name: str
    branch_node: str
    parent_operating_point: str | None
    host_edge: tuple[str, str]
    topological_fraction: float | None
    topological_position_source: str
    topological_confidence: str
    display_fraction: float | None
    display_position_source: str
    evidence: tuple[str, ...]
    raw_junction_node: str | None = None

    @property
    def edge_fraction(self) -> float | None:
        """Schema-7 compatibility alias; never use this value as layout state."""
        return self.topological_fraction

    @property
    def position_source(self) -> str:
        return self.topological_position_source

    @property
    def confidence(self) -> str:
        return self.topological_confidence


@dataclass(frozen=True)
class BranchAttachment:
    branch_node: str
    attachment_type: str
    host_edge: tuple[str, str] | None
    junction: str | None
    attached_node: str | None
    evidence: tuple[str, ...]
    confidence: str


@dataclass(frozen=True)
class HiddenExternalBoundaryEvidence:
    source_node: str
    external_name: str
    incoming_observations: int
    outgoing_observations: int
    services: tuple[int, ...]
    raw_connector: str | None
    directionality: str
    confidence: str
    evidence: tuple[str, ...]
    endpoint_observation_trust: str = "trusted"


@dataclass(frozen=True)
class SyntheticExternalBoundaryNode:
    id: str
    display_name: str
    source_node: str
    external_name: str
    evidence: tuple[str, ...]
    confidence: str
    raw_connector: str | None
    directionality: str
    display_offset: float = MINIMUM_DISPLAY_OFFSET_FRACTION
    node_origin: str = "synthetic_external_boundary"


@dataclass(frozen=True)
class ExplicitExternalBoundaryEvidence:
    route_axis_node: str
    raw_schedule_name: str
    external_name: str
    raw_connector: str | None
    incoming_observations: int
    outgoing_observations: int
    evidence: tuple[str, ...]
    confidence: str


@dataclass(frozen=True)
class DeferredExternalBoundaryCandidate:
    external_name: str
    possible_source_nodes: tuple[str, ...]
    trusted_incoming_count: int
    trusted_outgoing_count: int
    untrusted_incoming_count: int
    untrusted_outgoing_count: int
    raw_connector: str | None
    supporting_services: tuple[int, ...]
    confidence: str
    status: str
    reason: str
    confirmation_source: str | None = None


@dataclass(frozen=True)
class ExternalTargetResolution:
    source_node: str
    original_target: str
    normalized_candidate: str
    classification: str
    matched_node: str | None
    matched_raw_names: tuple[str, ...]
    raw_connector: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TopologyQuestion:
    id: str
    subject_node: str
    question_type: str
    question_text: str
    options: tuple[str, ...]
    evidence_summary: tuple[str, ...]
    confidence: str
    recommended_option: str | None
    status: str = "needs_user_confirmation"
    answer: str | None = None
    uncertainty_source: str = "topology_uncertainty"


@dataclass(frozen=True)
class BackboneEdge:
    source: str
    target: str
    evidence: dict[str, Any]
    confidence: str


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
    backbone_edges: dict[frozenset[str], BackboneEdge] = field(default_factory=dict)
    backbone_candidates: dict[frozenset[str], dict[str, Any]] = field(default_factory=dict)
    node_roles: dict[str, str] = field(default_factory=dict)
    topology_roles: dict[str, str] = field(default_factory=dict)
    boundary_roles: dict[str, str] = field(default_factory=dict)
    direction_changes: list[DirectionChangeEvidence] = field(default_factory=list)
    terminal_evidence: dict[str, TerminalEvidence] = field(default_factory=dict)
    travel_time_stats: dict[tuple[str, ...], PathTimeStats] = field(default_factory=dict)
    halt_aware_time_comparisons: dict[tuple[str, str, str], HaltAwareTravelTimeComparison] = field(default_factory=dict)
    intermediate_stop_or_skip_evidence: dict[tuple[str, str, str], IntermediateStopOrSkippedPointEvidence] = field(default_factory=dict)
    ordered_schedule_sequences: list[OrderedScheduleSequenceEvidence] = field(default_factory=list)
    same_service_triple_evidence: dict[tuple[str, str, str], SameServiceTripleEvidence] = field(default_factory=dict)
    triangle_hypotheses: list[TriangleHypothesisEvidence] = field(default_factory=list)
    between_evidence: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    triangle_resolutions: list[TriangleResolutionEvidence] = field(default_factory=list)
    raw_adjacency_evidence: dict[frozenset[str], RawAdjacencyEvidence] = field(default_factory=dict)
    backbone_scores: dict[frozenset[str], BackboneScore] = field(default_factory=dict)
    synthetic_junctions: dict[str, SyntheticJunctionNode] = field(default_factory=dict)
    branch_attachments: dict[str, BranchAttachment] = field(default_factory=dict)
    junction_position_estimates: dict[str, JunctionPositionEstimate] = field(default_factory=dict)
    pre_split_node_roles: dict[str, str] = field(default_factory=dict)
    role_changes: dict[str, dict[str, str]] = field(default_factory=dict)
    applied_between_resolutions: dict[frozenset[str], tuple[str, ...]] = field(default_factory=dict)
    between_constraints: dict[str, BetweenConstraint] = field(default_factory=dict)
    hidden_boundary_evidence: dict[tuple[str, str], HiddenExternalBoundaryEvidence] = field(default_factory=dict)
    synthetic_external_boundaries: dict[str, SyntheticExternalBoundaryNode] = field(default_factory=dict)
    explicit_external_boundaries: dict[str, ExplicitExternalBoundaryEvidence] = field(default_factory=dict)
    boundary_dedup_mapping: dict[str, str] = field(default_factory=dict)
    deferred_external_boundary_candidates: dict[str, DeferredExternalBoundaryCandidate] = field(default_factory=dict)
    topology_questions: dict[str, TopologyQuestion] = field(default_factory=dict)
    external_target_resolutions: dict[tuple[str, str], ExternalTargetResolution] = field(default_factory=dict)
    ignored_endpoint_observations: list[dict[str, Any]] = field(default_factory=list)
    deferred_questions: list[dict[str, Any]] = field(default_factory=list)
    component_roles: dict[str, str] = field(default_factory=dict)

    @property
    def visible_edges(self) -> tuple[DerivedRouteEdge, ...]:
        return tuple(edge for edge in self.edges.values() if edge.classification not in {"skip", "local_internal"})

    @property
    def branch_nodes(self) -> set[str]:
        roles = self.topology_roles or self.node_roles
        return {node for node, role in roles.items() if role == "branch_junction"}

    def to_operational_graph(self) -> OperationalRouteGraph:
        graph = OperationalRouteGraph()
        split_hosts = {frozenset(item.host_edge) for item in self.synthetic_junctions.values()}
        edge_branches = {item.branch_node for item in self.branch_attachments.values()
                         if item.attachment_type == "edge"}
        projected_edges = [edge for edge in self.visible_edges
                           if frozenset((edge.source, edge.target)) not in split_hosts
                           and not ({edge.source, edge.target} & edge_branches)]
        visible_nodes = {node for edge in projected_edges for node in (edge.source, edge.target)}
        visible_nodes.update(edge_branches)
        visible_nodes.update(node for item in self.synthetic_junctions.values() for node in item.host_edge)
        for node_id in visible_nodes:
            node = self.axis.nodes[node_id]
            graph.nodes[node_id] = OperationalRouteNode(
                node_id, node.display_name, node.raw_names, node.operating_points,
                "inferred" if self.node_roles.get(node_id) != "unresolved" else "unresolved",
                node.node_type,
            )
        for junction in self.synthetic_junctions.values():
            graph.nodes[junction.id] = OperationalRouteNode(
                junction.id, junction.display_name, (), (), junction.confidence,
                "synthetic_junction_node")
        for edge in projected_edges:
            graph.edges.append(OperationalRouteEdge(
                edge.source, edge.target, 1.0, {edge.classification: edge.observations},
                edge.confidence, edge.covered_path,
            ))
        host_groups: dict[frozenset[str], list[SyntheticJunctionNode]] = defaultdict(list)
        for junction in self.synthetic_junctions.values():
            host_groups[frozenset(junction.host_edge)].append(junction)
        for junctions in host_groups.values():
            a, b = junctions[0].host_edge
            ordered = sorted(junctions, key=lambda item: (
                item.topological_fraction if item.topological_fraction is not None else .5, item.id))
            chain = (a, *(item.id for item in ordered), b)
            chain_confidence = ("exact" if all(item.confidence == "exact" for item in ordered)
                                else "unresolved" if any(item.confidence == "unresolved" for item in ordered)
                                else "inferred")
            for source, target in zip(chain, chain[1:]):
                graph.edges.append(OperationalRouteEdge(
                    source, target, 1.0, {"backbone_split": 1},
                    chain_confidence, (source, target)))
            for junction in ordered:
                graph.edges.append(OperationalRouteEdge(
                    junction.id, junction.branch_node, 1.0, {"branch": 1},
                    junction.confidence, (junction.id, junction.branch_node)))
        for boundary in self.synthetic_external_boundaries.values():
            graph.nodes[boundary.id] = OperationalRouteNode(
                boundary.id, boundary.display_name, (), (), boundary.confidence,
                "synthetic_external_boundary")
            graph.edges.append(OperationalRouteEdge(
                boundary.source_node, boundary.id, boundary.display_offset,
                {"hidden_external_boundary": 1}, boundary.confidence,
                (boundary.source_node, boundary.id)))
        return graph

    def expand_axis_path(self, path: Iterable[str]) -> tuple[str, ...]:
        """Projects schedule-axis paths through derived junctions without mutating schedules."""
        source_path = tuple(path)
        result: list[str] = []
        for source, target in zip(source_path, source_path[1:]):
            result.append(source)
            hosted = sorted(
                (item for item in self.synthetic_junctions.values()
                 if frozenset(item.host_edge) == frozenset((source, target))),
                key=lambda item: ((item.topological_fraction if item.topological_fraction is not None else .5)
                                  if item.host_edge[0] == source else
                                  1 - (item.topological_fraction
                                       if item.topological_fraction is not None else .5)),
            )
            if hosted:
                result.extend(item.id for item in hosted)
                continue
            attachment = self.branch_attachments.get(target)
            if attachment and attachment.attachment_type == "edge" and attachment.junction:
                result.append(attachment.junction)
            elif source in self.branch_attachments:
                attachment = self.branch_attachments[source]
                if attachment.attachment_type == "edge" and attachment.junction:
                    result.append(attachment.junction)
        if source_path:
            result.append(source_path[-1])
        return tuple(result)

    def junction_fraction(self, node_id: str, *, for_display: bool = False) -> float | None:
        """Returns layout or topology state without changing the path sequence."""
        junction = self.synthetic_junctions[node_id]
        return junction.display_fraction if for_display else junction.topological_fraction


def _clock(value: str | None) -> int | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1]); second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    return hour * 3600 + minute * 60 + second if 0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60 else None


def _forward(end: int | None, start: int | None) -> float | None:
    if end is None or start is None:
        return None
    return float(end - start if end >= start else end + DAY_SECONDS - start)


def _first_time(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


def _stats(values: list[tuple[float, int]]) -> TravelTimeStats | None:
    if not values:
        return None
    ordered = sorted(value for value, _ in values)
    lower = ordered[max(0, int((len(ordered) - 1) * 0.25))]
    return TravelTimeStats(len(values), ordered[0], float(median(ordered)), lower, ordered[-1],
                           tuple(sorted({zid for _, zid in values})))


class CorridorGraphBuilder:
    """Builds a fixed backbone first; skip edges may only use that backbone."""

    def __init__(self, schedule: SchedulePointGraph, operating: OperatingPointGraph,
                 raw_graph: RawInfrastructureGraph | None = None) -> None:
        self.schedule = schedule
        self.operating = operating
        self.axis = operating.to_route_axis_graph()
        self.raw_graph = raw_graph
        self._current_travel_stats: dict[tuple[str, ...], PathTimeStats] = {}

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

    def _timings(self) -> dict[tuple[str, ...], PathTimeStats]:
        movement: dict[tuple[str, ...], list[tuple[float, int]]] = defaultdict(list)
        elapsed: dict[tuple[str, ...], list[tuple[float, int]]] = defaultdict(list)
        dwell: dict[tuple[str, ...], list[tuple[float, int]]] = defaultdict(list)
        for zid, points in self.schedule.service_schedules.items():
            mapped: list[tuple[str, object]] = []
            for point in points:
                raw = getattr(point, "planned_name", "") or getattr(point, "raw_name", "")
                op = self.operating.raw_to_operating_point.get(raw)
                if op is None:
                    continue
                axis = self.axis.operating_to_axis[op]
                if mapped and mapped[-1][0] == axis:
                    mapped[-1] = (axis, point)
                else:
                    mapped.append((axis, point))
            for start in range(len(mapped) - 1):
                movement_segments: list[float] = []
                dwell_sum = 0.0
                for end in range(start + 1, min(len(mapped), start + 6)):
                    left, right = mapped[end - 1][1], mapped[end][1]
                    if end - 1 > start:
                        at = _clock(getattr(left, "planned_arrival", None))
                        dep = _clock(getattr(left, "planned_departure", None))
                        dwell_sum += _forward(dep, at) or 0.0
                    departure = _clock(getattr(left, "planned_departure", None))
                    if departure is None:
                        departure = _clock(getattr(left, "planned_arrival", None))
                    arrival = _clock(getattr(right, "planned_arrival", None))
                    if arrival is None:
                        arrival = _clock(getattr(right, "planned_departure", None))
                    segment = _forward(arrival, departure)
                    if segment is None:
                        break
                    movement_segments.append(segment)
                    path = tuple(item[0] for item in mapped[start:end + 1])
                    first = mapped[start][1]; last = mapped[end][1]
                    first_dep = _first_time(_clock(getattr(first, "planned_departure", None)),
                                            _clock(getattr(first, "planned_arrival", None)))
                    last_arr = _first_time(_clock(getattr(last, "planned_arrival", None)),
                                           _clock(getattr(last, "planned_departure", None)))
                    total = _forward(last_arr, first_dep)
                    if total is not None:
                        movement[path].append((sum(movement_segments), zid))
                        elapsed[path].append((total, zid)); dwell[path].append((dwell_sum, zid))
        result: dict[tuple[str, ...], PathTimeStats] = {}
        for path in movement:
            m, e, d = _stats(movement[path]), _stats(elapsed[path]), _stats(dwell[path])
            if m and e and d:
                result[path] = PathTimeStats(path, m, e, d)
        return result

    def _reversals(self) -> list[DirectionChangeEvidence]:
        found: dict[tuple[str, str], tuple[int, set[int]]] = {}
        for zid, path in self._axis_paths().items():
            for left, terminal, right in zip(path, path[1:], path[2:]):
                if left == right and terminal != left:
                    count, services = found.get((terminal, left), (0, set())); services.add(zid)
                    found[(terminal, left)] = (count + 1, services)
            for index in range(len(path) - 4):
                a, b, terminal, b2, a2 = path[index:index + 5]
                if a == a2 and b == b2 and terminal != b:
                    count, services = found.get((terminal, b), (0, set())); services.add(zid)
                    found[(terminal, b)] = (count + 1, services)
        return [DirectionChangeEvidence(t, a, count, tuple(sorted(services)))
                for (t, a), (count, services) in found.items()]

    def _terminal_stats(self, reversals: list[DirectionChangeEvidence],
                        hidden_boundary_sources: set[str]) -> dict[str, TerminalEvidence]:
        starts: dict[str, int] = defaultdict(int); ends: dict[str, int] = defaultdict(int)
        through: dict[str, int] = defaultdict(int); neighbours: dict[str, set[str]] = defaultdict(set)
        for path in self._axis_paths().values():
            if not path:
                continue
            starts[path[0]] += 1; ends[path[-1]] += 1
            for before, node, after in zip(path, path[1:], path[2:]):
                if before != after:
                    through[node] += 1
            for left, right in zip(path, path[1:]):
                neighbours[left].add(right); neighbours[right].add(left)
        reversal_counts: dict[str, int] = defaultdict(int)
        for item in reversals:
            reversal_counts[item.terminal] += item.observations
        result: dict[str, TerminalEvidence] = {}
        for node in self.axis.nodes:
            label = self.axis.nodes[node].display_name
            boundary = label.startswith(("EA ", "Einfahrt ", "Ausfahrt ", "Abzw "))
            endpoint_weight = starts[node] + ends[node]
            raw_outgoing, boundary_connections = self._raw_continuations(node)
            contradictions = tuple(filter(None, (
                "raw_external_continuation" if raw_outgoing > len(neighbours[node]) else "",
                "multiple_raw_corridors" if raw_outgoing > 1 else "",
                "hidden_external_boundary" if node in hidden_boundary_sources else "",
            )))
            terminal = (not boundary and endpoint_weight >= 2 and through[node] == 0
                        and len(neighbours[node]) <= 1 and not contradictions)
            observed = not boundary and endpoint_weight >= 2 and through[node] == 0 and bool(contradictions)
            classification = ("external_boundary" if boundary else "terminal" if terminal else
                              "observed_schedule_boundary" if observed else "candidate")
            evidence = (("boundary_name",) if boundary else ()) + (("schedule_start_end", "single_stable_neighbor") if terminal else ())
            result[node] = TerminalEvidence(node, starts[node], ends[node], through[node], reversal_counts[node],
                                            tuple(sorted(neighbours[node])), boundary_connections, raw_outgoing,
                                            boundary_connections, contradictions, classification, evidence)
        return result

    def _raw_anchors(self) -> dict[str, set[str]]:
        anchors: dict[str, set[str]] = defaultdict(set)
        if self.raw_graph is None:
            return anchors
        for axis_id, axis_node in self.axis.nodes.items():
            raw_names = {raw for op in axis_node.operating_points for raw in self.operating.nodes[op].raw_names}
            for raw_node in self.raw_graph.nodes.values():
                if raw_node.raw_name in raw_names:
                    anchors[axis_id].add(raw_node.id)
        return anchors

    def _raw_continuations(self, node: str) -> tuple[int, tuple[str, ...]]:
        """Compresses the local anchor area into stable line-side corridor components."""
        if self.raw_graph is None:
            return 0, ()
        anchors = self._raw_anchors()
        reverse = {raw: axis for axis, values in anchors.items() for raw in values}
        local = anchors.get(node, set())
        boundary_starts = {neighbour for anchor in local
                           for neighbour in self.raw_graph.neighbours(anchor) if neighbour not in local}
        visited: set[str] = set(); corridors: list[set[str]] = []
        for start in sorted(boundary_starts):
            if start in visited:
                continue
            queue = deque([start]); component: set[str] = set(); reached: set[str] = set()
            has_unmapped_end = False
            while queue:
                current = queue.popleft()
                if current in visited or current in local:
                    continue
                visited.add(current); component.add(current)
                foreign = reverse.get(current)
                if foreign and foreign != node:
                    reached.add(foreign)
                    continue
                following = self.raw_graph.neighbours(current) - local
                if not following:
                    has_unmapped_end = True
                queue.extend(sorted(following - visited))
            if not component:
                continue
            labels = set(reached)
            if has_unmapped_end or not reached:
                labels.add("raw_graph_boundary")
            corridors.append(labels)
        evidence = tuple(sorted({label for corridor in corridors for label in corridor}))
        return len(corridors), evidence

    @staticmethod
    def _external_key(value: str | None) -> str:
        return " ".join((value or "").casefold().split())

    def _raw_external_connector(self, source: str, external_name: str) -> str | None:
        if self.raw_graph is None:
            return None
        target_key = self._external_key(external_name)
        targets = {node.id for node in self.raw_graph.nodes.values()
                   if self._external_key(node.raw_name) == target_key}
        starts = self._raw_anchors().get(source, set())
        if not starts or not targets:
            return None
        queue = deque(sorted(starts)); visited = set(starts)
        while queue:
            current = queue.popleft()
            if current in targets:
                return current
            for neighbour in sorted(self.raw_graph.neighbours(current) - visited):
                visited.add(neighbour); queue.append(neighbour)
        return None

    @staticmethod
    def _contains_known_name(value: str, known_name: str) -> bool:
        """Match a known platform name without rewriting the endpoint text."""
        if not known_name or len(known_name) < 2:
            return False
        return bool(re.search(rf"(?<!\w){re.escape(known_name)}(?!\w)", value))

    def _external_target_resolution(self, source: str, target: str) -> ExternalTargetResolution:
        normalized = self._external_key(target)
        source_node = self.axis.nodes[source]
        source_names = set(source_node.raw_names) | {source_node.id, source_node.display_name}
        for operating_id in source_node.operating_points:
            operating = self.operating.nodes.get(operating_id)
            if operating is not None:
                source_names.update(operating.raw_names)
                source_names.update((operating.id, operating.display_name))
        internal_matches = tuple(sorted({name for name in source_names if self._contains_known_name(
            normalized, self._external_key(name))}))
        if internal_matches:
            return ExternalTargetResolution(
                source, target, normalized, "same_operating_point_internal", source,
                internal_matches, None, ("known_member_of_source_operating_point",))

        for node_id, node in sorted(self.axis.nodes.items()):
            if node_id == source:
                continue
            names = set(node.raw_names) | {node.id, node.display_name}
            matches = tuple(sorted({name for name in names if self._contains_known_name(
                normalized, self._external_key(name))}))
            if matches:
                return ExternalTargetResolution(
                    source, target, normalized, "known_visible_operating_point", node_id,
                    matches, None, ("known_visible_operating_point",))

        raw_connector = self._raw_external_connector(source, target)
        if raw_connector:
            return ExternalTargetResolution(
                source, target, normalized, "confirmed_external_connector", None, (),
                raw_connector, ("raw_external_connector",))
        return ExternalTargetResolution(
            source, target, normalized, "unresolved", None, (), None,
            ("unmatched_zugdetails_endpoint",))

    def _hidden_external_boundaries(self, result: CorridorGraph) -> None:
        observed: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
            "trusted_incoming": 0, "trusted_outgoing": 0,
            "untrusted_incoming": 0, "untrusted_outgoing": 0,
            "services": set(), "name": "", "resolution": None,
        })
        for zid, path in self._axis_paths().items():
            if not path:
                continue
            origin, destination = self.schedule.service_endpoints.get(zid, (None, None))
            for source, external, direction in (
                    (path[0], origin, "incoming"), (path[-1], destination, "outgoing")):
                key = self._external_key(external)
                if not key:
                    continue
                provenance = self.schedule.service_provenance.get(zid)
                trusted = (provenance.start_trusted if direction == "incoming" else
                           provenance.end_trusted) if provenance else True
                resolution = self._external_target_resolution(source, external.strip())
                result.external_target_resolutions[(source, external.strip())] = resolution
                if resolution.classification in {
                        "same_operating_point_internal", "known_visible_operating_point", "non_topological_label"}:
                    continue
                item = observed[(source, key)]
                item[f"{'trusted' if trusted else 'untrusted'}_{direction}"] += 1
                item["services"].add(zid); item["name"] = external.strip(); item["resolution"] = resolution
                if not trusted:
                    ignored = {
                        "zid": zid, "source_node": source, "external_name": external.strip(),
                        "direction": direction, "endpoint_observation_trust": "untrusted",
                        "uncertainty_source": "insufficient_observation_history",
                        "discovery_source": provenance.discovery_source if provenance else "unknown",
                        "start_completeness": provenance.start_completeness if provenance else "unknown",
                        "end_completeness": provenance.end_completeness if provenance else "unknown",
                    }
                    result.ignored_endpoint_observations.append(ignored)
                    result.deferred_questions.append(ignored)
                    continue

        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for (source, key), item in observed.items():
            grouped[key].append((source, item))
        for key, entries in grouped.items():
            untrusted_total = sum(item["untrusted_incoming"] + item["untrusted_outgoing"]
                                  for _, item in entries)
            trusted_total = sum(item["trusted_incoming"] + item["trusted_outgoing"]
                                for _, item in entries)
            connectors = {item["resolution"].raw_connector for _, item in entries
                          if item["resolution"].raw_connector}
            if untrusted_total < 2 or not connectors:
                continue
            trusted_sources = {source for source, item in entries
                               if item["trusted_incoming"] + item["trusted_outgoing"]}
            possible_sources = tuple(sorted({source for source, _ in entries}))
            confirmed_source = next(iter(trusted_sources)) if len(trusted_sources) == 1 else None
            result.deferred_external_boundary_candidates[key] = DeferredExternalBoundaryCandidate(
                entries[0][1]["name"], possible_sources,
                sum(item["trusted_incoming"] for _, item in entries),
                sum(item["trusted_outgoing"] for _, item in entries),
                sum(item["untrusted_incoming"] for _, item in entries),
                sum(item["untrusted_outgoing"] for _, item in entries),
                sorted(connectors)[0],
                tuple(sorted({zid for _, item in entries for zid in item["services"]})),
                "exact" if confirmed_source else "inferred",
                "confirmed_automatically" if confirmed_source else "awaiting_trusted_observation",
                "trusted_endpoint_observation" if confirmed_source else "startup_truncated_endpoint_history",
                confirmed_source)

        for (source, _), item in observed.items():
            external_name = item["name"]
            resolution = item["resolution"]
            raw_connector = resolution.raw_connector
            incoming, outgoing = item["trusted_incoming"], item["trusted_outgoing"]
            total = incoming + outgoing
            candidate = result.deferred_external_boundary_candidates.get(self._external_key(external_name))
            bidirectional = bool(incoming and outgoing)
            # Names from ``von``/``nach`` alone can also denote the terminal
            # itself under a different spelling. Automatic topology therefore
            # requires repeated endpoint evidence plus a matching raw connector;
            # bidirectional details without raw confirmation become a question.
            resolved = raw_connector is not None and (
                total >= 2 or bool(candidate and candidate.status == "confirmed_automatically"
                                   and candidate.confirmation_source == source))
            directionality = ("bidirectional" if bidirectional else
                              "observed_incoming" if incoming else "observed_outgoing")
            evidence = ("schedule_endpoint", "zugdetails_von_nach") + (
                ("reverse_observations",) if bidirectional else ()) + (
                ("raw_external_connector",) if raw_connector else ())
            confidence = "exact" if bidirectional and raw_connector else "inferred" if resolved else "low"
            boundary = HiddenExternalBoundaryEvidence(
                source, external_name, incoming, outgoing, tuple(sorted(item["services"])),
                raw_connector, directionality, confidence, evidence)
            result.hidden_boundary_evidence[(source, external_name)] = boundary
            slug = re.sub(r"[^a-z0-9]+", "_", self._external_key(external_name)).strip("_")
            boundary_id = f"synthetic:external:{source.casefold()}:{slug or 'unknown'}"
            if resolved:
                source_names = self.axis.nodes[source].raw_names
                explicit_names = tuple(name for name in source_names
                                       if self._contains_known_name(self._external_key(name),
                                                                    self._external_key(external_name))
                                       and self._external_key(name) != self._external_key(external_name))
                schedule_neighbours = {target for left, target in self.axis.edges if left == source} | {
                    left for left, target in self.axis.edges if target == source}
                if explicit_names and len(schedule_neighbours) <= 1:
                    result.explicit_external_boundaries[source] = ExplicitExternalBoundaryEvidence(
                        source, explicit_names[0], external_name, raw_connector, incoming, outgoing,
                        (*evidence, "explicit_schedule_boundary", "endpoint_name_match"), confidence)
                    result.boundary_dedup_mapping[boundary_id] = source
                    continue
                result.synthetic_external_boundaries[boundary_id] = SyntheticExternalBoundaryNode(
                    boundary_id, external_name, source, external_name, evidence, confidence,
                    raw_connector, directionality)
                continue
            if candidate:
                continue
            if total == 0:
                continue
            question_id = f"question:terminal_or_external:{source}:{slug or 'unknown'}"
            result.topology_questions[question_id] = TopologyQuestion(
                question_id, source, "terminal_or_external_boundary",
                f"Ist {source} ein Streckenende oder der letzte sichtbare Punkt vor {external_name}?",
                ("terminal", "hidden_external_boundary"), (
                    f"source:{source}", f"external_candidate:{external_name}",
                    f"schedule_endpoint_observations:{total}",
                    f"destination_observations:{outgoing}", f"origin_observations:{incoming}",
                    f"raw_connector:{'confirmed' if raw_connector else 'none'}", "internal_match:none",
                    *evidence), confidence,
                "hidden_external_boundary" if raw_connector else None,
                uncertainty_source="ambiguous_endpoint")

    def _raw_adjacencies(self) -> dict[frozenset[str], RawAdjacencyEvidence]:
        result: dict[frozenset[str], RawAdjacencyEvidence] = {}
        if self.raw_graph is None:
            return result
        anchors = self._raw_anchors(); reverse = {raw: axis for axis, values in anchors.items() for raw in values}
        for source, targets in anchors.items():
            for start in targets:
                queue = deque([(start, (start,))]); visited = {start}
                while queue:
                    raw, path = queue.popleft()
                    for nxt in self.raw_graph.neighbours(raw):
                        if nxt in visited:
                            continue
                        visited.add(nxt); candidate = (*path, nxt)
                        other = reverse.get(nxt)
                        if other and other != source:
                            key = frozenset((source, other))
                            current = result.get(key)
                            if current is None or len(candidate) < len(current.path):
                                result[key] = RawAdjacencyEvidence(source, other, True, candidate, (), "exact")
                            continue
                        queue.append((nxt, candidate))
        return result

    def _stop_frequency(self, outer: tuple[str, str], middle: str) -> tuple[int, int]:
        observed = stops = 0
        for points in self.schedule.service_schedules.values():
            mapped: list[tuple[str, object]] = []
            for point in points:
                raw = getattr(point, "planned_name", "") or getattr(point, "raw_name", "")
                operating = self.operating.raw_to_operating_point.get(raw)
                if operating is None:
                    continue
                axis = self.axis.operating_to_axis[operating]
                if not mapped or mapped[-1][0] != axis:
                    mapped.append((axis, point))
            for left, candidate, right in zip(mapped, mapped[1:], mapped[2:]):
                if candidate[0] != middle or {left[0], right[0]} != set(outer):
                    continue
                observed += 1
                arrival = _clock(getattr(candidate[1], "planned_arrival", None))
                departure = _clock(getattr(candidate[1], "planned_departure", None))
                if (_forward(departure, arrival) or 0) > 0:
                    stops += 1
        return stops, observed

    def _ordered_schedule_sequences(self) -> list[OrderedScheduleSequenceEvidence]:
        sequences: list[OrderedScheduleSequenceEvidence] = []
        for zid, path in sorted(self._axis_paths().items()):
            provenance = self.schedule.service_provenance.get(zid)
            seen: set[tuple[str, str, str]] = set()
            for nodes in zip(path, path[1:], path[2:]):
                if len(set(nodes)) != 3 or nodes in seen:
                    continue
                seen.add(nodes)
                sequences.append(OrderedScheduleSequenceEvidence(
                    nodes, zid, "observed", True,
                    provenance.start_completeness if provenance else "unknown",
                    provenance.end_completeness if provenance else "unknown",
                    provenance.internal_order_trust if provenance else "reliable"))
        return sequences

    @staticmethod
    def _same_service_triple(
            outer: tuple[str, str], middle: str,
            sequences: list[OrderedScheduleSequenceEvidence],
    ) -> SameServiceTripleEvidence:
        forward = {item.service_zid for item in sequences if item.nodes == (outer[0], middle, outer[1])}
        reverse = {item.service_zid for item in sequences if item.nodes == (outer[1], middle, outer[0])}
        relevant = [item for item in sequences if item.service_zid in forward | reverse]
        truncated = tuple(sorted({item.service_zid for item in relevant
                                  if item.capture_start_completeness == "possibly_truncated_at_startup"}))
        strength = ("bidirectional" if forward and reverse else "observed_one_way"
                    if forward or reverse else "none")
        return SameServiceTripleEvidence(
            outer[0], middle, outer[1], tuple(sorted(forward)), tuple(sorted(reverse)),
            len(forward), len(reverse), tuple(sorted(forward | reverse)), len(forward), len(reverse),
            truncated, strength)

    def _raw_between_support(self, a: str, middle: str, c: str) -> str:
        anchors = self._raw_anchors()
        if self.raw_graph is None or any(len(anchors.get(node, ())) != 1 for node in (a, middle, c)):
            return "unresolved"
        path = self._raw_shortest_path(anchors[a], anchors[c])
        if not path:
            return "unresolved"
        middle_anchor = next(iter(anchors[middle]))
        return "positive" if middle_anchor in path[1:-1] else "branch_like"

    def _stop_or_skip_evidence(
            self, middle: str, outer: tuple[str, str], undirected: dict[frozenset[str], dict[str, Any]],
            triple: SameServiceTripleEvidence,
    ) -> IntermediateStopOrSkippedPointEvidence:
        a, c = outer
        forward = bool(triple.forward_services)
        reverse = bool(triple.reverse_services)
        chain_services = triple.total_services
        direct_edges = (self.axis.edges.get((a, c)), self.axis.edges.get((c, a)))
        direct_services = tuple(sorted({zid for edge in direct_edges if edge for zid in edge.services}))
        chain_observations = min(undirected[frozenset((a, middle))]["direct"],
                                 undirected[frozenset((middle, c))]["direct"])
        direct_observations = undirected[frozenset((a, c))]["direct"]
        confidence = "high" if forward and reverse and len(chain_services) >= 2 else "inferred"
        return IntermediateStopOrSkippedPointEvidence(
            middle, outer, chain_observations, direct_observations, forward, reverse,
            chain_services, direct_services, confidence)

    def _halt_aware_comparison(
            self, outer: tuple[str, str], middle: str, direct: PathTimeStats | None,
            via: PathTimeStats | None, strong_chain: bool,
    ) -> HaltAwareTravelTimeComparison:
        leg1 = self._best_time((outer[0], middle), self._current_travel_stats)
        leg2 = self._best_time((middle, outer[1]), self._current_travel_stats)
        direct_movement = direct.movement.median if direct else None
        via_movement = via.movement.median if via else None
        dwell = via.dwell.median if via else None
        stops, stop_observations = self._stop_frequency(outer, middle)
        stop_frequency = stops / stop_observations if stop_observations else 0.0
        movement_excess = (via_movement - direct_movement
                           if via_movement is not None and direct_movement is not None else None)
        elapsed_excess = (via.total_elapsed.median - direct.total_elapsed.median
                          if via and direct else None)
        ratio = direct_movement / max(via_movement, 1.0) if direct_movement is not None and via_movement else None
        stop_observed = stops > 0 or bool(dwell)
        if ratio is None:
            interpretation = "insufficient_data"
        elif ratio >= .8:
            interpretation = "consistent"
        elif ratio >= .55 and stop_observed and strong_chain:
            interpretation = "consistent_with_intermediate_stop"
        elif ratio >= .55:
            interpretation = "weakly_slower"
        elif ratio >= .4:
            interpretation = "strongly_slower"
        else:
            interpretation = "implausible_detour"
        return HaltAwareTravelTimeComparison(
            direct_movement, leg1.movement.median if leg1 else None,
            leg2.movement.median if leg2 else None, via_movement, dwell,
            stop_observed, stop_frequency, movement_excess, elapsed_excess,
            None, movement_excess, interpretation)

    def _triangles(self, result: CorridorGraph, undirected: dict[frozenset[str], dict[str, Any]]) -> None:
        sequences = self._ordered_schedule_sequences()
        result.ordered_schedule_sequences = sequences
        adjacency: dict[str, set[str]] = defaultdict(set)
        for key in undirected:
            a, b = tuple(key); adjacency[a].add(b); adjacency[b].add(a)
        seen: set[frozenset[str]] = set()
        for a in sorted(adjacency):
            for b in sorted(adjacency[a]):
                for c in sorted(adjacency[a] & adjacency[b]):
                    nodes_key = frozenset((a, b, c))
                    if len(nodes_key) != 3 or nodes_key in seen:
                        continue
                    seen.add(nodes_key); nodes = tuple(sorted(nodes_key))
                    interpretations: list[tuple[
                        float, str, tuple[str, ...], tuple[str, ...], tuple[str, str],
                        tuple[tuple[str, str], ...],
                    ]] = []
                    for middle in nodes:
                        outer = tuple(node for node in nodes if node != middle)
                        if ((outer[1], middle, outer[0]) in result.travel_time_stats
                                and (outer[0], middle, outer[1]) not in result.travel_time_stats):
                            outer = tuple(reversed(outer))
                        direct = self._best_time(outer, result.travel_time_stats)
                        via = self._best_time((outer[0], middle, outer[1]), result.travel_time_stats)
                        support: list[str] = []; contradict: list[str] = []; score = 0.0
                        chain = tuple(tuple(sorted((middle, endpoint))) for endpoint in outer)
                        chain_support = min(undirected[frozenset(edge)]["direct"] for edge in chain)
                        direct_support = undirected[frozenset(outer)]["direct"]
                        triple = self._same_service_triple(outer, middle, sequences)
                        result.same_service_triple_evidence[(outer[0], middle, outer[1])] = triple
                        pattern = self._stop_or_skip_evidence(middle, outer, undirected, triple)
                        result.intermediate_stop_or_skip_evidence[(outer[0], middle, outer[1])] = pattern
                        strong_chain = pattern.forward_chain_complete and pattern.reverse_chain_complete
                        pairwise_forward = all(edge in self.axis.edges for edge in ((outer[0], middle),
                                                                                  (middle, outer[1])))
                        pairwise_reverse = all(edge in self.axis.edges for edge in ((outer[1], middle),
                                                                                  (middle, outer[0])))
                        if triple.total_services:
                            score += 6.0; support.append("same_service_consecutive_triple")
                        if triple.forward_count and triple.reverse_count:
                            score += 4.0; support.append("bidirectional_same_service_triples")
                        if not triple.total_services and pairwise_forward and pairwise_reverse:
                            score += 1.0; support.append("pairwise_bidirectional_chain_only")
                        if triple.total_services and pattern.direct_observations:
                            score += 1.0; support.append("intermediate_stop_or_skipped_point")
                        comparison = self._halt_aware_comparison(outer, middle, direct, via, strong_chain)
                        result.halt_aware_time_comparisons[(outer[0], middle, outer[1])] = comparison
                        if comparison.comparison_interpretation == "consistent":
                            score += 3.0; support.append("travel_time_consistent")
                        elif comparison.comparison_interpretation == "consistent_with_intermediate_stop":
                            score += 1.0; support.append("travel_time_consistent_with_intermediate_stop")
                        elif comparison.comparison_interpretation == "strongly_slower":
                            score -= 2.0; contradict.append("via_movement_strongly_slower")
                        elif comparison.comparison_interpretation == "implausible_detour":
                            score -= 4.0; contradict.append("travel_time_implausible_detour")
                        if comparison.intermediate_dwell_median:
                            support.append(f"intermediate_dwell:{comparison.intermediate_dwell_median:g}s")
                        if chain_support >= direct_support:
                            score += 1.0; support.append("chain_schedule_support")
                        raw_between = self._raw_between_support(outer[0], middle, outer[1])
                        if raw_between == "positive":
                            score += 3.0; support.append("raw_between_positive")
                        elif raw_between == "branch_like":
                            score -= 2.0; contradict.append("raw_between_branch_like")
                        branch_contradiction = result.terminal_evidence.get(middle).classification == "terminal"
                        reversal_contradiction = any(item.terminal == middle for item in result.direction_changes)
                        if branch_contradiction:
                            score -= 6.0; contradict.append("terminal_middle_contradiction")
                        if reversal_contradiction:
                            score -= 8.0; contradict.append("reversal_middle_contradiction")
                        result.triangle_hypotheses.append(TriangleHypothesisEvidence(
                            (outer[0], middle, outer[1]), middle, triple.forward_count,
                            triple.reverse_count, chain_support, not bool(triple.total_services),
                            comparison.comparison_interpretation, raw_between,
                            branch_contradiction, reversal_contradiction, score))
                        interpretations.append((score, middle, tuple(support), tuple(contradict), outer, chain))
                    interpretations.sort(key=lambda item: (-item[0], item[1]))
                    best = interpretations[0]
                    triple_scores = [item for item in interpretations
                                     if result.same_service_triple_evidence[
                                         (item[4][0], item[1], item[4][1])].total_services]
                    ordered_conflict = (len(triple_scores) > 1 and
                                        triple_scores[0][0] - triple_scores[1][0] < 3.0)
                    positive = best[0] >= 3.0 and not ordered_conflict
                    for index, hypothesis in enumerate(result.triangle_hypotheses):
                        if frozenset(hypothesis.path) == nodes_key and hypothesis.middle == best[1]:
                            result.triangle_hypotheses[index] = replace(hypothesis, selected=positive)
                    if ordered_conflict:
                        question_id = "question:ordered_sequence:" + ":".join(nodes)
                        result.topology_questions[question_id] = TopologyQuestion(
                            question_id, best[1], "conflicting_ordered_schedule_sequences",
                            f"Widersprüchliche geordnete Fahrplansequenzen betreffen {' / '.join(nodes)}.",
                            tuple(" → ".join(item[4][:1] + (item[1],) + item[4][1:])
                                  for item in triple_scores),
                            tuple(f"{item[1]}:{item[0]:g}" for item in triple_scores),
                            "low", None, uncertainty_source="topology_conflict")
                    resolution = TriangleResolutionEvidence(
                        nodes, best[1] if positive else None, "high" if abs(best[0]) >= 3 else "low",
                        best[2], best[3], tuple(sorted(best[4])), best[5],
                    )
                    result.triangle_resolutions.append(resolution)
                    for score, middle, support, contradict, outer, chain in interpretations:
                        result.between_evidence[(outer[0], middle, outer[1])] = {
                            "confidence": "high" if score >= 3 else "rejected" if score < 0 else "low",
                            "score": score, "supporting_evidence": support,
                            "contradicting_evidence": contradict,
                            "stop_aware_interpretation": result.halt_aware_time_comparisons[
                                (outer[0], middle, outer[1])].comparison_interpretation,
                            "intermediate_stop_observed": result.halt_aware_time_comparisons[
                                (outer[0], middle, outer[1])].intermediate_stop_observed,
                            "through_or_skip_confidence": result.intermediate_stop_or_skip_evidence[
                                (outer[0], middle, outer[1])].confidence,
                            "raw_between_support": self._raw_between_support(outer[0], middle, outer[1]),
                        }

    def _compile_between_constraints(
            self, result: CorridorGraph,
    ) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
        """Compile all high-confidence decisions before touching union-find.

        Required chain edges are local topology constraints, not ordinary
        forest candidates.  Conflicts are therefore detected set-wise and
        deterministically rather than being inferred from union-find order.
        """
        candidates: dict[str, BetweenConstraint] = {}
        for triangle in sorted(result.triangle_resolutions, key=lambda item: (
                item.nodes, item.between_candidate or "", item.direct_edge or ())):
            if triangle.confidence != "high" or not triangle.between_candidate or not triangle.direct_edge:
                continue
            a, c = triangle.direct_edge
            path = (a, triangle.between_candidate, c)
            constraint_id = "between:" + ":".join(path)
            candidates[constraint_id] = BetweenConstraint(
                constraint_id, path, tuple(tuple(sorted(edge)) for edge in triangle.chain_edges),
                tuple(sorted(triangle.direct_edge)), "detected", triangle.confidence,
                (*triangle.supporting_evidence, *triangle.contradicting_evidence))

        conflicts: dict[str, set[str]] = defaultdict(set)
        items = sorted(candidates.items())
        for index, (left_id, left) in enumerate(items):
            left_required = {frozenset(edge) for edge in left.required_edges}
            left_forbidden = frozenset(left.forbidden_transitive_edge)
            for right_id, right in items[index + 1:]:
                right_required = {frozenset(edge) for edge in right.required_edges}
                right_forbidden = frozenset(right.forbidden_transitive_edge)
                contradictory = (left_forbidden in right_required or right_forbidden in left_required
                                 or (left_forbidden == right_forbidden and left.path != right.path))
                if contradictory:
                    conflicts[left_id].add(right_id); conflicts[right_id].add(left_id)

        required: set[frozenset[str]] = set()
        forbidden: set[frozenset[str]] = set()
        for constraint_id, constraint in items:
            conflict_ids = tuple(sorted(conflicts.get(constraint_id, ())))
            if conflict_ids:
                result.between_constraints[constraint_id] = replace(
                    constraint, status="conflicting", conflict_ids=conflict_ids)
                question_id = f"question:between_constraint:{constraint_id.removeprefix('between:')}"
                result.topology_questions[question_id] = TopologyQuestion(
                    question_id, constraint.path[1], "conflicting_between_constraints",
                    f"Widersprüchliche High-Between-Entscheidungen betreffen {' – '.join(constraint.path)}.",
                    tuple((constraint_id, *conflict_ids)), constraint.evidence,
                    "low", None, uncertainty_source="topology_conflict")
                continue
            applied = replace(constraint, status="applied")
            result.between_constraints[constraint_id] = applied
            direct_key = frozenset(applied.forbidden_transitive_edge)
            required.update(frozenset(edge) for edge in applied.required_edges)
            forbidden.add(direct_key)
            result.applied_between_resolutions[direct_key] = applied.path
        return required, forbidden

    def _backbone(self, result: CorridorGraph, terminal_approach: dict[str, DirectionChangeEvidence]) -> None:
        """Score contextual evidence first, then select the maximum-evidence forest."""
        undirected: dict[frozenset[str], dict[str, Any]] = {}
        for source, target in self.axis.edges:
            key = frozenset((source, target))
            item = undirected.setdefault(key, {"source": source, "target": target})
            item["direct"] = self._support(self.axis.edges, source, target)
            item["forward"] = self.axis.edges.get((source, target), ScheduleEdge(source, target)).observation_count
            item["reverse"] = self.axis.edges.get((target, source), ScheduleEdge(target, source)).observation_count
        result.raw_adjacency_evidence = self._raw_adjacencies()
        self._triangles(result, undirected)
        mandatory_chain_edges, forbidden_transitive_edges = self._compile_between_constraints(result)
        candidate_adjacency: dict[str, set[str]] = defaultdict(set)
        for key in undirected:
            left, right = tuple(key); candidate_adjacency[left].add(right); candidate_adjacency[right].add(left)
        for key, item in undirected.items():
            source, target = item["source"], item["target"]
            alternative = self._candidate_path(source, target, candidate_adjacency)
            if alternative:
                supports = [undirected[frozenset((a, b))]["direct"] for a, b in zip(alternative, alternative[1:])]
                if min(supports) >= 2 and item["direct"] >= min(supports):
                    result.backbone_candidates[key] = {
                        "classification": "alternative_route_candidate", "covered_path": alternative,
                        "direct_support": item["direct"], "path_support": min(supports),
                    }
        positive_chain: dict[frozenset[str], float] = defaultdict(float)
        transitive_penalty: dict[frozenset[str], float] = defaultdict(float)
        fast_direct: dict[frozenset[str], float] = defaultdict(float)
        rejected_middle: set[str] = set()
        for triangle in result.triangle_resolutions:
            if triangle.between_candidate:
                for edge in triangle.chain_edges:
                    positive_chain[frozenset(edge)] += 24.0
                if triangle.direct_edge:
                    transitive_penalty[frozenset(triangle.direct_edge)] += 24.0
            else:
                for outer_a, middle, outer_b in (
                        (key[0], key[1], key[2]) for key, value in result.between_evidence.items()
                        if frozenset(key) == frozenset(triangle.nodes) and value["score"] < 0):
                    fast_direct[frozenset((outer_a, outer_b))] += 28.0
                    rejected_middle.add(middle)
        for key, item in undirected.items():
            source, target = item["source"], item["target"]
            direct_stats = self._best_time((source, target), result.travel_time_stats)
            if direct_stats:
                item["median_movement_time"] = direct_stats.movement.median
            reverse = min(item["forward"], item["reverse"])
            infra = 8.0 if key in result.raw_adjacency_evidence else 0.0
            terminal_penalty = 0.0
            for node in key:
                terminal = result.terminal_evidence[node]
                if terminal.classification == "terminal" or node in rejected_middle:
                    terminal_penalty += 5.0
            branch_penalty = 6.0 if any(
                node in terminal_approach and (key - {node}) != {terminal_approach[node].approach}
                for node in key) else 0.0
            components = BackboneScore(
                schedule_support=item["direct"] * 10.0,
                reverse_support=reverse * 4.0,
                infrastructure_support=infra,
                travel_time_support=fast_direct[key],
                between_support=positive_chain[key],
                mainline_support=0.0,
                terminal_penalty=terminal_penalty,
                branch_penalty=branch_penalty,
                contradiction_penalty=transitive_penalty[key],
                final_score=(item["direct"] * 10.0 + reverse * 4.0 + infra + fast_direct[key]
                             + positive_chain[key] - terminal_penalty - branch_penalty
                             - transitive_penalty[key]),
            )
            result.backbone_scores[key] = components
            item["score"] = components
        ranked = sorted(undirected.items(), key=lambda entry: (
            -result.backbone_scores[entry[0]].final_score, tuple(sorted(entry[0]))))
        parent = {node: node for node in self.axis.nodes}

        def root(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]; node = parent[node]
            return node

        for key in sorted(mandatory_chain_edges, key=lambda item: tuple(sorted(item))):
            source, target = tuple(key)
            evidence = undirected[key]
            if root(source) != root(target):
                parent[root(source)] = root(target)
            evidence["selection"] = "selected_high_confidence_between_chain"
            confidence = "exact" if evidence["forward"] and evidence["reverse"] else "inferred"
            result.backbone_edges[key] = BackboneEdge(source, target, evidence, confidence)

        for key, evidence in ranked:
            if key in forbidden_transitive_edges or key in result.backbone_edges:
                if key in forbidden_transitive_edges:
                    evidence["selection"] = "rejected_high_confidence_between_transitive"
                continue
            source, target = tuple(key)
            # Terminal reversals admit only their confirmed approach into backbone.
            terminal_blocked = any(
                terminal in key and (key - {terminal}) != {change.approach}
                for terminal, change in terminal_approach.items()
            )
            if terminal_blocked or root(source) == root(target):
                evidence["selection"] = "rejected_terminal" if terminal_blocked else "rejected_cycle"
                continue
            parent[root(source)] = root(target)
            confidence = "exact" if evidence["forward"] and evidence["reverse"] else "inferred"
            evidence["selection"] = "selected_maximum_evidence_forest"
            evidence["infrastructure_support"] = (
                "raw_adjacent" if key in result.raw_adjacency_evidence else
                "not_observed" if self.raw_graph is not None else "not_available")
            result.backbone_edges[key] = BackboneEdge(source, target, evidence, confidence)

    @staticmethod
    def _candidate_path(source: str, target: str, adjacency: dict[str, set[str]]) -> tuple[str, ...] | None:
        queue = deque([(source, (source,))])
        while queue:
            node, path = queue.popleft()
            if len(path) > 6:
                continue
            for neighbour in sorted(adjacency[node]):
                if {node, neighbour} == {source, target} or neighbour in path:
                    continue
                candidate = (*path, neighbour)
                if neighbour == target:
                    return candidate
                queue.append((neighbour, candidate))
        return None

    @staticmethod
    def _best_time(path: tuple[str, ...], stats: dict[tuple[str, ...], PathTimeStats]) -> PathTimeStats | None:
        return stats.get(path) or stats.get(tuple(reversed(path)))

    def _backbone_path(self, source: str, target: str, result: CorridorGraph) -> tuple[str, ...] | None:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for key in result.backbone_edges:
            left, right = tuple(key); adjacency[left].add(right); adjacency[right].add(left)
        queue = deque([(source, (source,))])
        while queue:
            node, path = queue.popleft()
            if len(path) > 8:
                continue
            for neighbour in sorted(adjacency[node]):
                if neighbour in path:
                    continue
                candidate = (*path, neighbour)
                if neighbour == target:
                    return candidate
                queue.append((neighbour, candidate))
        return None

    def _junction_time_estimate(self, a: str, b: str, terminal: str) -> JunctionPositionEstimate:
        """Triangulates a relative operating position; it is explicitly not a distance."""
        fractions: list[tuple[float, int, float]] = []
        for left, right in ((a, b), (b, a)):
            # A directional estimate is only formed from observations in that
            # direction; a missing reverse run must not duplicate the forward data.
            ab = self._current_travel_stats.get((left, right))
            at = self._current_travel_stats.get((left, terminal))
            bt = self._current_travel_stats.get((right, terminal))
            if not (ab and at and bt) or ab.movement.median <= 0:
                continue
            t_ab = ab.movement.median; t_at = at.movement.median; t_bt = bt.movement.median
            distance_to_junction = (t_ab + t_at - t_bt) / 2.0
            branch_time = (t_at + t_bt - t_ab) / 2.0
            fraction = distance_to_junction / t_ab
            if not 0 <= fraction <= 1 or branch_time < 0:
                continue
            canonical_fraction = fraction if left == a else 1.0 - fraction
            observations = min(ab.movement.observations, at.movement.observations,
                               bt.movement.observations)
            residual = abs((distance_to_junction + branch_time) - t_at)
            fractions.append((canonical_fraction, observations, residual))
        if not fractions:
            return JunctionPositionEstimate(None, 0, None, None, None, "unresolved",
                                            "travel_time_triangulation")
        values = sorted(value for value, _, _ in fractions)
        estimate = float(median(values)); spread = values[-1] - values[0]
        observations = sum(count for _, count, _ in fractions)
        residual = float(median([value for _, _, value in fractions]))
        confidence = "exact" if observations >= 2 and spread <= .15 else "inferred"
        return JunctionPositionEstimate(estimate, observations, estimate, spread, residual,
                                        confidence, "travel_time_triangulation")

    def _raw_shortest_path(self, starts: set[str], ends: set[str]) -> tuple[str, ...] | None:
        if self.raw_graph is None or not starts or not ends:
            return None
        queue = deque((node, (node,)) for node in sorted(starts)); shortest: list[tuple[str, ...]] = []
        best_depth: dict[str, int] = {node: 0 for node in starts}; length: int | None = None
        while queue:
            node, path = queue.popleft(); depth = len(path) - 1
            if length is not None and depth > length:
                break
            if node in ends:
                length = depth; shortest.append(path); continue
            for neighbour in sorted(self.raw_graph.neighbours(node)):
                next_depth = depth + 1
                if neighbour in path or next_depth > best_depth.get(neighbour, next_depth):
                    continue
                best_depth[neighbour] = next_depth
                queue.append((neighbour, (*path, neighbour)))
        return shortest[0] if len(shortest) == 1 else None

    def _raw_junction_estimate(self, a: str, b: str, terminal: str) -> tuple[float, str] | None:
        anchors = self._raw_anchors()
        if any(len(anchors.get(node, set())) != 1 for node in (a, b, terminal)):
            return None
        ab = self._raw_shortest_path(anchors.get(a, set()), anchors.get(b, set()))
        at = self._raw_shortest_path(anchors.get(a, set()), anchors.get(terminal, set()))
        bt = self._raw_shortest_path(anchors.get(b, set()), anchors.get(terminal, set()))
        if not (ab and at and bt) or len(ab) < 2:
            return None
        candidates = set(ab) & set(at) & set(bt)
        candidates -= anchors.get(a, set()) | anchors.get(b, set()) | anchors.get(terminal, set())
        candidates = {node for node in candidates if len(self.raw_graph.neighbours(node)) >= 3}
        if len(candidates) != 1:
            return None
        raw_junction = candidates.pop()
        return ab.index(raw_junction) / (len(ab) - 1), raw_junction

    @staticmethod
    def _synthetic_id(branch: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", branch.casefold()).strip("_") or "branch"
        return f"synthetic:abzw_{slug}"

    @staticmethod
    def _display_position(topological_fraction: float) -> tuple[float, str]:
        if topological_fraction < MINIMUM_DISPLAY_OFFSET_FRACTION:
            return MINIMUM_DISPLAY_OFFSET_FRACTION, "layout_offset"
        if topological_fraction > 1.0 - MINIMUM_DISPLAY_OFFSET_FRACTION:
            return 1.0 - MINIMUM_DISPLAY_OFFSET_FRACTION, "layout_offset"
        return topological_fraction, "topology"

    def _derive_branch_attachments(self, result: CorridorGraph,
                                   terminal_approach: dict[str, DirectionChangeEvidence]) -> None:
        schedule_neighbours: dict[str, set[str]] = defaultdict(set)
        for source, target in self.axis.edges:
            schedule_neighbours[source].add(target); schedule_neighbours[target].add(source)
        for terminal, role in tuple(result.node_roles.items()):
            if role != "branch_terminal":
                continue
            neighbours = schedule_neighbours[terminal]
            host_candidates = [key for key in result.backbone_edges
                               if terminal not in key and key <= neighbours]
            if len(host_candidates) != 1:
                attached = next(iter(neighbours), None) if len(neighbours) == 1 else None
                result.branch_attachments[terminal] = BranchAttachment(
                    terminal, "node" if attached else "unresolved", None, None, attached,
                    ("single_schedule_neighbour",) if attached else ("ambiguous_attachment",),
                    "inferred" if attached else "unresolved")
                continue
            host_key = host_candidates[0]
            a, b = sorted(host_key)
            time_estimate = self._junction_time_estimate(a, b, terminal)
            raw_estimate = self._raw_junction_estimate(a, b, terminal)
            if raw_estimate:
                fraction, raw_node = raw_estimate
                source = "raw_infrastructure" if time_estimate.edge_fraction is None else "raw_and_travel_time"
                confidence = "exact"; evidence = ("backbone_host_edge", "two_sided_schedule_branch",
                                                   "raw_junction_projection")
            elif time_estimate.edge_fraction is not None:
                fraction = time_estimate.edge_fraction; raw_node = None
                source = time_estimate.source; confidence = time_estimate.confidence
                evidence = ("backbone_host_edge", "two_sided_schedule_branch",
                            "travel_time_triangulation")
            else:
                result.branch_attachments[terminal] = BranchAttachment(
                    terminal, "unresolved", (a, b), None, None,
                    ("host_edge_candidate", "position_unresolved"), "unresolved")
                result.junction_position_estimates[terminal] = time_estimate
                continue
            junction_id = self._synthetic_id(terminal)
            approach = terminal_approach.get(terminal).approach if terminal in terminal_approach else None
            parent_candidates = self.axis.nodes[approach].operating_points if approach else ()
            parent = parent_candidates[0] if len(parent_candidates) == 1 else None
            display_fraction, display_source = self._display_position(fraction)
            junction = SyntheticJunctionNode(
                junction_id, f"Abzw {self.axis.nodes[terminal].display_name}", terminal, parent,
                (a, b), fraction, source, confidence, display_fraction, display_source,
                evidence, raw_node)
            result.synthetic_junctions[junction_id] = junction
            result.junction_position_estimates[junction_id] = time_estimate
            result.branch_attachments[terminal] = BranchAttachment(
                terminal, "edge", (a, b), junction_id, None, evidence, confidence)
            result.node_roles[junction_id] = "branch_junction"
            self.axis.nodes[junction_id] = RouteAxisNode(
                junction_id, junction.display_name, (), (), None,
                {"synthetic_junction": 1}, "synthetic_junction_node")

    def _finalize_node_roles(self, result: CorridorGraph) -> None:
        """Recompute independent topology and boundary role dimensions."""
        result.pre_split_node_roles = dict(result.node_roles)
        graph = result.to_operational_graph()
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in graph.edges:
            if (graph.nodes[edge.source].node_type == "synthetic_external_boundary"
                    or graph.nodes[edge.target].node_type == "synthetic_external_boundary"):
                continue
            adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        topology: dict[str, str] = {}
        boundary: dict[str, str] = {}
        hidden_sources = {item.source_node for item in result.synthetic_external_boundaries.values()}
        for node in graph.nodes:
            degree = len(adjacency[node]); previous = result.pre_split_node_roles.get(node)
            terminal = result.terminal_evidence.get(node)
            if node in result.synthetic_external_boundaries:
                topology_role = "unresolved"
            elif node in result.synthetic_junctions:
                topology_role = "branch_junction" if degree > 2 else "unresolved"
            elif previous == "branch_terminal":
                topology_role = "branch_terminal"
            elif terminal and terminal.classification == "terminal":
                topology_role = "terminal"
            elif previous == "local_industrial":
                topology_role = previous
            elif degree > 2:
                topology_role = "branch_junction"
            elif degree >= 1:
                topology_role = "mainline"
            else:
                topology_role = "unresolved"

            if node in result.synthetic_external_boundaries:
                boundary_role = "external_boundary"
            elif node in result.explicit_external_boundaries:
                boundary_role = "external_boundary"
            elif node in hidden_sources:
                boundary_role = "boundary_adjacent"
            elif terminal and terminal.classification == "observed_schedule_boundary":
                boundary_role = "observed_schedule_boundary"
            elif terminal and terminal.classification == "external_boundary":
                boundary_role = "external_boundary"
            else:
                boundary_role = "none"
            topology[node] = topology_role; boundary[node] = boundary_role
            compatibility_role = (topology_role if topology_role in {
                "branch_junction", "branch_terminal", "terminal", "local_industrial"}
                else boundary_role if boundary_role != "none" else topology_role)
            if previous and previous != compatibility_role:
                result.role_changes[node] = {
                    "pre_split_node_role": previous,
                    "final_node_role": compatibility_role,
                    "topology_role": topology_role,
                    "boundary_role": boundary_role,
                    "role_change_reason": "final_operational_topology",
                }
        for node, previous in result.pre_split_node_roles.items():
            if node not in graph.nodes and previous == "local_industrial":
                topology[node] = "local_industrial"; boundary[node] = "none"
        result.topology_roles = topology
        result.boundary_roles = boundary
        result.node_roles = {
            node: (role if role in {"branch_junction", "branch_terminal", "terminal", "local_industrial"}
                   else boundary[node] if boundary[node] != "none" else role)
            for node, role in topology.items()
        }

    def build(self) -> CorridorGraph:
        result = CorridorGraph(self.axis)
        result.travel_time_stats = self._timings()
        self._current_travel_stats = result.travel_time_stats
        result.direction_changes = self._reversals()
        self._hidden_external_boundaries(result)
        hidden_sources = ({item.source_node for item in result.synthetic_external_boundaries.values()}
                          | set(result.explicit_external_boundaries))
        result.terminal_evidence = self._terminal_stats(result.direction_changes, hidden_sources)
        terminal_approach: dict[str, DirectionChangeEvidence] = {}
        for item in result.direction_changes:
            current = terminal_approach.get(item.terminal)
            if current is None or item.observations > current.observations:
                terminal_approach[item.terminal] = item

        # Phase D: immutable backbone. No edge has been called skip yet.
        self._backbone(result, terminal_approach)
        high_between_middles = {path[1] for path in result.applied_between_resolutions.values()}
        terminal_approach = {node: evidence for node, evidence in terminal_approach.items()
                             if node not in high_between_middles}

        # Phase E: classify raw directed observations only against fixed backbone.
        for pair, raw_edge in self.axis.edges.items():
            source, target = pair; key = frozenset(pair)
            classification, covered, confidence = "neighbour", (source, target), "inferred"
            evidence: dict[str, Any] = {"schedule_observations": raw_edge.observation_count}
            if key in result.applied_between_resolutions:
                path = result.applied_between_resolutions[key]
                if path[0] != source:
                    path = tuple(reversed(path))
                classification, covered = "skip", path
                evidence["between_final_action"] = "transitive_direct_edge_is_skip"
                evidence["applied_high_confidence_between"] = True
            elif key in result.backbone_edges:
                evidence["backbone"] = result.backbone_edges[key].evidence
                confidence = result.backbone_edges[key].confidence
            else:
                path = self._backbone_path(source, target, result)
                confirmed_between = bool(path and len(path) == 3 and result.between_evidence.get(
                    (path[0], path[1], path[2]), result.between_evidence.get(
                        (path[2], path[1], path[0]), {})).get("confidence") == "high")
                if path and len(path) > 2 and (confirmed_between or key not in result.backbone_candidates):
                    classification, covered = "skip", path
                    evidence["backbone_covered_path"] = True
                    for middle in path[1:-1]:
                        result.between_evidence.setdefault((source, middle, target), {}).update({
                            "backbone_path": path, "schedule_direct": raw_edge.observation_count,
                            "reverse_schedule": (target, source) in self.axis.edges,
                        })
                elif key in result.backbone_candidates:
                    classification = "alternative_route"
                    evidence["backbone_candidate"] = result.backbone_candidates[key]
                else:
                    classification = "alternative_route" if path else "unresolved"
            result.edges[pair] = DerivedRouteEdge(
                source, target, classification, evidence, covered, confidence,
                raw_edge.observation_count, tuple(sorted(raw_edge.services)),
            )

        for terminal, reversal in terminal_approach.items():
            result.node_roles[terminal] = "branch_terminal"
            for edge in result.edges.values():
                if terminal not in (edge.source, edge.target) or edge.classification == "skip":
                    continue
                other = edge.target if edge.source == terminal else edge.source
                if other == reversal.approach:
                    edge.classification = "branch"; edge.evidence["direction_change"] = reversal.observations
                elif frozenset((terminal, other)) not in result.backbone_edges:
                    edge.classification = "local_internal"

        # A strongly rejected between interpretation is a generic spur signal even
        # when the return working is represented by a separate STS schedule.
        rejected_middles = {middle for (_, middle, _), evidence in result.between_evidence.items()
                            if evidence.get("score", 0) < 0}
        rejected_middles -= high_between_middles
        for terminal in rejected_middles:
            incident = [key for key in result.backbone_edges if terminal in key]
            if len(incident) != 1:
                continue
            approach = next(iter(incident[0] - {terminal}))
            result.node_roles[terminal] = "branch_terminal"
            for edge in result.edges.values():
                if terminal in (edge.source, edge.target) and approach in (edge.source, edge.target):
                    edge.classification = "branch"
                    edge.evidence["negative_between_spur"] = True

        # Schedule-end terminals (e.g. terminus with separate outbound services).
        for node, evidence in result.terminal_evidence.items():
            if evidence.classification == "terminal":
                result.node_roles[node] = "terminal"
            elif evidence.classification == "external_boundary":
                result.node_roles[node] = "external_boundary"
            elif evidence.classification == "observed_schedule_boundary":
                result.node_roles[node] = "observed_schedule_boundary"

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
                        edge.classification = "local_internal"; edge.evidence["secondary_component"] = True

        neighbours: dict[str, set[str]] = defaultdict(set)
        for edge in result.visible_edges:
            neighbours[edge.source].add(edge.target); neighbours[edge.target].add(edge.source)
        for node in self.axis.nodes:
            if node in result.node_roles:
                continue
            degree = len(neighbours[node])
            result.node_roles[node] = "branch_junction" if degree > 2 else (
                "external_boundary" if degree == 1 else "mainline" if degree == 2 else "unresolved")
        for edge in result.edges.values():
            if edge.classification == "neighbour" and result.node_roles.get(edge.source) == "branch_junction":
                edge.classification = "branch"; edge.evidence["stable_branch_junction"] = True
        self._derive_branch_attachments(result, terminal_approach)
        self._finalize_node_roles(result)
        return result

    @staticmethod
    def _components(edges: Iterable[DerivedRouteEdge]) -> list[set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            adjacency[edge.source].add(edge.target); adjacency[edge.target].add(edge.source)
        result: list[set[str]] = []; unseen = set(adjacency)
        while unseen:
            start = unseen.pop(); component = {start}; stack = [start]
            while stack:
                node = stack.pop()
                for neighbour in adjacency[node] - component:
                    component.add(neighbour); unseen.discard(neighbour); stack.append(neighbour)
            result.append(component)
        return result
