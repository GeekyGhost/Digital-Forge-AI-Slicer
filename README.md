# Digital Forge 3D Slicer

A standalone, offline 3D workshop for the Anycubic Kobra S1: generate models from images, fix and rebuild meshes, split them by colour into printable parts with joints, and slice for single- or multi-colour (ACE Pro) printing. Painting, rigging, animation and rendering follow in later milestones.

Everything it needs — Python, the slicing engine, Blender, AI weights, your models and outputs — lives inside this folder. Nothing is installed into Windows or borrowed from another project.

## First run

```powershell
cd 'C:\AI\AI Slicer'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Setup downloads, checksum-verifies and unpacks (about 1 GB, resumable):

| Folder | What | Why |
|---|---|---|
| `runtime\python` | CPython 3.12.14 portable + pinned packages | the engine |
| `runtime\orca` | OrcaSlicer 2.4.2 portable | slicing engine with Anycubic's Kobra S1 profiles |
| `runtime\blender` | Blender 4.4.3 portable | render and rig workspaces (later milestones); skip with `-SkipBlender` |
| `runtime\trellis`, `models\trellis2` | TRELLIS.2 native CUDA runtime and Q8 weights | already present; setup only verifies them |

It then builds the desktop app with Node.js and Rust (the same toolchain as Digital Forge Audio Studio). After that, double-click **Digital Forge 3D Slicer.cmd**.

Other scripts: `scripts\dev.ps1` (live-reload while editing the UI), `scripts\test.ps1` (engine tests), `scripts\engine-only.ps1` (engine on port 7863 for browser debugging), `scripts\build.ps1` (rebuild the app).

## Workspaces

| Workspace | What you can do now |
|---|---|
| **Model** | Import STL, 3MF, OBJ, GLB, PLY. Move, rotate, scale, set exact size in mm, mirror, lay a face flat, duplicate. Printability check. **Shell Wrap** rebuilds any mesh as one closed solid: it seals holes and gaps, fixes flipped faces, handles double-walled AI meshes, can thicken paper-thin parts, and keeps colours. Quick repair, simplify (stays watertight), join/subtract/intersect, export STL/3MF/OBJ/GLB/PLY. |
| **Generate** | Image to textured 3D with TRELLIS.2 on your GPU (draft 512, standard 1024, high 1536), seed, background removal. |
| **Colour split** | Finds the paint colours on a textured model, with shading ignored so shadowed and lit green stay together. Fix regions with a brush, merge or add colours, then build real watertight parts. *Multi-colour print* keeps one object with a colour skin for the ACE Pro; *Separate parts* makes solid colour pieces. Then add pegs, dowels or magnets where the parts touch. |
| **Cut** | Horizontal or vertical planes, with tilt. Joints placed automatically at the thickest point(s) of the cut: round peg, square peg, dowel (printed separately), magnet pockets, dovetail. Fit clearance and a check that each joint has enough material around it. |
| **Prepare** | Kobra S1, 0.4 mm nozzle, plate type, up to 4 ACE Pro filament slots (material, colour, temperatures), per-object filament, arrange, drop to bed, print settings (quality, strength, supports, adhesion, speed, multi-colour, vase mode, plus any raw Orca key), then slice. |
| **Preview** | Toolpaths coloured by line type, filament or speed. Layer range, travel moves, time, grams and metres per slot, colour changes, slicer warnings. Save G-code or the 3MF project, or open it in the full OrcaSlicer. |
| Paint, Rig, Animate, Render | Planned; each says what is coming. |

The plate (objects, positions, filaments, settings) is saved automatically and restored on the next start. Every operation makes a new object, so the original is never overwritten: earlier versions stay in the object list, hidden.

## How it is built

```text
app/                  desktop app: Tauri 2 shell (Rust) + Vite/TypeScript UI + Three.js viewport
  src/workspaces/     one module per workspace
  src-tauri/          starts the engine on a free local port with a per-session token, stops it on exit
engine/dforge/        Python engine (HTTP on 127.0.0.1 only)
  mesh/               import/export, analysis, repair, Shell Wrap, exact solids (manifold3d)
  assembly/           colour detection, colour split, plane cuts, joints
  slicer/             Kobra S1 profiles, 3MF writer, OrcaSlicer runner, G-code reader
  generate/           TRELLIS.2 adapter
engine/tests/         engine test suite (includes a real two-colour slice when Orca is installed)
profiles/orca/        Kobra S1 machine, process and 8 filament presets, flattened from OrcaSlicer
profiles/anycubic-next/  presets GPT copied from Anycubic Slicer Next (reference)
runtime/, models/     bundled tools and AI weights (setup fills these)
data/objects, data/tasks, data/exports   your models, task outputs and logs
legacy/gpt-studio/    the previous TRELLIS Creation Studio build, kept for reference
```

Units are millimetres with Z up everywhere. Generated or glTF models with no real size are scaled to 100 mm tall on import; set the size you want in Model.

Slicing writes an OrcaSlicer project 3MF (one object per plate item, one part per colour with its filament) and runs `orca-slicer.exe` in command-line mode with the flattened Kobra S1 profiles. Orca's own data folder is kept in `runtime\orca-data`.

## Known limits

- The printer is reached by USB stick or through OrcaSlicer (**Open in OrcaSlicer**). Direct LAN printing to the Kobra S1 isn't built yet.
- Shell Wrap works at voxel resolution: details thinner than about one voxel can merge. Lower the detail value for fine features.
- Colour split on generated figures yields intricate parts; *Multi-colour print* is the practical mode for them. Joints need parts thick enough to hold them, and the app warns when they aren't.
- TRELLIS generation was validated by the earlier build on this machine. The new adapter uses the same runtime, with the upstream default of 12 sampling steps. GPT's character reference still came out as a flat sheet; try a clean front view on a plain background, or the 1536 setting.
