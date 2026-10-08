"""Printer, process and filament presets plus the settings the UI exposes.

Presets are flattened copies of OrcaSlicer's own Anycubic Kobra S1 system
profiles (profiles/orca). The UI edits a curated set of keys; anything else can
be overridden through the advanced key/value box.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import PROFILES

ORCA = PROFILES / "orca"
DEFAULT_PRINTER = "Anycubic Kobra S1 0.4 nozzle"
DEFAULT_PROCESS = "0.20mm Standard @Anycubic Kobra S1 0.4 nozzle"
DEFAULT_FILAMENT = "Anycubic PLA @Anycubic Kobra S1 0.4 nozzle"
MAX_FILAMENTS = 4  # ACE Pro slots on the Kobra S1 Combo
BED_TYPES = ["Textured PEI Plate", "High Temp Plate", "Cool Plate", "Engineering Plate"]

# key, label, type, group, options/range, unit, help
SETTINGS = [
    ("layer_height", "Layer height", "float", "Quality", (0.08, 0.32, 0.02), "mm", "Thinner layers look smoother but print slower."),
    ("initial_layer_print_height", "First layer height", "float", "Quality", (0.1, 0.35, 0.02), "mm", ""),
    ("wall_loops", "Walls", "int", "Strength", (1, 10, 1), "", "Number of perimeters. More walls = stronger parts."),
    ("top_shell_layers", "Top layers", "int", "Strength", (0, 20, 1), "", ""),
    ("bottom_shell_layers", "Bottom layers", "int", "Strength", (0, 20, 1), "", ""),
    ("sparse_infill_density", "Infill density", "percent", "Strength", (0, 100, 5), "%", ""),
    ("sparse_infill_pattern", "Infill pattern", "enum", "Strength",
     ["crosshatch", "gyroid", "grid", "cubic", "adaptivecubic", "lightning", "honeycomb", "3dhoneycomb", "rectilinear", "line", "triangles", "tri-hexagon", "supportcubic"], "", "Gyroid is strong in all directions; lightning only supports top surfaces."),
    ("seam_position", "Seam position", "enum", "Quality", ["aligned", "nearest", "back", "random"], "", "Where each layer starts. 'Back' hides seams on figures."),
    ("ironing_type", "Ironing", "enum", "Quality", ["no ironing", "top", "topmost", "solid"], "", "Smooths flat top surfaces."),
    ("fuzzy_skin", "Fuzzy skin", "enum", "Quality", ["none", "external", "all"], "", ""),
    ("enable_support", "Supports", "bool", "Support", None, "", "Needed for overhangs steeper than the threshold."),
    ("support_type", "Support type", "enum", "Support", ["tree(auto)", "normal(auto)", "tree(manual)", "normal(manual)"], "", "Tree supports suit figures and use less material."),
    ("support_style", "Support style", "enum", "Support", ["default", "grid", "snug", "tree_slim", "tree_strong", "tree_hybrid", "organic"], "", ""),
    ("support_threshold_angle", "Overhang threshold", "int", "Support", (0, 90, 5), "°", ""),
    ("support_on_build_plate_only", "Only from build plate", "bool", "Support", None, "", ""),
    ("brim_type", "Brim", "enum", "Adhesion", ["auto_brim", "no_brim", "outer_only", "inner_only", "outer_and_inner", "brim_ears"], "", ""),
    ("brim_width", "Brim width", "float", "Adhesion", (0, 20, 1), "mm", ""),
    ("skirt_loops", "Skirt loops", "int", "Adhesion", (0, 10, 1), "", ""),
    ("outer_wall_speed", "Outer wall speed", "float", "Speed", (10, 600, 10), "mm/s", ""),
    ("inner_wall_speed", "Inner wall speed", "float", "Speed", (10, 600, 10), "mm/s", ""),
    ("sparse_infill_speed", "Infill speed", "float", "Speed", (10, 600, 10), "mm/s", ""),
    ("top_surface_speed", "Top surface speed", "float", "Speed", (10, 600, 10), "mm/s", ""),
    ("travel_speed", "Travel speed", "float", "Speed", (50, 1000, 10), "mm/s", ""),
    ("enable_prime_tower", "Prime tower", "bool", "Multi-colour", None, "", "Wipes the nozzle between colour changes."),
    ("prime_tower_width", "Prime tower width", "float", "Multi-colour", (10, 80, 1), "mm", ""),
    ("print_sequence", "Print sequence", "enum", "Other", ["by layer", "by object"], "", ""),
    ("spiral_mode", "Spiral vase", "bool", "Other", None, "", "One continuous wall, no top. For vases."),
    ("enable_arc_fitting", "Arc fitting", "bool", "Other", None, "", ""),
]

FILAMENT_SETTINGS = [
    ("nozzle_temperature", "Nozzle", (150, 320, 5), "°C"),
    ("nozzle_temperature_initial_layer", "Nozzle (first layer)", (150, 320, 5), "°C"),
    ("textured_plate_temp", "Bed (textured PEI)", (0, 110, 5), "°C"),
    ("hot_plate_temp", "Bed (high temp plate)", (0, 110, 5), "°C"),
]


def _read(kind: str, name: str) -> dict:
    path = ORCA / kind / f"{name}.json"
    if not path.is_file():
        raise KeyError(f"{kind} preset '{name}' not found")
    return json.loads(path.read_text(encoding="utf-8"))


def presets() -> dict:
    index = json.loads((ORCA / "index.json").read_text(encoding="utf-8"))
    return index


def _scalar(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def schema() -> dict:
    process = _read("process", DEFAULT_PROCESS)
    machine = _read("machine", DEFAULT_PRINTER)
    groups: dict[str, list] = {}
    for key, label, kind, group, options, unit, help_text in SETTINGS:
        raw = _scalar(process.get(key, machine.get(key)))
        if kind == "bool":
            default = str(raw) in {"1", "true", "True"}
        elif kind == "percent":
            default = float(str(raw or "0").rstrip("%"))
        elif kind == "int":
            default = int(float(raw or 0))
        elif kind == "float":
            default = float(raw or 0)
        else:
            default = raw
        item = {"key": key, "label": label, "type": kind, "default": default, "unit": unit, "help": help_text}
        if kind == "enum":
            item["options"] = list(options) if default in options else [default, *options]
        elif options:
            item["min"], item["max"], item["step"] = options
        groups.setdefault(group, []).append(item)
    filaments = []
    for name in presets()["filament"]:
        data = _read("filament", name)
        filaments.append({
            "name": name,
            "label": name.split(" @")[0],
            "type": _scalar(data.get("filament_type")),
            "color": _scalar(data.get("default_filament_colour")) or "#F2754E",
            "settings": {key: float(_scalar(data.get(key)) or 0) for key, *_ in FILAMENT_SETTINGS},
        })
    area = machine.get("printable_area", ["0x0", "250x0", "250x250", "0x250"])
    xs = [float(p.split("x")[0]) for p in area]
    ys = [float(p.split("x")[1]) for p in area]
    return {
        "printer": {
            "name": DEFAULT_PRINTER,
            "model": machine.get("printer_model"),
            "bed": [max(xs) - min(xs), max(ys) - min(ys)],
            "height": float(machine.get("printable_height", 250)),
            "nozzle": float(_scalar(machine.get("nozzle_diameter")) or 0.4),
            "max_filaments": MAX_FILAMENTS,
            "bed_types": BED_TYPES,
        },
        "process": DEFAULT_PROCESS,
        "groups": [{"name": name, "settings": items} for name, items in groups.items()],
        "filaments": filaments,
        "filament_settings": [
            {"key": k, "label": label, "min": r[0], "max": r[1], "step": r[2], "unit": unit} for k, label, r, unit in FILAMENT_SETTINGS
        ],
        "default_filament": DEFAULT_FILAMENT,
    }


def _encode(original, value):
    """Match Orca's storage format: strings, '%' suffixes, and per-extruder lists."""
    if isinstance(value, bool):
        value = "1" if value else "0"
    elif isinstance(value, float) and value.is_integer():
        value = str(int(value))
    sample = _scalar(original)
    text = str(value)
    if isinstance(sample, str) and sample.endswith("%") and not text.endswith("%"):
        text += "%"
    return [text] if isinstance(original, list) else text


