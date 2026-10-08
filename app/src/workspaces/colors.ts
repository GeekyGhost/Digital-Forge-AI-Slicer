/** Colour split: find colour regions, paint corrections, and build real parts from them. */

import * as THREE from "three";
import { engine, type ObjectRecord, type PaletteEntry } from "../engine";
import { addDerived, addObject, removeItems } from "../scene";
import { state, type SceneItem } from "../state";
import { checkField, clear, h, numberField, section, spool } from "../ui/dom";
import { toast } from "../ui/toast";
import { viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

let panel: HTMLElement;
const detect = { n_colors: 4, shading_tolerance: 85, min_region_pct: 0.3 };
const build = { mode: "multicolor" as "multicolor" | "separate", depth_mm: 2.0, load_slots: true };

interface LabelView {
  item: SceneItem;
  mesh: THREE.Mesh;
  labels: Uint8Array;
  palette: PaletteEntry[];
  dirty: boolean;
}
let view: LabelView | null = null;
let activeLabel = 0;
let brushMm = 3;
let painting = false;
let entering = false;
let busy = "";
let lastParts: SceneItem[] = [];
let sourceForParts: string | null = null;
let interfaces: { a: number; b: number; a_id: string; b_id: string; area_mm2: number; origin: number[]; axis: number[]; max_radius_mm: number; use: boolean; type: string; diameter: number }[] = [];

function hexColor(hex: string) {
  return new THREE.Color(hex).convertSRGBToLinear();
}

async function enterLabelView(item: SceneItem, record: ObjectRecord) {
  leaveLabelView();
  const buffer = await engine.binary(`/api/objects/${item.objectId}/mesh.bin`);
  const header = new Uint32Array(buffer, 0, 4);
  const [nv, nf] = [header[0], header[1]];
  const vertices = new Float32Array(buffer, 16, nv * 3);
  const faces = new Uint32Array(buffer, 16 + nv * 12, nf * 3);
  let labels = new Uint8Array(nf);
  if (record.palette?.length) {
    try {
      labels = new Uint8Array(await engine.binary(`/api/objects/${item.objectId}/labels`));
    } catch {
      /* no labels saved yet */
    }
  }
  // Smooth normals from the shared-vertex mesh, then copied to every face corner.
  const smooth = new THREE.BufferGeometry();
  smooth.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
  smooth.setIndex(new THREE.BufferAttribute(faces, 1));
  smooth.computeVertexNormals();
  const vn = smooth.getAttribute("normal").array as Float32Array;
  const normals = new Float32Array(nf * 9);
  const positions = new Float32Array(nf * 9);
  for (let f = 0; f < nf; f++) {
    for (let k = 0; k < 3; k++) {
      const v = faces[f * 3 + k];
      positions[f * 9 + k * 3] = vertices[v * 3];
      positions[f * 9 + k * 3 + 1] = vertices[v * 3 + 1];
      positions[f * 9 + k * 3 + 2] = vertices[v * 3 + 2];
      normals[f * 9 + k * 3] = vn[v * 3];
      normals[f * 9 + k * 3 + 1] = vn[v * 3 + 1];
      normals[f * 9 + k * 3 + 2] = vn[v * 3 + 2];
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(nf * 9), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  smooth.dispose();
  geometry.computeBoundsTree();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.65, metalness: 0.0, side: THREE.DoubleSide }));
  mesh.userData.labelView = true;
  item.node.children.forEach((c) => (c.userData.wasVisible = c.visible, (c.visible = false)));
  item.node.add(mesh);
  view = { item, mesh, labels, palette: (record.palette ?? []).map((p) => ({ ...p })), dirty: false };
  recolorAll();
}

function leaveLabelView() {
  if (!view) return;
  const { item, mesh } = view;
  item.node.remove(mesh);
  mesh.geometry.disposeBoundsTree?.();
  mesh.geometry.dispose();
  item.node.children.forEach((c) => (c.visible = c.userData.wasVisible ?? true));
  view = null;
  viewport.render();
}

