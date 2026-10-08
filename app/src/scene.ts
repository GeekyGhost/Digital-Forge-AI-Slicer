/** Adding, replacing and removing plate objects, plus the object list. */

import * as THREE from "three";
import { engine, type ObjectRecord } from "./engine";
import { state, uid, type SavedItem, type SceneItem } from "./state";
import { BED, viewport } from "./viewport";
import { clear, h, spool } from "./ui/dom";
import { toast } from "./ui/toast";

export interface AddOptions {
  matrix?: THREE.Matrix4; // world placement; default: centre of the bed
  filament?: number;
  assembly?: string;
  select?: boolean;
  visible?: boolean;
  name?: string;
}

/** First spot near the bed centre where a new object's footprint doesn't overlap anything. */
function freeSpot(record: ObjectRecord): THREE.Vector3 {
  const [sx, sy] = record.analysis?.size_mm ?? [20, 20, 20];
  const taken = state.items.filter((i) => i.visible).map((i) => viewport.worldBox(i, false));
  const fits = (x: number, y: number) =>
    taken.every((b) => x + sx / 2 + 3 < b.min.x || x - sx / 2 - 3 > b.max.x || y + sy / 2 + 3 < b.min.y || y - sy / 2 - 3 > b.max.y);
  const cx = BED.x / 2;
  const cy = BED.y / 2;
  for (let r = 0; r <= 125; r += 5) {
    for (let a = 0; a < 360; a += r ? Math.max(8, 360 / (r / 2.5)) : 360) {
      const x = cx + r * Math.cos((a * Math.PI) / 180);
      const y = cy + r * Math.sin((a * Math.PI) / 180);
      if (x - sx / 2 >= 0 && y - sy / 2 >= 0 && x + sx / 2 <= BED.x && y + sy / 2 <= BED.y && fits(x, y)) return new THREE.Vector3(x, y, 0);
    }
  }
  return new THREE.Vector3(cx, cy, 0);
}

export async function addObject(record: ObjectRecord, options: AddOptions = {}): Promise<SceneItem> {
  const node = new THREE.Group();
  if (options.matrix) {
    options.matrix.decompose(node.position, node.quaternion, node.scale);
  } else {
    node.position.copy(freeSpot(record));
  }
  const item: SceneItem = {
    uid: uid(),
    objectId: record.id,
    name: options.name ?? record.name,
    record,
    node,
    filament: options.filament ?? record.filament ?? 1,
    assembly: options.assembly,
    visible: options.visible ?? true,
  };
  state.items.push(item);
  await viewport.load(item);
  if (options.select !== false) state.select([item.uid]);
  state.emit("items");
  return item;
}

/** Put derived objects exactly where their source was, hiding the source. */
export async function addDerived(source: SceneItem, records: ObjectRecord[], opts: { assembly?: boolean; hideSource?: boolean; filament?: (r: ObjectRecord) => number } = {}): Promise<SceneItem[]> {
  source.node.updateMatrix();
  const matrix = source.node.matrix.clone();
  const assembly = opts.assembly ? `a${Date.now().toString(36)}` : source.assembly;
  const added: SceneItem[] = [];
  for (const record of records) {
    const extra = record.parent === null || record.parent === undefined;
    added.push(
      await addObject(record, {
        matrix: extra ? undefined : matrix,
        assembly: extra ? undefined : assembly,
        filament: opts.filament ? opts.filament(record) : record.filament ?? source.filament,
        select: false,
      }),
    );
  }
  if (opts.hideSource !== false) setVisible(source, false);
  state.select(added.map((i) => i.uid));
  return added;
}

export function setVisible(item: SceneItem, visible: boolean) {
  item.visible = visible;
  item.node.visible = visible;
  if (!visible) state.select(state.selection.filter((u) => u !== item.uid));
  viewport.refreshHelpers();
  state.emit("items");
}

export function removeItems(items: SceneItem[]) {
  for (const item of items) {
    viewport.remove(item);
    state.items = state.items.filter((i) => i !== item);
  }
  state.select(state.selection.filter((u) => state.item(u)));
  state.emit("items");
}

export function duplicate(items: SceneItem[]) {
  const assemblies = new Map<string, string>();
  return Promise.all(
    items.map((item) => {
      item.node.updateMatrix();
      const m = item.node.matrix.clone().premultiply(new THREE.Matrix4().makeTranslation(12, 12, 0));
      let assembly: string | undefined;
      if (item.assembly) {
        if (!assemblies.has(item.assembly)) assemblies.set(item.assembly, `a${Date.now().toString(36)}${assemblies.size}`);
        assembly = assemblies.get(item.assembly);
      }
      return addObject(item.record, { matrix: m, filament: item.filament, assembly, name: item.name, select: false });
    }),
  );
}

