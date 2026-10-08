/** Prepare: filament slots, object placement and print settings for the Kobra S1, then slice. */

import { engine } from "../engine";
import { state, type FilamentSlot, type SceneItem } from "../state";
import { checkField, clear, h, numberField, section, selectField, spool } from "../ui/dom";
import { toast } from "../ui/toast";
import { groupByAssembly, rowMajor, viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

export interface PrinterSchema {
  printer: { name: string; model: string; bed: [number, number]; height: number; nozzle: number; max_filaments: number; bed_types: string[] };
  process: string;
  groups: { name: string; settings: Setting[] }[];
  filaments: { name: string; label: string; type: string; color: string; settings: Record<string, number> }[];
  filament_settings: { key: string; label: string; min: number; max: number; step: number; unit: string }[];
  default_filament: string;
}
interface Setting {
  key: string;
  label: string;
  type: "float" | "int" | "percent" | "bool" | "enum";
  default: number | boolean | string;
  unit: string;
  help: string;
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
}

let panel: HTMLElement;
let schema: PrinterSchema | null = null;
let slicing = false;
const openGroups = new Set(["Quality", "Strength", "Support"]);
let onSliced: (() => void) | null = null;

export function setOnSliced(fn: () => void) {
  onSliced = fn;
}

export async function loadSchema(): Promise<PrinterSchema> {
  if (!schema) schema = await engine.get<PrinterSchema>("/api/printer");
  if (!state.filaments.length) {
    const preset = schema.filaments.find((f) => f.name === schema!.default_filament) ?? schema.filaments[0];
    state.filaments = [{ preset: preset.name, color: "#E8E3D8", settings: {} }];
  }
  return schema;
}

function value(s: Setting) {
  return s.key in state.printSettings ? state.printSettings[s.key] : s.default;
}

function settingControl(s: Setting): HTMLElement {
  const set = (v: unknown) => {
    if (v === s.default) delete state.printSettings[s.key];
    else state.printSettings[s.key] = v;
    state.emit("settings");
    render();
  };
  const changed = s.key in state.printSettings;
  const label = changed ? `${s.label} •` : s.label;
  if (s.type === "bool") return checkField(label, Boolean(value(s)), set, s.help);
  if (s.type === "enum") return selectField(label, String(value(s)), s.options ?? [], set, s.help);
  return numberField({ label, value: Number(value(s)), min: s.min, max: s.max, step: s.step, unit: s.unit, title: s.help, onchange: (v) => set(s.type === "int" ? Math.round(v) : v) });
}

function filamentRow(slot: FilamentSlot, index: number): HTMLElement {
  const preset = schema!.filaments.find((f) => f.name === slot.preset) ?? schema!.filaments[0];
  return h(
    "details.group",
    { style: { borderBottom: "1px solid var(--line)" } },
    h(
      "summary",
      {},
      spool(slot.color, true),
      h("span", {}, `Slot ${index + 1}`, h("span.hint", {}, ` ${preset.label}`)),
    ),
    h(
      "div.body",
      {},
      h("div.field", {}, h("label", {}, "Colour"), h("input", { type: "color", value: slot.color.toLowerCase(), onchange: (e: Event) => ((slot.color = (e.target as HTMLInputElement).value.toUpperCase()), changed()) })),
      selectField("Material", slot.preset, schema!.filaments.map((f) => [f.name, f.label] as [string, string]), (v) => ((slot.preset = v), (slot.settings = {}), changed())),
      schema!.filament_settings.map((fs) =>
        numberField({
          label: fs.label,
          value: slot.settings[fs.key] ?? preset.settings[fs.key],
          min: fs.min,
          max: fs.max,
          step: fs.step,
          unit: fs.unit,
          onchange: (v) => ((slot.settings[fs.key] = v), changed()),
        }),
      ),
      state.filaments.length > 1
        ? h("button.btn.small.danger", {
            onclick: () => {
              state.filaments.splice(index, 1);
              state.items.forEach((i) => {
                if (i.filament > state.filaments.length) i.filament = 1;
              });
              changed();
            },
          }, "Remove slot")
        : null,
    ),
  );
}

function changed() {
  state.items.forEach((i) => viewport.applyMaterial(i));
  state.emit("filaments");
  render();
}

function picker(items: SceneItem[]): HTMLElement {
  return h("div.spool-picker", {},
    state.filaments.map((f, i) =>
      h("button", {
        title: `Slot ${i + 1}`,
        "aria-label": `Use filament slot ${i + 1}`,
        "aria-pressed": items.every((s) => s.filament === i + 1),
        onclick: () => {
          items.forEach((s) => {
            s.filament = i + 1;
            viewport.applyMaterial(s);
          });
          state.emit("items");
          render();
        },
      }, spool(f.color)),
    ),
  );
}

function objectSection(): HTMLElement {
  const selected = state.selected;
  const label = (i: SceneItem) => (i.name.includes(" · ") ? i.name.split(" · ").slice(1).join(" · ") : i.name);
  return section(
    "Objects",
    selected.length === 1
      ? h("div", {}, h("p", {}, selected[0].name), h("div.field", {}, h("label", {}, "Filament"), picker(selected)))
      : selected.length > 1
        ? h("div", {},
            h("p", {}, "Filament for each selected part"),
            selected.map((i) => h("div.field", {}, h("label", {}, label(i)), picker([i]))),
          )
        : h("p", {}, "Select objects to choose their filament. Clicking a multi-colour object selects all its parts."),
    h("div.row", {},
      h("button.btn", {
        onclick: () => {
          if (!viewport.arrange()) toast("Not everything fits", "Some objects still hang off the bed. Scale them down or print them on another plate.", "error");
          state.emit("items");
        },
      }, "Arrange"),
      h("button.btn", { onclick: () => (viewport.dropToBed(state.items.filter((i) => i.visible)), state.emit("items")) }, "Drop all to bed"),
    ),
  );
}

async function slice() {
  const visible = state.items.filter((i) => i.visible);
  if (!visible.length) return toast("Nothing to slice", "Add or show at least one object.", "error");
  const off = visible.filter((i) => i.node.userData.outOfBed);
  if (off.length) return toast("Objects off the bed", `${off.map((i) => i.name).join(", ")}. Use Arrange or move them inside the plate.`, "error");
  const advanced: Record<string, string> = {};
  for (const line of state.advanced.split("\n")) {
    const [k, ...rest] = line.split("=");
    if (k?.trim() && rest.length) advanced[k.trim()] = rest.join("=").trim();
  }
  const objects = groupByAssembly(visible).map((group) => ({
    name: group.length > 1 ? group[0].name.split(" · ")[0] : group[0].name,
    parts: group.map((i) => (i.node.updateMatrix(), { object_id: i.objectId, name: i.name, matrix: rowMajor(i.node.matrix), filament: Math.min(i.filament, state.filaments.length) })),
  }));
  slicing = true;
  render();
  try {
    const plate = {
      name: objects.length === 1 ? objects[0].name : `${objects[0].name} and ${objects.length - 1} more`,
      objects,
      filaments: state.filaments,
      settings: { ...state.printSettings, ...(Object.keys(advanced).length ? { advanced } : {}) },
      bed_type: state.bedType,
    };
    const task = await engine.run<{ gcode: string; project: string | null; stats: Record<string, unknown>; warnings: string[] }>("slice", plate, "Slice");
    state.lastSlice = { taskId: task.id, ...task.result! };
    state.emit("sliced");
    for (const w of task.result!.warnings) toast("Slicer warning", w, "info");
    onSliced?.();
  } catch (error) {
    toast("Slicing failed", (error as Error).message, "error");
  } finally {
    slicing = false;
    render();
  }
}

function render() {
  if (!panel) return;
  if (!schema) {
    clear(panel, section("Prepare", h("p", {}, "Loading printer profiles…")));
    return;
  }
  const p = schema.printer;
  clear(
    panel,
    h("div.section", {},
      h("button.btn.primary.block", { disabled: slicing, onclick: slice }, slicing ? "Slicing…" : "Slice plate"),
    ),
    section(
      p.model,
      h("p", {}, `${p.nozzle} mm nozzle · ${p.bed[0]} × ${p.bed[1]} × ${p.height} mm`),
      selectField("Build plate", state.bedType, p.bed_types, (v) => ((state.bedType = v), state.emit("settings"))),
    ),
    h("section.section", {},
      h("h2", {}, "Filaments", h("span.aside", {}, `${state.filaments.length} of ${p.max_filaments} ACE Pro slots`)),
      state.filaments.map((slot, i) => filamentRow(slot, i)),
      state.filaments.length < p.max_filaments
        ? h("button.btn.small", {
            style: { marginTop: "8px" },
            onclick: () => {
              state.filaments.push({ preset: schema!.default_filament, color: ["#D9483B", "#2F6FD6", "#2A2A2A", "#F2C230"][state.filaments.length % 4], settings: {} });
              changed();
            },
          }, "Add filament")
        : null,
    ),
    objectSection(),
    h("section.section", { style: { padding: "12px 0 0" } },
      h("h2", { style: { padding: "0 14px" } }, "Print settings",
        Object.keys(state.printSettings).length
          ? h("button.btn.small.ghost", { style: { marginLeft: "auto" }, onclick: () => ((state.printSettings = {}), state.emit("settings"), render()) }, "Reset to profile")
          : null,
      ),
      h("p.hint", { style: { padding: "0 14px" } }, `Based on ${schema.process}. Changed values are marked •.`),
      schema.groups.map((g) =>
        h(
          "details.group",
          {
            open: openGroups.has(g.name),
            ontoggle: (e: Event) => ((e.target as HTMLDetailsElement).open ? openGroups.add(g.name) : openGroups.delete(g.name)),
          },
          h("summary", {}, g.name),
          h("div.body", {}, g.settings.map(settingControl)),
        ),
      ),
      h(
        "details.group",
        {},
        h("summary", {}, "Advanced"),
        h("div.body", {},
          h("p.hint", {}, "Any OrcaSlicer setting, one per line: key = value"),
          h("textarea", { value: state.advanced, placeholder: "wall_sequence = outer wall/inner wall", onchange: (e: Event) => ((state.advanced = (e.target as HTMLTextAreaElement).value), state.emit("settings")) }),
        ),
      ),
    ),
  );
}

export const prepareWorkspace: WorkspaceModule = {
  id: "prepare",
  label: "Prepare",
  gizmo: true,
  filamentView: true,
  async mount(p) {
    panel = p;
    render();
    try {
      await loadSchema();
      state.items.forEach((i) => viewport.applyMaterial(i));
    } catch (error) {
      toast("Couldn't load printer profiles", (error as Error).message, "error");
    }
    render();
  },
  refresh: render,
};
