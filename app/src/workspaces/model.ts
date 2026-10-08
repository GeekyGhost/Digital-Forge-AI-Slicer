import * as THREE from "three";
import { engine, type ObjectRecord } from "../engine";
import { addDerived, addObject, duplicate, importFiles, removeItems } from "../scene";
import { state, type SceneItem } from "../state";
import { checkField, clear, h, numberField, section } from "../ui/dom";
import { MESH_ACCEPT, pickFiles, saveTaskFile } from "../ui/files";
import { toast } from "../ui/toast";
import { rowMajor, viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

let panel: HTMLElement;
let layFlatArmed = false;
const wrapOptions = { voxel_mm: 0, close_gaps_mm: 0, offset_mm: 0, keep_largest_only: false, fill_cavities: true };
let simplifyTarget = 100_000;
let exportFormat = "stl";

async function runOn(item: SceneItem, kind: string, params: Record<string, unknown>, title: string) {
  try {
    const task = await engine.run<{ objects: ObjectRecord[]; report?: Record<string, unknown> }>(kind, { object_id: item.objectId, ...params }, title);
    const added = await addDerived(item, task.result!.objects);
    const record = added[0]?.record;
    if (record) {
      const a = record.analysis;
      toast(`${title} finished`, a.watertight ? `Closed solid, ${a.faces.toLocaleString()} faces.` : `Still has ${a.issues.join(", ")}.`, a.watertight ? "success" : "info");
    }
  } catch (error) {
    toast(`${title} failed`, (error as Error).message, "error");
  }
}

function transformSection(item: SceneItem): HTMLElement {
  const node = item.node;
  const box = viewport.worldBox(item, false);
  const size = box.getSize(new THREE.Vector3());
  const rot = new THREE.Euler().setFromQuaternion(node.quaternion, "XYZ");
  const apply = () => {
    node.updateMatrix();
    viewport.refreshHelpers();
    state.emit("transform");
  };
  const axis = (i: number) => "XYZ"[i];
  const vec3 = (label: string, values: number[], unit: string, onchange: (i: number, v: number) => void, step = 1) =>
    h(
      "div",
      {},
      h("h3", {}, label),
      h(
        "div.grid3",
        {},
        values.map((v, i) =>
          h(
            "div",
            {},
            h("label", {}, axis(i)),
            h("div.with-unit", { style: { position: "relative" } },
              h("input", {
                type: "number",
                step,
                value: (Math.abs(v) < 0.005 ? 0 : v).toFixed(2),
                onchange: (e: Event) => onchange(i, parseFloat((e.target as HTMLInputElement).value)),
              }),
              h("em", { style: { position: "absolute", right: "6px", top: "6px", fontStyle: "normal", color: "var(--text-faint)", fontSize: "11px" } }, unit),
            ),
          ),
        ),
      ),
    );
  let uniform = true;
  return section(
    "Position and size",
    vec3("Position", [node.position.x, node.position.y, box.min.z], "mm", (i, v) => {
      if (!Number.isFinite(v)) return;
      if (i === 2) node.position.z += v - viewport.worldBox(item).min.z;
      else node.position.setComponent(i, v);
      apply();
    }),
    vec3("Rotation", [rot.x, rot.y, rot.z].map(THREE.MathUtils.radToDeg), "°", (i, v) => {
      if (!Number.isFinite(v)) return;
      const e = new THREE.Euler().setFromQuaternion(node.quaternion, "XYZ");
      e[("xyz" as const)[i] as "x" | "y" | "z"] = THREE.MathUtils.degToRad(v);
      node.quaternion.setFromEuler(e);
      viewport.dropToBed([item]);
      apply();
    }, 5),
    vec3("Size", [size.x, size.y, size.z], "mm", (i, v) => {
      if (!Number.isFinite(v) || v <= 0) return;
      const factor = v / size.getComponent(i);
      if (uniform) node.scale.multiplyScalar(factor);
      else node.scale.setComponent(i, node.scale.getComponent(i) * factor);
      viewport.dropToBed([item]);
      apply();
      refresh();
    }, 1),
    checkField("Keep proportions", uniform, (v) => (uniform = v)),
    h(
      "div.row",
      { style: { marginTop: "8px" } },
      h("button.btn.small", { onclick: () => viewport.dropToBed(state.selected) }, "Drop to bed"),
      h("button.btn.small", {
        onclick: () => {
          state.selected.forEach((i) => i.node.position.set(125, 125, i.node.position.z));
          apply();
        },
      }, "Centre"),
      h("button.btn.small", {
        "aria-pressed": layFlatArmed,
        title: "Click a face of the model to place it face-down on the bed",
        onclick: () => {
          layFlatArmed = !layFlatArmed;
          toast(layFlatArmed ? "Lay flat" : "Lay flat off", layFlatArmed ? "Click the face that should rest on the bed." : "");
          refresh();
        },
      }, layFlatArmed ? "Pick a face…" : "Lay flat"),
    ),
    h(
      "div.row",
      { style: { marginTop: "6px" } },
      ["X", "Y", "Z"].map((a, i) =>
        h("button.btn.small", {
          onclick: () => {
            node.scale.setComponent(i, -node.scale.getComponent(i));
            viewport.dropToBed([item]);
            apply();
          },
        }, `Mirror ${a}`),
      ),
    ),
  );
}

function checkSection(item: SceneItem): HTMLElement {
  const a = item.record.analysis;
  const scale = item.node.scale;
  const volumeScale = Math.abs(scale.x * scale.y * scale.z);
  return section(
    "Printability",
    a.issues.length
      ? h("ul.issues", {}, a.issues.map((i) => h("li", {}, i)))
      : h("ul.issues.ok", {}, h("li", {}, "Closed, outward-facing solid. Ready to slice.")),
    h(
      "dl.stat-grid",
      {},
      h("dt", {}, "Faces"), h("dd", {}, a.faces.toLocaleString()),
      h("dt", {}, "Volume"), h("dd", {}, a.volume_mm3 ? `${((a.volume_mm3 * volumeScale) / 1000).toFixed(1)} cm³` : "not closed"),
      h("dt", {}, "Separate pieces"), h("dd", {}, String(a.components)),
      h("dt", {}, "Open / bad edges"), h("dd", {}, `${a.boundary_edges.toLocaleString()} / ${a.nonmanifold_edges.toLocaleString()}`),
    ),
    item.record.notes?.length ? h("p.hint", {}, item.record.notes.join(" ")) : null,
  );
}

function fixSection(item: SceneItem): HTMLElement {
  const a = item.record.analysis;
  return section(
    "Fix and rebuild",
    h("h3", {}, "Shell Wrap"),
    h("p", {}, "Wraps a new skin around the model and rebuilds it as one closed solid. Seals holes and gaps, fixes flipped faces, and fills hidden voids. Colours carry over."),
    numberField({ label: "Detail (voxel)", value: wrapOptions.voxel_mm, min: 0, max: 5, step: 0.05, unit: "mm", title: "0 = automatic. Smaller keeps finer detail but takes longer.", onchange: (v) => (wrapOptions.voxel_mm = v) }),
    numberField({ label: "Close gaps up to", value: wrapOptions.close_gaps_mm, min: 0, max: 20, step: 0.1, unit: "mm", onchange: (v) => (wrapOptions.close_gaps_mm = v) }),
    numberField({ label: "Thicken by", value: wrapOptions.offset_mm, min: -5, max: 10, step: 0.1, unit: "mm", title: "Use this for paper-thin parts such as capes, ears and leaves.", onchange: (v) => (wrapOptions.offset_mm = v) }),
    checkField("Keep only the largest piece", wrapOptions.keep_largest_only, (v) => (wrapOptions.keep_largest_only = v)),
    checkField("Fill hidden voids", wrapOptions.fill_cavities, (v) => (wrapOptions.fill_cavities = v)),
    h("button.btn.block" + (a.recommended === "wrap" ? ".primary" : ""), { onclick: () => runOn(item, "wrap", { ...wrapOptions, voxel_mm: wrapOptions.voxel_mm || null }, "Shell Wrap") }, "Shell wrap"),
    h("h3", {}, "Quick repair"),
    h("p", {}, "Removes duplicate and zero-area faces, fixes face direction and fills small holes. Keeps the original surface."),
    h("button.btn", { onclick: () => runOn(item, "repair", { remove_small_pct: 0.05 }, "Repair") }, "Repair"),
    h("h3", {}, "Reduce faces"),
    numberField({ label: "Target faces", value: simplifyTarget, min: 1000, max: 5_000_000, step: 1000, onchange: (v) => (simplifyTarget = v) }),
    h("button.btn", { onclick: () => runOn(item, "simplify", { target_faces: simplifyTarget }, "Simplify") }, "Simplify"),
  );
}

function combineSection(items: SceneItem[]): HTMLElement {
  const [a, b] = items;
  const go = async (op: string) => {
    try {
      a.node.updateMatrix();
      b.node.updateMatrix();
      const task = await engine.run<{ objects: ObjectRecord[] }>("boolean", { a: a.objectId, b: b.objectId, op, matrix_a: rowMajor(a.node.matrix), matrix_b: rowMajor(b.node.matrix) }, `Combine (${op})`);
      // Results are already in plate coordinates.
      await addObject(task.result!.objects[0], { matrix: new THREE.Matrix4(), filament: a.filament });
      removeItems([a, b]);
    } catch (error) {
      toast("Combine failed", (error as Error).message, "error");
    }
  };
  return section(
    "Combine",
    h("p", {}, `“${a.name}” with “${b.name}”. Both must be closed solids.`),
    h("div.row", {},
      h("button.btn", { onclick: () => go("union") }, "Join"),
      h("button.btn", { onclick: () => go("difference") }, "Subtract second"),
      h("button.btn", { onclick: () => go("intersection") }, "Keep overlap"),
    ),
  );
}

function exportSection(items: SceneItem[]): HTMLElement {
  return section(
    "Export",
    h(
      "div.row",
      {},
      h("select", { style: { width: "90px" }, onchange: (e: Event) => (exportFormat = (e.target as HTMLSelectElement).value) },
        ["stl", "3mf", "obj", "glb", "ply"].map((f) => h("option", { value: f, selected: f === exportFormat }, f.toUpperCase())),
      ),
      h("button.btn", {
        onclick: async () => {
          try {
            const payload = items.map((i) => (i.node.updateMatrix(), { object_id: i.objectId, name: i.name, filament: i.filament, matrix: rowMajor(i.node.matrix) }));
            const name = items.length === 1 ? items[0].name : "export";
            const task = await engine.run<{ file: string }>("export", { items: payload, format: exportFormat, name }, "Export");
            await saveTaskFile(task.id, task.result!.file, [{ name: exportFormat.toUpperCase(), extensions: [exportFormat] }]);
          } catch (error) {
            toast("Export failed", (error as Error).message, "error");
          }
        },
      }, items.length > 1 ? `Export ${items.length} objects` : "Export"),
    ),
    h("p.hint", { style: { marginTop: "8px" } }, "Exports keep the size and position you see. GLB is written in metres, Y-up."),
  );
}

function addSection(): HTMLElement {
  const sizes: Record<string, number[]> = { box: [20, 20, 20], cylinder: [20, 20, 20], sphere: [20, 20, 20], cone: [20, 20, 25], torus: [30, 30, 8] };
  return section(
    "Add",
    h("button.btn.block", { onclick: async () => importFiles(await pickFiles(MESH_ACCEPT)) }, "Import model…"),
    h("h3", {}, "Basic shapes"),
    h("div.row", {},
      Object.keys(sizes).map((shape) =>
        h("button.btn.small", {
          onclick: async () => {
            try {
              const task = await engine.run<{ objects: ObjectRecord[] }>("primitive", { shape, size: sizes[shape] }, `Add ${shape}`);
              await addObject(task.result!.objects[0]);
            } catch (error) {
              toast("Couldn't add shape", (error as Error).message, "error");
            }
          },
        }, shape[0].toUpperCase() + shape.slice(1)),
      ),
    ),
  );
}

function refresh() {
  if (!panel) return;
  const items = state.selected;
  const item = state.primary;
  viewport.pickHandler = layFlatArmed
    ? (pick, _e, phase) => {
        if (phase !== "up") return phase === "down" && !!pick;
        if (pick) {
          viewport.layFlat(pick.item, pick.normal);
          layFlatArmed = false;
          refresh();
        }
        return true;
      }
    : null;
  clear(
    panel,
    addSection(),
    item
      ? [
          items.length === 1 ? transformSection(item) : section(`${items.length} objects selected`, h("p", {}, "Drag the gizmo to move them together, or pick one to edit it.")),
          items.length === 1 ? checkSection(item) : null,
          items.length === 1 ? fixSection(item) : null,
          items.length === 2 ? combineSection(items) : null,
          section(
            "Arrange",
            h("div.row", {},
              h("button.btn", { onclick: () => duplicate(items) }, "Duplicate"),
              h("button.btn.danger", { onclick: () => removeItems(items) }, "Remove from plate"),
            ),
          ),
          exportSection(items),
        ]
      : section("Nothing selected", h("p", {}, "Click a model in the viewport or the object list. Shift-click to select more than one.")),
  );
}

export const modelWorkspace: WorkspaceModule = {
  id: "model",
  label: "Model",
  gizmo: true,
  mount(p) {
    panel = p;
    refresh();
  },
  unmount() {
    layFlatArmed = false;
    viewport.pickHandler = null;
  },
  refresh,
};
