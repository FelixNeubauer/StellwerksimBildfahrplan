"""Globale, kleine Anwendungseinstellungen mit atomarer JSON-Persistenz."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import re

from infrastructure.artifact_identity import atomic_write_json

DEFAULT_TRAIN_COLOR = "#D0D0D0"
TRAIN_COLOR_MODES = ("colorful", "single", "train_type")
COLORFUL_TRAIN_COLORS = (
    "#00BCD4", "#FF9800", "#8BC34A", "#E040FB", "#2196F3",
    "#FF5252", "#FFD740", "#69F0AE", "#7C4DFF", "#FF4081",
    "#40C4FF", "#B2FF59", "#FF6E40", "#18FFFF", "#EEFF41",
    "#B388FF", "#FFAB40", "#64FFDA", "#82B1FF", "#EA80FC",
)
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TRAIN_TYPE = re.compile(r"^[A-Za-zÄÖÜäöü]+$")

# Explizite, deterministische Darstellungsliste. Sie basiert auf der im Auftrag
# aus dem STS-Handbuch bereitgestellten Deutschlandliste; RS und SAB sind
# projektspezifische Ergänzungen. Es gibt bewusst keine Präfixklassifizierung.
TRAIN_TYPE_CATEGORIES = {
    "local": (
        "ABR", "AKN", "ALX", "BKB", "BOB", "BSB", "CAN", "CBC", "DNR", "E",
        "EIB", "ERB", "EVB", "FEG", "FEX", "HEX", "HLB", "HTB", "HZL", "IRE",
        "ME", "MEr", "MR", "MRB", "NBE", "NEB", "NEG", "NOB", "NWB", "OE",
        "OLA", "OSB", "PEG", "PRE", "RB", "RE", "RS", "RT", "RTB", "S",
        "SAB", "SBB", "SHB", "STB", "SWE", "UBB", "VBG", "VEC", "VIA", "WEG",
        "WFB", "MEX",
    ),
    "long_distance": (
        "AZ", "CNL", "D", "DPF", "EC", "EN", "FLX", "IC", "ICE", "NJ", "RJ",
        "RJX", "TGV", "THA", "VX", "WEST", "X",
    ),
    "freight": (
        "CB", "CbZ", "CFA", "CFN", "CHL", "CIL", "CL", "CS", "CSQ", "CT",
        "DFG", "DG", "DGE", "DGN", "DGS", "DGX", "DGZ", "DNG", "EnKo", "EUC", "ExC",
        "EZ", "EZK", "FE", "FIR", "FR", "FS", "FX", "FZ", "FZT", "GAG",
        "GAGC", "GC", "GX", "GZ", "HGK", "ICG", "ICL", "IKE", "IKL", "IKS",
        "IRC", "KC", "KCL", "KT", "NG", "PIC", "RWE", "SGAG", "TC", "TEC", "TKC", "TRC",
    ),
    "shunting": ("Rf", "RA"),
    "other": (
        "AS", "Bauz", "DbZ", "DGV", "DLr", "DLt", "Dsts", "DZ", "Hilfz", "L",
        "Lok", "LOKF", "LNF", "LPF", "LPFT", "LR", "LRV", "LRZ", "LS", "LT",
        "LZ", "M", "MCT", "Mess", "NbZ", "PbZ", "RbZ", "Schadl", "Schadt",
        "Schadw", "SDZ", "SKL", "Sperrf", "T", "TFZ", "Tfzf", "Tfzl",
    ),
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


def normalize_train_type(value: str) -> str:
    """Liefert die case-insensitive ID, ohne den Original-Zugnamen anzutasten."""
    return str(value).strip().casefold()


def extract_train_type(train_name: str) -> str:
    match = re.match(r"\s*([A-Za-zÄÖÜäöü]+)", train_name or "")
    return match.group(1) if match else ""


def builtin_train_types() -> tuple[tuple[str, str], ...]:
    return tuple((name, category) for category, names in TRAIN_TYPE_CATEGORIES.items() for name in names)


_BUILTIN_BY_ID = {
    normalize_train_type(name): (name, category) for name, category in builtin_train_types()
}


@dataclass(frozen=True)
class CustomTrainType:
    name: str
    category: str
    color: str

    @property
    def normalized_id(self) -> str:
        return normalize_train_type(self.name)


def train_type_category(train_type: str, custom_train_types=()) -> str:
    normalized = normalize_train_type(train_type)
    for item in custom_train_types:
        if item.normalized_id == normalized:
            return item.category
    return _BUILTIN_BY_ID.get(normalized, ("", "other"))[1]


@dataclass(frozen=True)
class ApplicationSettings:
    train_color_mode: str = "single"
    single_train_color: str = DEFAULT_TRAIN_COLOR
    live_follow_position_percent: int = 50
    category_train_colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_COLORS))
    train_type_colors: dict[str, str] = field(default_factory=dict)
    custom_train_types: tuple[CustomTrainType, ...] = ()

    def validated(self) -> "ApplicationSettings":
        mode = self.train_color_mode if self.train_color_mode in TRAIN_COLOR_MODES else "single"
        color = normalize_hex_color(self.single_train_color) or DEFAULT_TRAIN_COLOR
        percent = max(0, min(100, int(self.live_follow_position_percent)))
        categories = {
            category: normalize_hex_color(self.category_train_colors.get(category, default)) or default
            for category, default in DEFAULT_CATEGORY_COLORS.items()
        }
        customs = []
        occupied = set(_BUILTIN_BY_ID)
        for raw in self.custom_train_types:
            try:
                item = raw if isinstance(raw, CustomTrainType) else CustomTrainType(**raw)
            except (TypeError, ValueError):
                continue
            normalized = item.normalized_id
            item_color = normalize_hex_color(item.color)
            if (not normalized or normalized in occupied or item.category not in TRAIN_TYPE_CATEGORIES
                    or item_color is None or not _TRAIN_TYPE.fullmatch(item.name.strip())):
                continue
            occupied.add(normalized)
            customs.append(CustomTrainType(item.name.strip(), item.category, item_color))
        type_colors = {}
        for key, value in self.train_type_colors.items():
            normalized_color = normalize_hex_color(value)
            normalized_id = normalize_train_type(key)
            if normalized_color is not None and normalized_id:
                type_colors[normalized_id] = normalized_color
        # Migration: alte Großschreibungs-Keys werden normalisiert; vorhandene
        # Nutzerfarben gewinnen und neue Builtins werden nicht erzwungen angelegt.
        return replace(self, train_color_mode=mode, single_train_color=color,
                       live_follow_position_percent=percent, category_train_colors=categories,
                       train_type_colors=type_colors, custom_train_types=tuple(customs))

    def all_train_types(self) -> tuple[tuple[str, str, bool], ...]:
        builtins = tuple((name, category, False) for name, category in builtin_train_types())
        customs = tuple((item.name, item.category, True) for item in self.custom_train_types)
        return builtins + customs

    def train_type_color(self, train_name: str) -> str:
        train_type = extract_train_type(train_name)
        normalized = normalize_train_type(train_type)
        category = train_type_category(train_type, self.custom_train_types)
        return self.train_type_colors.get(normalized, self.category_train_colors[category])

    def train_color(self, index: int, train_name: str = "", colorful_color: str | None = None) -> str:
        if self.train_color_mode == "colorful":
            return colorful_color or COLORFUL_TRAIN_COLORS[index % len(COLORFUL_TRAIN_COLORS)]
        if self.train_color_mode == "train_type":
            return self.train_type_color(train_name)
        return self.single_train_color

    def apply_category_color(self, category: str, color: str) -> "ApplicationSettings":
        normalized = normalize_hex_color(color)
        if category not in TRAIN_TYPE_CATEGORIES or normalized is None:
            return self
        categories = {**self.category_train_colors, category: normalized}
        types = dict(self.train_type_colors)
        for name, item_category, _custom in self.all_train_types():
            if item_category == category:
                types[normalize_train_type(name)] = normalized
        customs = tuple(replace(item, color=normalized) if item.category == category else item
                        for item in self.custom_train_types)
        return replace(self, category_train_colors=categories, train_type_colors=types,
                       custom_train_types=customs).validated()

    def with_train_type_color(self, train_type: str, color: str | None) -> "ApplicationSettings":
        types = dict(self.train_type_colors)
        key = normalize_train_type(train_type)
        normalized = normalize_hex_color(color) if color is not None else None
        if normalized is None:
            types.pop(key, None)
        else:
            types[key] = normalized
        customs = tuple(replace(item, color=normalized) if item.normalized_id == key and normalized else item
                        for item in self.custom_train_types)
        return replace(self, train_type_colors=types, custom_train_types=customs).validated()

    def add_custom_train_type(self, name: str, category: str, color: str) -> "ApplicationSettings":
        normalized = normalize_train_type(name)
        if normalized in {normalize_train_type(item[0]) for item in self.all_train_types()}:
            raise ValueError("Diese Zuggattung existiert bereits.")
        normalized_color = normalize_hex_color(color)
        if not normalized or not _TRAIN_TYPE.fullmatch(name.strip()) or category not in TRAIN_TYPE_CATEGORIES:
            raise ValueError("Ungültige Zuggattung oder Kategorie.")
        if normalized_color is None:
            raise ValueError("Ungültige Farbe.")
        item = CustomTrainType(name.strip(), category, normalized_color)
        types = {**self.train_type_colors, normalized: normalized_color}
        return replace(self, custom_train_types=(*self.custom_train_types, item),
                       train_type_colors=types).validated()

    def remove_custom_train_type(self, name: str) -> "ApplicationSettings":
        normalized = normalize_train_type(name)
        customs = tuple(item for item in self.custom_train_types if item.normalized_id != normalized)
        types = dict(self.train_type_colors)
        types.pop(normalized, None)
        return replace(self, custom_train_types=customs, train_type_colors=types).validated()

    def change_custom_train_type_category(self, name: str, category: str) -> "ApplicationSettings":
        if category not in TRAIN_TYPE_CATEGORIES:
            return self
        normalized = normalize_train_type(name)
        customs = tuple(replace(item, category=category) if item.normalized_id == normalized else item
                        for item in self.custom_train_types)
        return replace(self, custom_train_types=customs).validated()


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
