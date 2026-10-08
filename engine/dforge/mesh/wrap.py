"""Shell Wrap: rebuild any mesh as one closed, outward-facing solid.

The generalised winding number decides inside/outside robustly even when the
source has holes, gaps, flipped faces, self-intersections or overlapping shells.
A narrow-band exact distance keeps the new surface on the original one; optional
morphological closing bridges gaps, and enclosed voids are filled. The result is
remeshed with marching cubes, lightly smoothed, decimated and recoloured from the
source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import igl
import numpy as np
import trimesh
from scipy import ndimage
from skimage.measure import marching_cubes

from .core import Mesh, edge_topology, transfer_colors

MAX_CELLS = 24_000_000


@dataclass
class Grid:
    origin: np.ndarray  # world position of node (0, 0, 0)
    voxel: float
    shape: tuple[int, int, int]

    def points(self) -> np.ndarray:
        axes = [self.origin[i] + self.voxel * np.arange(self.shape[i], dtype=np.float64) for i in range(3)]
        return np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)

    def to_world(self, ijk: np.ndarray) -> np.ndarray:
        return self.origin + np.asarray(ijk, dtype=np.float64) * self.voxel


def make_grid(mesh: Mesh, voxel: float, pad: float) -> Grid:
    lo, hi = mesh.bounds
    lo = lo - pad
    hi = hi + pad
    while True:
        shape = tuple(int(np.ceil((hi[i] - lo[i]) / voxel)) + 1 for i in range(3))
        if np.prod(shape) <= MAX_CELLS:
            return Grid(lo, voxel, shape)  # type: ignore[arg-type]
        voxel *= 1.15


def auto_voxel(mesh: Mesh, detail: int = 300) -> float:
    return float(np.clip(mesh.extents.max() / detail, 0.12, 2.0))


def winding_inside(mesh: Mesh, points: np.ndarray, chunk: int = 4_000_000) -> np.ndarray:
    """|winding number| > 0.5, so flipped shells still count as solid."""
    V = mesh.vertices
    F = mesh.faces
    out = np.empty(len(points), dtype=bool)
    for start in range(0, len(points), chunk):
        w = igl.fast_winding_number(V, F, points[start : start + chunk])
        out[start : start + chunk] = np.abs(w) > 0.5
    return out


def surface_mask(mesh: Mesh, grid: Grid) -> np.ndarray:
    """Grid nodes within about half a voxel of the surface (dense surface sampling)."""
    tri = mesh.vertices[mesh.faces]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    count = int(np.clip(4.0 * area.sum() / grid.voxel**2, 10_000, 12_000_000))
    rng = np.random.default_rng(0)
    face = rng.choice(len(area), size=count, p=area / area.sum())
    r1 = np.sqrt(rng.random(count))
    r2 = rng.random(count)
    samples = (1 - r1)[:, None] * tri[face, 0] + (r1 * (1 - r2))[:, None] * tri[face, 1] + (r1 * r2)[:, None] * tri[face, 2]
    samples = np.vstack([samples, mesh.vertices])
    ijk = np.rint((samples - grid.origin) / grid.voxel).astype(np.int64)
    ijk = np.clip(ijk, 0, np.array(grid.shape) - 1)
    mask = np.zeros(grid.shape, dtype=bool)
    mask[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
    return mask


def wrap(
    mesh: Mesh,
    voxel_mm: float | None = None,
    close_gaps_mm: float = 0.0,
    offset_mm: float = 0.0,
    fill_cavities: bool = True,
    smooth_iterations: int = 4,
    target_faces: int | None = None,
    keep_largest_only: bool = False,
    progress: Callable[[float, str], None] = lambda p, m: None,
) -> tuple[Mesh, dict]:
    """Wrap a sheet around the model from the outside and rebuild a closed solid.

    Two complementary tests decide what is solid:
      * flood fill: empty space reachable from outside without crossing the
        surface (gaps narrower than 2 x close_gaps_mm are sealed first). This
        handles double-walled shells, overlapping parts and flipped faces.
      * winding number: robust for single-walled meshes whose holes are too
        large for the flood fill to seal.
    """
    voxel = float(voxel_mm or auto_voxel(mesh))
    gap_cells = int(np.ceil(max(close_gaps_mm, 0) / voxel))
    pad = (4 + gap_cells) * voxel + max(offset_mm, 0)
    grid = make_grid(mesh, voxel, pad)
    voxel = grid.voxel
    gap_cells = int(np.ceil(max(close_gaps_mm, 0) / voxel))
    progress(0.04, f"Voxel grid {grid.shape[0]} x {grid.shape[1]} x {grid.shape[2]} at {voxel:.2f} mm")

    wall = ndimage.binary_dilation(surface_mask(mesh, grid), iterations=1)
    barrier = ndimage.binary_dilation(wall, iterations=gap_cells) if gap_cells else wall
    progress(0.15, "Flooding from outside")
    labels, count = ndimage.label(~barrier)
    border = np.unique(np.concatenate([
        labels[0].ravel(), labels[-1].ravel(), labels[:, 0].ravel(),
        labels[:, -1].ravel(), labels[:, :, 0].ravel(), labels[:, :, -1].ravel(),
    ]))
    border = border[border > 0]
    outside = np.isin(labels, border)
    cavities = int(count - len(border))
    if gap_cells:
        outside = ndimage.binary_dilation(outside, iterations=gap_cells) & ~wall
    if not fill_cavities:
        enclosed = (labels > 0) & ~np.isin(labels, border)
        outside |= enclosed & ~wall
    del labels, barrier

    progress(0.3, "Testing inside/outside with winding numbers")
    points = grid.points()
    solid = ~outside | winding_inside(mesh, points).reshape(grid.shape)
    if not solid.any():
        raise ValueError("No solid volume found. Try a larger 'close gaps' or 'thicken' value.")

    progress(0.45, "Measuring distance to the original surface")
    edge = solid ^ ndimage.binary_erosion(solid, border_value=0)
    edge |= ndimage.binary_dilation(solid) & ~solid
    band = ndimage.binary_dilation(edge, iterations=2)
    field = np.where(
        solid,
        -ndimage.distance_transform_edt(solid).astype(np.float32),
        ndimage.distance_transform_edt(~solid).astype(np.float32),
    ) * np.float32(voxel)
    band_index = np.flatnonzero(band)
    query = points[band_index]
    sq, closest_face, closest = igl.point_mesh_squared_distance(query, mesh.vertices, mesh.faces)
    exact = np.sqrt(sq).astype(np.float32)
    inner = solid.reshape(-1)[band_index]
    # Wall cells touching the outside lie on either side of the real surface.
    # Use the nearest face's normal to tell which side, so the rebuilt surface
    # sits on the original instead of one voxel outside it.
    near_outside = ndimage.binary_dilation(outside, iterations=1 + gap_cells).reshape(-1)[band_index]
    tri = mesh.vertices[mesh.faces[closest_face]]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    front = np.einsum("ij,ij->i", query - closest, normals) > 0
    inner = np.where(inner & near_outside & front & ~np.asarray(winding_inside(mesh, query)), False, inner)
    floor = np.float32(0.05 * voxel)
    flat = field.reshape(-1)
    flat[band_index] = np.where(inner, -np.maximum(exact, floor), np.maximum(exact, floor))
    del points
    field -= np.float32(offset_mm)
    if fill_cavities:
        # Sign corrections can leave tiny sealed pockets; anything sealed is solid.
        empty, count = ndimage.label(field >= 0)
        if count > 1:
            keep = np.unique(np.concatenate([
                empty[0].ravel(), empty[-1].ravel(), empty[:, 0].ravel(),
                empty[:, -1].ravel(), empty[:, :, 0].ravel(), empty[:, :, -1].ravel(),
            ]))
            sealed = (empty > 0) & ~np.isin(empty, keep)
            field[sealed] = -np.abs(field[sealed]) - np.float32(0.05 * voxel)
        del empty

    progress(0.7, "Building the new surface")
    verts, faces, _, _ = marching_cubes(field, level=0.0, spacing=(voxel, voxel, voxel), allow_degenerate=False)
    verts = verts + grid.origin
    tm = trimesh.Trimesh(verts, faces, process=True)
    tm.update_faces(tm.nondegenerate_faces())
    tm.remove_unreferenced_vertices()
    if tm.volume < 0:
        tm.invert()
    parts = tm.split(only_watertight=False)
    if len(parts) > 1:
        volumes = np.array([abs(p.volume) for p in parts])
        keep = [p for p, v in zip(parts, volumes) if v >= (volumes.max() if keep_largest_only else voxel**3 * 8)]
        tm = trimesh.util.concatenate(keep)

    if smooth_iterations > 0:
        progress(0.8, "Smoothing voxel steps")
        trimesh.smoothing.filter_taubin(tm, iterations=smooth_iterations)

    budget = target_faces or min(max(len(mesh.faces), 60_000), 400_000)
    if len(tm.faces) > budget:
        progress(0.86, f"Reducing to about {budget:,} faces")
        from .solid import simplify_solid

        reduced = simplify_solid(Mesh(np.asarray(tm.vertices), np.asarray(tm.faces)), budget, max_tolerance=0.3 * voxel)
        tm = trimesh.Trimesh(reduced.vertices, reduced.faces, process=False)

    progress(0.94, "Transferring colours")
    colors = transfer_colors(mesh, tm.vertices, tm.faces)
    result = Mesh(np.asarray(tm.vertices), np.asarray(tm.faces), colors)
    topo = edge_topology(result.faces)
    report = {
        "voxel_mm": round(voxel, 4),
        "grid": list(grid.shape),
        "close_gaps_mm": close_gaps_mm,
        "offset_mm": offset_mm,
        "filled_cavities": cavities if fill_cavities else 0,
        "faces": int(len(result.faces)),
        "watertight": topo["boundary_edges"] == 0 and topo["nonmanifold_edges"] == 0,
        "volume_mm3": round(float(abs(tm.volume)), 2),
        "notes": [
            "Details thinner than about one voxel can merge or disappear; lower the voxel size for finer detail.",
        ],
    }
    return result, report
