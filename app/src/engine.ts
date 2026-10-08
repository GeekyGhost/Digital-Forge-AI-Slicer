/** Client for the local Python engine (spawned by the Tauri shell on 127.0.0.1). */

export interface Analysis {
  faces: number;
  vertices: number;
  size_mm: [number, number, number];
  bounds_mm: [[number, number, number], [number, number, number]];
  volume_mm3: number | null;
  watertight: boolean;
  winding_consistent: boolean;
  components: number;
  boundary_edges: number;
  nonmanifold_edges: number;
  fits_printer: boolean;
  has_color: boolean;
  issues: string[];
  recommended: "ok" | "repair" | "wrap";
}

export interface PaletteEntry {
  label: number;
  color: string;
  area_pct: number;
  faces: number;
  filament: number;
}

export interface ObjectRecord {
  id: string;
  name: string;
  kind: "mesh" | "image";
  created: string;
  parent?: string | null;
  operation?: string | null;
  analysis: Analysis;
  has_color?: boolean;
  notes?: string[];
  palette?: PaletteEntry[];
  filament?: number;
  split_color?: string;
  label?: number;
  report?: Record<string, unknown> | null;
  file?: string;
  width?: number;
  height?: number;
  import?: { textured?: boolean } | null;
}

export interface Task<R = any> {
  id: string;
  kind: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";
  progress: number;
  message: string;
  result: R | null;
  error: string | null;
}

export interface Capabilities {
  root: string;
  orca: { ready: boolean; path: string | null };
  blender: { ready: boolean; path: string | null };
  trellis: { ready: boolean; path: string | null; missing_checkpoints: string[] };
}

type TaskListener = (task: Task) => void;

class Engine {
  url = (import.meta.env.VITE_ENGINE_URL as string | undefined) ?? "http://127.0.0.1:7863";
  token = "";
  private listeners = new Set<TaskListener>();
  private watching = new Map<string, Task>();

  async connect(): Promise<void> {
    // Inside the desktop app the shell tells us where it started the engine.
    if ("__TAURI_INTERNALS__" in window) {
      const { invoke } = await import("@tauri-apps/api/core");
      for (let i = 0; i < 600; i++) {
        const info = await invoke<{ url: string; token: string } | null>("engine_info");
        if (info) {
          this.url = info.url;
          this.token = info.token;
          break;
        }
        await sleep(100);
      }
    }
    const started = Date.now();
    for (;;) {
      try {
        const res = await fetch(`${this.url}/api/health`);
        if (res.ok) return;
      } catch {
        /* engine still starting */
      }
      if (Date.now() - started > 90_000) {
        let detail = "";
        if ("__TAURI_INTERNALS__" in window) {
          const { invoke } = await import("@tauri-apps/api/core");
          detail = await invoke<string>("engine_log").catch(() => "");
        }
        throw new Error("The engine did not start. " + (detail ? detail.slice(-600) : "Run scripts\\setup.ps1 first."));
      }
      await sleep(300);
    }
  }

  resource(path: string): string {
    const sep = path.includes("?") ? "&" : "?";
    return `${this.url}${path}${this.token ? `${sep}token=${this.token}` : ""}`;
  }

  async request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.token) headers["X-DForge-Token"] = this.token;
    let payload: BodyInit | undefined;
    if (body instanceof FormData || body instanceof Uint8Array || body instanceof ArrayBuffer) {
      payload = body as BodyInit;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await fetch(`${this.url}${path}`, { method, headers, body: payload });
    if (!res.ok) {
      let message = `${res.status} ${res.statusText}`;
      try {
        message = (await res.json()).error ?? message;
      } catch {
        /* not JSON */
      }
      throw new Error(message);
    }
    if (raw) return (await res.arrayBuffer()) as T;
    return (await res.json()) as T;
  }

  get<T>(path: string) {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body?: unknown) {
    return this.request<T>("POST", path, body ?? {});
  }
  binary(path: string) {
    return this.request<ArrayBuffer>("GET", path, undefined, true);
  }

  onTask(listener: TaskListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Submit a task and resolve with its result when it finishes. */
  async run<R = any>(kind: string, params: Record<string, unknown>, title?: string): Promise<Task<R>> {
    const task = await this.post<Task<R>>("/api/tasks", { kind, params, title });
    return this.follow(task);
  }

  async upload(file: File): Promise<Task<{ objects: ObjectRecord[] }>> {
    const form = new FormData();
    form.append("file", file, file.name);
    const task = await this.request<Task>("POST", "/api/upload", form);
    return this.follow(task);
  }

  cancel(id: string) {
    return this.post(`/api/tasks/${id}/cancel`);
  }

  private async follow<R>(task: Task<R>): Promise<Task<R>> {
    this.watching.set(task.id, task);
    this.emit(task);
    for (;;) {
      await sleep(task.status === "queued" ? 400 : 250);
      task = await this.get<Task<R>>(`/api/tasks/${task.id}`);
      this.emit(task);
      if (["done", "failed", "cancelled", "interrupted"].includes(task.status)) {
        this.watching.delete(task.id);
        if (task.status === "done") return task;
        throw new TaskError(task);
      }
    }
  }

  private emit(task: Task) {
    for (const l of this.listeners) l(task);
  }
}

export class TaskError extends Error {
  constructor(public task: Task) {
    super(task.status === "cancelled" ? "Cancelled" : task.error || `${task.title} failed`);
  }
}

export function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export const engine = new Engine();
