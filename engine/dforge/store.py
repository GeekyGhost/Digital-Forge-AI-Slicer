"""Immutable object library. Every operation creates a new object; sources are never edited."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import OBJECTS
from .mesh.analyze import analyze
from .mesh.core import Mesh
from .mesh.io import IMAGE_SUFFIXES, MESH_SUFFIXES, display_scene, load_file

_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_id(object_id: str) -> str:
    if len(object_id) != 32 or any(c not in "0123456789abcdef" for c in object_id):
        raise KeyError("Unknown object")
    return object_id


class ObjectStore:
    def __init__(self, root: Path = OBJECTS):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, object_id: str) -> Path:
        return self.root / _valid_id(object_id)

    def path(self, object_id: str, name: str) -> Path:
        if "/" in name or "\\" in name or name.startswith("."):
            raise KeyError("Invalid file name")
        path = self.folder(object_id) / name
        if not path.is_file():
            raise KeyError(f"{name} not found")
        return path

    def get(self, object_id: str) -> dict:
        meta = self.folder(object_id) / "object.json"
        if not meta.is_file():
            raise KeyError("Unknown object")
        return json.loads(meta.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        records = []
        for meta in self.root.glob("*/object.json"):
            try:
                record = json.loads(meta.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not record.get("hidden"):
                records.append(record)
        return sorted(records, key=lambda r: r["created"], reverse=True)

    def mesh(self, object_id: str) -> Mesh:
        return Mesh.load(self.path(object_id, "mesh.npz"))

    def _write(self, record: dict) -> dict:
        folder = self.folder(record["id"])
        tmp = folder / "object.json.tmp"
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(folder / "object.json")
        return record

    def update(self, object_id: str, **changes) -> dict:
        with _lock:
            record = self.get(object_id)
            record.update(changes)
            return self._write(record)

    def _new(self, name: str, kind: str, **extra) -> dict:
        object_id = uuid.uuid4().hex
        self.folder(object_id).mkdir(parents=True)
        return {"id": object_id, "name": name, "kind": kind, "created": now(), **extra}

    def import_file(self, source: Path, name: str | None = None) -> dict:
        source = Path(source)
        suffix = source.suffix.lower()
        name = name or source.stem
        if suffix in IMAGE_SUFFIXES:
            record = self._new(name, "image")
            target = self.folder(record["id"]) / f"image{suffix}"
            shutil.copyfile(source, target)
            from PIL import Image

            with Image.open(target) as img:
                record.update({"file": target.name, "width": img.width, "height": img.height, "has_alpha": "A" in img.getbands()})
            return self._write(record)
        if suffix not in MESH_SUFFIXES:
            raise ValueError(f"Unsupported file type {suffix}")
        mesh, scene, info = load_file(source)
        record = self._new(name, "mesh", source_file=source.name)
        folder = self.folder(record["id"])
        shutil.copyfile(source, folder / f"source{suffix}")
        scene.export(folder / "display.glb")
        return self._finish(record, mesh, info=info)

    def add_mesh(
        self,
        mesh: Mesh,
        name: str,
        parent: str | None = None,
        operation: str | None = None,
        report: dict | None = None,
        color: tuple[int, int, int] | None = None,
        hidden: bool = False,
        extra: dict | None = None,
    ) -> dict:
        record = self._new(name, "mesh", parent=parent, operation=operation, hidden=hidden, **(extra or {}))
        if parent:
            # Derived objects share their parent's frame so they appear in place.
            record["frame_of"] = self.get(parent).get("frame_of", parent)
        display_scene(mesh, color).export(self.folder(record["id"]) / "display.glb")
        return self._finish(record, mesh, report=report, color=color)

    def _finish(self, record: dict, mesh: Mesh, info: dict | None = None, report: dict | None = None, color=None) -> dict:
        folder = self.folder(record["id"])
        mesh.save(folder / "mesh.npz")
        stats = analyze(mesh)
        record.update(
            {
                "units": "mm",
                "up": "+Z",
                "analysis": stats,
                "has_color": mesh.face_colors is not None,
                "display_color": list(color) if color else None,
                "import": info,
                "report": report,
                "notes": (info or {}).get("notes", []),
            }
        )
        with _lock:
            return self._write(record)

    def labels_path(self, object_id: str) -> Path:
        return self.folder(object_id) / "labels.npy"

    def save_labels(self, object_id: str, labels: np.ndarray, palette: list) -> None:
        np.save(self.labels_path(object_id), labels.astype(np.uint8))
        self.update(object_id, palette=palette)

    def load_labels(self, object_id: str) -> np.ndarray | None:
        path = self.labels_path(object_id)
        return np.load(path) if path.is_file() else None
