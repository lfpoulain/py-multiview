"""Supervision of the ffplay/ffmpeg child processes.

Every child is registered under a key ("player:<id>", "route:<id>", "sender").
The supervisor restarts children that die (with back-off), tracks whether
media is actually flowing (from ffplay ``-stats`` / ffmpeg ``-progress``
output) and, on Windows, binds them to a Job Object so they are killed with
the application even after a crash.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import IO

from .ffmpeg import creationflags

log = logging.getLogger(__name__)

ACTIVITY_TIMEOUT = 2.5  # seconds without progress before media is considered lost
STARTUP_GRACE = 1.5  # seconds during which a fresh process is "starting"
INITIAL_BACKOFF = 0.5
MAX_BACKOFF = 10.0
STABLE_UPTIME = 15.0  # a process that ran this long resets its back-off


class Status(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    WAITING = "waiting"  # running, no media yet (e.g. SRT listener waiting for a caller)
    ACTIVE = "active"  # running and media is flowing
    RETRYING = "retrying"  # exited, will be relaunched
    ERROR = "error"  # cannot run (configuration / missing binary)


@dataclass
class ProcessSpec:
    args: list[str]
    label: str
    progress: str = "none"  # "ffplay" (stderr -stats) | "ffmpeg" (stdout -progress) | "none"
    env: dict[str, str] = field(default_factory=dict)
    graceful_quit: bool = False  # send "q" on stdin before killing (ffmpeg)
    # Called with the child's stdin right after launch; returns a function stopping the feed.
    stdin_feeder: Callable[[IO[bytes]], Callable[[], None]] | None = None

    def signature(self) -> tuple:
        return (tuple(self.args), tuple(sorted(self.env.items())))


# "  12.34 M-V:  0.001 fd=   0 aq=    0KB vq=  12KB sq=    0B"
_FFPLAY_STATUS_RE = re.compile(r"^\s*(nan|-?\d+(?:\.\d+)?)\s+(?:[AMV]-[AV])?\s*:.*\bfd=")


@dataclass
class ProcessInfo:
    status: Status
    detail: str = ""
    pid: int | None = None
    restarts: int = 0
    uptime: float = 0.0


class _JobObject:
    """Windows Job Object with KILL_ON_JOB_CLOSE: children die with us."""

    def __init__(self) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            self._create()
        except Exception:
            log.exception("Job Object indisponible: les processus enfants ne seront pas liés")
            self.handle = None

    def _create(self) -> None:
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32 = kernel32

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle

    def assign(self, proc: subprocess.Popen) -> None:
        if not self.handle:
            return
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, int(proc._handle)):
                log.warning("AssignProcessToJobObject a échoué (pid %s)", proc.pid)
        except Exception:
            log.exception("AssignProcessToJobObject")


class ManagedProcess:
    def __init__(self, key: str, spec: ProcessSpec, job: _JobObject, log_buffer: deque[str]) -> None:
        self.key = key
        self.spec = spec
        self._job = job
        self.proc: subprocess.Popen | None = None
        self.log = log_buffer
        self.started_at = 0.0
        self.last_activity = 0.0
        self._last_progress: str | None = None
        self.restarts = 0
        self.next_retry_at: float | None = None
        self.exit_code: int | None = None
        self.launch_error: str | None = None
        self._backoff = INITIAL_BACKOFF
        self._stop_feed: Callable[[], None] | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        self.next_retry_at = None
        self.exit_code = None
        self.launch_error = None
        self.last_activity = 0.0
        self._last_progress = None
        env = None
        if self.spec.env:
            env = dict(os.environ)
            env.update(self.spec.env)
        self._append(f"$ {subprocess.list2cmdline(self.spec.args)}")
        try:
            self.proc = subprocess.Popen(
                self.spec.args,
                stdin=subprocess.PIPE if self.spec.graceful_quit or self.spec.stdin_feeder else subprocess.DEVNULL,
                stdout=subprocess.PIPE if self.spec.progress == "ffmpeg" else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=creationflags(),
            )
        except OSError as exc:
            self.proc = None
            self.launch_error = f"Lancement impossible: {exc}"
            self._append(self.launch_error)
            log.error("[%s] %s", self.key, self.launch_error)
            return False
        self._job.assign(self.proc)
        self.started_at = time.monotonic()
        log.info("[%s] démarré (pid %s): %s", self.key, self.proc.pid, subprocess.list2cmdline(self.spec.args))
        threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True).start()
        if self.spec.progress == "ffmpeg":
            threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()
        if self.spec.stdin_feeder:
            self._stop_feed = self.spec.stdin_feeder(self.proc.stdin)
        return True

    def _end_feed(self) -> None:
        if self._stop_feed is not None:
            self._stop_feed()
            self._stop_feed = None

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        self.next_retry_at = None
        self._end_feed()
        if proc is None or proc.poll() is not None:
            return
        log.info("[%s] arrêt (pid %s)", self.key, proc.pid)
        if self.spec.graceful_quit and proc.stdin and not self.spec.stdin_feeder:
            # Let ffmpeg/libomt close the stream cleanly, off the UI thread.
            def _quit() -> None:
                try:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                _kill(proc)

            threading.Thread(target=_quit, daemon=True).start()
            return
        _kill(proc)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def uptime(self) -> float:
        return time.monotonic() - self.started_at if self.alive() else 0.0

    # -- output parsing ----------------------------------------------------

    def _append(self, line: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {line}")

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            buffer = b""
            while True:
                chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                if not chunk:
                    break
                buffer += chunk
                # ffplay terminates its status line with "\r", regular logs with "\n".
                parts = re.split(rb"[\r\n]", buffer)
                buffer = parts.pop()
                for raw in parts:
                    self._on_stderr_line(raw.decode("utf-8", errors="replace").rstrip())
            if buffer:
                self._on_stderr_line(buffer.decode("utf-8", errors="replace").rstrip())
        except Exception:
            pass

    def _on_stderr_line(self, line: str) -> None:
        if not line.strip():
            return
        if self.spec.progress == "ffplay":
            match = _FFPLAY_STATUS_RE.match(line)
            if match:
                clock = match.group(1)
                if clock != "nan" and clock != self._last_progress:
                    self._last_progress = clock
                    self.last_activity = time.monotonic()
                return
        self._append(line)

    def _read_progress(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith(("total_size=", "out_time_us=")):
                    if line != self._last_progress and not line.endswith(("=0", "=N/A")):
                        self._last_progress = line
                        self.last_activity = time.monotonic()
        except Exception:
            pass

    def last_error(self) -> str:
        """Last line written by the child itself (not our own annotations)."""
        for line in reversed(self.log):
            text = line.split("  ", 1)[-1]
            if not text.startswith(("$ ", "Processus terminé")):
                return text
        return ""

    # -- status ------------------------------------------------------------

    def info(self, now: float) -> ProcessInfo:
        pid = self.proc.pid if self.alive() else None
        if self.launch_error:
            return ProcessInfo(Status.ERROR, self.launch_error, restarts=self.restarts)
        if self.alive():
            if self.last_activity and now - self.last_activity < ACTIVITY_TIMEOUT:
                status = Status.ACTIVE
            elif now - self.started_at < STARTUP_GRACE:
                status = Status.STARTING
            else:
                status = Status.WAITING
            return ProcessInfo(status, pid=pid, restarts=self.restarts, uptime=now - self.started_at)
        if self.next_retry_at is not None:
            if self.exit_code == 0:
                detail = "Flux interrompu par la source, réarmement…"
            else:
                delay = max(0, round(self.next_retry_at - now))
                detail = f"Relance dans {delay} s" if delay else "Relance…"
                err = self.last_error()
                if err:
                    detail += f" — {err}"
            return ProcessInfo(Status.RETRYING, detail, restarts=self.restarts)
        return ProcessInfo(Status.STOPPED, restarts=self.restarts)

    def watchdog(self, now: float) -> None:
        """Schedule / perform a relaunch when the process died on its own."""
        if self.launch_error:
            return
        if self.proc is not None and self.proc.poll() is not None:
            self.exit_code = self.proc.returncode
            uptime = now - self.started_at
            self._append(f"Processus terminé (code {self.exit_code}) après {uptime:.1f} s")
            log.warning("[%s] terminé (code %s) après %.1f s", self.key, self.exit_code, uptime)
            self.proc = None
            self._end_feed()
            if uptime >= STABLE_UPTIME:
                self._backoff = INITIAL_BACKOFF
            self.next_retry_at = now + self._backoff
            self._backoff = min(self._backoff * 2, MAX_BACKOFF)
        elif self.proc is None and self.next_retry_at is not None and now >= self.next_retry_at:
            self.restarts += 1
            self.start()


def _kill(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        # Tree kill: covers launcher shims (e.g. Chocolatey) that spawn the real binary.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags(),
            check=False,
        )
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        log.warning("Le processus %s ne s'est pas terminé", proc.pid)


class Supervisor:
    def __init__(self) -> None:
        self._job = _JobObject()
        self._items: dict[str, ManagedProcess] = {}
        # Logs outlive the processes so they can still be read after a stop.
        self._logs: dict[str, deque[str]] = {}

    def ensure(self, key: str, spec: ProcessSpec) -> ManagedProcess:
        """Run ``spec`` under ``key``; restart only if the command changed."""
        current = self._items.get(key)
        if current is not None and current.spec.signature() == spec.signature() and not current.launch_error:
            current.spec = spec
            return current
        if current is not None:
            current.stop()
        item = ManagedProcess(key, spec, self._job, self._logs.setdefault(key, deque(maxlen=300)))
        if current is not None:
            item.restarts = current.restarts
        self._items[key] = item
        item.start()
        return item

    def stop(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            item.stop()

    def stop_prefix(self, prefix: str) -> None:
        for key in [k for k in self._items if k.startswith(prefix)]:
            self.stop(key)

    def stop_all(self) -> None:
        for key in list(self._items):
            self.stop(key)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._items if k.startswith(prefix)]

    def get(self, key: str) -> ManagedProcess | None:
        return self._items.get(key)

    def log_lines(self, key: str) -> list[str]:
        return list(self._logs.get(key, ()))

    def clear_log(self, key: str) -> None:
        buffer = self._logs.get(key)
        if buffer is not None:
            buffer.clear()

    def info(self, key: str) -> ProcessInfo:
        item = self._items.get(key)
        return item.info(time.monotonic()) if item else ProcessInfo(Status.STOPPED)

    def tick(self) -> None:
        now = time.monotonic()
        for item in list(self._items.values()):
            item.watchdog(now)
