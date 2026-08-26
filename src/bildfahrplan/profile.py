"""Explizite, verlustfreie Streckenprofile ohne Namensheuristiken."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from infrastructure.model import RoutePath


@dataclass(frozen=True)
class OperatingPoint:
    id: str
    label: str
    position: float
    raw_names: tuple[str, ...]


@dataclass(frozen=True)
class RouteProfile:
    name: str
    operating_points: tuple[OperatingPoint, ...]

    @classmethod
    def load(cls, path: str | Path) -> "RouteProfile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        points = tuple(
            OperatingPoint(
                id=item["id"], label=item["label"], position=float(item["position"]),
                raw_names=tuple(item.get("raw_names", ())),
            )
            for item in data["operating_points"]
        )
        return cls(name=data["name"], operating_points=points)

    def resolve(self, raw_name: str) -> OperatingPoint | None:
        """Loest nur explizit konfigurierte Originalnamen auf."""
        return next((point for point in self.operating_points if raw_name in point.raw_names), None)

    @property
    def ticks(self) -> dict[float, str]:
        return {point.position: point.label for point in self.operating_points}

    @property
    def route_path(self) -> RoutePath:
        """Lineare, explizit konfigurierte Auswahl mit relativer Achseneinheit."""
        return RoutePath(
            id=self.name, name=self.name,
            nodes=tuple(point.id for point in self.operating_points),
            positions=tuple(point.position for point in self.operating_points),
            axis_unit="relative",
        )
