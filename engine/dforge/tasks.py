"""Background tasks with progress, cancellation, logs and results that survive restarts.

Two lanes run side by side: "gpu" for AI generation and "cpu" for everything
else, so slicing never waits behind a long generation.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from .config import TASKS
from .process import Cancelled, run
from .store import ObjectStore, now

GPU_KINDS = {"generate"}


class Context:
    def __init__(self, manager: "TaskManager", task: dict):
        self.manager = manager
        self.task = task
        self.folder = TASKS / task["id"]
        self.log = self.folder / "task.log"
        self.store: ObjectStore = manager.store
        self._last_save = 0.0

    def progress(self, fraction: float, message: str = "") -> None:
        self.task["progress"] = round(max(0.0, min(1.0, float(fraction))), 3)
        if message:
            self.task["message"] = message
            with open(self.log, "a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
        if time.monotonic() - self._last_save > 0.5:
            self.manager._save(self.task)
            self._last_save = time.monotonic()
        if self.cancelled():
            raise Cancelled("Cancelled")

    def cancelled(self) -> bool:
        return bool(self.task.get("cancel"))

    def run(self, command: list, timeout: float = 3600, **kwargs) -> int:
        return run(command, self.log, self.cancelled, timeout=timeout, cwd=self.folder, **kwargs)


class TaskManager:
    def __init__(self, store: ObjectStore, handlers: dict[str, Callable[[Context, dict], dict]]):
        self.store = store
        self.handlers = handlers
        self.tasks: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.queues = {"cpu": queue.Queue(), "gpu": queue.Queue()}
        self._recover()
        for lane, q in self.queues.items():
            threading.Thread(target=self._worker, args=(q,), name=f"tasks-{lane}", daemon=True).start()

    def _recover(self) -> None:
        for path in sorted(TASKS.glob("*/task.json")):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if task.get("status") in {"queued", "running"}:
                task.update(status="interrupted", error="The app closed before this task finished. Run it again.")
                self._save(task)
            self.tasks[task["id"]] = task

    def _save(self, task: dict) -> None:
        folder = TASKS / task["id"]
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "task.json.tmp"
        tmp.write_text(json.dumps(task, indent=1, default=str), encoding="utf-8")
        tmp.replace(folder / "task.json")

    def submit(self, kind: str, params: dict, title: str | None = None, prepare: Callable[[Path], None] | None = None) -> dict:
        if kind not in self.handlers:
            raise ValueError(f"Unknown task '{kind}'")
        task = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "title": title or kind.capitalize(),
            "params": params,
            "status": "queued",
            "progress": 0.0,
            "message": "Waiting",
            "created": now(),
            "result": None,
            "error": None,
            "cancel": False,
        }
        (TASKS / task["id"]).mkdir(parents=True, exist_ok=True)
        if prepare:
            prepare(TASKS / task["id"])  # e.g. store an upload before any worker can see the task
        with self.lock:
            self.tasks[task["id"]] = task
        self._save(task)
        self.queues["gpu" if kind in GPU_KINDS else "cpu"].put(task["id"])
        return task

    def folder(self, task_id: str) -> Path:
        if task_id not in self.tasks:
            raise KeyError("Unknown task")
        return TASKS / task_id

    def get(self, task_id: str) -> dict:
        if task_id not in self.tasks:
            raise KeyError("Unknown task")
        return self.tasks[task_id]

    def list(self, limit: int = 60) -> list[dict]:
        return sorted(self.tasks.values(), key=lambda t: t["created"], reverse=True)[:limit]

    def cancel(self, task_id: str) -> dict:
        task = self.get(task_id)
        task["cancel"] = True
        if task["status"] == "queued":
            task.update(status="cancelled", message="Cancelled")
        self._save(task)
        return task

    def _worker(self, q: queue.Queue) -> None:
        while True:
            task_id = q.get()
            task = self.tasks.get(task_id)
            if not task or task["status"] != "queued":
                continue
            context = Context(self, task)
            task.update(status="running", started=now(), message="Starting")
            self._save(task)
            try:
                result = self.handlers[task["kind"]](context, task["params"])
                task.update(status="done", progress=1.0, result=result, message="Done")
            except Cancelled:
                task.update(status="cancelled", message="Cancelled")
            except Exception as error:  # noqa: BLE001 - every failure is reported to the UI
                with open(context.log, "a", encoding="utf-8") as handle:
                    handle.write(traceback.format_exc())
                task.update(status="failed", error=str(error) or error.__class__.__name__, message="Failed")
            task["finished"] = now()
            self._save(task)
