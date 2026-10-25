"""Drive speed on one temporary file: the Performance check's only write.

Writes ``PERF_DISK_TEST_MB`` to a new file in %TEMP%, reads it back in
``PERF_DISK_CHUNK_MB`` blocks, then reads random 4 KB blocks one at a
time for ``PERF_DISK_RANDOM_S``. Each phase stops early on a very slow
drive (``PERF_DISK_PHASE_MAX_S``) and on cancel.

On Windows the file is opened unbuffered and write-through
(``FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH``), so Windows' cache
can't flatter the figures, and delete-on-close, so it disappears even if
SysCheck is killed. Elsewhere, or if that open fails, ordinary buffered
I/O is used and the result is flagged ``unbuffered=False`` (not scored).
The file is removed in a ``finally`` either way.
"""

from __future__ import annotations

import mmap
import os
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from .. import config

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]
SampleFn = Callable[[Dict[str, object]], None]

BLOCK = 4096                    # random-read size; also the alignment used

# CreateFileW flags (winbase.h).
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_CREATE_NEW = 1
_FILE_ATTRIBUTE_TEMPORARY = 0x100
_FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
_FILE_FLAG_NO_BUFFERING = 0x20000000
_FILE_FLAG_WRITE_THROUGH = 0x80000000


def _noop(*_: object) -> None:
    pass


class _WinFile:
    """Unbuffered, write-through, delete-on-close file (Windows only).

    Unbuffered I/O needs sector-aligned buffers, sizes and offsets: the
    buffer is an anonymous mmap (page-aligned) and every size and offset
    is a multiple of 4 KB.
    """

    unbuffered = True

    def __init__(self, path: Path, buffer_size: int) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE)
        io_args = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                   ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
        k32.WriteFile.argtypes = io_args
        k32.ReadFile.argtypes = io_args
        k32.SetFilePointerEx.argtypes = (wintypes.HANDLE, ctypes.c_longlong,
                                         wintypes.LPVOID, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._ctypes, self._k32 = ctypes, k32
        self._done = wintypes.DWORD()

        handle = k32.CreateFileW(
            str(path), _GENERIC_READ | _GENERIC_WRITE, 0, None, _CREATE_NEW,
            _FILE_ATTRIBUTE_TEMPORARY | _FILE_FLAG_DELETE_ON_CLOSE
            | _FILE_FLAG_NO_BUFFERING | _FILE_FLAG_WRITE_THROUGH, None)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW failed",
                          str(path))
        self._handle = handle
        try:
            self._buf = mmap.mmap(-1, buffer_size)
            self._buf.write(os.urandom(buffer_size))  # incompressible data
            self._view = (ctypes.c_char * buffer_size).from_buffer(self._buf)
        except Exception:
            k32.CloseHandle(handle)
            raise
        self._addr = ctypes.addressof(self._view)

    def _check(self, ok: int, size: int, what: str) -> None:
        if not ok or self._done.value != size:
            raise OSError(self._ctypes.get_last_error(), f"{what} failed")

    def write(self, size: int) -> None:
        ok = self._k32.WriteFile(self._handle, self._addr, size,
                                 self._ctypes.byref(self._done), None)
        self._check(ok, size, "WriteFile")

    def read(self, size: int) -> None:
        ok = self._k32.ReadFile(self._handle, self._addr, size,
                                self._ctypes.byref(self._done), None)
        self._check(ok, size, "ReadFile")

    def seek(self, offset: int) -> None:
        if not self._k32.SetFilePointerEx(self._handle, offset, None, 0):
            raise OSError(self._ctypes.get_last_error(), "SetFilePointerEx")

    def sync(self) -> None:
        pass                            # write-through: already on the drive

    def close(self) -> None:
        self._k32.CloseHandle(self._handle)          # deletes the file
        del self._view
        self._buf.close()


class _PyFile:
    """Ordinary buffered file, for other systems or when unbuffered I/O
    isn't possible. Reads may come from the cache."""

    unbuffered = False

    def __init__(self, path: Path, buffer_size: int) -> None:
        self._f = open(path, "x+b", buffering=0)
        self._buf = memoryview(bytearray(os.urandom(buffer_size)))

    def write(self, size: int) -> None:
        self._f.write(self._buf[:size])

    def read(self, size: int) -> None:
        self._f.readinto(self._buf[:size])

    def seek(self, offset: int) -> None:
        self._f.seek(offset)

    def sync(self) -> None:
        os.fsync(self._f.fileno())

    def close(self) -> None:
        self._f.close()


