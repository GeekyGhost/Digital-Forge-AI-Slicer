import type { Workspace } from "../state";

export interface WorkspaceModule {
  id: Workspace;
  label: string;
  planned?: boolean;
  /** Transform gizmo available in this workspace. */
  gizmo?: boolean;
  /** Show objects in their filament colour instead of their texture. */
  filamentView?: boolean;
  mount(panel: HTMLElement): void;
  unmount?(): void;
  /** Selection or objects changed. */
  refresh?(): void;
}
