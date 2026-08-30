"""Testbare Plotdekorationen, direkt aus dem X-Achsenlayout abgeleitet."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Callable

from .x_axis import BildfahrplanXAxisLayout


@dataclass(frozen=True)
class LineSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: str
    instance_id: str


@dataclass(frozen=True)
class StationLabelPlacement:
    node_id: str
    text: str
    x: float
    pixel_x: float
    anchor_x: float
    rotation: int


@dataclass(frozen=True)
class StationRouteLabelLayout:
    instance_id: str
    orientation: str
    labels: tuple[StationLabelPlacement, ...]
    required_header_height: int


@dataclass(frozen=True)
class StationHeaderLayout:
    routes: tuple[StationRouteLabelLayout, ...]
    global_header_height: int


LABEL_HORIZONTAL_PADDING_PX = 6
LABEL_FRAME_GAP_PX = 6
LABEL_SAFETY_MARGIN_PX = 4
ONE_MINUTE_TICK_MIN_SPACING_PX = 20
TIME_TICK_INTERVALS_MINUTES = (1, 5, 10, 15, 30, 60)


@dataclass(frozen=True)
class TimeGridLine:
    time: float
    kind: str


def visible_hour_ticks(start: float, end: float) -> tuple[float, ...]:
    return tuple(float(value) for value in range(ceil(start / 3600) * 3600,
                                                 floor(end / 3600) * 3600 + 1, 3600))


def build_time_grid(start: float, end: float) -> tuple[TimeGridLine, ...]:
    """Klassifiziert jede sichtbare 5-Minuten-Marke genau einmal."""
    step = 5 * 60
    first = ceil(start / step) * step
    result = []
    for value in range(first, floor(end / step) * step + 1, step):
        minute = (value // 60) % 60
        kind = "full_hour" if minute == 0 else "quarter_hour" if minute % 15 == 0 else "five_minute"
        result.append(TimeGridLine(float(value), kind))
    return tuple(result)


def choose_time_tick_interval(duration_seconds: float, plot_height_px: float,
                              font_height_px: float) -> int:
    """Wählt 1 Minute nur bei genug Pixelplatz, sonst bevorzugt 5 Minuten."""
    duration_minutes = max(duration_seconds / 60, 1 / 60)
    pixels_per_minute = max(0.0, plot_height_px) / duration_minutes
    required = max(ONE_MINUTE_TICK_MIN_SPACING_PX, font_height_px + 4)
    for interval in TIME_TICK_INTERVALS_MINUTES:
        if pixels_per_minute * interval >= required:
            return interval
    return TIME_TICK_INTERVALS_MINUTES[-1]


def build_time_axis_ticks(start: float, end: float, interval_minutes: int) -> tuple[float, ...]:
    step = interval_minutes * 60
    first = ceil(start / step) * step
    return tuple(float(value) for value in range(first, floor(end / step) * step + 1, step))


def build_route_plot_segments(layout: BildfahrplanXAxisLayout, start: float, end: float,
                              y_ticks: tuple[TimeGridLine | float, ...]) -> tuple[LineSegment, ...]:
    """Erzeugt Rahmen und Grid ausschließlich innerhalb jeder RouteSpan."""
    result: list[LineSegment] = []
    for route in layout.routes:
        for tick in y_ticks:
            y = tick.time if isinstance(tick, TimeGridLine) else tick
            kind = f"grid_{tick.kind}" if isinstance(tick, TimeGridLine) else "time_grid"
            result.append(LineSegment(route.start_x, y, route.end_x, y, kind, route.instance_id))
        for node in route.nodes:
            result.append(LineSegment(node.x, start, node.x, end, "station_grid", route.instance_id))
        result.extend((
            LineSegment(route.start_x, start, route.end_x, start, "frame", route.instance_id),
            LineSegment(route.start_x, end, route.end_x, end, "frame", route.instance_id),
            LineSegment(route.start_x, start, route.start_x, end, "frame", route.instance_id),
            LineSegment(route.end_x, start, route.end_x, end, "frame", route.instance_id),
        ))
    return tuple(result)


def build_station_header_layout(
    layout: BildfahrplanXAxisLayout,
    pixel_x: Callable[[float], float],
    text_size: Callable[[str], tuple[int, int]],
) -> StationHeaderLayout:
    """Entscheidet pro Route anhand tatsächlicher Pixelboxen über die Drehung."""
    routes = []
    for route in layout.routes:
        measured = [(node, pixel_x(node.x), *text_size(node.label)) for node in route.nodes]
        bounds = []
        for index, (_node, x, width, _height) in enumerate(measured):
            if index == 0:
                bounds.append((x, x + width))
            elif index == len(measured) - 1:
                bounds.append((x - width, x))
            else:
                bounds.append((x - width / 2, x + width / 2))
        overlaps = any(
            left[1] + LABEL_HORIZONTAL_PADDING_PX > right[0]
            for left, right in zip(bounds, bounds[1:])
        )
        orientation = "vertical" if overlaps else "horizontal"
        rotation = -90 if overlaps else 0
        max_width = max((width for _node, _x, width, _height in measured), default=0)
        max_height = max((height for _node, _x, _width, height in measured), default=0)
        labels = []
        for index, node in enumerate(route.nodes):
            anchor_x = 0.0 if index == 0 else 1.0 if index == len(route.nodes) - 1 else 0.5
            labels.append(StationLabelPlacement(
                node.node_id, node.label, node.x, measured[index][1], anchor_x, rotation,
            ))
        content_height = max_width if overlaps else max_height
        routes.append(StationRouteLabelLayout(
            route.instance_id, orientation, tuple(labels),
            content_height + LABEL_FRAME_GAP_PX + LABEL_SAFETY_MARGIN_PX,
        ))
    global_height = max((route.required_header_height for route in routes), default=0)
    return StationHeaderLayout(tuple(routes), global_height)
