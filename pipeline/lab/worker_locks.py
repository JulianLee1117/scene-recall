"""Role exclusivity with a lifetime guard against legacy all-job workers."""
from contextlib import contextmanager
import errno
import os

from filelock import FileLock, Timeout

from pipeline.lab.job_roles import kinds_for_role


def _windows_shared_lock(fd):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    handle, overlap = msvcrt.get_osfhandle(fd), Overlapped()
    # FAIL_IMMEDIATELY without EXCLUSIVE_LOCK takes a shared byte-range lock.
    # The installed FileLock and legacy msvcrt.locking use this same byte 0.
    if not kernel.LockFileEx(handle, 1, 0, 1, 0, ctypes.byref(overlap)):
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EAGAIN, "Legacy worker owns the state directory")
        raise ctypes.WinError(error)

    def unlock():
        if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap)):
            raise ctypes.WinError(ctypes.get_last_error())

    return unlock


@contextmanager
def _shared_legacy_lock(path):
    # Keep the inode/file identity stable. A role process itself owns the guard,
    # so launcher death cannot expose its still-draining job to legacy recovery.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    unlock = None
    try:
        try:
            if os.name == "nt":
                unlock = _windows_shared_lock(fd)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                unlock = lambda: fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise Timeout(str(path)) from exc
            raise
        yield
    finally:
        try:
            if unlock is not None:
                unlock()
        finally:
            os.close(fd)


@contextmanager
def worker_lock(root, role="all"):
    kinds_for_role(role)
    root.mkdir(parents=True, exist_ok=True)
    legacy = root / ".worker.lock"
    if role == "all":
        with FileLock(legacy, timeout=0, preserve_lock_file=True):
            yield
    else:
        with _shared_legacy_lock(legacy):
            with FileLock(root / f".worker-{role}.lock", timeout=0, preserve_lock_file=True):
                yield
