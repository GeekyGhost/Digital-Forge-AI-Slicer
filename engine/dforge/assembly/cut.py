"""Planar cuts with joints, and joints between touching colour parts."""

from __future__ import annotations

import numpy as np
import igl
from scipy import ndimage
from scipy.spatial import cKDTree

from ..mesh.core import Mesh, face_normals
from ..mesh.solid import from_manifold, frame_matrix, to_manifold
from . import connectors

import manifold3d as mf


def _section_points(solid: mf.Manifold, origin: np.ndarray, normal: np.ndarray, count: int, spacing_mm: float):
    """Best joint positions on the cut face: the deepest points of the cross-section."""
    to_plane = np.linalg.inv(frame_matrix(origin, normal))
    local = solid.transform(to_plane[:3, :4].tolist())
    section = local.slice(0.0)
    polygons = section.to_polygons()
    if not polygons or section.area() <= 0:
        return []
    allpts = np.vstack(polygons)
    lo, hi = allpts.min(0), allpts.max(0)
    res = max(0.1, float((hi - lo).max()) / 400.0)
    w = int(np.ceil((hi[0] - lo[0]) / res)) + 3
    h = int(np.ceil((hi[1] - lo[1]) / res)) + 3
    from skimage.draw import polygon as fill_polygon

    xs = lo[0] + (np.arange(w) - 1) * res
    ys = lo[1] + (np.arange(h) - 1) * res
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    inside = np.zeros((w, h), dtype=bool)
    for poly in polygons:  # even-odd fill handles holes in the section
        p = np.asarray(poly)
        rr, cc = fill_polygon((p[:, 0] - lo[0]) / res + 1, (p[:, 1] - lo[1]) / res + 1, shape=(w, h))
        mask = np.zeros((w, h), dtype=bool)
        mask[rr, cc] = True
        inside ^= mask
    depth = ndimage.distance_transform_edt(inside) * res
    chosen = []
    work = depth.copy()
    for _ in range(count):
        i = int(np.argmax(work))
        if work.flat[i] <= 0:
            break
        ix, iy = np.unravel_index(i, work.shape)
        point2 = np.array([xs[ix], ys[iy]])
        chosen.append((point2, float(depth[ix, iy])))
        dist = np.hypot(gx - point2[0], gy - point2[1])
        work[dist < spacing_mm] = 0
    frame = frame_matrix(origin, normal)
    return [
        {"origin": (frame @ np.array([p[0], p[1], 0.0, 1.0]))[:3].tolist(), "max_radius_mm": round(r, 2)}
        for p, r in chosen
    ]


def suggest_cut_joints(mesh: Mesh, origin, normal, count: int = 1, spacing_mm: float = 15.0) -> list[dict]:
    return _section_points(to_manifold(mesh), np.asarray(origin, float), np.asarray(normal, float), count, spacing_mm)


def plane_cut(mesh: Mesh, origin, normal, joints: list[dict] | None = None, auto_joints: dict | None = None):
    """Cut along a plane. Part A is behind the plane, part B is on the +normal side.

    joints: [{type, origin, params, spin}] placed on the cut face. auto_joints:
    {type, count, params} to place joints at the thickest points of the section.
    """
    origin = np.asarray(origin, dtype=float)
    normal = np.asarray(normal, dtype=float)
    normal = normal / np.linalg.norm(normal)
    solid = to_manifold(mesh)
    joints = list(joints or [])
    if auto_joints and auto_joints.get("type") and auto_joints.get("count", 0) > 0:
        params = auto_joints.get("params", {})
        spacing = float(params.get("diameter", connectors.DEFAULTS["diameter"])) * 3
        for spot in _section_points(solid, origin, normal, int(auto_joints["count"]), spacing):
            joints.append({"type": auto_joints["type"], "origin": spot["origin"], "params": params, "max_radius_mm": spot["max_radius_mm"]})
    above, below = solid.split_by_plane(normal.tolist(), float(normal @ origin))
    if above.is_empty() or below.is_empty():
        raise ValueError("The plane does not pass through the model")
    for joint in joints:
        joint["axis"] = normal.tolist()
    part_a, part_b, extras, checks = connectors.apply(below, above, joints)
    parts = [
        {"name": "A", "mesh": from_manifold(part_a, mesh), "pieces": len(part_a.decompose()), "volume_mm3": round(part_a.volume(), 2)},
        {"name": "B", "mesh": from_manifold(part_b, mesh), "pieces": len(part_b.decompose()), "volume_mm3": round(part_b.volume(), 2)},
    ]
    for name, solid_extra in extras:
        parts.append({"name": name, "mesh": from_manifold(solid_extra), "pieces": 1, "volume_mm3": round(solid_extra.volume(), 2), "extra": True})
    report = {
        "plane": {"origin": origin.tolist(), "normal": normal.tolist()},
        "joints": [{k: v for k, v in j.items() if k != "axis"} for j in joints],
        "checks": checks,
        "warnings": [w for c in checks for w in c["warnings"]],
    }
    return parts, report


def find_interfaces(parts: list[Mesh], source: Mesh, tolerance: float = 0.05) -> list[dict]:
    """Where colour parts touch each other (not the outer surface), and the best joint spot."""
    results = []
    for i in range(len(parts)):
        a = parts[i]
        ca = a.centroids()
        na = face_normals(a.vertices, a.faces)
        tri = a.vertices[a.faces]
        area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        on_surface, _, _ = igl.point_mesh_squared_distance(ca, source.vertices, source.faces)
        internal = on_surface > tolerance**2
        for j in range(len(parts)):
            if j == i:
                continue
            b = parts[j]
            sq, _, _ = igl.point_mesh_squared_distance(ca, b.vertices, b.faces)
            touching = internal & (sq < tolerance**2)
            if j < i or touching.sum() < 3:
                continue
            idx = np.flatnonzero(touching)
            others = np.flatnonzero(~touching)
            if len(others):
                depth, _ = cKDTree(ca[others]).query(ca[idx], workers=-1)
            else:
                depth = np.full(len(idx), np.inf)
            best = idx[int(np.argmax(depth))]
            results.append({
                "a": i,
                "b": j,
                "area_mm2": round(float(area[idx].sum()), 2),
                "origin": ca[best].tolist(),
                "axis": na[best].tolist(),
                "max_radius_mm": round(float(depth.max()), 2),
            })
    return results


def join_parts(parts: list[Mesh], joints: list[dict]) -> tuple[list[Mesh], list[tuple[str, Mesh]], list[dict]]:
    """Apply joints between parts. joint: {a, b, type, origin, axis, params}."""
    solids = [to_manifold(p) for p in parts]
    extras: list[tuple[str, Mesh]] = []
    checks = []
    for joint in joints:
        a, b = int(joint["a"]), int(joint["b"])
        new_a, new_b, extra, check = connectors.apply(solids[a], solids[b], [joint])
        solids[a], solids[b] = new_a, new_b
        extras += [(name, from_manifold(s)) for name, s in extra]
        checks += [{**c, "a": a, "b": b} for c in check]
    meshes = []
    for original, solid in zip(parts, solids):
        m = from_manifold(solid)
        if original.face_colors is not None:
            m.face_colors = np.tile(original.face_colors[0], (len(m.faces), 1))
        meshes.append(m)
    return meshes, extras, checks
