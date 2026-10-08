"""Read sliced G-code into a compact toolpath preview plus print statistics.

The preview keeps every extrusion move in order, tagged with its feature type,
filament, layer and speed, so the viewport can colour by any of them and show
any range of layers. G-code is only read, never rewritten.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

FEATURES = [
    ("Outer wall", "#FF7D38"),
    ("Inner wall", "#FFC64A"),
    ("Overhang wall", "#4A6BFF"),
    ("Sparse infill", "#B33B3B"),
    ("Internal solid infill", "#D45ACF"),
    ("Top surface", "#FF3D3D"),
    ("Bottom surface", "#7A6CFF"),
    ("Bridge", "#4CA7DB"),
    ("Internal Bridge", "#4CA7DB"),
    ("Gap infill", "#FFFFFF"),
    ("Skirt", "#00876E"),
    ("Brim", "#00876E"),
    ("Support", "#3AD173"),
    ("Support interface", "#257A47"),
    ("Support transition", "#257A47"),
    ("Prime tower", "#B3E3AB"),
    ("Ironing", "#FFA6A6"),
    ("Custom", "#5E7487"),
    ("Other", "#8C8C8C"),
]
FEATURE_INDEX = {name.lower(): i for i, (name, _) in enumerate(FEATURES)}
NUMBER = r"([+-]?(?:\d+\.?\d*|\.\d+))"
AXES = re.compile(r"([XYZEFIJR])" + NUMBER)


def _feature(name: str) -> int:
    key = name.strip().lower()
    if key in FEATURE_INDEX:
        return FEATURE_INDEX[key]
    if "support" in key:
        return FEATURE_INDEX["support"]
    if "tower" in key or "wipe" in key:
        return FEATURE_INDEX["prime tower"]
    return FEATURE_INDEX["other"]


def _duration(text: str) -> float:
    seconds = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", text):
        seconds += float(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return seconds


def parse(path: Path, max_segments: int = 6_000_000, max_travels: int = 1_500_000) -> dict:
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    absolute, absolute_e = True, True
    feed = 0.0
    feature = FEATURE_INDEX["custom"]
    tool = 0
    layer = -1
    layer_z: list[float] = []
    layer_start: list[int] = []
    seg = np.empty((min(max_segments, 1_000_000), 6), np.float32)
    meta = np.empty((len(seg), 4), np.float32)  # feature, tool, layer, speed mm/s
    n = 0
    trav = np.empty((min(max_travels, 500_000), 6), np.float32)
    t = 0
    stats: dict = {"tool_changes": 0}
    truncated = False

    def grow(array, needed):
        size = len(array)
        while size < needed:
            size *= 2
        out = np.empty((size, array.shape[1]), array.dtype)
        out[: len(array)] = array
        return out

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line[0] == ";":
                body = line[1:].strip()
                if body.startswith("TYPE:"):
                    feature = _feature(body[5:])
                elif body == "LAYER_CHANGE":
                    layer += 1
                    layer_start.append(n)
                    layer_z.append(pos["Z"])
                elif body.startswith("Z:") and layer >= 0 and len(layer_z) == layer + 1:
                    try:
                        layer_z[layer] = float(body[2:])
                    except ValueError:
                        pass
                elif "estimated printing time (normal mode)" in body:
                    stats["time_s"] = _duration(body.split("=", 1)[1])
                elif body.startswith("filament used [mm]"):
                    stats["filament_mm"] = [float(v) for v in re.findall(NUMBER, body.split("=", 1)[1])]
                elif body.startswith("filament used [g]"):
                    stats["filament_g"] = [float(v) for v in re.findall(NUMBER, body.split("=", 1)[1])]
                elif body.startswith("total filament used [g]"):
                    stats["total_g"] = float(re.findall(NUMBER, body.split("=", 1)[1])[0])
                elif body.startswith("filament_colour ="):
                    stats["colors"] = [c.strip() for c in body.split("=", 1)[1].split(";")]
                elif body.startswith("total filament change"):
                    stats["filament_changes"] = int(float(re.findall(NUMBER, body)[0]))
                continue
            code = line.split(";", 1)[0].strip().upper()
            if not code:
                continue
            if code[0] == "T" and code[1:].isdigit():
                new = int(code[1:])
                if new != tool and n > 0:
                    stats["tool_changes"] += 1
                tool = new
                continue
            word = code.split(" ", 1)[0]
            if word == "G90":
                absolute = True
                absolute_e = True
            elif word == "G91":
                absolute = False
                absolute_e = False
            elif word == "M82":
                absolute_e = True
            elif word == "M83":
                absolute_e = False
            elif word == "G92":
                for axis, value in AXES.findall(code):
                    if axis in pos:
                        pos[axis] = float(value)
            elif word in ("G0", "G1", "G2", "G3"):
                values = {axis: float(value) for axis, value in AXES.findall(code)}
                if "F" in values:
                    feed = values["F"]
                old = (pos["X"], pos["Y"], pos["Z"])
                for axis in "XYZ":
                    if axis in values:
                        pos[axis] = values[axis] if absolute else pos[axis] + values[axis]
                extrude = 0.0
                if "E" in values:
                    if absolute_e:
                        extrude = values["E"] - pos["E"]
                        pos["E"] = values["E"]
                    else:
                        extrude = values["E"]
                new = (pos["X"], pos["Y"], pos["Z"])
                if new == old:
                    continue
                points = [old, new]
                if word in ("G2", "G3") and ("I" in values or "J" in values):
                    cx, cy = old[0] + values.get("I", 0.0), old[1] + values.get("J", 0.0)
                    a0 = math.atan2(old[1] - cy, old[0] - cx)
                    a1 = math.atan2(new[1] - cy, new[0] - cx)
                    if word == "G3" and a1 <= a0:
                        a1 += 2 * math.pi
                    if word == "G2" and a1 >= a0:
                        a1 -= 2 * math.pi
                    r = math.hypot(old[0] - cx, old[1] - cy)
                    steps = max(2, int(abs(a1 - a0) * r / 0.5))
                    points = [
                        (cx + r * math.cos(a0 + (a1 - a0) * k / steps), cy + r * math.sin(a0 + (a1 - a0) * k / steps), old[2] + (new[2] - old[2]) * k / steps)
                        for k in range(steps + 1)
                    ]
                if extrude > 0 and layer >= 0:
                    for a, b in zip(points[:-1], points[1:]):
                        if n >= max_segments:
                            truncated = True
                            break
                        if n >= len(seg):
                            seg = grow(seg, n + 1)
                            meta = grow(meta, n + 1)
                        seg[n] = (*a, *b)
                        meta[n] = (feature, tool, layer, feed / 60.0)
                        n += 1
                elif extrude <= 0 and t < max_travels and (new[0] != old[0] or new[1] != old[1]):
                    if t >= len(trav):
                        trav = grow(trav, t + 1)
                    trav[t] = (*old, *new)
                    t += 1
    if n == 0:
        raise ValueError("The G-code contains no extrusion moves")
    layer_start.append(n)
    layers = [
        {"index": i, "z": round(layer_z[i], 4), "start": layer_start[i], "end": layer_start[i + 1]}
        for i in range(len(layer_z))
        if layer_start[i + 1] > layer_start[i]
    ]
    return {
        "segments": seg[:n],
        "meta": meta[:n],
        "travels": trav[:t],
        "layers": layers,
        "stats": {**stats, "segments": n, "travel_moves": t, "layers": len(layers), "truncated": truncated},
    }


def write_preview(gcode: Path, folder: Path) -> dict:
    data = parse(gcode)
    seg, meta, trav = data["segments"], data["meta"], data["travels"]
    blobs = [
        ("segments", seg.astype(np.float32).tobytes(), "float32", 6),
        ("feature", meta[:, 0].astype(np.uint8).tobytes(), "uint8", 1),
        ("tool", meta[:, 1].astype(np.uint8).tobytes(), "uint8", 1),
        ("speed", meta[:, 3].astype(np.float32).tobytes(), "float32", 1),
        ("travels", trav.astype(np.float32).tobytes(), "float32", 6),
    ]
    index = {}
    offset = 0
    with open(folder / "preview.bin", "wb") as out:
        for name, blob, dtype, width in blobs:
            pad = (-offset) % 4
            out.write(b"\0" * pad)
            offset += pad
            out.write(blob)
            index[name] = {"offset": offset, "bytes": len(blob), "type": dtype, "width": width}
            offset += len(blob)
    info = {
        "fields": index,
        "layers": data["layers"],
        "features": [{"name": n, "color": c} for n, c in FEATURES],
        "stats": data["stats"],
    }
    (folder / "preview.json").write_text(json.dumps(info), encoding="utf-8")
    return info
