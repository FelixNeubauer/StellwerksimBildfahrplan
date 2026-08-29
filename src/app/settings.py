"""Globale, kleine Anwendungseinstellungen mit atomarer JSON-Persistenz."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import re

from infrastructure.artifact_identity import atomic_write_json

DEFAULT_TRAIN_COLOR = "#D0D0D0"
TRAIN_COLOR_MODES = ("colorful", "single")
COLORFUL_TRAIN_COLORS = ("#1565C0", "#2E7D32", "#6A1B9A", "#EF6C00", "#00838F")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_hex_color(value: str) -> str | None:
    value = str(value).strip()
    return value.upper() if _HEX_COLOR.fullmatch(value) else None


@dataclass(frozen=True)
class ApplicationSettings:
    train_color_mode: str = "single"
    single_train_color: str = DEFAULT_TRAIN_COLOR
    live_follow_position_percent: int = 50

    def validated(self) -> "ApplicationSettings":
        mode = self.train_color_mode if self.train_color_mode in TRAIN_COLOR_MODES else "single"
        color = normalize_hex_color(self.single_train_color) or DEFAULT_TRAIN_COLOR
        percent = max(0, min(100, int(self.live_follow_position_percent)))
        return replace(self, train_color_mode=mode, single_train_color=color,
                       live_follow_position_percent=percent)

    def train_color(self, index: int) -> str:
        if self.train_color_mode == "colorful":
            return COLORFUL_TRAIN_COLORS[index % len(COLORFUL_TRAIN_COLORS)]
        return self.single_train_color


class ApplicationSettingsStore:
    def __init__(self, config_directory: str | Path) -> None:
        self.path = Path(config_directory) / "settings.json"

    def load(self) -> ApplicationSettings:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ApplicationSettings()
        known = {key: payload[key] for key in asdict(ApplicationSettings()) if key in payload}
        try:
            return ApplicationSettings(**known).validated()
        except (TypeError, ValueError):
            return ApplicationSettings()

    def save(self, settings: ApplicationSettings) -> None:
        atomic_write_json(self.path, asdict(settings.validated()))
