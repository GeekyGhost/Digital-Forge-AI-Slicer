"""Engine checks on small synthetic models. Run: runtime\\python\\python.exe -m unittest discover -s engine\\tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

TEMP = tempfile.mkdtemp(prefix="dforge-test-")
os.environ.setdefault("DFORGE_ROOT", str(Path(__file__).resolve().parents[2]))
# Keep test objects out of the real library.
import dforge.config as config  # noqa: E402

for name in ("DATA", "OBJECTS", "TASKS", "EXPORTS", "LOGS"):
    setattr(config, name, Path(TEMP) / name.lower())
    getattr(config, name).mkdir(parents=True, exist_ok=True)

import numpy as np  # noqa: E402
import trimesh  # noqa: E402

from dforge.assembly import connectors  # noqa: E402
from dforge.assembly.colors import detect_palette  # noqa: E402
from dforge.assembly.cut import plane_cut  # noqa: E402
from dforge.assembly.split import split_by_labels  # noqa: E402
from dforge.mesh.analyze import analyze  # noqa: E402
from dforge.mesh.core import Mesh, edge_topology, weld  # noqa: E402
from dforge.mesh.io import load_file  # noqa: E402
from dforge.mesh.repair import repair, simplify  # noqa: E402
from dforge.mesh.wrap import wrap  # noqa: E402
from dforge.slicer import gcode, profiles  # noqa: E402
from dforge.slicer.threemf import Part, PrintObject, write_3mf  # noqa: E402


def sphere(radius=20.0, subdivisions=4) -> Mesh:
    s = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    return Mesh(np.asarray(s.vertices), np.asarray(s.faces))


def two_colour_box() -> Mesh:
    box = trimesh.creation.box(extents=(40, 20, 20)).subdivide().subdivide()
    centroids = box.triangles_center
    colors = np.where(centroids[:, :1] < 0, [[220, 40, 40]], [[40, 60, 220]]).astype(np.uint8)
    return Mesh(np.asarray(box.vertices), np.asarray(box.faces), colors)


class MeshTests(unittest.TestCase):
    def test_wrap_seals_double_walled_leaky_shell(self):
        # Outer skin with a hole, plus an inward-facing inner skin: the shape
        # generated models often have. Winding numbers alone see no solid here.
        outer = sphere(20)
        inner = sphere(19.4)
        inner = Mesh(inner.vertices, inner.faces[:, ::-1])
        keep = outer.centroids()[:, 2] < 18.5  # cut a hole in the top
        outer = Mesh(outer.vertices, outer.faces[keep])
        merged = Mesh(np.vstack([outer.vertices, inner.vertices]), np.vstack([outer.faces, inner.faces + len(outer.vertices)]))
        self.assertGreater(edge_topology(weld(merged).faces)["boundary_edges"], 0)
        result, report = wrap(merged, voxel_mm=0.6, close_gaps_mm=1.0)
        a = analyze(result)
        self.assertTrue(a["watertight"], a["issues"])
        self.assertEqual(a["components"], 1)
        expected = 4 / 3 * np.pi * 20**3
        self.assertLess(abs(a["volume_mm3"] - expected) / expected, 0.08)

    def test_repair_and_simplify_keep_solid(self):
        s = sphere(10, 5)
        doubled = Mesh(s.vertices, np.vstack([s.faces, s.faces[:50]]))
        fixed, _ = repair(doubled)
        self.assertEqual(analyze(fixed)["duplicate_faces"], 0)
        small, _ = simplify(fixed, 2000)
        a = analyze(small)
        self.assertTrue(a["watertight"])
        self.assertLessEqual(a["faces"], 2000)

    def test_textured_glb_import_samples_colour(self):
        box = trimesh.creation.box(extents=(0.04, 0.02, 0.02))
        uv = np.zeros((len(box.vertices), 2))
        image = np.zeros((8, 8, 3), np.uint8)
        image[:] = (10, 200, 30)
        from PIL import Image

        material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(image), baseColorFactor=[255, 255, 255, 255])
        box.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
        path = Path(TEMP) / "box.glb"
        box.export(path)
        mesh, _, info = load_file(path)
        self.assertEqual(info["color_source"], "texture")
        np.testing.assert_allclose(mesh.extents, [40, 20, 20], atol=0.01)  # metres -> mm, Y-up -> Z-up
        self.assertTrue(np.all(np.abs(mesh.face_colors.astype(int) - [10, 200, 30]) < 3))
        self.assertAlmostEqual(mesh.bounds[0][2], 0.0, places=4)


class AssemblyTests(unittest.TestCase):
    def test_colour_split_partitions_volume_exactly(self):
        box = two_colour_box()
        labels, palette = detect_palette(box, 2, shading_tolerance=0.0)
        self.assertEqual(len(palette), 2)
        parts, report = split_by_labels(box, labels, palette, voxel_mm=0.8)
        self.assertEqual(len(parts), 2)
        total = sum(analyze(p["mesh"])["volume_mm3"] for p in parts)
        self.assertAlmostEqual(total, 40 * 20 * 20, delta=1.0)
        for p in parts:
            self.assertTrue(analyze(p["mesh"])["watertight"])
            self.assertAlmostEqual(p["volume_mm3"], 8000, delta=8000 * 0.08)

    def test_every_joint_type_gives_closed_parts(self):
        cyl = trimesh.creation.cylinder(radius=20, height=60, sections=96)
        cyl.apply_translation((0, 0, 30))
        mesh = Mesh(np.asarray(cyl.vertices), np.asarray(cyl.faces))
        for kind in connectors.TYPES:
            parts, report = plane_cut(mesh, (0, 0, 30), (0, 0, 1), auto_joints={"type": kind, "count": 1, "params": {"diameter": 6, "depth": 6}})
            for part in parts:
                a = analyze(part["mesh"])
                self.assertTrue(a["watertight"], f"{kind} {part['name']}")
            self.assertFalse(report["warnings"], kind)
            lower = analyze(parts[0]["mesh"])
            if kind in ("pin", "square", "dovetail"):
                self.assertGreater(lower["bounds_mm"][1][2], 30.5, f"{kind} peg should rise above the cut")
            if kind == "dowel":
                self.assertEqual(len(parts), 3)


class SlicerTests(unittest.TestCase):
    def test_profile_encoding(self):
        folder = Path(TEMP) / "profiles"
        paths = profiles.write_profiles(folder, {"sparse_infill_density": 20.0, "wall_loops": 3.0, "enable_support": True}, [{"preset": profiles.DEFAULT_FILAMENT, "color": "#112233", "settings": {"nozzle_temperature": 210.0}}])
        process = json.loads(paths["process"].read_text())
        self.assertEqual(process["sparse_infill_density"], "20%")
        self.assertEqual(process["wall_loops"], "3")
        self.assertEqual(process["enable_support"], "1")
        filament = json.loads(paths["filaments"][0].read_text())
        self.assertEqual(filament["nozzle_temperature"], ["210"])
        with self.assertRaises(ValueError):
            profiles.write_profiles(folder, {"not_a_setting": 1}, [])

    def test_3mf_has_parts_with_filaments(self):
        a = sphere(5, 2)
        b = sphere(5, 2)
        b.vertices[:, 0] += 12
        path = write_3mf(Path(TEMP) / "plate.3mf", [PrintObject("thing", [Part("red", a, 1), Part("blue", b, 2)], {"sparse_infill_density": "10%"})])
        with zipfile.ZipFile(path) as z:
            config_xml = z.read("Metadata/model_settings.config").decode()
            model = z.read("3D/3dmodel.model").decode()
        self.assertIn('key="extruder" value="2"', config_xml)
        self.assertIn('key="sparse_infill_density" value="10%"', config_xml)
        self.assertEqual(model.count("<component "), 2)

    def test_gcode_preview(self):
        text = "\n".join([
            "; HEADER", "M83", "G1 Z0.2 F600", ";LAYER_CHANGE", ";Z:0.2", ";TYPE:Outer wall",
            "G1 X10 Y10 E.5", "G1 X20 Y10 E.4", "G1 X20 Y20", "T1", ";TYPE:Sparse infill",
            ";LAYER_CHANGE", ";Z:0.4", "G1 Z0.4", "G2 X30 Y20 I5 J0 E1.2",
            "; estimated printing time (normal mode) = 1h 2m 3s", "; filament used [g] = 1.50, 0.25",
        ])
        path = Path(TEMP) / "t.gcode"
        path.write_text(text)
        data = gcode.parse(path)
        self.assertEqual(len(data["layers"]), 2)
        self.assertEqual(data["stats"]["time_s"], 3723)
        self.assertEqual(data["stats"]["filament_g"], [1.5, 0.25])
        self.assertEqual(data["meta"][0][0], gcode.FEATURE_INDEX["outer wall"])
        self.assertEqual(int(data["meta"][-1][1]), 1)  # tool 1 after T1
        self.assertGreater(data["stats"]["segments"], 4)  # arc was linearised


@unittest.skipUnless(config.orca_executable(), "OrcaSlicer not installed in runtime/orca")
class OrcaTests(unittest.TestCase):
    def test_two_colour_slice(self):
        from dforge.server import create_app
        from dforge.store import ObjectStore

        store = ObjectStore(config.OBJECTS)
        box = two_colour_box()
        labels, palette = detect_palette(box, 2, shading_tolerance=0.0)
        parts, _ = split_by_labels(box, labels, palette, voxel_mm=0.8)
        ids = [store.add_mesh(p["mesh"], f"part {p['label']}")["id"] for p in parts]
        app = create_app(None).test_client()
        matrix = np.eye(4)
        matrix[:2, 3] = 125
        plate = {
            "objects": [{"name": "box", "parts": [{"object_id": i, "matrix": matrix.tolist(), "filament": k + 1} for k, i in enumerate(ids)]}],
            "filaments": [{"preset": profiles.DEFAULT_FILAMENT, "color": "#DD2222"}, {"preset": profiles.DEFAULT_FILAMENT, "color": "#2244DD"}],
            "settings": {"layer_height": 0.28},
        }
        task = app.post("/api/tasks", json={"kind": "slice", "params": plate}).get_json()
        for _ in range(600):
            task = app.get(f"/api/tasks/{task['id']}").get_json()
            if task["status"] not in ("queued", "running"):
                break
            time.sleep(0.5)
        self.assertEqual(task["status"], "done", task.get("error"))
        stats = task["result"]["stats"]
        self.assertEqual(len(stats["filament_g"]), 2)
        self.assertTrue(all(g > 0 for g in stats["filament_g"]))


if __name__ == "__main__":
    sys.exit(unittest.main())
