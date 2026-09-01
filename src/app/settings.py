"""Globale, kleine Anwendungseinstellungen mit atomarer JSON-Persistenz."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import re

from infrastructure.artifact_identity import atomic_write_json

DEFAULT_TRAIN_COLOR = "#D0D0D0"
TRAIN_COLOR_MODES = ("colorful", "single", "train_type")
COLORFUL_TRAIN_COLORS = ("#1565C0", "#2E7D32", "#6A1B9A", "#EF6C00", "#00838F")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Das STS-Handbuch belegt im vorliegenden Projekt RE-Beispiele, liefert aber
# keine vollständige Gattungsliste. Die übrigen expliziten Kürzel sind deshalb
# eine korrigierbare Darstellungs-Whitelist; niemals erkannte Kürzel fallen ohne
# Namensheuristik in ``other``.
TRAIN_TYPE_CATEGORIES = {
    "local": ("RB", "RE", "IRE", "S", "MEX"),
    "long_distance": ("ICE", "IC", "EC", "RJ", "RJX", "FLX"),
    "freight": ("DG", "DGS", "EZ", "FEG", "GAG", "GC", "GZ", "NG", "SGAG"),
    "shunting": ("RF",),
    "other": ("T", "TFZF"),
}
CATEGORY_LABELS = {
    "local": "Nahverkehr", "long_distance": "Fernverkehr", "freight": "Güterverkehr",
    "shunting": "Rangierfahrten", "other": "Sonstiges",
}
DEFAULT_CATEGORY_COLORS = {
    "local": "#4FC3F7", "long_distance": "#EF5350", "freight": "#FFB74D",
    "shunting": "#BA68C8", "other": "#BDBDBD",
}


def normalize_hex_color(value: str) -> str | None:
    value = str(value).strip()
    return value.upper() if _HEX_COLOR.fullmatch(value) else None


def extract_train_type(train_name: str) -> str:
    match = re.match(r"\s*([A-Za-zÄÖÜäöü]+)", train_name or "")
    return match.group(1).upper() if match else ""


def train_type_category(train_type: str) -> str:
    normalized = train_type.upper()
    return next((category for category, values in TRAIN_TYPE_CATEGORIES.items()
                 if normalized in values), "other")


@dataclass(frozen=True)
class ApplicationSettings:
    train_color_mode: str = "single"
    single_train_color: str = DEFAULT_TRAIN_COLOR
    live_follow_position_percent: int = 50
    category_train_colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_COLORS))
    train_type_colors: dict[str, str] = field(default_factory=dict)

    def validated(self) -> "ApplicationSettings":
        mode = self.train_color_mode if self.train_color_mode in TRAIN_COLOR_MODES else "single"
        color = normalize_hex_color(self.single_train_color) or DEFAULT_TRAIN_COLOR
        percent = max(0, min(100, int(self.live_follow_position_percent)))
        categories = {
            category: normalize_hex_color(self.category_train_colors.get(category, default)) or default
            for category, default in DEFAULT_CATEGORY_COLORS.items()
        }
        type_colors = {}
        for key, value in self.train_type_colors.items():
            normalized_type_color = normalize_hex_color(value)
            if normalized_type_color is not None:
                type_colors[key.upper()] = normalized_type_color
        return replace(self, train_color_mode=mode, single_train_color=color,
                       live_follow_position_percent=percent, category_train_colors=categories,
                       train_type_colors=type_colors)

    def train_color(self, index: int, train_name: str = "") -> str:
        if self.train_color_mode == "colorful":
            return COLORFUL_TRAIN_COLORS[index % len(COLORFUL_TRAIN_COLORS)]
        if self.train_color_mode == "train_type":
            train_type = extract_train_type(train_name)
            category = train_type_category(train_type)
            return self.train_type_colors.get(train_type, self.category_train_colors[category])
        return self.single_train_color

    def apply_category_color(self, category: str, color: str) -> "ApplicationSettings":
        normalized = normalize_hex_color(color)
        if category not in TRAIN_TYPE_CATEGORIES or normalized is None:
            return self
        categories = {**self.category_train_colors, category: normalized}
        types = {**self.train_type_colors,
                 **{train_type: normalized for train_type in TRAIN_TYPE_CATEGORIES[category]}}
        return replace(self, category_train_colors=categories, train_type_colors=types).validated()

    def with_train_type_color(self, train_type: str, color: str | None) -> "ApplicationSettings":
        types = dict(self.train_type_colors)
        key = train_type.upper()
        normalized = normalize_hex_color(color) if color is not None else None
        if normalized is None:
            types.pop(key, None)
        else:
            types[key] = normalized
        return replace(self, train_type_colors=types).validated()


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
