"""Deterministische, GUI-unabhängige Farbvergabe für nahe Zugtrassen."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import sqrt
from typing import Iterable

from app.settings import COLORFUL_TRAIN_COLORS

COLOR_CONFLICT_TIME_SECONDS = 5 * 60
COLOR_CONFLICT_X_DISTANCE = 0.03


@dataclass(frozen=True)
class TrainSegmentExtent:
    instance_id: str
    min_x: float
    max_x: float
    min_time: float
    max_time: float


@dataclass(frozen=True)
class VisibleTrainGeometry:
    zid: int
    extents: tuple[TrainSegmentExtent, ...]


def _interval_distance(left_min: float, left_max: float, right_min: float, right_max: float) -> float:
    return max(0.0, left_min - right_max, right_min - left_max)


def trains_conflict(left: VisibleTrainGeometry, right: VisibleTrainGeometry) -> bool:
    return any(
        a.instance_id == b.instance_id
        and _interval_distance(a.min_x, a.max_x, b.min_x, b.max_x) <= COLOR_CONFLICT_X_DISTANCE
        and _interval_distance(a.min_time, a.max_time, b.min_time, b.max_time) <= COLOR_CONFLICT_TIME_SECONDS
        for a in left.extents for b in right.extents
    )


@lru_cache(maxsize=None)
def _oklab(color: str) -> tuple[float, float, float]:
    values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
              for value in values]
    red, green, blue = linear
    l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


@lru_cache(maxsize=None)
def color_distance(left: str, right: str) -> float:
    return sqrt(sum((a - b) ** 2 for a, b in zip(_oklab(left), _oklab(right))))


def assign_colorful_train_colors(
    geometries: Iterable[VisibleTrainGeometry], existing: dict[int, str] | None = None,
) -> dict[int, str]:
    """Behält vorhandene Farben und färbt nur neue Züge kontrastreich ein."""
    trains = {item.zid: item for item in geometries}
    result = {zid: color for zid, color in (existing or {}).items()
              if zid in trains and color in COLORFUL_TRAIN_COLORS}
    neighbors = {zid: set() for zid in trains}
    ordered = sorted(trains)
    for index, zid in enumerate(ordered):
        for other in ordered[index + 1:]:
            if trains_conflict(trains[zid], trains[other]):
                neighbors[zid].add(other)
                neighbors[other].add(zid)
    pending = sorted((zid for zid in trains if zid not in result),
                     key=lambda zid: (-len(neighbors[zid]), zid))
    for zid in pending:
        used = [result[other] for other in sorted(neighbors[zid]) if other in result]
        if not used:
            result[zid] = COLORFUL_TRAIN_COLORS[abs(zid) % len(COLORFUL_TRAIN_COLORS)]
            continue
        result[zid] = max(
            COLORFUL_TRAIN_COLORS,
            key=lambda candidate: (min(color_distance(candidate, color) for color in used),
                                   -COLORFUL_TRAIN_COLORS.index(candidate)),
        )
    return result

