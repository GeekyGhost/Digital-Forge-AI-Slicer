/** Preview: inspect sliced toolpaths layer by layer, then save or open the result. */

import { engine } from "../engine";
import { GcodeView, type ColorMode } from "../gcodeview";
import { state } from "../state";
import { checkField, clear, formatTime, h, section, spool } from "../ui/dom";
import { saveTaskFile } from "../ui/files";
import { toast } from "../ui/toast";
import { viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

let panel: HTMLElement;
let gcode: GcodeView | null = null;
let loadedTask = "";
let low = 0;
let high = 0;
let travels = false;
let loading = false;
let resizeHooked = false;
let keyHandler: ((e: KeyboardEvent) => void) | null = null;

function applyRange() {
  gcode?.setLayerRange(low, high);
  viewport.render();
}

async function ensureLoaded() {
  const slice = state.lastSlice;
  if (!slice || loadedTask === slice.taskId || loading) return;
  loading = true;
  render();
  try {
    gcode ??= new GcodeView();
    const info = await gcode.load(slice.taskId);
    loadedTask = slice.taskId;
    low = 0;
    high = info.layers.length - 1;
    applyRange();
    if (!viewport.overlay.children.includes(gcode.group)) viewport.overlay.add(gcode.group);
    const canvas = viewport.renderer.domElement;
    gcode.resize(canvas.clientWidth, canvas.clientHeight);
    if (!resizeHooked) {
      viewport.resizeListeners.push((w, hgt) => gcode?.resize(w, hgt));
      resizeHooked = true;
    }
  } catch (error) {
    toast("Couldn't load the toolpaths", (error as Error).message, "error");
  } finally {
    loading = false;
    render();
  }
}

function layerSlider(label: string, get: () => number, set: (v: number) => void): HTMLElement {
  const max = (gcode?.info?.layers.length ?? 1) - 1;
  const z = gcode?.info?.layers[get()]?.z ?? 0;
  return h("div.field", {},
    h("label", {}, `${label}: ${get() + 1} · ${z.toFixed(2)} mm`),
    h("input", {
      type: "range", min: 0, max, step: 1, value: get(),
      oninput: (e: Event) => {
        set(parseInt((e.target as HTMLInputElement).value));
        if (low > high) label === "Bottom" ? (high = low) : (low = high);
        applyRange();
        const labelEl = (e.target as HTMLElement).previousElementSibling!;
        const zz = gcode?.info?.layers[get()]?.z ?? 0;
        labelEl.textContent = `${label}: ${get() + 1} · ${zz.toFixed(2)} mm`;
      },
      onchange: () => render(),
    }),
  );
}

function render() {
  if (!panel) return;
  const slice = state.lastSlice;
  if (!slice) {
    clear(panel, section("Preview", h("p", {}, "Nothing sliced yet. Set up the plate in Prepare and press Slice plate.")));
    return;
  }
  if (loading || !gcode?.info) {
    clear(panel, section("Preview", h("p", {}, "Loading toolpaths…")));
    return;
  }
  const stats = slice.stats;
  const colors: string[] = stats.colors ?? [];
  const grams: number[] = stats.filament_g ?? [];
  const metres: number[] = (stats.filament_mm ?? []).map((v: number) => v / 1000);
  const info = gcode.info;
  const legend = gcode.mode === "feature"
    ? info.features
        .map((f, i) => ({ ...f, i }))
        .filter((f) => countFeature(f.i) > 0)
        .map((f) =>
          h("label.check", {},
            h("input", {
              type: "checkbox",
              checked: !gcode!.hiddenFeatures.has(f.i),
              onchange: (e: Event) => {
                (e.target as HTMLInputElement).checked ? gcode!.hiddenFeatures.delete(f.i) : gcode!.hiddenFeatures.add(f.i);
                gcode!.applyHidden();
                viewport.render();
              },
            }),
            h("i", { style: { background: f.color, width: "14px", height: "4px", borderRadius: "2px", display: "inline-block" } }),
            f.name,
          ),
        )
    : gcode.mode === "filament"
      ? colors.map((c, i) => h("div.row", {}, spool(c), `Slot ${i + 1}`))
      : [h("p.hint", {}, `Blue is slow (${gcode.speedRange[0].toFixed(0)} mm/s), red is fast (${gcode.speedRange[1].toFixed(0)} mm/s).`)];
  clear(
    panel,
    section(
      "Print summary",
      h("dl.stat-grid", {},
        h("dt", {}, "Print time"), h("dd", {}, formatTime(stats.time_s)),
        h("dt", {}, "Layers"), h("dd", {}, String(stats.layers)),
        h("dt", {}, "Filament"), h("dd", {}, stats.total_g ? `${stats.total_g.toFixed(1)} g` : "–"),
        colors.length > 1 ? [h("dt", {}, "Colour changes"), h("dd", {}, String(stats.filament_changes ?? stats.tool_changes ?? 0))] : null,
      ),
      colors.map((c, i) =>
        h("div.row", { style: { justifyContent: "space-between", margin: "4px 0" } },
          h("span.row", {}, spool(c), `Slot ${i + 1}`),
          h("span", {}, `${(grams[i] ?? 0).toFixed(1)} g · ${(metres[i] ?? 0).toFixed(2)} m`),
        ),
      ),
      slice.warnings.map((w) => h("p.hint.warn", {}, w)),
      stats.truncated ? h("p.hint.warn", {}, "Very large print: only part of the toolpath is shown.") : null,
    ),
    section(
      "Layers",
      layerSlider("Top", () => high, (v) => (high = v)),
      layerSlider("Bottom", () => low, (v) => (low = v)),
      h("p.hint", {}, "Up and down arrow keys step the top layer."),
    ),
    section(
      "Show",
      h("div.segmented", { role: "group", style: { marginBottom: "10px" } },
        (["feature", "filament", "speed"] as ColorMode[]).map((m) =>
          h("button", {
            "aria-pressed": gcode!.mode === m,
            onclick: () => {
              gcode!.mode = m;
              gcode!.recolor();
              viewport.render();
              render();
            },
          }, m === "feature" ? "Line type" : m === "filament" ? "Filament" : "Speed"),
        ),
      ),
      legend,
      checkField("Travel moves", travels, (v) => {
        travels = v;
        gcode!.showTravels(v);
        viewport.render();
      }),
    ),
    section(
      "Save",
      h("div.row", {},
        h("button.btn.primary", { onclick: () => saveTaskFile(slice.taskId, slice.gcode, [{ name: "G-code", extensions: ["gcode"] }]) }, "Save G-code…"),
        slice.project ? h("button.btn", { onclick: () => saveTaskFile(slice.taskId, slice.project!, [{ name: "3MF project", extensions: ["3mf"] }]) }, "Save 3MF…") : null,
      ),
      h("div.row", { style: { marginTop: "6px" } },
        slice.project
          ? h("button.btn.small", { onclick: () => engine.post("/api/open", { task_id: slice.taskId, target: "orca", file: slice.project }).catch((e) => toast("Couldn't open OrcaSlicer", e.message, "error")) }, "Open in OrcaSlicer")
          : null,
        h("button.btn.small", { onclick: () => engine.post("/api/open", { task_id: slice.taskId, target: "folder" }) }, "Show files"),
      ),
      h("p.hint", { style: { marginTop: "8px" } }, "Copy the G-code to a USB drive for the Kobra S1, or open the 3MF in OrcaSlicer to send it over the network."),
    ),
  );
}

const featureCounts = new Map<number, number>();
function countFeature(index: number): number {
  if (!featureCounts.has(-1)) {
    const data = (gcode as unknown as { feature: Uint8Array }).feature;
    for (let i = 0; i < data.length; i++) featureCounts.set(data[i], (featureCounts.get(data[i]) ?? 0) + 1);
    featureCounts.set(-1, 1);
  }
  return featureCounts.get(index) ?? 0;
}

export const previewWorkspace: WorkspaceModule = {
  id: "preview",
  label: "Preview",
  mount(p) {
    panel = p;
    state.items.forEach((i) => (i.node.visible = false));
    viewport.showHelpers = false;
    viewport.refreshHelpers();
    if (gcode) {
      viewport.overlay.add(gcode.group);
      gcode.group.visible = true;
    }
    featureCounts.clear();
    viewport.render();
    render();
    ensureLoaded().then(() => featureCounts.clear()).then(render);
    keyHandler = (e: KeyboardEvent) => {
      if (!gcode?.info || (e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        high = Math.max(low, Math.min(gcode.info.layers.length - 1, high + (e.key === "ArrowUp" ? 1 : -1)));
        applyRange();
        render();
      }
    };
    window.addEventListener("keydown", keyHandler);
  },
  unmount() {
    state.items.forEach((i) => (i.node.visible = i.visible));
    viewport.showHelpers = true;
    viewport.refreshHelpers();
    if (gcode) gcode.group.visible = false;
    if (keyHandler) window.removeEventListener("keydown", keyHandler);
    viewport.render();
  },
};
