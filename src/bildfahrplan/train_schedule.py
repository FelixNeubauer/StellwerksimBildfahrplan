"""GUI-unabhaengige Aufbereitung eines vollstaendigen Zugfahrplans."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable

from .timeline import format_axis_time, parse_clock


@dataclass(frozen=True)
class TrainScheduleRow:
    source_index: int
    operating_point: str
    raw_schedule_name: str
    arrival: str
    departure: str
    flags: tuple[str, ...]
    raw_flags: str
    completed: bool
    group_index: int


@dataclass(frozen=True)
class TrainScheduleViewModel:
    zid: int
    train_name: str
    origin: str
    destination: str
    delay: int | None
    rows: tuple[TrainScheduleRow, ...]
    in_current_snapshot: bool

    @property
    def signature(self) -> tuple[object, ...]:
        return (self.train_name, self.origin, self.destination, self.delay,
                self.rows, self.in_current_snapshot)


# Nur fachlich im Handbuch bzw. durch die STS-Fahrplanattribute belegte Codes
# werden ausgeschrieben. Unbekannte Tokens bleiben verlustfrei technisch sichtbar.
FLAG_LABELS = {
    "D": "Durchfahrt",
    "A": "Frühere Abfahrt möglich",
    "R": "Wendet",
    "K": "Kuppelt",
    "F": "Flügelt",
    "E": "Neuer Fahrplan",
    "L": "Setzt Lok um",
}
_FLAG_TOKEN = re.compile(r"([A-Z])(?:\[([^]]*)\]|\(([^)]*)\))?")


def format_schedule_flags(entry: object) -> tuple[str, ...]:
    """Formatiert bekannte Flags deutsch und erhaelt unbekannte Rohinformation."""
    raw = str(getattr(entry, "flags_raw", "") or "").strip()
    if not raw:
        return ()
    result: list[str] = []
    consumed = [False] * len(raw)
    for match in _FLAG_TOKEN.finditer(raw):
        consumed[match.start():match.end()] = [True] * (match.end() - match.start())
        token, square, round_value = match.groups()
        if token == "P" and square is not None:
            continue
        label = FLAG_LABELS.get(token)
        rendered = label if label else match.group(0)
        detail = square if square is not None else round_value
        if detail and label and token not in {"E", "F", "K"}:
            rendered = f"{rendered} ({detail})"
        if rendered not in result:
            result.append(rendered)
    remainder = "".join(char for index, char in enumerate(raw) if not consumed[index]).strip()
    if remainder and remainder not in result:
        result.append(remainder)
    return tuple(result)


def sequential_group_indices(operating_points: Iterable[str]) -> tuple[int, ...]:
    result: list[int] = []
    previous = object()
    group = -1
    for value in operating_points:
        if value != previous:
            group += 1
            previous = value
        result.append(group)
    return tuple(result)


def _identity(point: object) -> tuple[str, str | None, str | None]:
    return (str(getattr(point, "planned_name", None) or getattr(point, "raw_name", "")),
            getattr(point, "planned_arrival", None), getattr(point, "planned_departure", None))


def remaining_indices(original: Iterable[object], current: Iterable[object]) -> frozenset[int]:
    """Ordnet den Restfahrplan reihenfolgestabil der spaetesten passenden Teilfolge zu.

    Die spaeteste vollstaendige Zuordnung verhindert bei wiederholten identischen
    Punkten, dass ein bereits abgearbeiteter frueherer Halt aktiv bleibt.
    """
    originals = tuple(map(_identity, original))
    currents = tuple(map(_identity, current))
    if not currents:
        return frozenset()
    solutions: list[tuple[int, ...]] = []

    def match(current_index: int, start: int, chosen: tuple[int, ...]) -> None:
        if current_index == len(currents):
            solutions.append(chosen)
            return
        for index in range(start, len(originals)):
            if originals[index] == currents[current_index]:
                match(current_index + 1, index + 1, (*chosen, index))

    match(0, 0, ())
    return frozenset(max(solutions, key=lambda value: value[0])) if solutions else frozenset()


def _clock(value: str | None) -> str:
    if not value:
        return ""
    try:
        return format_axis_time(parse_clock(value))
    except (ValueError, AttributeError):
        return str(value)


def build_train_schedule_view_model(
    service: object, operating_point_for: Callable[[str, object], str | None] | None = None,
    *, in_current_snapshot: bool = True,
) -> TrainScheduleViewModel:
    original = tuple(getattr(service, "original_schedule", ()))
    current = tuple(getattr(service, "current_schedule", ())) if in_current_snapshot else ()
    active = remaining_indices(original, current)
    ops = []
    for point in original:
        raw = str(getattr(point, "planned_name", None) or getattr(point, "raw_name", ""))
        mapped = getattr(point, "operating_point", None)
        if not mapped and operating_point_for:
            mapped = operating_point_for(raw, point)
        ops.append(str(mapped or raw))
    groups = sequential_group_indices(ops)
    origin = str(getattr(service, "origin", "") or "")
    destination = str(getattr(service, "destination", "") or "")
    rows = tuple(TrainScheduleRow(
        index, ops[index],
        str(getattr(point, "planned_name", None) or getattr(point, "raw_name", "")),
        _clock(getattr(point, "planned_arrival", None)),
        _clock(getattr(point, "planned_departure", None)),
        format_schedule_flags(point), str(getattr(point, "flags_raw", "") or ""),
        index not in active, groups[index],
    ) for index, point in enumerate(original))
    return TrainScheduleViewModel(
        int(getattr(service, "zid")), str(getattr(service, "name", "")),
        origin, destination, getattr(service, "current_delay", None), rows, in_current_snapshot,
    )