function recolorAll() {
  if (!view) return;
  const colors = view.mesh.geometry.getAttribute("color") as THREE.BufferAttribute;
  const palette = view.palette.length ? view.palette.map((p) => hexColor(p.color)) : [new THREE.Color(0.6, 0.6, 0.62)];
  const arr = colors.array as Float32Array;
  for (let f = 0; f < view.labels.length; f++) {
    const c = palette[view.labels[f]] ?? palette[0];
    for (let k = 0; k < 3; k++) {
      arr[f * 9 + k * 3] = c.r;
      arr[f * 9 + k * 3 + 1] = c.g;
      arr[f * 9 + k * 3 + 2] = c.b;
    }
  }
  colors.needsUpdate = true;
  viewport.render();
}

function paintAt(point: THREE.Vector3) {
  if (!view || !view.palette.length) return;
  const mesh = view.mesh;
  const geometry = mesh.geometry;
  const index = geometry.index!.array;
  const local = point.clone().applyMatrix4(mesh.matrixWorld.clone().invert());
  const scale = mesh.getWorldScale(new THREE.Vector3());
  const sphere = new THREE.Sphere(local, brushMm / Math.max(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z)));
  const labels = view.labels;
  const target = activeLabel;
  let changed = false;
  geometry.boundsTree!.shapecast({
    intersectsBounds: (box) => sphere.intersectsBox(box),
    intersectsTriangle: (tri, triIndex) => {
      if (tri.intersectsSphere(sphere)) {
        const face = Math.floor(index[triIndex * 3] / 3);
        if (labels[face] !== target) {
          labels[face] = target;
          changed = true;
        }
      }
      return false;
    },
  });
  if (changed) {
    view.dirty = true;
    recolorAll();
  }
}

async function saveRegions(): Promise<void> {
  if (!view) return;
  const palette = view.palette.map((p, i) => ({ ...p, label: i }));
  const path = `/api/objects/${view.item.objectId}/labels?palette=${encodeURIComponent(JSON.stringify(palette))}`;
  const record = await engine.request<ObjectRecord>("PUT", path, view.labels);
  view.item.record = record;
  view.dirty = false;
}

function removeLabel(label: number, into: number) {
  if (!view) return;
  const labels = view.labels;
  for (let f = 0; f < labels.length; f++) {
    let l = labels[f] === label ? into : labels[f];
    if (l > label) l -= 1;
    labels[f] = l;
  }
  view.palette.splice(label, 1);
  view.palette.forEach((p, i) => (p.label = i));
  activeLabel = Math.min(activeLabel, view.palette.length - 1);
  view.dirty = true;
  recolorAll();
  render();
}

async function runDetect(item: SceneItem) {
  busy = "Finding colour regions…";
  render();
  try {
    await engine.run("colors", { object_id: item.objectId, n_colors: detect.n_colors, shading_tolerance: detect.shading_tolerance / 100, min_region_pct: detect.min_region_pct }, "Find colours");
    const record = await engine.get<ObjectRecord>(`/api/objects/${item.objectId}`);
    item.record = record;
    await enterLabelView(item, record);
  } catch (error) {
    toast("Couldn't find colour regions", (error as Error).message, "error");
  } finally {
    busy = "";
    render();
  }
}

async function runWrapFirst(item: SceneItem) {
  busy = "Rebuilding as a closed solid…";
  render();
  try {
    const task = await engine.run<{ objects: ObjectRecord[] }>("wrap", { object_id: item.objectId }, "Shell Wrap");
    await addDerived(item, task.result!.objects);
  } catch (error) {
    toast("Shell Wrap failed", (error as Error).message, "error");
  } finally {
    busy = "";
    render();
  }
}

