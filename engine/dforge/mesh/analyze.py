"""Printability and topology report for a canonical mesh."""

from __future__ import annotations

import numpy as np
import trimesh

from .core import Mesh, edge_topology

BUILD_VOLUME_MM = (250.0, 250.0, 250.0)  # Anycubic Kobra S1


def analyze(mesh: Mesh, build_volume=BUILD_VOLUME_MM) -> dict:
    faces = mesh.faces
    triangles = mesh.vertices[faces]
    areas = 0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    degenerate = int((areas < 1e-10).sum())
    _, unique_index = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    duplicate = int(len(faces) - len(unique_index))
    topo = edge_topology(faces)
    tm = mesh.trimesh()
    components = int(trimesh.graph.connected_components(tm.face_adjacency, nodes=np.arange(len(faces)), min_len=1).__len__())
    watertight = topo["boundary_edges"] == 0 and topo["nonmanifold_edges"] == 0
    winding = bool(tm.is_winding_consistent) if watertight else False
    volume = float(tm.volume) if watertight else None
    extents = mesh.extents
    fits = bool(np.all(extents <= np.asarray(build_volume) + 1e-6))

    issues: list[str] = []
    if topo["boundary_edges"]:
        issues.append(f"{topo['boundary_edges']:,} open edges (holes or gaps)")
    if topo["nonmanifold_edges"]:
        issues.append(f"{topo['nonmanifold_edges']:,} nonmanifold edges")
    if degenerate:
        issues.append(f"{degenerate:,} zero-area faces")
    if duplicate:
        issues.append(f"{duplicate:,} duplicate faces")
    if watertight and not winding:
        issues.append("Inconsistent face orientation")
    if volume is not None and volume < 0:
        issues.append("Faces point inward (inverted solid)")
    if not fits:
        issues.append("Larger than the Kobra S1 build volume (250 × 250 × 250 mm)")
    if min(extents) < max(extents) * 0.02:
        issues.append("Nearly flat: check from the side, it may be a sheet rather than a solid")

    if watertight and winding and (volume or 0) > 0 and not degenerate and not duplicate:
        action = "ok"
    elif topo["nonmanifold_edges"] or topo["boundary_edges"] > 200 or components > 50:
        action = "wrap"
    else:
        action = "repair"

    return {
        "faces": int(len(faces)),
        "vertices": int(len(mesh.vertices)),
        "size_mm": [round(float(v), 3) for v in extents],
        "bounds_mm": mesh.bounds.round(3).tolist(),
        "surface_area_mm2": round(float(areas.sum()), 2),
        "volume_mm3": None if volume is None else round(abs(volume), 2),
        "watertight": bool(watertight),
        "winding_consistent": winding,
        "components": components,
        "degenerate_faces": degenerate,
        "duplicate_faces": duplicate,
        **topo,
        "fits_printer": fits,
        "has_color": mesh.face_colors is not None,
        "issues": issues,
        "recommended": action,
    }