def _open(path: Path, buffer_size: int, log: LogFn):
    if os.name == "nt":
        try:
            return _WinFile(path, buffer_size)
        except Exception as exc:  # noqa: BLE001
            log(f"[PERF] Unbuffered drive test unavailable ({exc}); using the "
                "cache, so reads aren't scored.")
            path.unlink(missing_ok=True)
    return _PyFile(path, buffer_size)


def measure_drive(folder: Optional[Path] = None, size_mb: Optional[int] = None,
                  should_stop: StopFn = lambda: False,
                  on_sample: SampleFn = _noop,
                  log: LogFn = _noop) -> Dict[str, object]:
    """Sequential write / read MB/s and 4 KB random reads per second on a
    temporary file in ``folder`` (default %TEMP%), which is always
    deleted afterwards."""
    folder = Path(folder or tempfile.gettempdir())
    size_mb = size_mb or config.PERF_DISK_TEST_MB
    chunk = config.PERF_DISK_CHUNK_MB * 2 ** 20
    total = size_mb * 2 ** 20 // chunk * chunk
    result: Dict[str, object] = {"ran": False, "reason": None,
                                 "folder": str(folder), "size_mb": size_mb,
                                 "unbuffered": False}
    try:
        free = shutil.disk_usage(folder).free
    except OSError as exc:
        result["reason"] = f"temporary folder not usable ({exc})"
        return result
    if free < total + config.PERF_DISK_MIN_FREE_MB * 2 ** 20:
        result["reason"] = "not enough free space for the test file"
        return result

    path = folder / f"syscheck-speed-{os.getpid()}-{int(time.time())}.tmp"
    f = None
    try:
        f = _open(path, chunk, log)
        result["unbuffered"] = f.unbuffered
        log(f"[PERF] Drive test file: {path} ({size_mb} MB, "
            f"{'unbuffered' if f.unbuffered else 'buffered'})")

        # Sequential write.
        written, start = 0, time.perf_counter()
        while written < total:
            if should_stop():
                result["reason"] = "stopped"
                return result
            f.write(chunk)
            written += chunk
            on_sample({"phase": "write", "pct": written / total * 100})
            if time.perf_counter() - start > config.PERF_DISK_PHASE_MAX_S:
                break
        f.sync()
        result["seq_write_mbps"] = round(
            written / 2 ** 20 / (time.perf_counter() - start), 1)

        # Sequential read of what was written.
        f.seek(0)
        done, start = 0, time.perf_counter()
        while done < written:
            if should_stop():
                result["reason"] = "stopped"
                return result
            f.read(chunk)
            done += chunk
            on_sample({"phase": "read", "pct": done / written * 100})
            if time.perf_counter() - start > config.PERF_DISK_PHASE_MAX_S:
                break
        result["seq_read_mbps"] = round(
            done / 2 ** 20 / (time.perf_counter() - start), 1)

        # Random 4 KB reads, one at a time.
        rng = random.Random(0)
        blocks = written // BLOCK
        reads, start = 0, time.perf_counter()
        end = start + config.PERF_DISK_RANDOM_S
        while time.perf_counter() < end:
            if should_stop():
                result["reason"] = "stopped"
                return result
            f.seek(rng.randrange(blocks) * BLOCK)
            f.read(BLOCK)
            reads += 1
            if reads % 256 == 0:
                on_sample({"phase": "random",
                           "pct": (time.perf_counter() - start)
                           / config.PERF_DISK_RANDOM_S * 100})
        elapsed = time.perf_counter() - start
        if reads:
            result["rand_read_iops"] = round(reads / elapsed)
            result["rand_read_ms"] = round(elapsed / reads * 1000, 3)
        result["ran"] = True
        return result
    except OSError as exc:
        log(f"[PERF] Drive test failed: {exc}")
        result["reason"] = f"the test file could not be written ({exc})"
        return result
    finally:
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log(f"[PERF] Could not delete {path}: {exc}")
