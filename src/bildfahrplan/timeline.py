"""GUI-unabhaengige Umwandlung von Collector-Fahrplaenen in Plotdaten."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Protocol

from .profile import OperatingPoint, RouteProfile

DAY_SECONDS = 24 * 60 * 60
DISTANCE_AXIS = "x"
TIME_AXIS = "y"
NOW_LINE_ANGLE = 0


class ScheduleLike(Protocol):
    planned_name: str
    raw_name: str
    planned_arrival: str | None
    planned_departure: str | None


@dataclass(frozen=True)
class PlotPoint:
    time_seconds: int
    position: float
    raw_name: str
    kind: str
    actual: bool = False
    source: str = "schedule"
    node_id: str | None = None
    direction: str | None = None
    instance_id: str | None = None
    route_id: str | None = None


@dataclass(frozen=True)
class TrainTrace:
    zid: int
    label: str
    planned: tuple[PlotPoint, ...]
    projected: tuple[PlotPoint, ...]


@dataclass(frozen=True)
class BoundaryEndpoint:
    node_id: str
    position: float
    raw_names: tuple[str, ...]


@dataclass(frozen=True)
class BoundaryRouteProjection:
    instance_id: str
    node_positions: tuple[float, ...]
    endpoints: tuple[BoundaryEndpoint, ...]


@dataclass(frozen=True)
class RouteInstanceProjectionPoint:
    instance_id: str
    route_id: str
    node_id: str
    position: float
    label: str
    raw_names: tuple[str, ...]


@dataclass(frozen=True)
class RouteInstanceProjection:
    instance_id: str
    route_id: str
    points: tuple[RouteInstanceProjectionPoint, ...]
    boundaries: tuple[BoundaryEndpoint, ...] = ()


@dataclass(frozen=True)
class RouteInstanceTrainSegment:
    instance_id: str
    route_id: str
    zid: int
    label: str
    planned: tuple[PlotPoint, ...]
    projected: tuple[PlotPoint, ...]


@dataclass(frozen=True)
class MinuteEventLabel:
    index: int
    position: float
    time_seconds: int
    text: str
    kind: str


@dataclass(frozen=True)
class ColoredMinuteEventLabel:
    event: MinuteEventLabel
    color: str


def parse_clock(value: str) -> int:
    """Konvertiert HH:MM[:SS] in Sekunden seit Tagesbeginn."""
    parts = value.strip().split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Ungueltige Fahrplanzeit: {value!r}")
    hour, minute, second = map(int, (*parts, "0") if len(parts) == 2 else parts)
    if not 0 <= hour < 24 or not 0 <= minute < 60 or not 0 <= second < 60:
        raise ValueError(f"Ungueltige Fahrplanzeit: {value!r}")
    return hour * 3600 + minute * 60 + second


def unwrap_time(seconds: int, reference: int) -> int:
    """Waehlt den zum Referenzzeitpunkt naechsten Simulationstag."""
    day = round((reference - seconds) / DAY_SECONDS)
    return seconds + day * DAY_SECONDS


def format_axis_time(seconds: float, with_seconds: bool = False) -> str:
    value = int(seconds) % DAY_SECONDS
    hour, rest = divmod(value, 3600)
    minute, second = divmod(rest, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}" if with_seconds else f"{hour:02d}:{minute:02d}"


def schedule_to_points(
    schedule: Iterable[ScheduleLike], profile: RouteProfile, reference_seconds: int,
    offset_seconds: int = 0,
) -> tuple[PlotPoint, ...]:
    """Erzeugt Ankunft/Abfahrt getrennt; unbekannte Raw-Namen bleiben unaufgeloest."""
    result: list[PlotPoint] = []
    previous: int | None = None
    for schedule_point in schedule:
        point_start = len(result)
        # Fuer die Plantrasse ist der unveraenderliche Planname massgeblich.
        raw_name = schedule_point.planned_name or schedule_point.raw_name
        location = profile.resolve(raw_name)
        if location is None:
            continue
        values = (("arrival", schedule_point.planned_arrival), ("departure", schedule_point.planned_departure))
        for kind, raw_time in values:
            if not raw_time:
                continue
            current = unwrap_time(parse_clock(raw_time), previous if previous is not None else reference_seconds)
            while previous is not None and current < previous:
                current += DAY_SECONDS
            current += offset_seconds
            result.append(PlotPoint(current, location.position, raw_name, kind, node_id=location.id))
            previous = current - offset_seconds
        # Eine Durchfahrt mit identischen an/ab-Zeiten braucht nur einen Punkt.
        if len(result) - point_start == 2 and result[-1].time_seconds == result[-2].time_seconds:
            result.pop()
    return tuple(result)


def is_renderable_service(service: object) -> bool:
    return getattr(service, "service_kind", "unknown") == "train"


def build_trace(service: object, profile: RouteProfile, reference_seconds: int) -> TrainTrace | None:
    if not is_renderable_service(service):
        return None
    # Absichtlich niemals current_schedule als Ersatz fuer die Planbasis verwenden.
    original = getattr(service, "original_schedule", ())
    planned = schedule_to_points(original, profile, reference_seconds)
    if not planned:
        return None
    delay = getattr(service, "current_delay", None) or 0
    projected = schedule_to_points(original, profile, reference_seconds, delay * 60)
    suffix = f" {delay:+d}" if delay else ""
    return TrainTrace(getattr(service, "zid"), f"{getattr(service, 'name', '')}{suffix}", planned, projected)


def build_route_instance_train_segments(
    service: object, routes: Iterable[RouteInstanceProjection], reference_seconds: int,
) -> tuple[RouteInstanceTrainSegment, ...]:
    """Projiziert eine Fahrplanfolge unabhängig in jede passende RouteInstance."""
    if not is_renderable_service(service):
        return ()
    schedule = tuple(getattr(service, "original_schedule", ()))
    result = []
    for route in routes:
        selected = _matching_schedule_run(schedule, route)
        if selected is None:
            continue
        profile = RouteProfile(
            route.instance_id,
            tuple(OperatingPoint(point.node_id, point.label, point.position, point.raw_names)
                  for point in route.points),
        )
        trace = build_trace(replace_service_schedule(service, selected), profile, reference_seconds)
        if trace is None:
            continue
        boundary = BoundaryRouteProjection(
            route.instance_id, tuple(point.position for point in route.points), route.boundaries)
        trace = extend_trace_to_boundaries(service, trace, (boundary,))
        annotate = lambda point: replace(
            point, instance_id=route.instance_id, route_id=route.route_id)
        result.append(RouteInstanceTrainSegment(
            route.instance_id, route.route_id, trace.zid, trace.label,
            tuple(annotate(point) for point in trace.planned),
            tuple(annotate(point) for point in trace.projected),
        ))
    return tuple(result)


def build_minute_event_labels(points: Iterable[PlotPoint]) -> tuple[MinuteEventLabel, ...]:
    """Erzeugt zweistellige Minuten aus vorhandenen Ankunfts-/Abfahrtspunkten."""
    result = []
    seen = set()
    for index, point in enumerate(points):
        if point.kind not in {"arrival", "departure"}:
            continue
        identity = (point.node_id or point.raw_name, point.position, point.time_seconds)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(MinuteEventLabel(
            index, point.position, point.time_seconds,
            f"{(point.time_seconds // 60) % 60:02d}", point.kind,
        ))
    return tuple(result)


def build_colored_minute_event_labels(
    points: Iterable[PlotPoint], train_color: str,
) -> tuple[ColoredMinuteEventLabel, ...]:
    """Bindet Minutenlabels ohne zweite Farbentscheidung an die Zuggrundfarbe."""
    return tuple(ColoredMinuteEventLabel(event, train_color)
                 for event in build_minute_event_labels(points))


def replace_service_schedule(service: object, schedule: tuple[object, ...]) -> object:
    """Kleine unveränderliche Sicht auf einen Service mit gefiltertem Plan."""
    class ServiceView:
        pass
    view = ServiceView()
    view.__dict__.update(getattr(service, "__dict__", {}))
    for name in ("zid", "name", "service_kind", "current_delay", "origin", "destination"):
        if hasattr(service, name):
            setattr(view, name, getattr(service, name))
    view.original_schedule = schedule
    return view


def _matching_schedule_run(
    schedule: tuple[object, ...], route: RouteInstanceProjection,
) -> tuple[object, ...] | None:
    raw_to_index = {
        raw_name: index
        for index, point in enumerate(route.points)
        for raw_name in point.raw_names
    }
    mapped = []
    for schedule_point in schedule:
        raw_name = getattr(schedule_point, "planned_name", None) or getattr(schedule_point, "raw_name", None)
        if raw_name in raw_to_index:
            mapped.append((schedule_point, raw_to_index[raw_name]))
    distinct = []
    for _point, index in mapped:
        if not distinct or distinct[-1] != index:
            distinct.append(index)
    if len(distinct) < 2:
        return None
    increasing = all(left < right for left, right in zip(distinct, distinct[1:]))
    decreasing = all(left > right for left, right in zip(distinct, distinct[1:]))
    if not increasing and not decreasing:
        return None
    return tuple(point for point, _index in mapped)


def extend_trace_to_boundaries(
    service: object, trace: TrainTrace, routes: Iterable[BoundaryRouteProjection],
) -> TrainTrace:
    """Extrapoliert ausschließlich exakt zugeordnete äußere Zuggrenzen.

    Die erzeugten Punkte sind keine STS-Planzeiten. Ihre Quelle bleibt am
    Polyline-Punkt als ``extrapolated_from_adjacent_segment`` erkennbar.
    """
    origin = _normal_text(getattr(service, "origin", None))
    destination = _normal_text(getattr(service, "destination", None))
    planned = trace.planned
    projected = trace.projected
    for route in routes:
        for endpoint in route.endpoints:
            names = {_normal_text(name) for name in endpoint.raw_names if _normal_text(name)}
            if origin and origin in names:
                planned = _extend_points(planned, route, endpoint, "entry")
                projected = _extend_points(projected, route, endpoint, "entry")
            if destination and destination in names:
                planned = _extend_points(planned, route, endpoint, "exit")
                projected = _extend_points(projected, route, endpoint, "exit")
    return TrainTrace(trace.zid, trace.label, planned, projected)


def _normal_text(value: object) -> str:
    return str(value).strip().casefold() if value is not None else ""


def _extend_points(points: tuple[PlotPoint, ...], route: BoundaryRouteProjection,
                   endpoint: BoundaryEndpoint, direction: str) -> tuple[PlotPoint, ...]:
    positions = set(route.node_positions)
    relevant = [point for point in points if point.position in positions]
    endpoint_names = {_normal_text(name) for name in endpoint.raw_names}
    if not relevant or any(_normal_text(point.raw_name) in endpoint_names for point in relevant):
        return points
    groups: list[list[PlotPoint]] = []
    for point in relevant:
        if groups and groups[-1][0].raw_name == point.raw_name:
            groups[-1].append(point)
        else:
            groups.append([point])
    if len(groups) < 2:
        return points
    if direction == "entry":
        boundary_group = groups[0]
        slope = _movement_slope(groups, from_start=True)
        join = next((point for point in boundary_group if point.kind == "arrival"), boundary_group[0])
        sign = -1
    else:
        boundary_group = groups[-1]
        slope = _movement_slope(groups, from_start=False)
        join = next((point for point in reversed(boundary_group) if point.kind == "departure"),
                    boundary_group[-1])
        sign = 1
    if slope is None:
        return points
    duration_per_x, inner_position = slope
    duration = abs(endpoint.position - inner_position) * duration_per_x
    boundary = PlotPoint(
        int(round(join.time_seconds + sign * duration)), endpoint.position, endpoint.raw_names[0],
        f"boundary_{direction}", source="extrapolated_from_adjacent_segment",
        node_id=endpoint.node_id, direction=direction,
    )
    return (boundary, *points) if direction == "entry" else (*points, boundary)


def _movement_slope(groups: list[list[PlotPoint]], *, from_start: bool) -> tuple[float, float] | None:
    pairs = zip(groups, groups[1:]) if from_start else zip(reversed(groups[:-1]), reversed(groups[1:]))
    for left, right in pairs:
        left_departure = next((point for point in reversed(left) if point.kind == "departure"), left[-1])
        right_arrival = next((point for point in right if point.kind == "arrival"), right[0])
        distance = abs(right_arrival.position - left_departure.position)
        duration = right_arrival.time_seconds - left_departure.time_seconds
        if distance > 0 and duration >= 0:
            inner_position = left_departure.position if from_start else right_arrival.position
            return duration / distance, inner_position
    return None
