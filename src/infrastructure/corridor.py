"""Backbone-first reconstruction of operational corridors from schedule evidence."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable

from .model import OperationalRouteEdge, OperationalRouteGraph, OperationalRouteNode, RawInfrastructureGraph
from .schedule_graph import OperatingPointGraph, RouteAxisGraph, ScheduleEdge, SchedulePointGraph

DAY_SECONDS = 86400


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
    direction_changes: list[DirectionChangeEvidence] = field(default_factory=list)
    terminal_evidence: dict[str, TerminalEvidence] = field(default_factory=dict)
    travel_time_stats: dict[tuple[str, ...], PathTimeStats] = field(default_factory=dict)
    between_evidence: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    triangle_resolutions: list[TriangleResolutionEvidence] = field(default_factory=list)
    raw_adjacency_evidence: dict[frozenset[str], RawAdjacencyEvidence] = field(default_factory=dict)
    backbone_scores: dict[frozenset[str], BackboneScore] = field(default_factory=dict)
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
                edge.source, edge.target, 1.0, {edge.classification: edge.observations},
                edge.confidence, edge.covered_path,
            ))
        return graph


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

    def _terminal_stats(self, reversals: list[DirectionChangeEvidence]) -> dict[str, TerminalEvidence]:
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
        """Conservative first-anchor projection; raw elements never become visible nodes."""
        if self.raw_graph is None:
            return 0, ()
        anchors = self._raw_anchors()
        reverse = {raw: axis for axis, values in anchors.items() for raw in values}
        found: set[str] = set(); exits = 0
        for start in anchors.get(node, ()):
            for first in self.raw_graph.neighbours(start):
                queue = deque([(first, start)]); visited = {start}
                reached = False
                while queue:
                    current, previous = queue.popleft()
                    if current in visited:
                        continue
                    visited.add(current)
                    if current in reverse and reverse[current] != node:
                        found.add(reverse[current]); reached = True; break
                    following = self.raw_graph.neighbours(current) - {previous}
                    if not following:
                        exits += 1; reached = True; break
                    queue.extend((item, current) for item in following)
                if not reached:
                    exits += 1
        labels = tuple(sorted(found | ({"raw_graph_boundary"} if exits else set())))
        return len(found) + (1 if exits else 0), labels

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

    def _triangles(self, result: CorridorGraph, undirected: dict[frozenset[str], dict[str, Any]]) -> None:
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
                        if direct and via:
                            ratio = direct.movement.median / max(via.movement.median, 1.0)
                            if ratio >= .8:
                                score += 3.0
                                support.append(
                                    f"via_movement_plausible:{via.movement.median:g}s/"
                                    f"direct:{direct.movement.median:g}s")
                            elif ratio <= .6:
                                score -= 4.0
                                contradict.append(
                                    f"direct_much_faster:{direct.movement.median:g}s/"
                                    f"via:{via.movement.median:g}s")
                            if via.dwell.median:
                                (contradict if ratio <= .6 else support).append(f"via_dwell:{via.dwell.median:g}s")
                        chain = tuple(tuple(sorted((middle, endpoint))) for endpoint in outer)
                        chain_support = min(undirected[frozenset(edge)]["direct"] for edge in chain)
                        direct_support = undirected[frozenset(outer)]["direct"]
                        if chain_support >= direct_support:
                            score += 1.0; support.append("chain_schedule_support")
                        raw_chain = sum(frozenset(edge) in result.raw_adjacency_evidence for edge in chain)
                        raw_direct = frozenset(outer) in result.raw_adjacency_evidence
                        if raw_chain == 2 and not raw_direct:
                            score += 2.0; support.append("raw_chain_adjacency")
                        interpretations.append((score, middle, tuple(support), tuple(contradict), outer, chain))
                    best = max(interpretations, key=lambda item: item[0])
                    positive = best[0] >= 3.0
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
                        }

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

        for key, evidence in ranked:
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

    def build(self) -> CorridorGraph:
        result = CorridorGraph(self.axis)
        result.travel_time_stats = self._timings()
        result.direction_changes = self._reversals()
        result.terminal_evidence = self._terminal_stats(result.direction_changes)
        terminal_approach: dict[str, DirectionChangeEvidence] = {}
        for item in result.direction_changes:
            current = terminal_approach.get(item.terminal)
            if current is None or item.observations > current.observations:
                terminal_approach[item.terminal] = item

        # Phase D: immutable backbone. No edge has been called skip yet.
        self._backbone(result, terminal_approach)

        # Phase E: classify raw directed observations only against fixed backbone.
        for pair, raw_edge in self.axis.edges.items():
            source, target = pair; key = frozenset(pair)
            classification, covered, confidence = "neighbour", (source, target), "inferred"
            evidence: dict[str, Any] = {"schedule_observations": raw_edge.observation_count}
            if key in result.backbone_edges:
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
