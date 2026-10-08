"""Printable joints between two parts that meet at a face.

Frame convention: origin on the shared face, +Z points from part A into part B.

Types
  pin      round peg on A, matching hole in B
  square   square peg on A (stops rotation), matching hole in B
  dowel    holes in both parts plus a separate printed dowel
  magnet   pockets in both parts for round magnets (glue them in)
  dovetail sliding dovetail key on A, slot in B (planar cuts)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import manifold3d as mf
import numpy as np

from ..mesh.solid import frame_matrix, place

TYPES = ("pin", "square", "dowel", "magnet", "dovetail")

DEFAULTS = {
    "diameter": 5.0,  # peg / dowel / magnet diameter
    "depth": 6.0,  # how far the peg reaches into B, or pocket depth per side
    "clearance": 0.15,  # radial gap for FDM fit
    "chamfer": 0.6,
    "magnet_height": 2.0,
    "wall": 1.2,  # minimum material around a hole
}


@dataclass
class JointGeometry:
    add_a: list[mf.Manifold] = field(default_factory=list)
    cut_a: list[mf.Manifold] = field(default_factory=list)
    add_b: list[mf.Manifold] = field(default_factory=list)
    cut_b: list[mf.Manifold] = field(default_factory=list)
    extras: list[tuple[str, mf.Manifold]] = field(default_factory=list)
    guards_a: list[mf.Manifold] = field(default_factory=list)
    guards_b: list[mf.Manifold] = field(default_factory=list)


def _cyl(r: float, z0: float, z1: float, segments: int = 48, r_top: float | None = None) -> mf.Manifold:
    return mf.Manifold.cylinder(z1 - z0, r, r if r_top is None else r_top, segments).translate((0, 0, z0))


def _chamfered_peg(r: float, z0: float, z1: float, chamfer: float) -> mf.Manifold:
    chamfer = min(chamfer, r * 0.5, (z1 - z0) * 0.4)
    body = _cyl(r, z0, z1 - chamfer)
    tip = _cyl(r, z1 - chamfer, z1, r_top=r - chamfer)
    return body + tip


def _box(sx: float, sy: float, z0: float, z1: float) -> mf.Manifold:
    return mf.Manifold.cube((sx, sy, z1 - z0), True).translate((0, 0, (z0 + z1) / 2))


def _dovetail(width: float, length: float, height: float, flare: float) -> mf.Manifold:
    """Trapezoid key in XZ, extruded along Y: narrow at z=0, wide at z=height."""
    half_bottom = width / 2
    half_top = width / 2 + flare
    polygon = [(-half_bottom, 0.0), (half_bottom, 0.0), (half_top, height), (-half_top, height)]
    key = mf.Manifold.extrude(mf.CrossSection([polygon]), length)  # along +Z
    # Map profile X->X, profile Y->Z, extrusion Z->Y.
    return key.transform([[1, 0, 0, 0], [0, 0, 1, -length / 2], [0, 1, 0, 0]])


def build(kind: str, params: dict, origin, axis, spin_deg: float = 0.0) -> JointGeometry:
    p = {**DEFAULTS, **{k: float(v) for k, v in (params or {}).items() if k in DEFAULTS}}
    r = p["diameter"] / 2
    c = p["clearance"]
    d = p["depth"]
    w = p["wall"]
    g = JointGeometry()
    if kind == "pin":
        anchor = max(2.0, r)
        g.add_a.append(_chamfered_peg(r, -anchor, d, p["chamfer"]))
        g.cut_b.append(_cyl(r + c, -0.01, d + c + 0.2))
        g.guards_a.append(_cyl(r + w, -anchor, -0.05))
        g.guards_b.append(_cyl(r + c + w, 0.05, d + c + w))
    elif kind == "square":
        s = p["diameter"]
        anchor = max(2.0, r)
        g.add_a.append(_box(s, s, -anchor, d - p["chamfer"]) + _box(s - p["chamfer"], s - p["chamfer"], d - p["chamfer"], d))
        g.cut_b.append(_box(s + 2 * c, s + 2 * c, -0.01, d + c + 0.2))
        g.guards_a.append(_box(s + 2 * w, s + 2 * w, -anchor, -0.05))
        g.guards_b.append(_box(s + 2 * (c + w), s + 2 * (c + w), 0.05, d + c + w))
    elif kind == "dowel":
        g.cut_a.append(_cyl(r + c, -(d + c + 0.2), 0.01))
        g.cut_b.append(_cyl(r + c, -0.01, d + c + 0.2))
        length = 2 * d - 0.4
        dowel = _chamfered_peg(r, 0, length / 2, p["chamfer"])
        dowel = dowel + dowel.mirror((0, 0, 1))
        g.extras.append(("dowel", dowel.translate((0, 0, length / 2))))
        g.guards_a.append(_cyl(r + c + w, -(d + c + w), -0.05))
        g.guards_b.append(_cyl(r + c + w, 0.05, d + c + w))
    elif kind == "magnet":
        h = p["magnet_height"] + 0.2
        g.cut_a.append(_cyl(r + c, -h, 0.01))
        g.cut_b.append(_cyl(r + c, -0.01, h))
        g.guards_a.append(_cyl(r + c + w, -(h + w), -0.05))
        g.guards_b.append(_cyl(r + c + w, 0.05, h + w))
    elif kind == "dovetail":
        width = p["diameter"]
        length = max(p["diameter"] * 3, 10.0)
        height = min(d, width)
        flare = height * 0.35
        g.add_a.append(_dovetail(width, length, height, flare) + _box(width, length, -2.0, 0.01))
        # The slot runs right through B so the key can slide in from the side.
        g.cut_b.append(_dovetail(width + 2 * c, 2000.0, height + c, flare))
        g.guards_b.append(_box(width + 2 * (flare + c + w), length, 0.05, height + c + w))
    else:
        raise ValueError(f"Unknown connector type '{kind}'")
    matrix = frame_matrix(origin, axis)
    if spin_deg:
        spin = np.eye(4)
        a = np.radians(spin_deg)
        spin[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
        matrix = matrix @ spin
    for name in ("add_a", "cut_a", "add_b", "cut_b", "guards_a", "guards_b"):
        setattr(g, name, [place(s, matrix) for s in getattr(g, name)])
    g.extras = [(n, s) for n, s in g.extras]  # extras stay in their own frame (print flat)
    return g


def coverage(container: mf.Manifold, guards: list[mf.Manifold]) -> float:
    """Fraction of the guard volume (material a joint needs around it) inside container."""
    total = sum(gd.volume() for gd in guards)
    if total <= 0:
        return 1.0
    inside = sum((container ^ gd).volume() for gd in guards)
    return inside / total


def apply(part_a: mf.Manifold, part_b: mf.Manifold, joints: list[dict]) -> tuple[mf.Manifold, mf.Manifold, list[tuple[str, mf.Manifold]], list[dict]]:
    """Apply joints. Each joint: {type, origin, axis, params, spin}. Returns new A, new B, extras, checks."""
    extras: list[tuple[str, mf.Manifold]] = []
    checks = []
    for index, joint in enumerate(joints):
        geometry = build(joint["type"], joint.get("params", {}), joint["origin"], joint["axis"], joint.get("spin", 0.0))
        cov_a = coverage(part_a, geometry.guards_a)
        cov_b = coverage(part_b, geometry.guards_b)
        warnings = []
        if cov_a < 0.97:
            warnings.append(f"Part A has only {cov_a:.0%} of the material this joint needs around it")
        if cov_b < 0.97:
            warnings.append(f"Part B has only {cov_b:.0%} of the material this joint needs around it")
        for s in geometry.add_a:
            part_a = part_a + s
        for s in geometry.cut_a:
            part_a = part_a - s
        for s in geometry.cut_b:
            part_b = part_b - s
        for s in geometry.add_b:
            part_b = part_b + s
        extras += [(f"{name} {index + 1}", s) for name, s in geometry.extras]
        checks.append({"index": index, "type": joint["type"], "coverage_a": round(cov_a, 3), "coverage_b": round(cov_b, 3), "warnings": warnings})
    return part_a, part_b, extras, checks
