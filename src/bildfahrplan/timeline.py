"""GUI-unabhaengige Umwandlung von Collector-Fahrplaenen in Plotdaten."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .profile import RouteProfile

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


@dataclass(frozen=True)
class TrainTrace:
    zid: int
    label: str
    planned: tuple[PlotPoint, ...]
    projected: tuple[PlotPoint, ...]


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
            result.append(PlotPoint(current, location.position, raw_name, kind))
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
