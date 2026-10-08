import "./styles.css";
import { engine } from "./engine";
import { duplicate, importFiles, removeItems, renderOutliner, restoreScene } from "./scene";
import { state, type Workspace } from "./state";
import { clear, h } from "./ui/dom";
import { mountTaskStrip } from "./ui/tasks";
import { toast } from "./ui/toast";
import { createViewport, viewport } from "./viewport";
import { colorsWorkspace } from "./workspaces/colors";
import { cutWorkspace } from "./workspaces/cut";
import { generateWorkspace } from "./workspaces/generate";
import { modelWorkspace } from "./workspaces/model";
import { animateWorkspace, paintWorkspace, renderWorkspace, rigWorkspace } from "./workspaces/planned";
import { loadSchema, prepareWorkspace, setOnSliced } from "./workspaces/prepare";
import { previewWorkspace } from "./workspaces/preview";
import type { WorkspaceModule } from "./workspaces/types";

const WORKSPACES: WorkspaceModule[] = [
  modelWorkspace,
  generateWorkspace,
  colorsWorkspace,
  cutWorkspace,
  prepareWorkspace,
  previewWorkspace,
  paintWorkspace,
  rigWorkspace,
  animateWorkspace,
  renderWorkspace,
];

const $ = (id: string) => document.getElementById(id)!;
let current: WorkspaceModule | null = null;
let gizmoMode: "translate" | "rotate" | "scale" = "translate";

function switchTo(id: Workspace) {
  const next = WORKSPACES.find((w) => w.id === id)!;
  if (current === next) return;
  current?.unmount?.();
  current = next;
  state.workspace = id;
  viewport.gizmoEnabled = !!next.gizmo;
  viewport.setFilamentView(!!next.filamentView);
  viewport.attachGizmo(state.primary);
  next.mount($("inspector"));
  renderNav();
  renderTools();
  state.emit("workspace");
}

function renderNav() {
  const nav = $("workspaces");
  const button = (w: WorkspaceModule) =>
    h(
      "button" + (w.planned ? ".planned" : ""),
      { "aria-current": current?.id === w.id, onclick: () => switchTo(w.id), title: w.planned ? `${w.label} (planned)` : w.label },
      w.label,
    );
  clear(nav, WORKSPACES.filter((w) => !w.planned).map(button), h("span.divider"), WORKSPACES.filter((w) => w.planned).map(button));
}

function renderTools() {
  const left = $("tools-left");
  const right = $("tools-right");
  const showGizmo = !!current?.gizmo;
  left.style.display = showGizmo ? "flex" : "none";
  clear(
    left,
    (
      [
        ["translate", "Move", "W"],
        ["rotate", "Rotate", "E"],
        ["scale", "Scale", "R"],
      ] as const
    ).map(([mode, label, key]) =>
      h("button", { "aria-pressed": gizmoMode === mode, title: `${label} (${key})`, onclick: () => setGizmo(mode) }, label),
    ),
  );
  clear(
    right,
    h("button", { title: "Home view", onclick: () => viewport.home() }, "Iso"),
    h("button", { title: "Top view", onclick: () => viewport.view("top") }, "Top"),
    h("button", { title: "Front view", onclick: () => viewport.view("front") }, "Front"),
    h("button", { title: "Frame selection (F)", onclick: () => viewport.frame(state.selected.length ? state.selected : state.items.filter((i) => i.visible)) }, "Fit"),
  );
}

function setGizmo(mode: typeof gizmoMode) {
  gizmoMode = mode;
  viewport.attachGizmo(state.primary, mode);
  renderTools();
}

function bindKeys() {
  window.addEventListener("keydown", (e) => {
    const target = e.target as HTMLElement;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    const key = e.key.toLowerCase();
    if (key === "delete" || key === "backspace") {
      if (state.selected.length && current?.gizmo) removeItems(state.selected);
    } else if (key === "d" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (state.selected.length) duplicate(state.selected);
    } else if (key === "a" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      state.select(state.items.filter((i) => i.visible).map((i) => i.uid));
    } else if (key === "f") {
      viewport.frame(state.selected.length ? state.selected : state.items.filter((i) => i.visible));
    } else if (current?.gizmo && key === "w") setGizmo("translate");
    else if (current?.gizmo && key === "e") setGizmo("rotate");
    else if (current?.gizmo && key === "r") setGizmo("scale");
    else if (key === "escape") state.select([]);
  });
}

function bindDrop() {
  const overlay = $("drop-overlay");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer?.types.includes("Files")) return;
    depth++;
    overlay.classList.add("on");
  });
  window.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) overlay.classList.remove("on");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.classList.remove("on");
    if (e.dataTransfer?.files.length) importFiles(e.dataTransfer.files);
  });
}

function setEngineStatus(text: string, kind: "ready" | "down" | "") {
  const pill = $("engine-pill");
  pill.className = "engine-pill" + (kind ? ` ${kind}` : "");
  pill.querySelector("span")!.textContent = text;
}

async function boot() {
  createViewport($("viewport"));
  renderOutliner($("outliner"));
  mountTaskStrip($("tasks"), $("status-text"));
  bindKeys();
  bindDrop();
  state.on("items", () => renderOutliner($("outliner")));
  state.on("filaments", () => renderOutliner($("outliner")));
  state.on("selection", () => {
    renderOutliner($("outliner"));
    viewport.attachGizmo(state.primary, gizmoMode);
    viewport.refreshHelpers();
    current?.refresh?.();
    const sel = state.selected;
    $("status-selection").textContent = sel.length === 1 ? sel[0].name : sel.length ? `${sel.length} objects` : "";
  });
  state.on("items", () => current?.refresh?.());
  viewport.onTransformEnd = () => {
    viewport.dropToBed(state.selected.filter((i) => i.node.position.z < -0.01 || viewport.worldBox(i).min.z < -0.01));
    renderOutliner($("outliner"));
    current?.refresh?.();
  };
  state.on("transform", () => renderOutliner($("outliner")));
  setOnSliced(() => switchTo("preview"));

  const saved = state.restore();
  renderNav();
  try {
    await engine.connect();
    setEngineStatus("Engine ready", "ready");
  } catch (error) {
    setEngineStatus("Engine offline", "down");
    toast("The engine isn't running", (error as Error).message, "error", 60_000);
    switchTo("model");
    return;
  }
  try {
    await loadSchema();
  } catch (error) {
    toast("Couldn't load printer profiles", (error as Error).message, "error");
  }
  if (saved?.items.length) await restoreScene(saved.items);
  switchTo(state.workspace);
}

boot();
