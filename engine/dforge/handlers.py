"""Task implementations. Each takes (context, params) and returns a JSON-able result."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import trimesh

from .assembly import colors as colormod
from .assembly.cut import find_interfaces, join_parts, plane_cut, suggest_cut_joints
from .assembly.split import split_by_labels
from .config import EXPORTS
from .generate import trellis
from .mesh.core import Mesh, transform
from .mesh.io import export_mesh
from .mesh.repair import repair, simplify
from .mesh.solid import from_manifold, to_manifold
from .mesh.wrap import wrap
from .tasks import Context


def _num(params: dict, key: str, default: float, low: float, high: float) -> float:
    value = params.get(key, default)
    if value is None:
        return default
    value = float(value)
    if not low <= value <= high:
        raise ValueError(f"{key.replace('_', ' ')} must be between {low} and {high}")
    return value


def _source(ctx: Context, params: dict) -> tuple[dict, Mesh]:
    record = ctx.store.get(params["object_id"])
    if record["kind"] != "mesh":
        raise ValueError("Choose a 3D model")
    return record, ctx.store.mesh(record["id"])


def do_import(ctx: Context, params: dict) -> dict:
    ctx.progress(0.1, f"Reading {params['name']}")
    path = ctx.folder / params["file"]
    record = ctx.store.import_file(path, Path(params["name"]).stem)
    path.unlink(missing_ok=True)
    return {"objects": [record]}


def do_repair(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    ctx.progress(0.2, "Cleaning up the mesh")
    result, report = repair(mesh, _num(params, "remove_small_pct", 0.0, 0.0, 20.0) / 100.0)
    new = ctx.store.add_mesh(result, f"{record['name']} (repaired)", parent=record["id"], operation="repair", report=report)
    return {"objects": [new], "report": report}


def do_wrap(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    voxel = params.get("voxel_mm")
    result, report = wrap(
        mesh,
        voxel_mm=None if voxel in (None, "", 0) else _num(params, "voxel_mm", 0.4, 0.08, 5.0),
        close_gaps_mm=_num(params, "close_gaps_mm", 0.0, 0.0, 20.0),
        offset_mm=_num(params, "offset_mm", 0.0, -5.0, 10.0),
        fill_cavities=bool(params.get("fill_cavities", True)),
        smooth_iterations=int(_num(params, "smooth", 4, 0, 30)),
        target_faces=int(params["target_faces"]) if params.get("target_faces") else None,
        keep_largest_only=bool(params.get("keep_largest_only", False)),
        progress=ctx.progress,
    )
    new = ctx.store.add_mesh(result, f"{record['name']} (wrapped)", parent=record["id"], operation="wrap", report=report)
    return {"objects": [new], "report": report}


def do_simplify(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    ctx.progress(0.2, "Reducing faces")
    result, report = simplify(mesh, int(_num(params, "target_faces", 100_000, 100, 5_000_000)))
    new = ctx.store.add_mesh(result, f"{record['name']} (simplified)", parent=record["id"], operation="simplify", report=report)
    return {"objects": [new], "report": report}


def do_colors(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    ctx.progress(0.2, "Finding colour regions")
    labels, palette = colormod.detect_palette(
        mesh,
        n_colors=int(_num(params, "n_colors", 4, 1, 16)),
        fixed=params.get("fixed") or None,
        min_region_pct=_num(params, "min_region_pct", 0.3, 0.0, 10.0),
        shading_tolerance=_num(params, "shading_tolerance", 0.85, 0.0, 1.0),
    )
    ctx.store.save_labels(record["id"], labels, palette)
    return {"object_id": record["id"], "palette": palette}


def do_split(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    labels = ctx.store.load_labels(record["id"])
    palette = record.get("palette")
    if labels is None or not palette:
        raise ValueError("Find or paint colour regions first")
    parts, report = split_by_labels(
        mesh,
        labels,
        palette,
        voxel_mm=_num(params, "voxel_mm", 0, 0, 5) or None,
        shell_mm=_num(params, "shell_mm", 0.0, 0.0, 50.0),
        core_label=params.get("core_label"),
        smoothing=_num(params, "smoothing", 1.0, 0.0, 4.0),
        progress=ctx.progress,
    )
    objects = []
    for part in parts:
        new = ctx.store.add_mesh(
            part["mesh"],
            f"{record['name']} · colour {part['label'] + 1}",
            parent=record["id"],
            operation="split",
            color=None,
            extra={"filament": part["filament"], "split_color": part["color"], "label": part["label"]},
        )
        objects.append(new)
    return {"objects": objects, "report": report, "group": record["id"]}


def do_suggest_joints(ctx: Context, params: dict) -> dict:
    ids = params.get("part_ids") or []
    if len(ids) < 2:
        raise ValueError("Select at least two parts")
    ctx.progress(0.2, "Finding where parts touch")
    parts = [ctx.store.mesh(i) for i in ids]
    source = ctx.store.mesh(params["source_id"]) if params.get("source_id") else Mesh(
        np.vstack([p.vertices for p in parts]),
        np.vstack([p.faces + sum(len(q.vertices) for q in parts[:k]) for k, p in enumerate(parts)]),
    )
    interfaces = find_interfaces(parts, source)
    for item in interfaces:
        item["a_id"], item["b_id"] = ids[item["a"]], ids[item["b"]]
    return {"interfaces": interfaces}


def do_join(ctx: Context, params: dict) -> dict:
    ids = params["part_ids"]
    ctx.progress(0.2, "Adding joints")
    parts = [ctx.store.mesh(i) for i in ids]
    meshes, extras, checks = join_parts(parts, params.get("joints", []))
    objects = []
    for object_id, mesh in zip(ids, meshes):
        record = ctx.store.get(object_id)
        objects.append(ctx.store.add_mesh(mesh, record["name"], parent=object_id, operation="joint",
                                          extra={k: record[k] for k in ("filament", "split_color", "label") if k in record}))
    for name, mesh in extras:
        objects.append(ctx.store.add_mesh(mesh, name.capitalize(), operation="joint", color=(220, 220, 220)))
    return {"objects": objects, "replaces": ids, "checks": checks}


def do_cut(ctx: Context, params: dict) -> dict:
    record, mesh = _source(ctx, params)
    ctx.progress(0.2, "Cutting")
    parts, report = plane_cut(mesh, params["origin"], params["normal"], params.get("joints"), params.get("auto_joints"))
    objects = []
    for part in parts:
        if part.get("extra"):
            objects.append(ctx.store.add_mesh(part["mesh"], f"{record['name']} · {part['name']}", operation="cut", color=(220, 220, 220)))
        else:
            objects.append(ctx.store.add_mesh(part["mesh"], f"{record['name']} · {part['name']}", parent=record["id"], operation="cut"))
    return {"objects": objects, "replaces": [record["id"]], "report": report}


def do_suggest_cut(ctx: Context, params: dict) -> dict:
    _, mesh = _source(ctx, params)
    return {"spots": suggest_cut_joints(mesh, params["origin"], params["normal"], int(params.get("count", 1)), float(params.get("spacing_mm", 15)))}


PRIMITIVES = {
    "box": lambda s: trimesh.creation.box(extents=s),
    "cylinder": lambda s: trimesh.creation.cylinder(radius=s[0] / 2, height=s[2], sections=96),
    "sphere": lambda s: trimesh.creation.icosphere(subdivisions=5, radius=s[0] / 2),
    "cone": lambda s: trimesh.creation.cone(radius=s[0] / 2, height=s[2], sections=96),
    "torus": lambda s: trimesh.creation.torus(major_radius=s[0] / 2 - s[2] / 2, minor_radius=s[2] / 2),
}


def do_primitive(ctx: Context, params: dict) -> dict:
    shape = params.get("shape", "box")
    if shape not in PRIMITIVES:
        raise ValueError("Unknown shape")
    size = [float(v) for v in params.get("size", [20, 20, 20])]
    tm = PRIMITIVES[shape](size)
    lo = tm.bounds[0]
    tm.apply_translation([-(tm.bounds[0][0] + tm.bounds[1][0]) / 2, -(tm.bounds[0][1] + tm.bounds[1][1]) / 2, -lo[2]])
    mesh = Mesh(np.asarray(tm.vertices), np.asarray(tm.faces))
    return {"objects": [ctx.store.add_mesh(mesh, shape.capitalize(), operation="primitive")]}


def do_boolean(ctx: Context, params: dict) -> dict:
    """Combine two placed objects. Matrices map each object into the shared world."""
    op = params.get("op", "union")
    a = transform(ctx.store.mesh(params["a"]), np.asarray(params["matrix_a"]).reshape(4, 4))
    b = transform(ctx.store.mesh(params["b"]), np.asarray(params["matrix_b"]).reshape(4, 4))
    ctx.progress(0.3, f"Boolean {op}")
    sa, sb = to_manifold(a), to_manifold(b)
    result = {"union": sa + sb, "difference": sa - sb, "intersection": sa ^ sb}.get(op)
    if result is None:
        raise ValueError("Unknown boolean operation")
    if result.is_empty():
        raise ValueError("The result is empty")
    merged = Mesh(np.vstack([a.vertices, b.vertices]), np.vstack([a.faces, b.faces + len(a.vertices)]),
                  np.vstack([c for c in (a.face_colors, b.face_colors) if c is not None]) if a.face_colors is not None and b.face_colors is not None else None)
    mesh = from_manifold(result, merged if merged.face_colors is not None else None)
    name = f"{ctx.store.get(params['a'])['name']} {op}"
    return {"objects": [ctx.store.add_mesh(mesh, name, operation="boolean")], "world": True, "replaces": [params["a"], params["b"]]}


def do_slice(ctx: Context, params: dict) -> dict:
    from .slicer.orca import slice_plate

    return slice_plate(ctx.store, params, ctx.folder, ctx.log, ctx.cancelled, ctx.progress)


def do_export(ctx: Context, params: dict) -> dict:
    fmt = params.get("format", "stl").lower()
    if fmt not in {"stl", "3mf", "obj", "glb", "ply"}:
        raise ValueError("Unknown export format")
    items = params.get("items", [])
    if not items:
        raise ValueError("Nothing to export")
    meshes = []
    for item in items:
        mesh = ctx.store.mesh(item["object_id"])
        if item.get("matrix"):
            mesh = transform(mesh, np.asarray(item["matrix"]).reshape(4, 4))
        meshes.append(mesh)
    ctx.progress(0.4, "Writing file")
    name = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in params.get("name", "export"))[:60] or "export"
    if fmt == "3mf":
        from .slicer.threemf import Part, PrintObject, write_3mf

        objects = [PrintObject(name=it.get("name", f"object {i + 1}"), parts=[Part(it.get("name", "part"), m, int(it.get("filament", 1)))]) for i, (it, m) in enumerate(zip(items, meshes))]
        out = write_3mf(ctx.folder / f"{name}.3mf", objects)
    else:
        offset = 0
        verts, faces, cols = [], [], []
        for m in meshes:
            verts.append(m.vertices)
            faces.append(m.faces + offset)
            cols.append(m.face_colors if m.face_colors is not None else np.full((len(m.faces), 3), 200, np.uint8))
            offset += len(m.vertices)
        out = export_mesh(Mesh(np.vstack(verts), np.vstack(faces), np.vstack(cols)), ctx.folder / f"{name}.{fmt}")
    if params.get("destination"):
        dest = Path(params["destination"])
        shutil.copyfile(out, dest)
        return {"file": out.name, "saved_to": str(dest)}
    copy = EXPORTS / out.name
    shutil.copyfile(out, copy)
    return {"file": out.name, "saved_to": str(copy)}


def do_generate(ctx: Context, params: dict) -> dict:
    image_record = ctx.store.get(params["image_id"])
    if image_record["kind"] != "image":
        raise ValueError("Choose an image")
    image = ctx.store.path(image_record["id"], image_record["file"])
    output = ctx.folder / "generated.glb"
    args = trellis.command(image, output, params)
    ctx.progress(0.02, "Loading TRELLIS.2")

    def on_line(line: str) -> None:
        stage = trellis.stage_of(line)
        if stage and stage != ctx.task.get("message"):
            ctx.task["message"] = stage

    code = ctx.run(args, timeout=float(params.get("timeout", 3600)), on_line=on_line)
    if code != 0 or not output.is_file():
        raise RuntimeError(f"TRELLIS.2 failed (exit code {code}). See the task log.")
    ctx.progress(0.92, "Importing the generated model")
    record = ctx.store.import_file(output, f"{image_record['name']} 3D")
    ctx.store.update(record["id"], generated={k: params.get(k) for k in ("resolution", "seed", "background", "steps")}, parent=image_record["id"])
    return {"objects": [ctx.store.get(record["id"])]}


HANDLERS = {
    "import": do_import,
    "repair": do_repair,
    "wrap": do_wrap,
    "simplify": do_simplify,
    "colors": do_colors,
    "split": do_split,
    "suggest_joints": do_suggest_joints,
    "join": do_join,
    "cut": do_cut,
    "suggest_cut": do_suggest_cut,
    "primitive": do_primitive,
    "boolean": do_boolean,
    "slice": do_slice,
    "export": do_export,
    "generate": do_generate,
}
