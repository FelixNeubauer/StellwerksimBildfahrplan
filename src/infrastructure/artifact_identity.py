"""Gemeinsame Identitaetsmetadaten fuer stellwerksbezogene JSON-Artefakte."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SavedStellwerkIdentity:
    aid: int
    name: str


@dataclass(frozen=True)
class IdentityValidation:
    status: str
    current: SavedStellwerkIdentity
    saved: SavedStellwerkIdentity | None = None
    path: Path | None = None
    candidates: tuple[Path, ...] = ()


def artifact_metadata(identity: SavedStellwerkIdentity, artifact_type: str, schema_version: int) -> dict[str, Any]:
    return {"schema_version": schema_version, "artifact_type": artifact_type,
            "stellwerk": asdict(identity), "aid": identity.aid, "stellwerk_name": identity.name,
            "saved_at": datetime.now(timezone.utc).isoformat()}


def saved_identity(data: dict[str, Any], fallback_aid: int | None = None) -> SavedStellwerkIdentity | None:
    item = data.get("stellwerk", {})
    aid = item.get("aid", data.get("aid", fallback_aid))
    name = item.get("name", data.get("stellwerk_name"))
    return SavedStellwerkIdentity(int(aid), str(name)) if aid is not None and name else None


def validate_saved_stellwerk_identity(data: dict[str, Any], current: SavedStellwerkIdentity,
                                       path: Path | None = None) -> IdentityValidation:
    saved = saved_identity(data, current.aid)
    if saved is None:
        return IdentityValidation("legacy_identity_confirmation", current, path=path)
    if saved == current:
        return IdentityValidation("match", current, saved, path)
    if saved.aid == current.aid:
        return IdentityValidation("name_changed", current, saved, path)
    if saved.name == current.name:
        return IdentityValidation("aid_changed", current, saved, path)
    return IdentityValidation("different_installation", current, saved, path)


def find_identity_candidate(directory: str | Path, current: SavedStellwerkIdentity,
                            artifact_type: str) -> IdentityValidation:
    matches: list[tuple[Path, SavedStellwerkIdentity]] = []
    for path in Path(directory).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = saved_identity(data)
        if data.get("artifact_type", artifact_type) == artifact_type and identity and identity.name == current.name:
            matches.append((path, identity))
    if len(matches) == 1:
        path, identity = matches[0]
        return IdentityValidation("aid_changed", current, identity, path)
    if len(matches) > 1:
        return IdentityValidation("ambiguous", current, candidates=tuple(path for path, _ in matches))
    return IdentityValidation("different_installation", current)


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                    default=lambda value: sorted(value) if isinstance(value, set) else str(value)) + "\n",
                         encoding="utf-8")
    os.replace(temporary, target)
    return target


def archive_artifact(path: str | Path) -> Path:
    source = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = source.parent / "archive" / f"{source.stem}.{stamp}{source.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(source, target)
    return target
