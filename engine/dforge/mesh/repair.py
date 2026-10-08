"""Non-destructive clean-up and decimation. Holes larger than a few triangles go to Shell Wrap."""

from __future__ import annotations

import numpy as np
import trimesh

from .core import Mesh, drop_faces, transfer_colors, weld


def repair(mesh: Mesh, min_component_fraction: float = 0.0) -> tuple[Mesh, dict]:
    report: dict = {"steps": []}
    work = weld(mesh)
    tri = work.vertices[work.faces]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    keep = area > 1e-10
    keep &= (work.faces[:, 0] != work.faces[:, 1]) & (work.faces[:, 1] != work.faces[:, 2]) & (work.faces[:, 0] != work.faces[:, 2])
    _, first = np.unique(np.sort(work.faces, axis=1), axis=0, return_index=True)
    unique_mask = np.zeros(len(work.faces), bool)
    unique_mask[first] = True
    removed_degenerate = int((~keep).sum())
    removed_duplicate = int((keep & ~unique_mask).sum())
    work = drop_faces(work, keep & unique_mask)
    report["steps"].append(f"Removed {removed_degenerate:,} zero-area and {removed_duplicate:,} duplicate faces")

    if min_component_fraction > 0:
        tm = work.trimesh()
        groups = trimesh.graph.connected_components(tm.face_adjacency, nodes=np.arange(len(work.faces)), min_len=1)
        threshold = max(1, int(len(work.faces) * min_component_fraction))
        keep = np.zeros(len(work.faces), bool)
        dropped = 0
        for group in groups:
            if len(group) >= threshold:
                keep[group] = True
            else:
                dropped += 1
        if dropped:
            work = drop_faces(work, keep)
            report["steps"].append(f"Removed {dropped:,} small loose pieces")

    tm = work.trimesh()
    original_faces = len(tm.faces)
    trimesh.repair.fix_winding(tm)
    trimesh.repair.fix_inversion(tm, multibody=True)
    report["steps"].append("Made face orientation consistent and outward")
    trimesh.repair.fill_holes(tm)
    added = len(tm.faces) - original_faces
    if added:
        report["steps"].append(f"Filled {added:,} small-hole triangles")
    colors = None
    if work.face_colors is not None:
        colors = np.vstack([work.face_colors, transfer_colors(work, tm.vertices, tm.faces[original_faces:])]) if added else work.face_colors
    result = Mesh(np.asarray(tm.vertices), np.asarray(tm.faces), colors)
    return result, report


def simplify(mesh: Mesh, target_faces: int) -> tuple[Mesh, dict]:
    target_faces = int(max(100, target_faces))
    if target_faces >= len(mesh.faces):
        return mesh.copy(), {"steps": ["Already at or below the face budget"]}
    from .solid import is_solid, simplify_solid

    if is_solid(mesh):
        result = simplify_solid(mesh, target_faces, max_tolerance=float(mesh.extents.max()) * 0.01)
        return result, {"steps": [f"Reduced {len(mesh.faces):,} → {len(result.faces):,} faces (kept watertight)"]}
    tm = mesh.trimesh()
    reduced = tm.simplify_quadric_decimation(face_count=target_faces)
    colors = transfer_colors(mesh, reduced.vertices, reduced.faces)
    result = Mesh(np.asarray(reduced.vertices), np.asarray(reduced.faces), colors)
    return result, {"steps": [f"Reduced {len(mesh.faces):,} → {len(result.faces):,} faces"]}
