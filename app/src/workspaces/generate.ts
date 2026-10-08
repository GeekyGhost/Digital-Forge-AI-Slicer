import { engine, type Capabilities, type ObjectRecord } from "../engine";
import { addObject } from "../scene";
import { checkField, clear, h, numberField, section, selectField } from "../ui/dom";
import { IMAGE_ACCEPT, pickFiles } from "../ui/files";
import { toast } from "../ui/toast";
import { viewport } from "../viewport";
import type { WorkspaceModule } from "./types";

let panel: HTMLElement;
let image: ObjectRecord | null = null;
let images: ObjectRecord[] = [];
let caps: Capabilities | null = null;
let busy = false;
const options = { resolution: "1024", seed: 42, background: "auto", steps: 12, texture: true };

async function upload(files: File[]) {
  const file = files[0];
  if (!file) return;
  try {
    const task = await engine.upload(file);
    image = task.result!.objects[0];
    images = [image, ...images.filter((i) => i.id !== image!.id)];
    render();
  } catch (error) {
    toast("Couldn't load the image", (error as Error).message, "error");
  }
}

async function generate() {
  if (!image) return;
  busy = true;
  render();
  try {
    const task = await engine.run<{ objects: ObjectRecord[] }>(
      "generate",
      { image_id: image.id, resolution: Number(options.resolution), seed: options.seed, background: options.background, steps: options.steps, texture: options.texture },
      "Generate 3D",
    );
    const item = await addObject(task.result!.objects[0]);
    viewport.frame([item]);
    const a = item.record.analysis;
    toast(
      "Model generated",
      a.recommended === "ok" ? "Closed solid, ready to print." : "Generated meshes are usually open or double-walled. Use Shell Wrap in Model before slicing.",
      "success",
    );
  } catch (error) {
    toast("Generation failed", (error as Error).message, "error");
  } finally {
    busy = false;
    render();
  }
}

function render() {
  if (!panel) return;
  if (caps && !caps.trellis.ready) {
    clear(
      panel,
      section(
        "Image to 3D",
        h("p.hint.warn", {}, "TRELLIS.2 isn't installed in this project yet."),
        h("p", {}, caps.trellis.missing_checkpoints.length ? `Missing weights: ${caps.trellis.missing_checkpoints.join(", ")}.` : "The runtime folder runtime\\trellis is empty."),
        h("p", {}, "Run scripts\\setup.ps1 to download the runtime and weights into this folder."),
      ),
    );
    return;
  }
  const drop = h(
    "div.dropzone",
    {
      tabIndex: 0,
      onclick: async () => upload(await pickFiles(IMAGE_ACCEPT, false)),
      ondragover: (e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        drop.classList.add("over");
      },
      ondragleave: () => drop.classList.remove("over"),
      ondrop: (e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        drop.classList.remove("over");
        upload(Array.from(e.dataTransfer?.files ?? []));
      },
    },
    image ? h("img", { src: engine.resource(`/api/objects/${image.id}/image`), alt: image.name }) : null,
    image ? `${image.name} · ${image.width} × ${image.height}` : "Drop a picture here, or click to choose one",
  );
  clear(
    panel,
    section(
      "Image to 3D",
      h("p", {}, "One clear subject, whole and uncropped, on a plain background works best. Front or three-quarter views give the most reliable shapes."),
      drop,
      images.length > 1
        ? h("div.row", { style: { marginTop: "8px" } },
            images.slice(0, 8).map((img) =>
              h("button.btn.small" + (img.id === image?.id ? ".primary" : ""), { onclick: () => ((image = img), render()) }, img.name.slice(0, 14)),
            ),
          )
        : null,
    ),
    section(
      "Settings",
      selectField("Quality", options.resolution, [["512", "Draft (fast)"], ["1024", "Standard"], ["1536", "High (slow)"]], (v) => (options.resolution = v)),
      h("div.field", {},
        h("label", {}, "Seed"),
        h("div.row", { style: { flexWrap: "nowrap" } },
          h("input", { type: "number", value: String(options.seed), onchange: (e: Event) => (options.seed = parseInt((e.target as HTMLInputElement).value) || 0) }),
          h("button.btn.small", { title: "New random seed", onclick: () => ((options.seed = Math.floor(Math.random() * 2 ** 31)), render()) }, "↻"),
        ),
      ),
      selectField("Background", options.background, [["auto", "Keep transparency, else remove"], ["birefnet", "Always remove (BiRefNet)"], ["threshold", "White background keyer"]], (v) => (options.background = v)),
      numberField({ label: "Sampling steps", value: options.steps, min: 4, max: 50, step: 1, onchange: (v) => (options.steps = Math.round(v)) }),
      checkField("Generate texture", options.texture, (v) => (options.texture = v)),
      h("button.btn.primary.block", { disabled: !image || busy, onclick: generate }, busy ? "Generating…" : "Generate model"),
      h("p.hint", { style: { marginTop: "8px" } }, "Runs on your GPU with the bundled TRELLIS.2 runtime. Draft takes under a minute; Standard a few minutes."),
    ),
  );
}

export const generateWorkspace: WorkspaceModule = {
  id: "generate",
  label: "Generate",
  async mount(p) {
    panel = p;
    render();
    try {
      caps = await engine.get<Capabilities>("/api/capabilities");
      const all = await engine.get<ObjectRecord[]>("/api/objects");
      images = all.filter((o) => o.kind === "image");
      const latest = (window as unknown as { __lastImage?: ObjectRecord }).__lastImage;
      image = latest ?? image ?? images[0] ?? null;
    } catch {
      /* status shown elsewhere */
    }
    render();
  },
};
