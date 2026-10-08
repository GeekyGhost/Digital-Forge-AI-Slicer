/** Planar cut with joints: split big or awkward models into printable pieces that fit back together. */

import * as THREE from "three";
import { engine, type ObjectRecord } from "../engine";
import { addDerived } from "../scene";
import { state, type SceneItem } from "../state";
import { clear, h, numberField, section, selectField } from "../ui/dom";
import { toast } from "../ui/toast";
import { viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

let panel: HTMLElement;
const plane = { axis: "z" as "x" | "y" | "z", position: 50, tiltA: 0, tiltB: 0 };
const joint = { type: "pin", count: 1, diameter: 5, depth: 6, clearance: 0.15, magnet_height: 2 };
let helper: THREE.Group | null = null;
let busy = false;

function worldPlane(item: SceneItem): { origin: THREE.Vector3; normal: THREE.Vector3; box: THREE.Box3 } {
  const box = viewport.worldBox(item, false);
  const base = { x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1) }[plane.axis];
  const tiltAxes = { x: [new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)], y: [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1)], z: [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0)] }[plane.axis];
  const normal = base.clone()
    .applyAxisAngle(tiltAxes[0], THREE.MathUtils.degToRad(plane.tiltA))
    .applyAxisAngle(tiltAxes[1], THREE.MathUtils.degToRad(plane.tiltB))
    .normalize();
  const centre = box.getCenter(new THREE.Vector3());
  const i = { x: 0, y: 1, z: 2 }[plane.axis];
  const along = box.min.getComponent(i) + (box.max.getComponent(i) - box.min.getComponent(i)) * (plane.position / 100);
  const origin = centre.clone();
  origin.setComponent(i, along);
  return { origin, normal, box };
}

function drawHelper() {
  removeHelper();
  const item = state.primary;
  if (!item) return;
  const { origin, normal, box } = worldPlane(item);
  const size = box.getSize(new THREE.Vector3()).length() * 0.75;
  helper = new THREE.Group();
  const quad = new THREE.Mesh(
    new THREE.PlaneGeometry(size, size),
    new THREE.MeshBasicMaterial({ color: 0xe8a33d, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }),
  );
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(quad.geometry), new THREE.LineBasicMaterial({ color: 0xe8a33d }));
  const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), size * 0.25, 0xe8a33d);
  helper.add(quad, edge, arrow);
  helper.position.copy(origin);
  helper.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
  helper.traverse((o) => (o.userData.overlay = true));
  viewport.overlay.add(helper);
  viewport.render();
}

function removeHelper() {
  if (helper) {
    viewport.overlay.remove(helper);
    helper = null;
    viewport.render();
  }
}

async function doCut(item: SceneItem) {
  item.node.updateMatrix();
  const { origin, normal } = worldPlane(item);
  const inverse = item.node.matrix.clone().invert();
  const localOrigin = origin.clone().applyMatrix4(inverse);
  const localNormal = normal.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(inverse)).normalize();
  const s = item.node.scale;
  const scale = (Math.abs(s.x) + Math.abs(s.y) + Math.abs(s.z)) / 3;
  if (Math.max(Math.abs(s.x), Math.abs(s.y), Math.abs(s.z)) / Math.min(Math.abs(s.x), Math.abs(s.y), Math.abs(s.z)) > 1.01) {
    toast("Uneven scaling", "Joint holes will stretch with the model. Scale it evenly for accurate joints.", "info");
  }
  const params = {
    diameter: joint.diameter / scale,
    depth: joint.depth / scale,
    clearance: joint.clearance / scale,
    magnet_height: joint.magnet_height / scale,
  };
  busy = true;
  render();
  try {
    const task = await engine.run<{ objects: ObjectRecord[]; report: { warnings: string[]; joints: unknown[] } }>(
      "cut",
      {
        object_id: item.objectId,
        origin: localOrigin.toArray(),
        normal: localNormal.toArray(),
        auto_joints: joint.type === "none" ? null : { type: joint.type, count: joint.count, params },
      },
      "Cut",
    );
    const parts = await addDerived(item, task.result!.objects);
    // Pull the top part away along the cut normal so the cut is visible.
    const top = parts.find((p) => p.name.endsWith("· B"));
    if (top) {
      top.node.position.addScaledVector(normal, 10);
      top.node.updateMatrix();
    }
    viewport.refreshHelpers();
    const warnings = task.result!.report.warnings;
    toast("Cut", warnings.length ? warnings.join(" ") : `${parts.length} parts. Use Lay flat in Model to put each cut face on the bed.`, warnings.length ? "info" : "success");
  } catch (error) {
    toast("Cut failed", (error as Error).message, "error");
  } finally {
    busy = false;
    render();
  }
}

