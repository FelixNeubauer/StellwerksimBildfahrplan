"""Monotone UI-Interpolation zwischen autoritativen STS-Zeitantworten."""

from __future__ import annotations

import time
from collections.abc import Callable


class SimTimeInterpolator:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic, max_extrapolation: float = 10.0) -> None:
        self.monotonic = monotonic
        self.max_extrapolation = max_extrapolation
        self._sim_seconds: float | None = None
        self._sync_monotonic: float | None = None
        self._frozen_value: float | None = None

    def synchronize(self, simtime_ms: int | None, sim_day: int = 0) -> None:
        if simtime_ms is None:
            return
        self._sim_seconds = sim_day * 86400 + simtime_ms / 1000
        self._sync_monotonic = self.monotonic()
        self._frozen_value = None

    def value(self, connected: bool) -> tuple[float | None, bool]:
        if self._sim_seconds is None or self._sync_monotonic is None:
            return None, False
        elapsed = max(0.0, self.monotonic() - self._sync_monotonic)
        if not connected:
            if self._frozen_value is None:
                self._frozen_value = self._sim_seconds + min(elapsed, self.max_extrapolation)
            return self._frozen_value, False
        self._frozen_value = None
        if elapsed > self.max_extrapolation:
            return self._sim_seconds + self.max_extrapolation, False
        return self._sim_seconds + elapsed, True
