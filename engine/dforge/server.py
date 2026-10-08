"""Local HTTP API used by the desktop app. Binds to 127.0.0.1 only."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from flask import Flask, Response, jsonify, request, send_file
from werkzeug.utils import secure_filename

from . import APP_NAME, __version__
from .config import EXPORTS, TASKS, capabilities, orca_executable
from .handlers import HANDLERS
from .mesh.io import IMAGE_SUFFIXES, MESH_SUFFIXES, mesh_binary
from .slicer import profiles
from .store import ObjectStore
from .tasks import TaskManager

MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GiB


def create_app(token: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
    store = ObjectStore()
    tasks = TaskManager(store, HANDLERS)

    @app.before_request
    def authorize():
        if request.method == "OPTIONS":
            return Response(status=204)
        if token and request.path.startswith("/api/") and request.path != "/api/health":
            supplied = request.headers.get("X-DForge-Token") or request.args.get("token")
            if supplied != token:
                return jsonify(error="Not authorised"), 401
        return None

    @app.after_request
    def cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-DForge-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(KeyError)
    def not_found(error):
        return jsonify(error=str(error).strip("'\"")), 404

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify(error=str(error)), 400

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, app=APP_NAME, version=__version__)

    @app.get("/api/capabilities")
    def caps():
        return jsonify(capabilities())

    @app.get("/api/printer")
    def printer():
        return jsonify(profiles.schema())

    # Objects -----------------------------------------------------------
    @app.get("/api/objects")
    def objects():
        return jsonify(store.list())

    @app.get("/api/objects/<object_id>")
    def object_info(object_id):
        return jsonify(store.get(object_id))

    @app.patch("/api/objects/<object_id>")
    def object_rename(object_id):
        body = request.get_json(force=True) or {}
        changes = {k: body[k] for k in ("name", "hidden") if k in body}
        return jsonify(store.update(object_id, **changes))

    @app.get("/api/objects/<object_id>/display.glb")
    def object_display(object_id):
        return send_file(store.path(object_id, "display.glb"), mimetype="model/gltf-binary")

    @app.get("/api/objects/<object_id>/mesh.bin")
    def object_mesh(object_id):
        return Response(mesh_binary(store.mesh(object_id)), mimetype="application/octet-stream")

    @app.get("/api/objects/<object_id>/image")
    def object_image(object_id):
        record = store.get(object_id)
        return send_file(store.path(object_id, record["file"]))

    @app.get("/api/objects/<object_id>/labels")
    def labels_get(object_id):
        labels = store.load_labels(object_id)
        if labels is None:
            raise KeyError("No colour regions yet")
        return Response(labels.astype(np.uint8).tobytes(), mimetype="application/octet-stream")

    @app.put("/api/objects/<object_id>/labels")
    def labels_put(object_id):
        record = store.get(object_id)
        data = np.frombuffer(request.get_data(), dtype=np.uint8)
        faces = record["analysis"]["faces"]
        if len(data) != faces:
            raise ValueError(f"Expected {faces} labels, got {len(data)}")
        palette = request.args.get("palette")
        import json as _json

        palette = _json.loads(palette) if palette else record.get("palette") or []
        if data.max(initial=0) >= max(len(palette), 1):
            raise ValueError("A painted label has no palette colour")
        store.save_labels(object_id, data.copy(), palette)
        return jsonify(store.get(object_id))

    # Uploads and tasks ---------------------------------------------------
    @app.post("/api/upload")
    def upload():
        file = request.files.get("file")
        if file is None or not file.filename:
            raise ValueError("No file uploaded")
        name = secure_filename(file.filename) or "upload"
        suffix = Path(name).suffix.lower()
        if suffix not in MESH_SUFFIXES | IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported file type {suffix or '(none)'}")
        task = tasks.submit(
            "import",
            {"file": f"upload{suffix}", "name": file.filename},
            title=f"Import {file.filename}",
            prepare=lambda folder: file.save(folder / f"upload{suffix}"),
        )
        return jsonify(task)

    @app.post("/api/tasks")
    def task_submit():
        body = request.get_json(force=True) or {}
        kind = body.get("kind", "")
        if kind == "import":
            raise ValueError("Use /api/upload to import files")
        return jsonify(tasks.submit(kind, body.get("params") or {}, body.get("title")))

    @app.get("/api/tasks")
    def task_list():
        return jsonify(tasks.list())

    @app.get("/api/tasks/<task_id>")
    def task_get(task_id):
        return jsonify(tasks.get(task_id))

    @app.post("/api/tasks/<task_id>/cancel")
    def task_cancel(task_id):
        return jsonify(tasks.cancel(task_id))

    @app.get("/api/tasks/<task_id>/log")
    def task_log(task_id):
        path = tasks.folder(task_id) / "task.log"
        text = path.read_text(encoding="utf-8", errors="replace")[-200_000:] if path.is_file() else ""
        return Response(text, mimetype="text/plain")

    @app.get("/api/tasks/<task_id>/files/<name>")
    def task_file(task_id, name):
        folder = tasks.folder(task_id)
        path = (folder / secure_filename(name)).resolve()
        if folder.resolve() not in path.parents or not path.is_file():
            raise KeyError("File not found")
        return send_file(path, as_attachment=request.args.get("download") == "1")

    @app.post("/api/tasks/<task_id>/save")
    def task_save(task_id):
        """Copy a task output to a path the user picked in a save dialog."""
        body = request.get_json(force=True) or {}
        folder = tasks.folder(task_id)
        source = (folder / secure_filename(body.get("file", ""))).resolve()
        if folder.resolve() not in source.parents or not source.is_file():
            raise KeyError("File not found")
        destination = Path(body.get("destination") or (EXPORTS / source.name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copyfile(source, destination)
        return jsonify(saved_to=str(destination))

    @app.post("/api/open")
    def open_external():
        """Open a task output folder, or a sliced project in the full OrcaSlicer app."""
        body = request.get_json(force=True) or {}
        target = body.get("target", "folder")
        folder = tasks.folder(body["task_id"]) if body.get("task_id") else EXPORTS
        if target == "orca":
            orca = orca_executable()
            if orca is None:
                raise ValueError("OrcaSlicer is not installed")
            path = (folder / secure_filename(body.get("file", ""))).resolve()
            if not path.is_file():
                raise KeyError("File not found")
            subprocess.Popen([str(orca), "--datadir", str(orca.parents[0].parent / "orca-data"), str(path)], cwd=folder)
        elif os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return jsonify(ok=True)

    return app


def watch_parent(pid: int) -> None:
    """Exit when the desktop app that started us goes away."""

    def alive() -> bool:
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259  # STILL_ACTIVE
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def loop() -> None:
        while alive():
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def serve(port: int, token: str | None, parent_pid: int | None) -> None:
    from waitress import serve as waitress_serve

    if parent_pid:
        watch_parent(parent_pid)
    app = create_app(token)
    print(f"{APP_NAME} engine {__version__} on http://127.0.0.1:{port}", flush=True)
    waitress_serve(app, host="127.0.0.1", port=port, threads=8, channel_timeout=600, max_request_body_size=MAX_UPLOAD)


def new_token() -> str:
    return uuid.uuid4().hex


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 7863, None, None)
