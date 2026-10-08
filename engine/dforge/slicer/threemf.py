"""Write an OrcaSlicer-compatible 3MF project: objects, multi-colour parts, per-object settings.

Each object is a set of parts (one mesh per filament). Parts are stored in
printer coordinates (mm, Z-up, bed origin at the front-left corner).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from ..mesh.core import Mesh

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '<Default Extension="config" ContentType="text/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)


@dataclass
class Part:
    name: str
    mesh: Mesh
    extruder: int = 1  # 1-based filament slot


@dataclass
class PrintObject:
    name: str
    parts: list[Part]
    settings: dict = field(default_factory=dict)


def _mesh_xml(object_id: int, name: str, mesh: Mesh) -> str:
    v = mesh.vertices
    f = mesh.faces
    vertices = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in v.tolist())
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f.tolist())
    return (
        f'<object id="{object_id}" name={quoteattr(name)} type="model"><mesh>'
        f"<vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>"
    )


def write_3mf(path: Path, objects: list[PrintObject], filament_colors: list[str] | None = None) -> Path:
    resources: list[str] = []
    build: list[str] = []
    config: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>']
    next_id = 1
    for obj in objects:
        part_ids = []
        for part in obj.parts:
            if len(part.mesh.faces) == 0:
                continue
            resources.append(_mesh_xml(next_id, part.name, part.mesh))
            part_ids.append((next_id, part))
            next_id += 1
        if not part_ids:
            continue
        object_id = next_id
        next_id += 1
        components = "".join(f'<component objectid="{pid}"/>' for pid, _ in part_ids)
        resources.append(f'<object id="{object_id}" name={quoteattr(obj.name)} type="model"><components>{components}</components></object>')
        build.append(f'<item objectid="{object_id}" printable="1"/>')
        first = part_ids[0][1].extruder
        config.append(f'<object id="{object_id}"><metadata key="name" value={quoteattr(obj.name)}/>')
        config.append(f'<metadata key="extruder" value="{first}"/>')
        for key, value in (obj.settings or {}).items():
            config.append(f"<metadata key={quoteattr(str(key))} value={quoteattr(str(value))}/>")
        for index, (_, part) in enumerate(part_ids, start=1):
            config.append(
                f'<part id="{index}" subtype="normal_part"><metadata key="name" value={quoteattr(part.name)}/>'
                f'<metadata key="extruder" value="{part.extruder}"/></part>'
            )
        config.append("</object>")
    config.append("</config>")
    if not build:
        raise ValueError("Nothing to print: the plate is empty")
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Application">Digital Forge 3D Slicer</metadata>'
        f"<resources>{''.join(resources)}</resources><build>{''.join(build)}</build></model>"
    )
    path = Path(path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/model_settings.config", "".join(config))
    path.write_bytes(buffer.getvalue())
    return path


def bed_check(objects: list[PrintObject], bed=(250.0, 250.0), height=250.0) -> list[str]:
    problems = []
    for obj in objects:
        verts = np.vstack([p.mesh.vertices for p in obj.parts if len(p.mesh.vertices)])
        lo, hi = verts.min(0), verts.max(0)
        if lo[0] < -0.01 or lo[1] < -0.01 or hi[0] > bed[0] + 0.01 or hi[1] > bed[1] + 0.01:
            problems.append(f"{obj.name} is outside the {bed[0]:.0f} × {bed[1]:.0f} mm bed")
        if hi[2] > height + 0.01:
            problems.append(f"{obj.name} is taller than {height:.0f} mm")
        if lo[2] < -0.05:
            problems.append(f"{obj.name} goes below the bed")
    return problems
