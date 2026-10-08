"""Fetch pinned native TRELLIS.2 assets, resume transfers, and verify SHA-256."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "v0.8.1"
REPO = "pwilkin/trellis.cpp"
HF = "ilintar/trellis2-gguf"


def request(url, **kwargs):
    return urllib.request.urlopen(
        urllib.request.Request(
            url, headers={"User-Agent": "TrellisStudio-Setup/0.1", **kwargs}
        ),
        timeout=90,
    )


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(item):
    path = ROOT / item["destination"]
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = item["sha256"]
    if (
        path.exists()
        and path.stat().st_size == item["size"]
        and digest(path) == expected
    ):
        print(f"Verified {path.name}", flush=True)
        return
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(5):
        try:
            start = part.stat().st_size if part.exists() else 0
            if start < item["size"]:
                block = 16 * 1024 * 1024

                def chunk(offset):
                    end = min(offset + block, item["size"]) - 1
                    separator = "&" if "?" in item["url"] else "?"
                    url = (
                        item["url"] + separator + f"download=true&range_start={offset}"
                    )
                    with request(url, Range=f"bytes={offset}-{end}") as response:
                        if response.status != 206 or not response.headers.get(
                            "Content-Range", ""
                        ).startswith(f"bytes {offset}-{end}/"):
                            raise ValueError(
                                "Server did not honor the requested byte range"
                            )
                        data = response.read()
                    if len(data) != end - offset + 1:
                        raise ValueError("Incomplete range response")
                    return data

                with (
                    part.open("ab") as target,
                    ThreadPoolExecutor(max_workers=4) as chunks,
                ):
                    while start < item["size"]:
                        offsets = list(
                            range(start, min(start + block * 4, item["size"]), block)
                        )
                        for data in chunks.map(chunk, offsets):
                            target.write(data)
                            start += len(data)
                        target.flush()
                        print(
                            f"{path.name}: {start * 100 / item['size']:.0f}%",
                            flush=True,
                        )
            if part.stat().st_size != item["size"] or digest(part) != expected:
                part.unlink(missing_ok=True)
                raise ValueError(f"Size or SHA-256 mismatch: {path.name}")
            part.replace(path)
            print(f"Downloaded and verified {path.name}", flush=True)
            return
        except Exception as error:
            print(f"Retry {attempt + 1}: {path.name}: {error}", flush=True)
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def main():
    manifest_path = ROOT / "config" / "downloads.lock.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        with request(
            f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}"
        ) as response:
            release = json.load(response)
        asset = next(
            a for a in release["assets"] if a["name"] == "trellis-cuda-windows-x64.zip"
        )
        with request(f"https://huggingface.co/api/models/{HF}") as response:
            revision = json.load(response)["sha"]
        with request(
            f"https://huggingface.co/api/models/{HF}/tree/{revision}/q8"
        ) as response:
            weights = json.load(response)
        items = [
            {
                "url": asset["browser_download_url"],
                "destination": "downloads/" + asset["name"],
                "size": asset["size"],
                "sha256": asset["digest"].removeprefix("sha256:"),
            }
        ]
        items.extend(
            {
                "url": f"https://huggingface.co/{HF}/resolve/{revision}/{w['path']}",
                "destination": "models/trellis2/" + w["path"],
                "size": w["size"],
                "sha256": w["lfs"]["oid"],
            }
            for w in weights
            if w["path"].endswith(".gguf")
        )
        manifest = {
            "runtime_release": RELEASE,
            "runtime_source": REPO,
            "model_source": HF,
            "model_revision": revision,
            "assets": items,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    required = sum(
        a["size"] for a in manifest["assets"] if not (ROOT / a["destination"]).exists()
    )
    if shutil.disk_usage(ROOT).free < required + 4 * 1024**3:
        raise RuntimeError(
            "Insufficient free disk space; need downloads plus 4 GiB working space"
        )
    print(
        f"Pinned {RELEASE}, Q8 revision {manifest['model_revision']}; {required / 1024**3:.2f} GiB remaining",
        flush=True,
    )
    # Fetch the runtime first so its CLI can be inspected while weights transfer.
    download(manifest["assets"][0])
    runtime = ROOT / "runtime" / "trellis"
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ROOT / manifest["assets"][0]["destination"]) as archive:
        for entry in archive.infolist():
            if (
                not (runtime / entry.filename)
                .resolve()
                .is_relative_to(runtime.resolve())
            ):
                raise ValueError("Unsafe archive path")
        archive.extractall(runtime)
    print("Native runtime ready", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, manifest["assets"][1:]))
    receipt = {
        "manifest_sha256": digest(manifest_path),
        "verified_at": time.time(),
        "assets": len(manifest["assets"]),
    }
    (ROOT / "config" / "download-verification.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print("TRELLIS.2 download complete", flush=True)


if __name__ == "__main__":
    main()
