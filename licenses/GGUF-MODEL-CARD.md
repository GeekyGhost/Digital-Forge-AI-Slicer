---
license: other
tags:
- gguf
- ggml
- image-to-3d
- trellis
---

# TRELLIS.2 — GGUF

GGUF weights for **TRELLIS.2 (image-to-3D)**, converted for use with
[trellis.cpp](https://github.com/pwilkin/trellis.cpp) — a standalone C++/GGML runtime (CUDA + Vulkan), and
with [Lemonade](https://github.com/lemonade-sdk/lemonade) as a GGML media backend.

Source model: https://huggingface.co/microsoft/TRELLIS-image-large
License: see the source model.

## Files
The full pipeline: `dinov3`, `ss_flow`/`ss_dec` (sparse structure), `shape_flow_512`/`shape_flow_1024`/`shape_dec`, `tex_flow_512`/`tex_flow_1024`/`tex_dec`, and `birefnet` (optional background removal, CUDA only).
