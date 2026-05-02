from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SPLIT_RE = re.compile(r"[\\/]+")
_REPO_ANCHORS = ("data", "results")


def _split_parts(raw_path: str) -> list[str]:
    return [part for part in _SPLIT_RE.split(raw_path.strip()) if part and part != "."]


def _relative_from_repo_root(raw_path: str) -> Path | None:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return None


def _relative_from_anchor(raw_path: str) -> Path | None:
    parts = list(PureWindowsPath(raw_path).parts) if _WINDOWS_ABS_RE.match(raw_path) else _split_parts(raw_path)
    lowered = [part.lower() for part in parts]
    for anchor in _REPO_ANCHORS:
        if anchor in lowered:
            return Path(*parts[lowered.index(anchor) :])
    return None


def to_repo_relative_path(path_like: str | Path | None) -> str:
    raw_path = str(path_like or "").strip()
    if not raw_path:
        return ""

    if _WINDOWS_ABS_RE.match(raw_path):
        relative = _relative_from_anchor(raw_path)
        if relative is not None:
            return relative.as_posix()
        return raw_path.replace("\\", "/")

    relative = _relative_from_repo_root(raw_path)
    if relative is not None:
        return relative.as_posix()

    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        relative = _relative_from_anchor(raw_path)
        if relative is not None:
            return relative.as_posix()
        return candidate.as_posix()

    return Path(*_split_parts(raw_path)).as_posix()


def resolve_repo_path(path_like: str | Path | None) -> Path:
    raw_path = str(path_like or "").strip()
    if not raw_path:
        return PROJECT_ROOT

    if _WINDOWS_ABS_RE.match(raw_path):
        anchored = _relative_from_anchor(raw_path)
        if anchored is not None:
            return (PROJECT_ROOT / anchored).resolve()
        return Path(raw_path.replace("\\", "/"))

    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    normalized = to_repo_relative_path(raw_path)
    return (PROJECT_ROOT / normalized).resolve()


def normalize_config_paths(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}

    normalized = {key: value for key, value in dict(config).items() if key not in {"mode", "manual_pdf_path"}}

    output = normalized.get("output")
    if isinstance(output, dict):
        normalized_output = dict(output)
        for key in ("results_dir", "assets_dir"):
            value = normalized_output.get(key)
            if value not in (None, ""):
                normalized_output[key] = to_repo_relative_path(str(value))
        normalized["output"] = normalized_output

    storage = normalized.get("storage")
    if isinstance(storage, dict):
        normalized_storage = dict(storage)
        for key in ("local_dir", "fetch_dir", "cache_dir"):
            value = normalized_storage.get(key)
            if value not in (None, ""):
                normalized_storage[key] = to_repo_relative_path(str(value))
        normalized["storage"] = normalized_storage

    return normalized
