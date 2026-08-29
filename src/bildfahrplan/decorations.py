"""Testbare Plotdekorationen, direkt aus dem X-Achsenlayout abgeleitet."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

from .x_axis import BildfahrplanXAxisLayout


@dataclass(frozen=True)
class LineSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: str
    instance_id: str


def visible_hour_ticks(start: float, end: float) -> tuple[float, ...]:
    return tuple(float(value) for value in range(ceil(start / 3600) * 3600,
                                                 floor(end / 3600) * 3600 + 1, 3600))


def build_route_plot_segments(layout: BildfahrplanXAxisLayout, start: float, end: float,
                              y_ticks: tuple[float, ...]) -> tuple[LineSegment, ...]:
    """Erzeugt Rahmen und Grid ausschließlich innerhalb jeder RouteSpan."""
    result: list[LineSegment] = []
    for route in layout.routes:
        for y in y_ticks:
            result.append(LineSegment(route.start_x, y, route.end_x, y, "time_grid", route.instance_id))
        for node in route.nodes:
            result.append(LineSegment(node.x, start, node.x, end, "station_grid", route.instance_id))
        result.extend((
            LineSegment(route.start_x, start, route.end_x, start, "frame", route.instance_id),
            LineSegment(route.start_x, end, route.end_x, end, "frame", route.instance_id),
            LineSegment(route.start_x, start, route.start_x, end, "frame", route.instance_id),
            LineSegment(route.end_x, start, route.end_x, end, "frame", route.instance_id),
        ))
    return tuple(result)
