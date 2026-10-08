"""Group a model's surface colours into a small filament palette.

Weighted k-means in CIELAB (perceptual distance), then clean-up on the face
adjacency graph: a majority filter removes speckle and regions smaller than a
minimum area are absorbed by their dominant neighbour. Users can then repaint
regions by hand in the viewport.
"""

from __future__ import annotations

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..mesh.core import Mesh


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{int(v):02X}" for v in rgb[:3])


def face_areas(mesh: Mesh) -> np.ndarray:
    tri = mesh.vertices[mesh.faces]
    return 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)


def face_neighbors(faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pairs of faces sharing an edge (works on nonmanifold meshes too)."""
    n = len(faces)
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    owner = np.repeat(np.arange(n), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = np.all(edges[1:] == edges[:-1], axis=1)
    return owner[:-1][same], owner[1:][same]


def _cleanup(labels: np.ndarray, areas: np.ndarray, a: np.ndarray, b: np.ndarray, k: int, min_area: float, passes: int) -> np.ndarray:
    n = len(labels)
    labels = labels.copy()
    for _ in range(passes):
        votes = np.zeros((n, k), dtype=np.float64)
        np.add.at(votes, (a, labels[b]), 1.0)
        np.add.at(votes, (b, labels[a]), 1.0)
        best = votes.argmax(axis=1)
        strong = votes[np.arange(n), best] >= 2
        labels = np.where(strong & (votes[np.arange(n), labels] < 1), best, labels)
    for _ in range(4):
        same = labels[a] == labels[b]
        graph = coo_matrix((np.ones(same.sum()), (a[same], b[same])), shape=(n, n))
        count, region = connected_components(graph, directed=False)
        region_area = np.bincount(region, weights=areas, minlength=count)
        small = region_area < min_area
        if not small.any():
            break
        # Each small region adopts the label it borders most (by shared edges).
        border = ~same & small[region[a]]
        border_b = ~same & small[region[b]]
        votes = np.zeros((count, k))
        np.add.at(votes, (region[a][border], labels[b][border]), 1.0)
        np.add.at(votes, (region[b][border_b], labels[a][border_b]), 1.0)
        has = votes.sum(axis=1) > 0
        target = votes.argmax(axis=1)
        change = small & has
        if not change.any():
            break
        labels = np.where(change[region], target[region], labels)
    return labels


def detect_palette(
    mesh: Mesh,
    n_colors: int = 4,
    fixed: list[str] | None = None,
    min_region_pct: float = 0.3,
    smooth_passes: int = 4,
    shading_tolerance: float = 0.85,
    seed: int = 0,
) -> tuple[np.ndarray, list[dict]]:
    """shading_tolerance in [0, 1]: how much light/dark differences are ignored.

    Generated textures bake in shading, so the same paint appears lighter and
    darker. Down-weighting lightness keeps a shaded green with the lit green,
    while hue and saturation still separate genuinely different paints.
    """
    if mesh.face_colors is None:
        raise ValueError("This model has no colour. Paint regions by hand or import a textured model.")
    areas = face_areas(mesh)
    weight = np.array([1.0 - 0.85 * float(np.clip(shading_tolerance, 0, 1)), 1.0, 1.0])
    lab = rgb_to_lab(mesh.face_colors) * weight
    if fixed:
        centers = rgb_to_lab(np.array([hex_to_rgb(c) for c in fixed])) * weight
        k = len(centers)
    else:
        k = int(np.clip(n_colors, 1, 16))
        rng = np.random.default_rng(seed)
        sample = rng.choice(len(lab), size=min(len(lab), 150_000), p=areas / areas.sum())
        centers, _ = kmeans2(lab[sample], k, minit="++", seed=seed, iter=30)
        for _ in range(8):  # area-weighted Lloyd refinement on all faces
            assign = ((lab[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
            for i in range(k):
                w = areas[assign == i]
                if w.sum() > 0:
                    centers[i] = (lab[assign == i] * w[:, None]).sum(0) / w.sum()
    labels = ((lab[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
    a, b = face_neighbors(mesh.faces)
    labels = _cleanup(labels, areas, a, b, k, areas.sum() * min_region_pct / 100.0, smooth_passes)
    return finalize(mesh, labels, k, fixed)


def finalize(mesh: Mesh, labels: np.ndarray, k: int | None = None, fixed: list[str] | None = None) -> tuple[np.ndarray, list[dict]]:
    """Sort labels by area and describe each one."""
    areas = face_areas(mesh)
    k = int(k or labels.max() + 1)
    total = np.bincount(labels, weights=areas, minlength=k)
    used = [i for i in np.argsort(-total) if total[i] > 0]
    remap = np.zeros(k, dtype=np.int64)
    remap[used] = np.arange(len(used))
    labels = remap[labels]
    palette = []
    for new, old in enumerate(used):
        mask = labels == new
        if fixed:
            rgb = hex_to_rgb(fixed[old])
        elif mesh.face_colors is not None:
            # Baked-in shading darkens the average; the lit half shows the real paint colour.
            cols = mesh.face_colors[mask].astype(np.float64)
            w = areas[mask]
            light = cols.mean(axis=1)
            lit = light >= np.median(light)
            rgb = tuple(int(v) for v in (cols[lit] * w[lit, None]).sum(0) / max(w[lit].sum(), 1e-12))
        else:
            rgb = (200, 200, 200)
        palette.append({
            "label": new,
            "color": rgb_to_hex(rgb),
            "area_pct": round(float(total[old] / total.sum() * 100), 2),
            "faces": int(mask.sum()),
            "filament": new + 1,
        })
    return labels.astype(np.uint8), palette
