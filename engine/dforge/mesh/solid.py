"""Exact solid operations via manifold3d (booleans, plane splits, safe decimation)."""

from __future__ import annotations

import manifold3d as mf
import numpy as np

from .core import Mesh, transfer_colors


def to_manifold(mesh: Mesh) -> mf.Manifold:
    solid = mf.Manifold(mf.Mesh(vert_properties=mesh.vertices.astype(np.float32), tri_verts=mesh.faces.astype(np.uint32)))
    if solid.status() != mf.Error.NoError:
        raise ValueError(
            f"Not a closed solid ({solid.status().name}). Run Shell Wrap first to make it watertight."
        )
    return solid


def from_manifold(solid: mf.Manifold, color_source: Mesh | None = None) -> Mesh:
    data = solid.to_mesh()
    vertices = np.asarray(data.vert_properties)[:, :3].astype(np.float64)
    faces = np.asarray(data.tri_verts).astype(np.int64)
    colors = transfer_colors(color_source, vertices, faces) if color_source is not None else None
    return Mesh(vertices, faces, colors)


def is_solid(mesh: Mesh) -> bool:
    try:
        to_manifold(mesh)
        return True
    except ValueError:
        return False


def simplify_solid(mesh: Mesh, target_faces: int, max_tolerance: float = 0.25) -> Mesh:
    """Reduce a closed mesh towards target_faces while guaranteeing it stays manifold."""
    solid = to_manifold(mesh)
    if solid.num_tri() <= target_faces:
        return mesh
    low, high = 0.0, max_tolerance
    best = solid.simplify(high)
    if best.num_tri() > target_faces:
        return from_manifold(best, mesh)
    for _ in range(7):
        mid = (low + high) / 2
        trial = solid.simplify(mid)
        if trial.num_tri() <= target_faces:
            best, high = trial, mid
        else:
            low = mid
    return from_manifold(best, mesh)


def cylinder(radius: float, height: float, segments: int = 48) -> mf.Manifold:
    return mf.Manifold.cylinder(height, radius, radius, segments)


def box(size) -> mf.Manifold:
    return mf.Manifold.cube(tuple(float(v) for v in size), True)


def frame_matrix(origin, axis) -> np.ndarray:
    """4x4 affine whose +Z maps to axis and origin to origin."""
    z = np.asarray(axis, dtype=np.float64)
    z = z / np.linalg.norm(z)
    helper = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(helper, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
    return m


def place(solid: mf.Manifold, matrix: np.ndarray) -> mf.Manifold:
    m = np.asarray(matrix, dtype=np.float64)
    return solid.transform(m[:3, :4].tolist())
