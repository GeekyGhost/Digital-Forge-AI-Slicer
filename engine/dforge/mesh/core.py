"""Canonical mesh representation shared by every geometry operation.

Units are millimetres and +Z is up (the printer's frame). Colours are stored per
face so they survive welding, booleans and remeshing by nearest-face transfer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy.spatial import cKDTree


@dataclass
class Mesh:
    vertices: np.ndarray  # (N, 3) float64, mm, Z-up
    faces: np.ndarray  # (M, 3) int64
    face_colors: np.ndarray | None = None  # (M, 3) uint8 RGB
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.vertices = np.ascontiguousarray(self.vertices, dtype=np.float64)
        self.faces = np.ascontiguousarray(self.faces, dtype=np.int64)
        if self.face_colors is not None:
            self.face_colors = np.ascontiguousarray(self.face_colors[:, :3], dtype=np.uint8)
            if len(self.face_colors) != len(self.faces):
                raise ValueError("face_colors must have one entry per face")

    @property
    def bounds(self) -> np.ndarray:
        return np.array([self.vertices.min(0), self.vertices.max(0)])

    @property
    def extents(self) -> np.ndarray:
        b = self.bounds
        return b[1] - b[0]

    def trimesh(self, process: bool = False) -> trimesh.Trimesh:
        return trimesh.Trimesh(self.vertices, self.faces, process=process, validate=False)

    def centroids(self) -> np.ndarray:
        return self.vertices[self.faces].mean(axis=1)

    def copy(self) -> "Mesh":
        return Mesh(
            self.vertices.copy(),
            self.faces.copy(),
            None if self.face_colors is None else self.face_colors.copy(),
            dict(self.meta),
        )

    @classmethod
    def from_trimesh(cls, mesh: trimesh.Trimesh, face_colors: np.ndarray | None = None) -> "Mesh":
        return cls(np.asarray(mesh.vertices), np.asarray(mesh.faces), face_colors)

    def save(self, path) -> None:
        arrays = {"vertices": self.vertices.astype(np.float32), "faces": self.faces.astype(np.int32)}
        if self.face_colors is not None:
            arrays["face_colors"] = self.face_colors
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path) -> "Mesh":
        with np.load(path) as data:
            colors = data["face_colors"] if "face_colors" in data.files else None
            return cls(data["vertices"].astype(np.float64), data["faces"].astype(np.int64), colors)


def weld(mesh: Mesh, digits: int = 6) -> Mesh:
    """Merge coincident vertices (UV seams split them) without touching face order."""
    rounded = np.round(mesh.vertices, digits)
    unique, inverse = np.unique(rounded, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    # Keep the first original coordinate for each merged group.
    first = np.full(len(unique), -1, dtype=np.int64)
    order = np.arange(len(inverse))[::-1]
    first[inverse[order]] = order
    faces = inverse[mesh.faces]
    return Mesh(mesh.vertices[first], faces, mesh.face_colors, dict(mesh.meta))


def drop_faces(mesh: Mesh, keep: np.ndarray) -> Mesh:
    keep = np.asarray(keep, dtype=bool)
    faces = mesh.faces[keep]
    colors = None if mesh.face_colors is None else mesh.face_colors[keep]
    used = np.unique(faces)
    remap = np.full(len(mesh.vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return Mesh(mesh.vertices[used], remap[faces], colors, dict(mesh.meta))


def face_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    tri = np.asarray(vertices)[np.asarray(faces)]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def transfer_colors(source: Mesh, target_vertices: np.ndarray, target_faces: np.ndarray, k: int = 8) -> np.ndarray | None:
    """Colour each target face from the nearest source face facing the same way.

    Generated meshes are often double-walled; the hidden inner wall carries
    junk texture. Requiring agreeing normals keeps the visible colour.
    """
    if source.face_colors is None or len(target_faces) == 0:
        return None
    target_vertices = np.asarray(target_vertices)
    target_faces = np.asarray(target_faces)
    tree = cKDTree(source.centroids())
    centroids = target_vertices[target_faces].mean(axis=1)
    k = min(k, len(source.faces))
    _, index = tree.query(centroids, k=k, workers=-1)
    index = index.reshape(len(centroids), k)
    src_n = face_normals(source.vertices, source.faces)
    dst_n = face_normals(target_vertices, target_faces)
    agree = np.einsum("ijk,ik->ij", src_n[index], dst_n) > 0.2
    first = np.where(agree.any(axis=1), agree.argmax(axis=1), 0)
    return source.face_colors[index[np.arange(len(index)), first]]


def transform(mesh: Mesh, matrix: np.ndarray) -> Mesh:
    matrix = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
    vertices = mesh.vertices @ matrix[:3, :3].T + matrix[:3, 3]
    faces = mesh.faces
    if np.linalg.det(matrix[:3, :3]) < 0:  # mirroring flips winding
        faces = faces[:, ::-1]
    return Mesh(vertices, faces, mesh.face_colors, dict(mesh.meta))


def edge_topology(faces: np.ndarray) -> dict:
    """Counts of boundary (1 face) and nonmanifold (>2 faces) edges on a welded mesh."""
    if len(faces) == 0:
        return {"edges": 0, "boundary_edges": 0, "nonmanifold_edges": 0}
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return {
        "edges": int(len(counts)),
        "boundary_edges": int((counts == 1).sum()),
        "nonmanifold_edges": int((counts > 2).sum()),
    }
