# Third-party components

This inventory lists components with their own terms. It does not relicense anything. Full texts are in `licenses/` or ship inside each runtime folder.

## Bundled tools (downloaded by setup into `runtime/`)

| Component | Version | Terms | Notes |
|---|---|---|---|
| CPython (python-build-standalone) | 3.12.14 / 20260901 | PSF and bundled library licences | `runtime/python/LICENSE.txt` and friends |
| OrcaSlicer | 2.4.2 portable | AGPL-3.0 | Run as a separate program; unmodified. Source: https://github.com/OrcaSlicer/OrcaSlicer. Kobra S1 presets in `profiles/orca` are flattened copies of its Anycubic system profiles. |
| Blender | 4.4.3 portable | GPL-3.0-or-later | Run as a separate program. |
| trellis.cpp | 0.8.1 | MIT | `licenses/MIT-trellis.cpp.txt` |
| TRELLIS.2 | — | MIT | `licenses/MIT-TRELLIS.2.txt` |
| Converted Q8 checkpoints | ilintar/trellis2-gguf | Original component terms apply | `licenses/GGUF-MODEL-CARD.md` |
| DINOv3 encoder (inside the checkpoints) | — | Meta DINOv3 licence | `licenses/Meta-DINOv3-LICENSE.md`. Quantising does not remove these terms. |
| BiRefNet | — | MIT | `licenses/MIT-BiRefNet.txt` |

## Engine Python packages (`engine/requirements.lock.txt`)

NumPy, SciPy, scikit-image, networkx, trimesh, Pillow (HPND), Flask, Werkzeug, Jinja2, click, itsdangerous, MarkupSafe, blinker: BSD/MIT-style. Waitress: ZPL 2.1. manifold3d: Apache-2.0. libigl: MPL-2.0. fast-simplification: MIT. ImageIO, tifffile, lazy-loader, packaging: BSD/Apache.

## App packages (`app/package-lock.json`, `app/src-tauri/Cargo.lock`)

Tauri 2 and its plugins: MIT or Apache-2.0. three.js: MIT. three-mesh-bvh: MIT. Barlow and Barlow Semi Condensed fonts (@fontsource): SIL Open Font License 1.1. Vite, TypeScript: MIT / Apache-2.0. Rust crates: see `Cargo.lock`; overwhelmingly MIT/Apache-2.0.

## Kept for reference in `legacy/gpt-studio`

The previous build's DigitalForgeRig copy (GPL-3.0), Make-It-Animatable weights (MIT), model-viewer bundle (Apache-2.0) and Anycubic Slicer Next profiles (AGPL-3.0). None of these is loaded by the new app.

## Before sharing builds

The app's own code is yours to license. Shipping the bundled runtimes alongside it means passing on their licences and, for AGPL/GPL programs, pointing to their source. They are separate executables here, not linked into the app. The DINOv3 terms govern any redistribution of the TRELLIS weights.