async function runBuild(item: SceneItem) {
  if (!view) return;
  busy = "Building parts…";
  render();
  try {
    if (view.dirty) await saveRegions();
    const palette = view.palette;
    const params: Record<string, unknown> = { object_id: item.objectId, shell_mm: build.mode === "multicolor" ? build.depth_mm : 0 };
    if (build.mode === "multicolor") params.core_label = 0;
    const task = await engine.run<{ objects: ObjectRecord[] }>("split", params, "Build colour parts");
    if (build.load_slots) {
      const slots = Math.min(palette.length, 4);
      for (let i = 0; i < slots; i++) {
        const existing = state.filaments[i];
        state.filaments[i] = { preset: existing?.preset ?? "Anycubic PLA @Anycubic Kobra S1 0.4 nozzle", color: palette[i].color, settings: existing?.settings ?? {} };
      }
      if (palette.length > 4) toast("More than 4 colours", "The ACE Pro holds 4 filaments. Colours 5 and up share slots: reassign them in Prepare.", "info");
    }
    leaveLabelView();
    const parts = await addDerived(item, task.result!.objects, {
      assembly: build.mode === "multicolor",
      filament: (r) => Math.min(r.filament ?? (r.label ?? 0) + 1, 4),
    });
    lastParts = parts;
    sourceForParts = item.objectId;
    interfaces = [];
    state.emit("filaments");
    toast("Parts ready", `${parts.length} watertight parts.` + (build.mode === "multicolor" ? " They print together as one multi-colour object." : " Add joints below, then arrange them in Prepare."), "success");
  } catch (error) {
    toast("Couldn't build parts", (error as Error).message, "error");
  } finally {
    busy = "";
    render();
  }
}

async function findJoints() {
  busy = "Finding where parts touch…";
  render();
  try {
    const task = await engine.run<{ interfaces: typeof interfaces }>("suggest_joints", { part_ids: lastParts.map((p) => p.objectId), source_id: sourceForParts }, "Find joints");
    interfaces = task.result!.interfaces.map((i) => {
      const diameter = Math.max(2, Math.min(6, Math.floor(i.max_radius_mm * 2 * 0.6 * 2) / 2));
      return { ...i, use: i.max_radius_mm >= 2, type: "pin", diameter };
    });
    if (!interfaces.length) toast("No touching faces", "These parts don't share an internal face, so there's nowhere to put a joint.");
  } catch (error) {
    toast("Couldn't find joints", (error as Error).message, "error");
  } finally {
    busy = "";
    render();
  }
}

async function addJoints() {
  const chosen = interfaces.filter((i) => i.use);
  if (!chosen.length) return;
  busy = "Adding joints…";
  render();
  try {
    const task = await engine.run<{ objects: ObjectRecord[]; replaces: string[]; checks: { warnings: string[] }[] }>(
      "join",
      {
        part_ids: lastParts.map((p) => p.objectId),
        joints: chosen.map((i) => ({ a: i.a, b: i.b, type: i.type, origin: i.origin, axis: i.axis, params: { diameter: i.diameter, depth: Math.max(3, i.diameter) } })),
      },
      "Add joints",
    );
    const records = task.result!.objects;
    const replaced: SceneItem[] = [];
    for (let k = 0; k < lastParts.length; k++) {
      const old = lastParts[k];
      old.node.updateMatrix();
      replaced.push(await addObject(records[k], { matrix: old.node.matrix.clone(), filament: old.filament, assembly: old.assembly, select: false }));
    }
    for (const extra of records.slice(lastParts.length)) await addObject(extra, { select: false });
    removeItems(lastParts);
    lastParts = replaced;
    interfaces = [];
    const warnings = task.result!.checks.flatMap((c) => c.warnings);
    toast("Joints added", warnings.length ? warnings.join(" ") : "All joints have enough material around them.", warnings.length ? "info" : "success");
  } catch (error) {
    toast("Couldn't add joints", (error as Error).message, "error");
  } finally {
    busy = "";
    render();
  }
}

