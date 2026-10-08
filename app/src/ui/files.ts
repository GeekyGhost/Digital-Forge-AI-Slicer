import { engine } from "../engine";
import { toast } from "./toast";

const tauri = () => "__TAURI_INTERNALS__" in window;

/** Save a task output where the user chooses (desktop) or download it (browser). */
export async function saveTaskFile(taskId: string, file: string, filters: { name: string; extensions: string[] }[]) {
  if (tauri()) {
    const { save } = await import("@tauri-apps/plugin-dialog");
    const destination = await save({ defaultPath: file, filters });
    if (!destination) return;
    const result = await engine.post<{ saved_to: string }>(`/api/tasks/${taskId}/save`, { file, destination });
    toast("Saved", result.saved_to, "success");
  } else {
    const a = document.createElement("a");
    a.href = engine.resource(`/api/tasks/${taskId}/files/${encodeURIComponent(file)}?download=1`);
    a.download = file;
    a.click();
  }
}

export async function pickFiles(accept: string, multiple = true): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.onchange = () => resolve(Array.from(input.files ?? []));
    input.click();
  });
}

export const MESH_ACCEPT = ".stl,.3mf,.obj,.glb,.gltf,.ply,.off";
export const IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp";
