"""GUI-unabhängige Schätzung und Validierung von Streckenkilometern.

Die Schätzung ist ausdrücklich eine korrigierbare Darstellungshilfe.  Sie
leitet keine physische Gleislänge aus den STS-Daten ab.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Iterable, Mapping, Sequence

DAY_SECONDS = 24 * 60 * 60
KM_PER_MINUTE = 100.0 / 60.0


@dataclass(frozen=True)
class KilometrageValidation:
    valid: bool
    values: tuple[float, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class KilometrageEstimate:
    kilometres: tuple[float, ...]
    segment_minutes: tuple[float, ...]


def validate_kilometrage(values: Iterable[object], expected_count: int) -> KilometrageValidation:
    raw = list(values)
    if len(raw) != expected_count:
        return KilometrageValidation(False, message="Die Anzahl der Kilometerangaben passt nicht zur Strecke.")
    if any(value is None or (isinstance(value, str) and not value.strip()) for value in raw):
        return KilometrageValidation(False, message="Leere Kilometerzellen sind nicht zulässig.")
    try:
        parsed = tuple(float(value.replace(",", ".")) if isinstance(value, str) else float(value)
                       for value in raw)
    except (TypeError, ValueError):
        return KilometrageValidation(False, message="Kilometerangaben müssen vollständig numerisch sein.")
    if not all(isfinite(value) for value in parsed):
        return KilometrageValidation(False, message="Kilometerangaben müssen endliche Zahlen sein.")
    increasing = all(left <= right for left, right in zip(parsed, parsed[1:]))
    decreasing = all(left >= right for left, right in zip(parsed, parsed[1:]))
    if not increasing and not decreasing:
        return KilometrageValidation(
            False, parsed, "Kilometerangaben müssen insgesamt monoton steigen oder fallen.")
    return KilometrageValidation(True, parsed)


def _clock(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return hour * 3600 + minute * 60 + second


def _elapsed_minutes(left: object, right: object) -> float | None:
    departure = _clock(getattr(left, "planned_departure", None))
    if departure is None:
        departure = _clock(getattr(left, "planned_arrival", None))
    arrival = _clock(getattr(right, "planned_arrival", None))
    if arrival is None:
        arrival = _clock(getattr(right, "planned_departure", None))
    if departure is None or arrival is None:
        return None
    elapsed = (arrival - departure) % DAY_SECONDS
    # Fahrten von mehr als zwölf Stunden sind für eine benachbarte lokale
    # Streckenbeobachtung keine belastbare Evidenz.
    return elapsed / 60.0 if 0 < elapsed <= DAY_SECONDS / 2 else None


def estimate_kilometrage(
    ordered_node_ids: Sequence[str], node_types: Mapping[str, str], services: Iterable[object],
    raw_to_node: Mapping[str, str], *, edge_fallback_minutes: float = 3.0,
) -> KilometrageEstimate:
    """Schätzt kumulative Kilometer aus Medianen planmäßiger Fahrzeiten.

    Eine Beobachtung, die mehrere Streckenpunkte überspringt, wird gleichmäßig
    auf die unbekannten Teilstücke verteilt. Direkte Beobachtungen haben dabei
    Vorrang. Fehlende Randsegmente an Einfahrten erhalten drei Minuten. Für
    vollständig unbeobachtete innere Segmente wird die robuste Mitte der
    bekannten Segmente (sonst ebenfalls drei Minuten) verwendet.
    """
    count = len(ordered_node_ids)
    if count == 0:
        return KilometrageEstimate((), ())
    index = {node_id: position for position, node_id in enumerate(ordered_node_ids)}
    intervals: dict[tuple[int, int], list[float]] = {}
    for service in services:
        if getattr(service, "service_kind", "train") != "train":
            continue
        mapped: list[tuple[int, object]] = []
        for point in getattr(service, "original_schedule", ()):
            raw = getattr(point, "planned_name", None) or getattr(point, "raw_name", None)
            node_id = raw_to_node.get(raw)
            if node_id not in index:
                continue
            current = index[node_id]
            if mapped and mapped[-1][0] == current:
                mapped[-1] = (current, point)
            else:
                mapped.append((current, point))
        for (left_index, left), (right_index, right) in zip(mapped, mapped[1:]):
            if left_index == right_index:
                continue
            duration = _elapsed_minutes(left, right)
            if duration is None:
                continue
            low, high = sorted((left_index, right_index))
            intervals.setdefault((low, high), []).append(duration)

    segments: list[float | None] = [None] * max(0, count - 1)
    for position in range(len(segments)):
        direct = intervals.get((position, position + 1))
        if direct:
            segments[position] = float(median(direct))

    # Kürzestes belastbares überspannendes Intervall füllt eine zusammenhängende
    # Lücke gleichmäßig; bereits direkt bekannte Segmente bleiben unverändert.
    for (left, right), observations in sorted(intervals.items(), key=lambda item: item[0][1] - item[0][0]):
        unknown = [position for position in range(left, right) if segments[position] is None]
        known_total = sum(segments[position] or 0.0 for position in range(left, right)
                         if segments[position] is not None)
        remaining = float(median(observations)) - known_total
        if unknown and remaining > 0:
            share = remaining / len(unknown)
            for position in unknown:
                segments[position] = share

    if segments and segments[0] is None and node_types.get(ordered_node_ids[0]) == "entry":
        segments[0] = edge_fallback_minutes
    if segments and segments[-1] is None and node_types.get(ordered_node_ids[-1]) == "entry":
        segments[-1] = edge_fallback_minutes
    known = [value for value in segments if value is not None]
    inner_fallback = float(median(known)) if known else edge_fallback_minutes
    completed = tuple(value if value is not None else inner_fallback for value in segments)
    kilometres = [0.0]
    for minutes in completed:
        kilometres.append(kilometres[-1] + minutes * KM_PER_MINUTE)
    return KilometrageEstimate(tuple(kilometres), completed)
