/** Workspaces on the roadmap. They say plainly what is coming instead of pretending to work. */

import { clear, h, section } from "../ui/dom";
import type { WorkspaceModule } from "./types";

function planned(id: WorkspaceModule["id"], label: string, intro: string, items: string[]): WorkspaceModule {
  return {
    id,
    label,
    planned: true,
    mount(panel) {
      clear(
        panel,
        section(
          label,
          h("p", {}, intro),
          h("ul.planned-list", {}, items.map((i) => h("li", {}, i))),
          h("p.hint", { style: { marginTop: "12px" } }, "Not built yet. Models you make here already keep their colours, so they will carry into this workspace."),
        ),
      );
    },
  };
}

export const paintWorkspace = planned("paint", "Paint", "Texture and colour painting directly on the model.", [
  "Layered brushes with masks, like Substance Painter",
  "Paint filament regions for multi-colour prints",
  "Bake and repaint generated textures",
  "Fill by colour, smart selection by curvature",
]);

export const rigWorkspace = planned("rig", "Rig", "Skeletons and skin weights for posing and animation.", [
  "Automatic humanoid and creature rigging in its own runtime",
  "Bone editing with mirror and snapping",
  "Heat-diffusion skin weights with brush corrections",
  "Deformation test poses before you commit",
]);

export const animateWorkspace = planned("animate", "Animate", "Key poses on a timeline.", [
  "Pose mode with IK handles",
  "Timeline and dope sheet",
  "Clip library and retargeting",
  "Pose a figure, then export it as a printable solid",
]);

export const renderWorkspace = planned("render", "Render", "Turntables and stills with the bundled Blender.", [
  "Studio lighting presets",
  "Turntable videos for product listings",
  "Transparent PNG stills for Facebook and shop images",
]);
