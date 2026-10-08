"""Import/export between files and the canonical millimetre, Z-up mesh."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .core import Mesh, transform, weld

MESH_SUFFIXES = {".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".off"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

# glTF is +Y up in metres; printers are +Z up in millimetres.
Y_UP_TO_Z_UP = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
DEFAULT_GENERATED_HEIGHT_MM = 100.0


def _texture_image(material) -> tuple[np.ndarray | None, np.ndarray]:
    """Return (RGB uint8 image or None, RGBA float factor) for a trimesh material."""
    if material is None:
        return None, np.ones(4)
    image = getattr(material, "baseColorTexture", None)
    factor = getattr(material, "baseColorFactor", None)
    if image is None:
        image = getattr(material, "image", None)
    if factor is None:
        diffuse = getattr(material, "diffuse", None)
        factor = np.asarray(diffuse, dtype=np.float64) / 255.0 if diffuse is not None else np.ones(4)
    factor = np.asarray(factor, dtype=np.float64).reshape(-1)
    if factor.max() > 1.0:
        factor = factor / 255.0
    if len(factor) == 3:
        factor = np.append(factor, 1.0)
    if image is not None:
        image = np.asarray(image.convert("RGB"))
    return image, factor


def face_colors_of(geometry: trimesh.Trimesh) -> tuple[np.ndarray | None, str]:
    """Per-face RGB from texture, vertex colours, face colours or material colour."""
    visual = geometry.visual
    faces = np.asarray(geometry.faces)
    if visual is None or len(faces) == 0:
        return None, "none"
    if visual.kind == "texture" and getattr(visual, "uv", None) is not None:
        image, factor = _texture_image(visual.material)
        if image is not None and len(visual.uv) == len(geometry.vertices):
            uv = np.asarray(visual.uv, dtype=np.float64)
            # Average four samples per face: centroid plus three inset corners.
            corners = uv[faces]
            centroid = corners.mean(axis=1, keepdims=True)
            samples = np.concatenate([centroid, centroid + 0.5 * (corners - centroid)], axis=1)
            h, w = image.shape[:2]
            u = np.mod(samples[..., 0], 1.0)
            v = np.mod(samples[..., 1], 1.0)
            px = np.clip((u * w).astype(np.int64), 0, w - 1)
            py = np.clip(((1.0 - v) * h).astype(np.int64), 0, h - 1)
            colors = image[py, px].astype(np.float64).mean(axis=1)
            colors *= factor[:3]
            return np.clip(colors, 0, 255).astype(np.uint8), "texture"
        _, factor = _texture_image(visual.material)
        color = np.clip(factor[:3] * 255, 0, 255).astype(np.uint8)
        return np.tile(color, (len(faces), 1)), "material"
    if visual.kind == "vertex":
        vc = np.asarray(visual.vertex_colors)[:, :3].astype(np.float64)
        return vc[faces].mean(axis=1).astype(np.uint8), "vertex"
    if visual.kind == "face":
        return np.asarray(visual.face_colors)[:, :3].astype(np.uint8), "face"
    return None, "none"


def load_file(path: Path) -> tuple[Mesh, trimesh.Scene, dict]:
    """Load any supported mesh file into (canonical mesh, display scene, info).

    The display scene keeps original textures for the viewport. Both share the
    same transform, so the canonical mesh and display align exactly.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in MESH_SUFFIXES:
        raise ValueError(f"Unsupported mesh format {suffix}")
    scene = trimesh.load(path, force="scene", process=False)
    if not scene.geometry:
        raise ValueError("The file contains no mesh geometry")
    notes: list[str] = []
    gltf = suffix in {".glb", ".gltf"}
    matrix = np.eye(4)
    if gltf:
        matrix = Y_UP_TO_Z_UP.copy()
        matrix[:3, :3] *= 1000.0  # metres -> millimetres

    parts = scene.dump(concatenate=False)
    vertices, faces, colors = [], [], []
    sources = set()
    offset = 0
    any_color = False
    for geometry in parts:
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue
        fc, source = face_colors_of(geometry)
        sources.add(source)
        any_color |= fc is not None
        vertices.append(np.asarray(geometry.vertices, dtype=np.float64))
        faces.append(np.asarray(geometry.faces, dtype=np.int64) + offset)
        colors.append(fc if fc is not None else np.full((len(geometry.faces), 3), 200, np.uint8))
        offset += len(geometry.vertices)
    if not faces:
        raise ValueError("The file contains no triangles")
    mesh = Mesh(np.vstack(vertices), np.vstack(faces), np.vstack(colors) if any_color else None)
    mesh = transform(mesh, matrix)

    # Generated or normalised models have no real size. Give them a sensible one.
    extents = mesh.extents
    if gltf and (extents.max() > 250.0 or extents.max() < 1.0):
        scale = DEFAULT_GENERATED_HEIGHT_MM / max(extents[2], 1e-9)
        if extents.max() * scale > 240.0:
            scale = 240.0 / extents.max()
        scale_matrix = np.diag([scale, scale, scale, 1.0])
        mesh = transform(mesh, scale_matrix)
        matrix = scale_matrix @ matrix
        notes.append(
            f"No real-world size in file: scaled to {mesh.extents[2]:.0f} mm tall. Set the print size before slicing."
        )

    # Centre on XY and sit on Z=0 so new objects land on the bed.
    lo, hi = mesh.bounds
    centre = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    shift = np.eye(4)
    shift[:3, 3] = centre
    mesh = transform(mesh, shift)
    matrix = shift @ matrix

    display = scene.copy()
    display.apply_transform(matrix)
    info = {
        "format": suffix.lstrip("."),
        "color_source": sorted(s for s in sources if s != "none")[0] if sources - {"none"} else "none",
        "textured": "texture" in sources,
        "import_matrix": matrix.tolist(),
        "notes": notes,
    }
    return weld(mesh), display, info


