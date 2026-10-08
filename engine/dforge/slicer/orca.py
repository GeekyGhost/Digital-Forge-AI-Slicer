"""Slice a plate with the bundled OrcaSlicer engine (command-line mode)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import RUNTIME, orca_executable
from ..mesh.core import transform
from ..process import run
from . import profiles
from .gcode import write_preview
from .threemf import Part, PrintObject, bed_check, write_3mf


def build_plate(store, plate: dict) -> list[PrintObject]:
    """plate.objects: [{name, settings, parts: [{object_id, matrix (4x4 row-major, bed mm), filament}]}]"""
    objects = []
    for index, item in enumerate(plate.get("objects", [])):
        parts = []
        for p_index, part in enumerate(item.get("parts", [])):
            mesh = store.mesh(part["object_id"])
            matrix = np.asarray(part["matrix"], dtype=np.float64).reshape(4, 4)
            placed = transform(mesh, matrix)
            parts.append(Part(name=part.get("name") or f"part {p_index + 1}", mesh=placed, extruder=int(part.get("filament", 1))))
        if parts:
            # Sit every object on the bed, as the slicer would (parts move together).
            floor = min(float(p.mesh.vertices[:, 2].min()) for p in parts)
            for p in parts:
                p.mesh.vertices[:, 2] -= floor
            objects.append(PrintObject(name=item.get("name") or f"object {index + 1}", parts=parts, settings=item.get("settings") or {}))
    if not objects:
        raise ValueError("Add at least one object to the plate")
    return objects


def slice_plate(
    store,
    plate: dict,
    folder: Path,
    log: Path,
    cancelled: Callable[[], bool],
    progress: Callable[[float, str], None],
) -> dict:
    orca = orca_executable()
    if orca is None:
        raise RuntimeError("The OrcaSlicer engine is not installed. Run setup to download it into runtime\\orca.")
    schema = profiles.schema()
    printer = schema["printer"]
    progress(0.05, "Placing objects")
    objects = build_plate(store, plate)
    problems = bed_check(objects, tuple(printer["bed"]), printer["height"])
    if problems:
        raise ValueError("; ".join(problems))
    filaments = plate.get("filaments") or [{"preset": profiles.DEFAULT_FILAMENT, "color": "#F2754E"}]
    used = sorted({p.extruder for o in objects for p in o.parts})
    if used and used[-1] > len(filaments):
        raise ValueError(f"An object uses filament {used[-1]} but only {len(filaments)} filament slots are set up")
    paths = profiles.write_profiles(folder, dict(plate.get("settings") or {}), filaments, plate.get("bed_type"))
    progress(0.15, "Writing print project")
    project = write_3mf(folder / "plate.3mf", objects)
    colors = [f.get("color", "#F2754E") for f in filaments]
    data_dir = RUNTIME / "orca-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    out = folder / "out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    command = [
        orca,
        "--datadir", data_dir,
        "--slice", "0",
        "--load-settings", f"{paths['machine']};{paths['process']}",
        "--load-filaments", ";".join(str(p) for p in paths["filaments"]),
        "--filament-colour", ";".join(colors),
        "--outputdir", out,
        "--export-3mf", "print.3mf",
        project,
    ]
    progress(0.2, "Slicing with OrcaSlicer")
    code = run(command, log, cancelled, timeout=3600, cwd=folder)
    result_path = out / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    gcode = out / "plate_1.gcode"
    if code != 0 or not gcode.is_file():
        message = result.get("error_string") or f"OrcaSlicer exited with code {code}"
        raise RuntimeError(f"Slicing failed: {message}. See the task log for details.")
    progress(0.8, "Reading toolpaths")
    name = (plate.get("name") or "plate").strip() or "plate"
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name)[:60]
    final_gcode = folder / f"{safe}.gcode"
    shutil.move(gcode, final_gcode)
    final_3mf = folder / f"{safe}.3mf"
    if (out / "print.3mf").is_file():
        shutil.move(out / "print.3mf", final_3mf)
    preview = write_preview(final_gcode, folder)
    warnings = []
    for plate_info in result.get("sliced_plates", []):
        if plate_info.get("warning_message"):
            warnings.append(plate_info["warning_message"])
    return {
        "gcode": final_gcode.name,
        "project": final_3mf.name if final_3mf.is_file() else None,
        "stats": preview["stats"],
        "layers": len(preview["layers"]),
        "warnings": warnings,
        "filaments": [{"slot": i + 1, "color": c, "preset": f.get("preset")} for i, (c, f) in enumerate(zip(colors, filaments))],
    }