function paletteSection(): HTMLElement {
  const v = view!;
  return section(
    "Colour regions",
    h("p", {}, "Pick a colour, then paint on the model to fix regions. Each colour becomes a part."),
    h(
      "ul.palette",
      {},
      v.palette.map((p, i) =>
        h(
          "li" + (i === activeLabel ? ".active" : ""),
          { onclick: () => ((activeLabel = i), render()) },
          h("input", {
            type: "color",
            value: p.color.toLowerCase(),
            onclick: (e: Event) => e.stopPropagation(),
            oninput: (e: Event) => {
              p.color = (e.target as HTMLInputElement).value.toUpperCase();
              v.dirty = true;
              recolorAll();
            },
          }),
          h("span", {}, `Colour ${i + 1}`, h("span.hint", {}, ` · ${p.area_pct.toFixed(1)}%`)),
          v.palette.length > 1
            ? h(
                "select",
                {
                  style: { width: "84px", height: "24px" },
                  title: "Merge this colour into another",
                  onclick: (e: Event) => e.stopPropagation(),
                  onchange: (e: Event) => {
                    const into = parseInt((e.target as HTMLSelectElement).value);
                    if (into >= 0) removeLabel(i, into);
                  },
                },
                h("option", { value: "-1" }, "Merge…"),
                v.palette.map((_, j) => (j === i ? null : h("option", { value: String(j) }, `into ${j + 1}`))),
              )
            : h("span"),
          h("span"),
        ),
      ),
    ),
    h("div.row", {},
      h("button.btn.small", {
        onclick: () => {
          v.palette.push({ label: v.palette.length, color: "#FFFFFF", area_pct: 0, faces: 0, filament: v.palette.length + 1 });
          activeLabel = v.palette.length - 1;
          v.dirty = true;
          render();
        },
      }, "Add colour"),
      h("button.btn.small", { disabled: !v.dirty, onclick: async () => { await saveRegions(); render(); toast("Regions saved", ""); } }, v.dirty ? "Save regions" : "Saved"),
    ),
    numberField({ label: "Brush size", value: brushMm, min: 0.5, max: 30, step: 0.5, unit: "mm", onchange: (val) => (brushMm = val) }),
    h("p.hint", {}, "Hold the left mouse button to paint. Orbit with the right button, pan with the middle."),
  );
}

function render() {
  if (!panel) return;
  const item = state.primary;
  if (!item) {
    leaveLabelView();
    clear(panel, section("Colour split", h("p", {}, "Select a coloured model. Textured models from Generate or GLB/OBJ/PLY files with colours work best; any model can be painted by hand."), ...jointsBlock()));
    return;
  }
  if (view && view.item !== item) leaveLabelView();
  const record = item.record;
  const solid = record.analysis.watertight;
  if (!view && !entering && record.palette?.length && solid) {
    entering = true;
    enterLabelView(item, record).finally(() => {
      entering = false;
      render();
    });
  }
  const detectSection = section(
    "Find colours",
    !solid
      ? h("div", {},
          h("p.hint.warn", {}, "Splitting needs a closed solid, and this model has holes or broken faces."),
          h("button.btn.primary.block", { disabled: !!busy, onclick: () => runWrapFirst(item) }, "Shell wrap first"),
        )
      : null,
    numberField({ label: "Number of colours", value: detect.n_colors, min: 1, max: 16, step: 1, onchange: (v) => (detect.n_colors = Math.round(v)), title: "The ACE Pro holds 4 filaments." }),
    numberField({ label: "Ignore shading", value: detect.shading_tolerance, min: 0, max: 100, step: 5, unit: "%", onchange: (v) => (detect.shading_tolerance = v), title: "Higher keeps shadowed and lit areas of one paint together." }),
    numberField({ label: "Smallest region", value: detect.min_region_pct, min: 0, max: 10, step: 0.1, unit: "%", onchange: (v) => (detect.min_region_pct = v), title: "Smaller specks merge into their neighbours." }),
    h("button.btn.block" + (view ? "" : ".primary"), { disabled: !solid || !!busy, onclick: () => runDetect(item) }, view ? "Find again" : "Find colour regions"),
    !record.has_color && solid ? h("p.hint", { style: { marginTop: "8px" } }, "This model has no colour data. Find 1 colour, then add colours and paint them on.") : null,
  );
  const buildSection = view
    ? section(
        "Build parts",
        h("div.segmented", { role: "group", style: { marginBottom: "8px" } },
          h("button", { "aria-pressed": build.mode === "multicolor", onclick: () => ((build.mode = "multicolor"), render()) }, "Multi-colour print"),
          h("button", { "aria-pressed": build.mode === "separate", onclick: () => ((build.mode = "separate"), render()) }, "Separate parts"),
        ),
        build.mode === "multicolor"
          ? h("p", {}, "One object printed in one go on the ACE Pro. Colours form a skin; the inside uses colour 1 to save filament swaps.")
          : h("p", {}, "Every colour becomes a solid part you print on its own and glue or join together."),
        build.mode === "multicolor"
          ? numberField({ label: "Colour depth", value: build.depth_mm, min: 0.4, max: 10, step: 0.2, unit: "mm", onchange: (v) => (build.depth_mm = v) })
          : null,
        checkField("Load these colours into the filament slots", build.load_slots, (v) => (build.load_slots = v)),
        h("button.btn.primary.block", { disabled: !!busy, onclick: () => runBuild(item) }, "Build parts"),
      )
    : null;
  clear(panel, busy ? h("div.section", {}, h("p", {}, busy)) : null, detectSection, view ? paletteSection() : null, buildSection, ...jointsBlock());
}

