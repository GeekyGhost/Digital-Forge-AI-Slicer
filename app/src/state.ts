/** App state: what is on the plate, what is selected, filament slots and print settings. */

import * as THREE from "three";
import type { ObjectRecord } from "./engine";

export interface FilamentSlot {
  preset: string;
  color: string;
  settings: Record<string, number>;
}

export interface SceneItem {
  uid: string;
  objectId: string;
  name: string;
  record: ObjectRecord;
  node: THREE.Group; // transform holder; mesh content loads into it
  filament: number;
  assembly?: string; // parts sharing an assembly print as one multi-colour object
  visible: boolean;
}

export interface SavedItem {
  objectId: string;
  name: string;
  matrix: number[];
  filament: number;
  assembly?: string;
  visible: boolean;
}

export type Workspace = "model" | "generate" | "colors" | "cut" | "prepare" | "preview" | "paint" | "rig" | "animate" | "render";

export interface SliceSummary {
  taskId: string;
  gcode: string;
  project: string | null;
  stats: Record<string, any>;
  warnings: string[];
}

type Listener = () => void;

class AppState {
  items: SceneItem[] = [];
  selection: string[] = [];
  workspace: Workspace = "model";
  filaments: FilamentSlot[] = [];
  printSettings: Record<string, unknown> = {};
  advanced = "";
  bedType = "Textured PEI Plate";
  lastSlice: SliceSummary | null = null;
  private listeners = new Map<string, Set<Listener>>();

  on(event: string, listener: Listener): () => void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(listener);
    return () => this.listeners.get(event)!.delete(listener);
  }

  emit(event: string) {
    this.listeners.get(event)?.forEach((l) => l());
    if (event !== "save") this.scheduleSave();
  }

  get selected(): SceneItem[] {
    return this.selection.map((uid) => this.items.find((i) => i.uid === uid)).filter(Boolean) as SceneItem[];
  }

  get primary(): SceneItem | undefined {
    return this.selected[0];
  }

  select(uids: string[]) {
    this.selection = uids.filter((uid) => this.items.some((i) => i.uid === uid));
    this.emit("selection");
  }

  item(uid: string) {
    return this.items.find((i) => i.uid === uid);
  }

  filamentColor(slot: number): string {
    return this.filaments[slot - 1]?.color ?? "#9aa3ae";
  }

  // Persistence --------------------------------------------------------------
  private saveTimer = 0;
  private scheduleSave() {
    clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => this.save(), 400);
  }

  save() {
    const data = {
      version: 1,
      items: this.items.map<SavedItem>((i) => (i.node.updateMatrix(), {
        objectId: i.objectId,
        name: i.name,
        matrix: i.node.matrix.toArray(),
        filament: i.filament,
        assembly: i.assembly,
        visible: i.visible,
      })),
      filaments: this.filaments,
      printSettings: this.printSettings,
      advanced: this.advanced,
      bedType: this.bedType,
      lastSlice: this.lastSlice,
      workspace: this.workspace,
    };
    try {
      localStorage.setItem("dforge.scene", JSON.stringify(data));
    } catch {
      /* storage unavailable: the scene simply isn't restored next time */
    }
  }

  restore(): { items: SavedItem[] } | null {
    try {
      const raw = localStorage.getItem("dforge.scene");
      if (!raw) return null;
      const data = JSON.parse(raw);
      this.filaments = data.filaments ?? [];
      this.printSettings = data.printSettings ?? {};
      this.advanced = data.advanced ?? "";
      this.bedType = data.bedType ?? this.bedType;
      this.lastSlice = data.lastSlice ?? null;
      this.workspace = data.workspace && !["paint", "rig", "animate", "render"].includes(data.workspace) ? data.workspace : "model";
      return { items: data.items ?? [] };
    } catch {
      return null;
    }
  }
}

export const state = new AppState();

let counter = 0;
export function uid(): string {
  counter += 1;
  return `i${Date.now().toString(36)}${counter}`;
}
