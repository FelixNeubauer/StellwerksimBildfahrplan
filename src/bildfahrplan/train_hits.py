"""Deterministische, GUI-unabhaengig testbare Zugtreffer."""

from dataclasses import dataclass
from math import hypot
from typing import Iterable

TRAIN_HIT_TOLERANCE_PX = 7.0


@dataclass(frozen=True)
class TrainHitCandidate:
    zid: int
    train_name: str
    distance_px: float
    source: str


def point_segment_distance(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return hypot(px - ax, py - ay)
    fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))


def line_hit_candidates(point, lines: Iterable[tuple[int, str, Iterable[tuple[float, float]]]],
                        tolerance: float = TRAIN_HIT_TOLERANCE_PX) -> tuple[TrainHitCandidate, ...]:
    best: dict[int, TrainHitCandidate] = {}
    for zid, name, points in lines:
        coordinates = tuple(points)
        distances = [point_segment_distance(point, left, right)
                     for left, right in zip(coordinates, coordinates[1:])]
        if distances and min(distances) <= tolerance:
            candidate = TrainHitCandidate(zid, name, min(distances), "line")
            if zid not in best or candidate.distance_px < best[zid].distance_px:
                best[zid] = candidate
    return tuple(sorted(best.values(), key=lambda candidate: (candidate.distance_px,
                                                               candidate.train_name.casefold(), candidate.zid)))


def prefer_label(candidates: Iterable[TrainHitCandidate], label_zid: int | None) -> tuple[TrainHitCandidate, ...]:
    candidates = tuple(candidates)
    if label_zid is None:
        return candidates
    label = next((candidate for candidate in candidates if candidate.zid == label_zid), None)
    return (TrainHitCandidate(label_zid, label.train_name if label else str(label_zid), 0.0, "label"),)
