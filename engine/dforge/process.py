"""Run external tools (OrcaSlicer, TRELLIS, Blender) with cancel, timeout and full logs.

On Windows a Job Object ties every child process to the task, so cancelling or
closing the app never leaves orphaned slicer or GPU processes behind.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable


class Cancelled(RuntimeError):
    pass


class _JobObject:
    def __init__(self, process: subprocess.Popen):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [
                ("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                ("min_working", c.c_size_t), ("max_working", c.c_size_t), ("active", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD),
            ]

        class IO(c.Structure):
            _fields_ = [(n, c.c_uint64) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]

        class Limits(c.Structure):
            _fields_ = [
                ("basic", Basic), ("io", IO), ("process_memory", c.c_size_t),
                ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t),
            ]

        k = c.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = w.HANDLE
        k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.CloseHandle.argtypes = [w.HANDLE]
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            return
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)) and k.AssignProcessToJobObject(
            handle, w.HANDLE(int(process._handle))
        ):
            self.handle, self.kernel = handle, k
        else:
            k.CloseHandle(handle)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run(
    command: list,
    log: Path,
    cancelled: Callable[[], bool] = lambda: False,
    timeout: float = 3600,
    cwd: Path | None = None,
    env: dict | None = None,
    on_line: Callable[[str], None] | None = None,
) -> int:
    environment = os.environ.copy()
    environment.update({"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    if os.name != "nt":
        environment.setdefault("LC_ALL", "C")
    environment.update(env or {})
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    command = [str(c) for c in command]
    with open(log, "a", encoding="utf-8", buffering=1) as output:
        output.write("\n$ " + subprocess.list2cmdline(command) + "\n")
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT, **options)
        job = _JobObject(process)
        start = time.monotonic()
        position = 0
        try:
            while process.poll() is None:
                if cancelled():
                    raise Cancelled("Cancelled")
                if time.monotonic() - start > timeout:
                    raise TimeoutError(f"{Path(command[0]).name} took longer than {timeout:.0f} s")
                if on_line:
                    position = _follow(log, position, on_line)
                time.sleep(0.25)
            if on_line:
                _follow(log, position, on_line)
            return process.returncode
        finally:
            _kill(process)
            job.close()


def _follow(log: Path, position: int, on_line: Callable[[str], None]) -> int:
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(position)
            for line in handle:
                on_line(line.rstrip())
            return handle.tell()
    except OSError:
        return position
