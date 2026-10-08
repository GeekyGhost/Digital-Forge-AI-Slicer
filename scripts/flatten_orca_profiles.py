"""Flatten OrcaSlicer vendor profiles into standalone JSON files.

Orca system presets inherit from shared base files by name. The slicer CLI takes
self-contained files, so this resolves each inheritance chain and writes one
flat machine/process/filament JSON per preset.

Usage:
    python scripts/flatten_orca_profiles.py <orca resources/profiles/Anycubic> <out dir> [--match "Kobra S1 0.4"]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_vendor(folder: Path) -> dict[str, dict[str, dict]]:
    presets: dict[str, dict[str, dict]] = {"machine": {}, "process": {}, "filament": {}}
    for kind in presets:
        for path in sorted((folder / kind).rglob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            name = data.get("name")
            if name:
                presets[kind][name] = data
    return presets


def resolve(kind: str, name: str, presets: dict, base_presets: dict | None = None) -> dict:
    chain = []
    seen = set()
    current = name
    while current:
        if current in seen:
            raise ValueError(f"Inheritance loop at {current}")
        seen.add(current)
        data = presets[kind].get(current) or (base_presets or {}).get(kind, {}).get(current)
        if data is None:
            raise KeyError(f"{kind} preset '{current}' not found (needed by '{name}')")
        chain.append(data)
        current = data.get("inherits", "")
    merged: dict = {}
    for data in reversed(chain):
        merged.update(data)
    merged["inherits"] = ""
    merged["from"] = "system"
    merged["name"] = name
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--match", default="Kobra S1 0.4 nozzle")
    parser.add_argument("--base", type=Path, help="Optional OrcaFilamentLibrary folder")
    args = parser.parse_args()
    presets = load_vendor(args.vendor)
    base = load_vendor(args.base) if args.base else None
    index = {"machine": [], "process": [], "filament": []}
    for kind in index:
        (args.out / kind).mkdir(parents=True, exist_ok=True)
        for name, data in presets[kind].items():
            if data.get("instantiation") != "true" or args.match not in name:
                continue
            flat = resolve(kind, name, presets, base)
            target = args.out / kind / f"{name}.json"
            target.write_text(json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8")
            index[kind].append(name)
    (args.out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(json.dumps({k: len(v) for k, v in index.items()}))


if __name__ == "__main__":
    main()