export async function restoreScene(saved: SavedItem[]) {
  for (const entry of saved) {
    try {
      const record = await engine.get<ObjectRecord>(`/api/objects/${entry.objectId}`);
      const matrix = new THREE.Matrix4().fromArray(entry.matrix);
      await addObject(record, { matrix, filament: entry.filament, assembly: entry.assembly, visible: entry.visible, name: entry.name, select: false });
    } catch {
      /* object removed from the library: skip it */
    }
  }
  state.select([]);
}

export async function importFiles(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    try {
      const task = await engine.upload(file);
      for (const record of task.result!.objects) {
        if (record.kind === "image") {
          state.emit("image-imported");
          (window as unknown as { __lastImage?: ObjectRecord }).__lastImage = record;
          toast(`Image ${file.name} ready`, "Open Generate to turn it into a 3D model.");
          continue;
        }
        const item = await addObject(record);
        viewport.frame([item]);
        for (const note of record.notes ?? []) toast(record.name, note);
        if (record.analysis.recommended === "wrap") {
          toast("This model needs fixing before printing", "It has holes or broken geometry. Use Shell Wrap in Model to rebuild it as a closed solid.", "error");
        }
      }
    } catch (error) {
      toast(`Couldn't import ${file.name}`, (error as Error).message, "error");
    }
  }
}

// Object list ----------------------------------------------------------------
export function renderOutliner(root: HTMLElement) {
  if (!state.items.length) {
    clear(
      root,
      h(
        "div.empty",
        {},
        h("strong", {}, "The plate is empty"),
        "Drop STL, 3MF, OBJ, GLB or PLY files anywhere, or generate a model from an image.",
      ),
    );
    return;
  }
  const selected = new Set(state.selection);
  const seen = new Set<string>();
  const rows: HTMLElement[] = [];
  const row = (item: SceneItem, child: boolean) =>
    h(
      "li" + (selected.has(item.uid) ? ".selected" : "") + (item.visible ? "" : ".hidden-item") + (child ? ".child" : ""),
      {
        onclick: (e: MouseEvent) => {
          if (e.shiftKey || e.ctrlKey) {
            const set = new Set(state.selection);
            set.has(item.uid) ? set.delete(item.uid) : set.add(item.uid);
            state.select([...set]);
          } else state.select([item.uid]);
        },
        ondblclick: () => viewport.frame([item]),
        title: item.record.analysis?.issues?.join("\n") || "Ready to print",
      },
      spool(state.filamentColor(item.filament)),
      h("span.name", {}, child && item.name.includes(" · ") ? item.name.split(" · ").slice(1).join(" · ") : item.name),
      item.node.userData.outOfBed && item.visible ? h("span.flag", {}, "off bed") : item.record.analysis?.recommended === "wrap" ? h("span.flag", {}, "needs fix") : h("span"),
      h(
        "button.eye",
        {
          title: item.visible ? "Hide" : "Show",
          "aria-label": item.visible ? `Hide ${item.name}` : `Show ${item.name}`,
          onclick: (e: MouseEvent) => {
            e.stopPropagation();
            setVisible(item, !item.visible);
          },
        },
        item.visible ? "◉" : "○",
      ),
    );
  for (const item of state.items) {
    if (seen.has(item.uid)) continue;
    if (item.assembly) {
      const group = state.items.filter((i) => i.assembly === item.assembly);
      group.forEach((i) => seen.add(i.uid));
      rows.push(
        h(
          "li" + (group.every((i) => selected.has(i.uid)) ? ".selected" : ""),
          { onclick: () => state.select(group.map((i) => i.uid)), title: "Prints as one multi-part object" },
          h("span", {}, "▾"),
          h("span.name", { title: group[0].name.split(" · ")[0] }, `${group[0].name.split(" · ")[0]}`, h("span.hint", {}, ` ${group.length} parts`)),
          h("span"),
          h("span"),
        ),
      );
      group.forEach((i) => rows.push(row(i, true)));
    } else {
      seen.add(item.uid);
      rows.push(row(item, false));
    }
  }
  const visible = state.items.filter((i) => i.visible).length;
  clear(
    root,
    h(
      "div.section",
      { style: { paddingBottom: "8px" } },
      h("h2", {}, "Objects", h("span.aside", {}, `${visible} on plate`)),
      state.items.some((i) => !i.visible)
        ? h("button.btn.small.ghost", { title: "Hidden objects stay in the library; this only tidies the list", onclick: () => removeItems(state.items.filter((i) => !i.visible)) }, "Remove hidden from list")
        : null,
    ),
    h("ul.objects", {}, rows),
  );
}