def write_profiles(folder: Path, settings: dict, filaments: list[dict], bed_type: str | None = None) -> dict:
    """Write effective machine/process/filament JSON files for one slice."""
    folder.mkdir(parents=True, exist_ok=True)
    machine = _read("machine", DEFAULT_PRINTER)
    process = _read("process", DEFAULT_PROCESS)
    allowed = {key for key, *_ in SETTINGS}
    advanced = settings.pop("advanced", {}) if isinstance(settings.get("advanced"), dict) else {}
    for key, value in settings.items():
        if key not in allowed:
            raise ValueError(f"Unknown print setting '{key}'")
        process[key] = _encode(process.get(key, ""), value)
    for key, value in advanced.items():
        target = machine if key in machine and key not in process else process
        target[key] = _encode(target.get(key, ""), value)
    if bed_type:
        if bed_type not in BED_TYPES:
            raise ValueError("Unknown bed type")
        process["curr_bed_type"] = bed_type
    paths = {"machine": folder / "machine.json", "process": folder / "process.json", "filaments": []}
    paths["machine"].write_text(json.dumps(machine, indent=1), encoding="utf-8")
    paths["process"].write_text(json.dumps(process, indent=1), encoding="utf-8")
    if not filaments:
        filaments = [{"preset": DEFAULT_FILAMENT, "color": "#F2754E"}]
    if len(filaments) > MAX_FILAMENTS:
        raise ValueError(f"The Kobra S1 Combo holds at most {MAX_FILAMENTS} filaments")
    allowed_f = {k for k, *_ in FILAMENT_SETTINGS}
    for index, slot in enumerate(filaments):
        data = _read("filament", slot.get("preset") or DEFAULT_FILAMENT)
        for key, value in (slot.get("settings") or {}).items():
            if key in allowed_f:
                data[key] = _encode(data.get(key, ""), value)
        data["default_filament_colour"] = [slot.get("color", "#F2754E")]
        path = folder / f"filament{index + 1}.json"
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        paths["filaments"].append(path)
    return paths
