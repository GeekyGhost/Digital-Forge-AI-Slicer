"""Image-to-3D with the native TRELLIS.2 runtime (trellis.cpp, CUDA, Q8 weights)."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import TRELLIS_CHECKPOINTS, TRELLIS_MODELS, trellis_executable

RESOLUTIONS = (512, 1024, 1536)
STAGES = [
    ("background", "Removing background"),
    ("dinov3", "Reading the image"),
    ("ss_flow", "Sparse structure"),
    ("shape", "Shaping geometry"),
    ("tex", "Generating texture"),
    ("decode", "Decoding"),
    ("remesh", "Remeshing"),
    ("uv", "Unwrapping UVs"),
    ("bake", "Baking texture"),
    ("export", "Writing GLB"),
]


def check_ready() -> Path:
    binary = trellis_executable()
    missing = [n for n in TRELLIS_CHECKPOINTS if not (TRELLIS_MODELS / f"{n}.gguf").is_file()]
    if binary is None or missing:
        raise RuntimeError(
            "TRELLIS.2 is not installed" + (f" (missing {', '.join(missing)})" if missing else "") + ". Run setup."
        )
    return binary


def command(image: Path, output: Path, params: dict) -> list:
    binary = check_ready()
    resolution = int(params.get("resolution", 1024))
    if resolution not in RESOLUTIONS:
        raise ValueError("Resolution must be 512, 1024 or 1536")
    seed = int(params.get("seed", 42))
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("Seed must be 0 to 2147483647")
    background = params.get("background", "auto")
    if background not in {"auto", "birefnet", "threshold"}:
        raise ValueError("Unknown background mode")
    args = [
        binary, image, output,
        "--models", TRELLIS_MODELS,
        "--require-gpu", "--verbose",
        "--res", resolution,
        "--seed", seed,
        "--webp", "off",  # PNG textures load everywhere
    ]
    if background != "auto":
        args += ["--bg-removal", background]
    if params.get("steps"):
        args += ["--steps", int(params["steps"])]
    if params.get("texture") is False:
        args += ["--no-texture"]
    if params.get("full_detail"):
        args += ["--decim", 0]  # keep the full-resolution mesh (very large)
    return args


def stage_of(line: str) -> str | None:
    lower = line.lower()
    for key, label in STAGES:
        if re.search(rf"\b{re.escape(key)}", lower):
            return label
    return None
