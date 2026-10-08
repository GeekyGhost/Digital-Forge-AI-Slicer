"""Project-relative paths. Everything the app needs lives under the project folder."""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(os.environ.get("DFORGE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DATA = ROOT / "data"
OBJECTS = DATA / "objects"
TASKS = DATA / "tasks"
EXPORTS = DATA / "exports"
LOGS = DATA / "logs"
RUNTIME = ROOT / "runtime"
PROFILES = ROOT / "profiles"
MODELS = ROOT / "models"
WINDOWS = os.name == "nt"

for folder in (OBJECTS, TASKS, EXPORTS, LOGS):
    folder.mkdir(parents=True, exist_ok=True)


def settings() -> dict:
    """Optional machine-local overrides in config/settings.json (never required)."""
    path = ROOT / "config" / "settings.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            return {}
    return {}


def _find(folder: Path, names: tuple[str, ...]) -> Path | None:
    if not folder.is_dir():
        return None
    for name in names:
        direct = folder / name
        if direct.is_file():
            return direct
    for name in names:
        found = sorted(folder.rglob(name), key=lambda p: len(p.parts))
        if found:
            return found[0]
    return None


@lru_cache(maxsize=None)
def orca_executable() -> Path | None:
    override = os.environ.get("DFORGE_ORCA") or settings().get("orca")
    if override and Path(override).is_file():
        return Path(override)
    names = ("orca-slicer.exe",) if WINDOWS else ("AppRun", "orca-slicer")
    return _find(RUNTIME / "orca", names)


@lru_cache(maxsize=None)
def blender_executable() -> Path | None:
    return _find(RUNTIME / "blender", ("blender.exe",) if WINDOWS else ("blender",))


@lru_cache(maxsize=None)
def trellis_executable() -> Path | None:
    return _find(RUNTIME / "trellis", ("trellis-cli.exe",) if WINDOWS else ("trellis-cli",))


TRELLIS_MODELS = MODELS / "trellis2" / "q8"
TRELLIS_CHECKPOINTS = (
    "birefnet", "dinov3", "ss_flow", "ss_dec", "shape_flow_512", "shape_flow_1024",
    "shape_dec", "tex_flow_512", "tex_flow_1024", "tex_dec",
)


def capabilities() -> dict:
    trellis = trellis_executable()
    missing = [n for n in TRELLIS_CHECKPOINTS if not (TRELLIS_MODELS / f"{n}.gguf").is_file()]
    orca = orca_executable()
    blender = blender_executable()
    return {
        "python": sys.version.split()[0],
        "root": str(ROOT),
        "orca": {"ready": orca is not None, "path": str(orca) if orca else None},
        "blender": {"ready": blender is not None, "path": str(blender) if blender else None},
        "trellis": {
            "ready": trellis is not None and not missing,
            "path": str(trellis) if trellis else None,
            "missing_checkpoints": missing,
        },
    }
