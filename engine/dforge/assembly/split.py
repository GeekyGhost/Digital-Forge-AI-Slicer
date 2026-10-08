"""Turn painted colour regions into real, watertight solids.

Each interior point takes the colour of the nearest surface region, giving a
3D partition of the model. Smooth cutter volumes are built from that partition
and applied with exact booleans in sequence, so:
  * the outside surface of every part is the original surface, unchanged;
  * parts tile the model exactly, with no gaps or overlaps between them.

`shell_mm` limits colour to a skin of that thickness and gives the core to one
"core" region. That suits multi-colour printing (fewer filament swaps inside).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes

from ..mesh.core import Mesh
from ..mesh.solid import from_manifold, to_manifold
from ..mesh.wrap import make_grid, winding_inside
from .colors import hex_to_rgb

import manifold3d as mf


def _cutter(indicator: np.ndarray, origin: np.ndarray, voxel: float, sigma: float) -> mf.Manifold:
    field = ndimage.gaussian_filter(indicator.astype(np.float32), sigma=sigma) if sigma > 0 else indicator.astype(np.float32)
    field = np.pad(field, 1, constant_values=0.0)  # guarantees a closed surface
    verts, faces, _, _ = marching_cubes(field, level=0.5, spacing=(voxel, voxel, voxel), allow_degenerate=False)
    verts = verts + origin - voxel
    # marching_cubes winds faces for the region above the level; flip to outward.
    solid = mf.Manifold(mf.Mesh(vert_properties=verts.astype(np.float32), tri_verts=faces[:, ::-1].astype(np.uint32)))
    if solid.status() != mf.Error.NoError or solid.volume() <= 0:
        solid = mf.Manifold(mf.Mesh(vert_properties=verts.astype(np.float32), tri_verts=faces.astype(np.uint32)))
    if solid.status() != mf.Error.NoError:
        raise RuntimeError(f"Could not build a cutter volume ({solid.status().name})")
    return solid


def split_by_labels(
    mesh: Mesh,
    labels: np.ndarray,
    palette: list[dict],
    voxel_mm: float | None = None,
    shell_mm: float = 0.0,
    core_label: int | None = None,
    smoothing: float = 1.0,
    min_part_mm3: float = 1.0,
    progress: Callable[[float, str], None] = lambda p, m: None,
) -> tuple[list[dict], dict]:
    solid = to_manifold(mesh)
    count = int(labels.max()) + 1
    voxel = float(voxel_mm or np.clip(mesh.extents.max() / 220.0, 0.15, 1.5))
    grid = make_grid(mesh, voxel, pad=3 * voxel)
    voxel = grid.voxel
    progress(0.05, f"Partitioning the volume ({grid.shape[0]} x {grid.shape[1]} x {grid.shape[2]})")
    points = grid.points()
    inside = winding_inside(mesh, points).reshape(grid.shape)
    region = ndimage.binary_dilation(inside, iterations=2)
    tree = cKDTree(mesh.centroids())
    index = np.flatnonzero(region)
    _, nearest = tree.query(points[index], k=1, workers=-1)
    volume_labels = np.full(grid.shape, -1, dtype=np.int16)
    volume_labels.reshape(-1)[index] = labels[nearest]
    del points

    if shell_mm > 0:
        if core_label is None:
            core_label = 0
        depth = ndimage.distance_transform_edt(inside) * voxel
        volume_labels[(depth > shell_mm) & inside] = core_label

    sizes = np.bincount(volume_labels[volume_labels >= 0].ravel(), minlength=count)
    present = [i for i in range(count) if sizes[i] > 0]
    base = core_label if core_label is not None and core_label in present else max(present, key=lambda i: sizes[i])
    order = sorted((i for i in present if i != base), key=lambda i: sizes[i])

    parts: dict[int, mf.Manifold] = {}
    remaining = solid
    for step, label in enumerate(order):
        progress(0.15 + 0.7 * step / max(len(order), 1), f"Cutting colour {label + 1}")
        cutter = _cutter(volume_labels == label, grid.origin, voxel, smoothing)
        piece = remaining ^ cutter
        remaining = remaining - cutter
        if piece.volume() >= min_part_mm3:
            parts[label] = piece
    parts[base] = remaining

    progress(0.9, "Checking parts")
    results = []
    for label in sorted(parts):
        piece = parts[label]
        if piece.is_empty() or piece.volume() < min_part_mm3:
            continue
        info = palette[label] if label < len(palette) else {"color": "#C8C8C8", "filament": label + 1}
        part_mesh = from_manifold(piece)
        part_mesh.face_colors = np.tile(np.array(hex_to_rgb(info["color"]), np.uint8), (len(part_mesh.faces), 1))
        results.append(
            {
                "label": label,
                "color": info["color"],
                "filament": info.get("filament", label + 1),
                "mesh": part_mesh,
                "volume_mm3": round(float(piece.volume()), 2),
                "pieces": len(piece.decompose()),
            }
        )
    total = sum(r["volume_mm3"] for r in results)
    report = {
        "voxel_mm": round(voxel, 4),
        "shell_mm": shell_mm,
        "base_label": base,
        "parts": [{k: v for k, v in r.items() if k != "mesh"} for r in results],
        "volume_check_mm3": {"source": round(float(solid.volume()), 2), "parts_total": round(total, 2)},
    }
    return results, report