function render() {
  if (!panel) return;
  const item = state.primary;
  if (!item) {
    removeHelper();
    clear(panel, section("Cut", h("p", {}, "Select a model to cut it into pieces, for example to fit the bed, avoid supports, or print parts in different colours.")));
    return;
  }
  drawHelper();
  const solid = item.record.analysis.watertight;
  const update = () => drawHelper();
  clear(
    panel,
    section(
      "Cut plane",
      !solid ? h("p.hint.warn", {}, "Cutting needs a closed solid. Run Shell Wrap in Model first.") : null,
      h("div.segmented", { role: "group", style: { marginBottom: "8px" } },
        (["x", "y", "z"] as const).map((a) =>
          h("button", { "aria-pressed": plane.axis === a, onclick: () => ((plane.axis = a), render()) }, a === "z" ? "Horizontal" : `Vertical ${a.toUpperCase()}`),
        ),
      ),
      h("div.field", {}, h("label", {}, "Position"), h("input", { type: "range", min: 1, max: 99, step: 0.5, value: plane.position, oninput: (e: Event) => ((plane.position = parseFloat((e.target as HTMLInputElement).value)), update()) })),
      h("div.field", {}, h("label", {}, "Tilt A"), h("input", { type: "range", min: -60, max: 60, step: 1, value: plane.tiltA, oninput: (e: Event) => ((plane.tiltA = parseFloat((e.target as HTMLInputElement).value)), update()) })),
      h("div.field", {}, h("label", {}, "Tilt B"), h("input", { type: "range", min: -60, max: 60, step: 1, value: plane.tiltB, oninput: (e: Event) => ((plane.tiltB = parseFloat((e.target as HTMLInputElement).value)), update()) })),
      h("button.btn.small", { onclick: () => ((plane.tiltA = 0), (plane.tiltB = 0), render()) }, "Straighten"),
    ),
    section(
      "Joints",
      selectField("Type", joint.type, [["none", "None (glue)"], ["pin", "Round peg"], ["square", "Square peg (no twist)"], ["dowel", "Dowel (printed separately)"], ["magnet", "Magnet pockets"], ["dovetail", "Dovetail slide"]], (v) => ((joint.type = v), render())),
      joint.type !== "none"
        ? [
            numberField({ label: "How many", value: joint.count, min: 1, max: 6, step: 1, onchange: (v) => (joint.count = Math.round(v)), title: "Placed at the thickest points of the cut face." }),
            numberField({ label: joint.type === "magnet" ? "Magnet diameter" : joint.type === "dovetail" ? "Width" : "Diameter", value: joint.diameter, min: 1, max: 40, step: 0.5, unit: "mm", onchange: (v) => (joint.diameter = v) }),
            joint.type === "magnet"
              ? numberField({ label: "Magnet height", value: joint.magnet_height, min: 0.5, max: 20, step: 0.5, unit: "mm", onchange: (v) => (joint.magnet_height = v) })
              : numberField({ label: "Depth", value: joint.depth, min: 1, max: 40, step: 0.5, unit: "mm", onchange: (v) => (joint.depth = v) }),
            numberField({ label: "Clearance", value: joint.clearance, min: 0, max: 1, step: 0.05, unit: "mm", onchange: (v) => (joint.clearance = v), title: "Gap for a snug fit. 0.1–0.2 mm suits most PLA prints." }),
          ]
        : null,
      h("button.btn.primary.block", { disabled: !solid || busy, onclick: () => doCut(item) }, busy ? "Cutting…" : "Cut"),
      h("p.hint", { style: { marginTop: "8px" } }, "Pegs go on the lower part and holes in the upper part. Print a small test pair to tune clearance for your filament."),
    ),
  );
}

export const cutWorkspace: WorkspaceModule = {
  id: "cut",
  label: "Cut",
  mount(p) {
    panel = p;
    render();
  },
  unmount: removeHelper,
  refresh: render,
};