function jointsBlock(): HTMLElement[] {
  if (!lastParts.length || lastParts.some((p) => !state.items.includes(p))) return [];
  return [
    section(
      "Joints between parts",
      h("p", {}, "Pegs, dowels or magnets where parts meet, so separate prints line up and hold."),
      interfaces.length
        ? h(
            "div",
            {},
            interfaces.map((i) =>
              h(
                "div",
                { style: { borderTop: "1px solid var(--line)", padding: "8px 0" } },
                h("label.check", {},
                  h("input", { type: "checkbox", checked: i.use, onchange: (e: Event) => (i.use = (e.target as HTMLInputElement).checked) }),
                  spool(state.filamentColor(lastParts[i.a].filament)), spool(state.filamentColor(lastParts[i.b].filament)),
                  `Colour ${i.a + 1} to ${i.b + 1}`,
                ),
                h("p.hint" + (i.max_radius_mm < 2 ? ".warn" : ""), {}, `${i.area_mm2.toFixed(0)} mm² contact, room for about Ø${(i.max_radius_mm * 2).toFixed(1)} mm`),
                h("div.row", {},
                  h("select", { style: { width: "120px" }, onchange: (e: Event) => (i.type = (e.target as HTMLSelectElement).value) },
                    [["pin", "Round peg"], ["square", "Square peg"], ["dowel", "Dowel"], ["magnet", "Magnets"]].map(([v, t]) => h("option", { value: v, selected: v === i.type }, t)),
                  ),
                  h("input", { type: "number", style: { width: "80px" }, value: String(i.diameter), step: 0.5, min: 1, title: "Diameter in mm", onchange: (e: Event) => (i.diameter = parseFloat((e.target as HTMLInputElement).value)) }),
                  h("span.hint", {}, "mm"),
                ),
              ),
            ),
            h("button.btn.primary.block", { disabled: !!busy, onclick: addJoints }, "Add joints"),
          )
        : h("button.btn.block", { disabled: !!busy, onclick: findJoints }, "Find where parts touch"),
    ),
  ];
}

export const colorsWorkspace: WorkspaceModule = {
  id: "colors",
  label: "Colour split",
  mount(p) {
    panel = p;
    viewport.pickHandler = (pick, event, phase) => {
      if (!view || !view.palette.length) return false;
      if (phase === "down") {
        if (!pick || pick.item !== view.item || event.button !== 0) return false;
        painting = true;
        paintAt(pick.point);
        return true;
      }
      if (phase === "move" && painting && pick && pick.item === view.item) paintAt(pick.point);
      if (phase === "up" && painting) {
        painting = false;
        render();
        return true;
      }
      return painting;
    };
    render();
  },
  unmount() {
    viewport.pickHandler = null;
    if (view?.dirty) saveRegions().catch(() => undefined);
    leaveLabelView();
  },
  refresh: render,
};
