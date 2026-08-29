"""GUI-unabhängige Geometrie der konfigurierten Bildfahrplan-X-Achse."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from infrastructure.editable_topology import EditableTopologyGraph

ROUTE_GAP_FRACTION = 0.025
ZERO_LENGTH_DISPLAY_WEIGHT_FRACTION = 0.05


@dataclass(frozen=True)
class AxisNodePosition:
    node_id: str
    label: str
    kilometre: float | None
    x: float


@dataclass(frozen=True)
class RouteDisplaySpan:
    instance_id: str
    route_id: str
    start_x: float
    end_x: float
    route_length: float
    nodes: tuple[AxisNodePosition, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteGap:
    start_x: float
    end_x: float


@dataclass(frozen=True)
class BildfahrplanXAxisLayout:
    routes: tuple[RouteDisplaySpan, ...]
    gaps: tuple[RouteGap, ...]
    diagnostics: tuple[str, ...] = ()


def build_bildfahrplan_x_axis(
    graph: EditableTopologyGraph, *, gap_fraction: float = ROUTE_GAP_FRACTION,
) -> BildfahrplanXAxisLayout:
    """Legt Instanzen in gespeicherter Reihenfolge auf die normierte Achse 0..1.

    Kilometer werden nur gelesen. Für jedes Segment wird der Betrag der
    Differenz benutzt; dadurch sind steigende und fallende Werte gleichwertig.
    """
    prepared = []
    global_diagnostics: list[str] = []
    for instance in sorted(graph.bildfahrplan_routes, key=lambda item: item.order):
        route = graph.defined_routes.get(instance.route_id)
        if route is None:
            global_diagnostics.append(f"Instanz {instance.instance_id}: Strecke {instance.route_id} fehlt.")
            continue
        ordered = list(route.ordered_node_ids)
        if instance.left_endpoint == route.endpoint_b:
            ordered.reverse()
        elif instance.left_endpoint != route.endpoint_a:
            global_diagnostics.append(
                f"Instanz {instance.instance_id}: linker Endpunkt ist ungültig; Definitionsrichtung verwendet.")
        values: list[float | None] = []
        diagnostics: list[str] = []
        for node_id in ordered:
            value = instance.kilometrage.get(node_id, route.default_kilometrage.get(node_id))
            try:
                parsed = float(value) if value is not None else None
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None and not isfinite(parsed):
                parsed = None
            values.append(parsed)
        complete = all(value is not None for value in values)
        if complete:
            segments = [abs(right - left) for left, right in zip(values, values[1:])]
        else:
            segments = [1.0] * max(0, len(ordered) - 1)
            diagnostics.append("Kilometrierung unvollständig; gleichmäßige Anzeige verwendet.")
        length = sum(segments)
        if length == 0 and len(ordered) > 1:
            display_segments = [1.0] * (len(ordered) - 1)
            diagnostics.append("Gesamtlänge 0 km; Knoten nur für die Anzeige gleichmäßig verteilt.")
        else:
            display_segments = segments
        prepared.append((instance, ordered, values, length, display_segments, diagnostics))

    count = len(prepared)
    if not count:
        return BildfahrplanXAxisLayout((), (), tuple(global_diagnostics))
    gap = max(0.0, min(float(gap_fraction), 1.0 / max(1, count - 1)))
    route_width = max(0.0, 1.0 - gap * (count - 1))
    lengths = [item[3] for item in prepared]
    positive_total = sum(lengths)
    if positive_total:
        zero_weight = positive_total * ZERO_LENGTH_DISPLAY_WEIGHT_FRACTION / count
        weights = [length if length > 0 else zero_weight for length in lengths]
    else:
        weights = [1.0] * count
    weight_total = sum(weights)

    spans: list[RouteDisplaySpan] = []
    gaps: list[RouteGap] = []
    cursor = 0.0
    for index, ((instance, ordered, values, length, segments, diagnostics), weight) in enumerate(
            zip(prepared, weights)):
        width = route_width * weight / weight_total if index < count - 1 else 1.0 - cursor
        end = cursor + width
        display_total = sum(segments)
        offsets = [0.0]
        for segment in segments:
            offsets.append(offsets[-1] + segment)
        nodes = tuple(AxisNodePosition(
            node_id, graph.nodes[node_id].display_name if node_id in graph.nodes else node_id,
            kilometre, cursor + (width * offset / display_total if display_total else 0.0),
        ) for node_id, kilometre, offset in zip(ordered, values, offsets))
        spans.append(RouteDisplaySpan(instance.instance_id, instance.route_id, cursor, end,
                                      length, nodes, tuple(diagnostics)))
        global_diagnostics.extend(f"Instanz {instance.instance_id}: {message}" for message in diagnostics)
        if index < count - 1:
            gaps.append(RouteGap(end, end + gap))
            cursor = end + gap
    return BildfahrplanXAxisLayout(tuple(spans), tuple(gaps), tuple(global_diagnostics))
