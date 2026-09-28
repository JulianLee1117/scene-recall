"""Opt-in local worker reloads, always between durable jobs."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path


RELOAD_EXIT_CODE = 75
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint(root=SOURCE_ROOT):
    """Hash runtime Python only; never walk tests, assets or environment trees."""
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and
                         name not in {"tests", "assets", "__pycache__", "node_modules"})
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix == ".py" and not path.is_symlink():
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
    return digest.hexdigest()


def source_changed(original):
    try:
        return source_fingerprint() != original
    except OSError:
        return True  # A source file may be between removal and atomic replacement.


def stable_sources(stop, *, different_from=None):
    """Wait for one readable source snapshot to remain unchanged for a second."""
    previous, since = None, 0.0
    while not stop.is_set():
        try:
            current = source_fingerprint()
        except OSError:
            current = None
        now = time.monotonic()
        if current != previous:
            previous, since = current, now
        if current is not None and current != different_from and now - since >= 1.0:
            return current
        stop.wait(0.25)
    return None


def _wait_for_windows_pipe_close(stop):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    # A blocking CRT read holds stdin's descriptor lock on Windows. Imports
    # such as NumPy can query isatty on that descriptor and deadlock startup.
    # Peek at the underlying anonymous pipe without acquiring that read lock.
    handle = msvcrt.get_osfhandle(0)
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    while not stop.is_set() and peek(handle, None, 0, None, None, None):
        stop.wait(0.25)


def watch_parent(stop):
    """EOF requests a graceful stop even when the Windows parent disappears."""
    def wait_for_close():
        try:
            if os.name == "nt":
                _wait_for_windows_pipe_close(stop)
            else:
                os.read(0, 1)
        except OSError:
            pass
        stop.set()

    threading.Thread(target=wait_for_close, name="worker-parent", daemon=True).start()


def supervise(command, stop, *, reload=True, role=None):
    """Keep a launcher alive across code reloads and failed startup imports."""
    prefix = f"[{role}] " if role else ""
    if reload:
        print(f"{prefix}Lab worker reload enabled: {SOURCE_ROOT} (runtime Python only)", flush=True)
    failed_sources = None
    while not stop.is_set():
        sources = stable_sources(stop, different_from=failed_sources) if reload else None
        if stop.is_set() or (reload and sources is None):
            break
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            # stdout/stderr remain visible in the caller's terminal or log.
            child = subprocess.Popen(command, stdin=subprocess.PIPE, **options)
        except OSError as exc:
            print(f"{prefix}Cannot start Lab worker: {exc}." +
                  (" Waiting for the next Python source change." if reload else ""), flush=True)
            if not reload:
                return 1
            failed_sources = sources
            continue
        print(f"{prefix}Lab worker process started: PID {child.pid}", flush=True)
        stopping = False
        try:
            while True:
                if stop.is_set() and not stopping:
                    print(f"{prefix}Stopping Lab worker after its active job; no further jobs will be claimed.", flush=True)
                    child.stdin.close()
                    stopping = True
                try:
                    code = child.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            child.stdin.close()
        if stop.is_set():
            return 0
        if code == 0:
            return code
        if not reload:
            print(f"{prefix}Lab worker exited with code {code}; queued jobs remain saved.", flush=True)
            return code
        if code == RELOAD_EXIT_CODE:
            failed_sources = None
        else:
            print(f"{prefix}Lab worker exited with code {code}; queued jobs remain saved. "
                  "Waiting for the next Python source change before retrying.", flush=True)
            failed_sources = sources
    return 0


def supervise_roles(commands, stop, *, reload=False):
    """One launcher, two independent children; one failed lane cannot stop the other."""
    results = {}

    def run(role, command):
        try:
            results[role] = supervise(command, stop, reload=reload, role=role)
        except Exception as exc:
            print(f"[{role}] Worker launcher failed: {exc}; queued jobs remain saved.", flush=True)
            results[role] = 1

    threads = [threading.Thread(target=run, args=(role, command), name=f"worker-{role}")
               for role, command in commands.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return max(results.values(), default=0)
