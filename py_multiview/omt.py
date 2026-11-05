"""Discovery of OMT sources advertised on the LAN (DNS-SD through libomt).

libomt keeps the announced sources in memory, so ``omtlib.discover()`` is a
plain function call. The FFmpeg route below is the fallback for when the
library cannot be loaded: it costs a process launch and a few seconds, and
only sees the sources announced during that window.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from . import omtlib
from .ffmpeg import creationflags

_OMT_LINE_RE = re.compile(r"^\[libomt[^\]]*\]\s+(.*)$")

# mDNS answers trickle in: give a just-started discovery a moment to fill up.
_SETTLE_STEP = 0.25


def parse_find_sources(output: str) -> tuple[list[str], bool]:
    """Extract source names from ``ffmpeg -find_sources 1 -f libomt`` output.

    Returns ``(sources, saw_header)``.
    """
    sources: list[str] = []
    in_block = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "No OMT Sources found" in line:
            return [], True
        if "OMT Sources" in line:
            in_block = True
            continue
        if not in_block:
            continue
        if "Error opening input" in line:
            break
        match = _OMT_LINE_RE.match(line)
        if match:
            name = match.group(1).strip()
            if name and set(name) != {"-"} and name not in sources:
                sources.append(name)
    return sources, in_block


def find_sources(ffmpeg: Path | None, timeout: float = 8.0) -> tuple[list[str], str | None]:
    """Return ``(sources, error)``; ``error`` is set when discovery itself failed."""
    if omtlib.available():
        return _find_sources_native(timeout), None
    return _find_sources_ffmpeg(ffmpeg, timeout)


def _find_sources_native(timeout: float) -> list[str]:
    """Poll libomt's in-memory list until it stops growing."""
    deadline = time.monotonic() + max(_SETTLE_STEP, min(timeout, 4.0))
    sources: list[str] = []
    while True:
        found = omtlib.discover()
        if len(found) > len(sources):
            sources = found
        elif sources:
            break  # nothing new since last poll
        if time.monotonic() >= deadline:
            break
        time.sleep(_SETTLE_STEP)
    return sources


def _find_sources_ffmpeg(ffmpeg: Path | None, timeout: float) -> tuple[list[str], str | None]:
    if ffmpeg is None or not ffmpeg.exists():
        return [], "Aucun FFmpeg compatible OMT trouvé (bin/omt/)."
    args = [
        str(ffmpeg), "-hide_banner", "-loglevel", "info", "-nostdin",
        "-find_sources", "1", "-f", "libomt", "-i", "",
        "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(1.0, timeout),
            check=False,
            creationflags=creationflags(),
        )
        output = (proc.stdout or b"").decode("utf-8", errors="replace")
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # Keep whatever was printed before the timeout.
        output = (exc.stdout or b"").decode("utf-8", errors="replace")
        returncode = None
    except OSError as exc:
        return [], f"Impossible de lancer ffmpeg: {exc}"

    sources, saw_header = parse_find_sources(output)
    if sources or saw_header:
        return sources, None
    if returncode is None:
        return [], f"Aucune réponse après {timeout:.0f} s."
    tail = "\n".join(output.strip().splitlines()[-4:]) or "aucun détail"
    if "libomt" in tail.lower() and "unknown" in tail.lower():
        return [], "Cette build de FFmpeg ne supporte pas libomt."
    return [], f"Découverte OMT échouée :\n{tail}"