def display_scene(mesh: Mesh, color: tuple[int, int, int] | None = None) -> trimesh.Scene:
    """A viewport scene for a canonical mesh: smooth normals, crisp per-face colours."""
    welded = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    normals = np.asarray(welded.vertex_normals)
    if mesh.face_colors is not None and color is None:
        # Unweld so every face owns its vertices (crisp colours) but keep the
        # welded normals so the surface still shades smoothly.
        vertices = mesh.vertices[mesh.faces].reshape(-1, 3)
        faces = np.arange(len(vertices)).reshape(-1, 3)
        rgba = np.repeat(np.hstack([mesh.face_colors, np.full((len(mesh.faces), 1), 255, np.uint8)]), 3, axis=0)
        geometry = trimesh.Trimesh(vertices, faces, vertex_colors=rgba, vertex_normals=normals[mesh.faces].reshape(-1, 3), process=False)
    else:
        rgba = np.array(list(color or (190, 196, 204)) + [255], dtype=np.uint8)
        geometry = trimesh.Trimesh(
            mesh.vertices, mesh.faces, vertex_normals=normals,
            vertex_colors=np.tile(rgba, (len(mesh.vertices), 1)), process=False,
        )
    return trimesh.Scene(geometry)


def export_mesh(mesh: Mesh, path: Path) -> Path:
    """Write STL/OBJ/PLY/3MF in mm Z-up, or GLB/GLTF converted to metres Y-up."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".glb", ".gltf"}:
        inverse = np.linalg.inv(Y_UP_TO_Z_UP)
        inverse[:3, :3] /= 1000.0
        converted = transform(mesh, inverse)
        scene = display_scene(converted)
        scene.export(path)
    elif suffix in {".stl", ".obj", ".ply", ".3mf", ".off"}:
        tm = mesh.trimesh()
        if mesh.face_colors is not None and suffix in {".ply", ".obj"}:
            tm.visual = trimesh.visual.ColorVisuals(tm, face_colors=mesh.face_colors)
        tm.export(path)
    else:
        raise ValueError(f"Unsupported export format {suffix}")
    return path


def mesh_binary(mesh: Mesh) -> bytes:
    """Compact viewport payload: header, float32 positions, uint32 indices, uint8 colours."""
    header = np.array([len(mesh.vertices), len(mesh.faces), 1 if mesh.face_colors is not None else 0, 0], np.uint32)
    parts = [header.tobytes(), mesh.vertices.astype(np.float32).tobytes(), mesh.faces.astype(np.uint32).tobytes()]
    if mesh.face_colors is not None:
        colors = mesh.face_colors.tobytes()
        parts.append(colors + b"\0" * ((4 - len(colors) % 4) % 4))
    return b"".join(parts)
