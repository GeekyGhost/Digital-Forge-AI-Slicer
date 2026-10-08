# Roadmap

## Milestone 1 — core app, viewport, slicer (this build)
Desktop shell, Z-up mm viewport with the Kobra S1 plate, import/export, transforms, printability checks, Shell Wrap, repair, simplify, booleans, TRELLIS.2 generation, colour detection and painting, colour split into watertight parts, joints between parts, plane cuts with joints, Kobra S1 profiles with 4 ACE Pro slots, OrcaSlicer slicing, toolpath preview, G-code and 3MF output.

## Milestone 2 — mesh quality
- Quad retopology (Instant Meshes / QuadriFlow, both permissively licensed) with feature-aware density
- Local Shell Wrap: rebuild only a selected region
- Wall-thickness and overhang heat maps on the model
- Auto-orient for fewest supports and best surface
- Hollowing with drain holes; text and logo embossing
- Undo across operations; a saved project file (.dforge)

## Milestone 3 — rigging and animation
- Own isolated rig runtime (MIA / UniRig) under runtime\rig, never borrowing another project's environment
- Bone editing, mirror, snapping
- Heat-diffusion weights with brush corrections and smoothing
- Pose mode with IK, timeline and key poses
- Pose a figure, then make it printable with Shell Wrap

## Milestone 4 — painting and rendering
- Layered texture painting with masks and fills
- Paint filament regions directly for multi-colour printing
- Blender-driven turntables and stills for product listings

## Milestone 5 — AI assistance and printer connection
- Local LLM assistant (Ollama, Qwen) that explains slicer warnings and suggests settings from the actual model and filament
- PartCrafter / Pixal3D as additional generators
- Kobra S1 LAN printing and status
