"""FFmpeg toolchain discovery and command-line builders.

Two kinds of FFmpeg builds are needed and none does everything:

* an **OMT** build (``--enable-libomt``) to publish/receive Open Media Transport,
* a **standard** build (``--enable-libsrt`` + GPU decoders) for SRT/UDP playback
  and SRT -> multicast routing.

Each candidate binary is probed once for its real capabilities, and every
launch picks a toolchain that actually supports what it needs.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .paths import APP_DIR, BIN_DIR, OMT_BIN_DIR, STANDARD_BIN_DIR

log = logging.getLogger(__name__)

EXE = ".exe" if sys.platform == "win32" else ""

# Decoder family -> ffplay ``-vcodec`` value (H.264 only: ffplay cannot pick per codec).
GPU_DECODERS = {
    "nvidia": "h264_cuvid",
    "intel": "h264_qsv",
    "amd": "h264_amf",
}


def creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass(frozen=True)
class Toolchain:
    label: str
    directory: str
    ffmpeg: Path | None
    ffplay: Path | None
    version: str = ""
    srt: bool = False
    omt: bool = False
    decoders: frozenset[str] = field(default_factory=frozenset)

    def describe(self) -> str:
        caps = [name for name, ok in (("SRT", self.srt), ("OMT", self.omt)) if ok]
        caps += sorted(k.upper() for k in self.decoders)
        return f"{self.label} — FFmpeg {self.version or '?'} [{', '.join(caps) or 'aucune capacité'}]"


def _run(args: list[str], timeout: float = 10.0) -> str:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            creationflags=creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Sonde %s échouée: %s", args[:2], exc)
        return ""
    return (proc.stdout or b"").decode("utf-8", errors="replace")


_VERSION_RE = re.compile(r"version\s+(\S+)")


def probe_toolchain(label: str, ffmpeg: Path | None, ffplay: Path | None) -> Toolchain:
    """Ask the binaries what they really support (protocols, muxers, decoders)."""
    probe_bin = ffmpeg or ffplay
    if probe_bin is None:
        return Toolchain(label, "", ffmpeg, ffplay)
    base = [str(probe_bin), "-hide_banner"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        version_f = pool.submit(_run, [str(probe_bin), "-version"])
        protocols_f = pool.submit(_run, base + ["-protocols"])
        formats_f = pool.submit(_run, base + ["-formats"])
        decoders_f = pool.submit(_run, base + ["-decoders"])
    match = _VERSION_RE.search(version_f.result().splitlines()[0] if version_f.result() else "")
    protocols = set(protocols_f.result().split())
    formats = formats_f.result()
    decoders_out = decoders_f.result()
    decoders = frozenset(
        family for family, codec in GPU_DECODERS.items() if re.search(rf"\s{codec}\s", decoders_out)
    )
    return Toolchain(
        label=label,
        directory=str(probe_bin.parent),
        ffmpeg=ffmpeg,
        ffplay=ffplay,
        version=match.group(1) if match else "",
        srt="srt" in protocols,
        omt=bool(re.search(r"\slibomt\s", formats)),
        decoders=decoders,
    )


def _candidates() -> list[tuple[str, Path | None, Path | None]]:
    found: list[tuple[str, Path | None, Path | None]] = []
    seen: set[str] = set()

    def add(label: str, directory: Path) -> None:
        ffmpeg = directory / f"ffmpeg{EXE}"
        ffplay = directory / f"ffplay{EXE}"
        key = str(directory.resolve()).lower() if directory.exists() else ""
        if not key or key in seen or not (ffmpeg.exists() or ffplay.exists()):
            return
        seen.add(key)
        found.append((label, ffmpeg if ffmpeg.exists() else None, ffplay if ffplay.exists() else None))

    add("OMT (intégré)", OMT_BIN_DIR)
    add("Standard (intégré)", STANDARD_BIN_DIR)
    add("bin/", BIN_DIR)
    add("Standard (à côté de l'exe)", APP_DIR / "bin" / "standard")

    ffmpeg_sys = shutil.which("ffmpeg")
    ffplay_sys = shutil.which("ffplay")
    if ffmpeg_sys or ffplay_sys:
        ffmpeg_p = Path(ffmpeg_sys) if ffmpeg_sys else None
        ffplay_p = Path(ffplay_sys) if ffplay_sys else None
        key = str((ffmpeg_p or ffplay_p).parent.resolve()).lower()
        if key not in seen:
            found.append(("Système (PATH)", ffmpeg_p, ffplay_p))
    return found


class ToolchainRegistry:
    """Lazily probes available FFmpeg builds and picks the right one per task."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._toolchains: list[Toolchain] | None = None

    def refresh(self) -> list[Toolchain]:
        candidates = _candidates()
        with ThreadPoolExecutor(max_workers=max(1, len(candidates))) as pool:
            toolchains = list(pool.map(lambda c: probe_toolchain(*c), candidates))
        for tc in toolchains:
            log.info("Toolchain: %s (%s)", tc.describe(), tc.directory)
        with self._lock:
            self._toolchains = toolchains
        return toolchains

    @property
    def toolchains(self) -> list[Toolchain]:
        with self._lock:
            cached = self._toolchains
        return cached if cached is not None else self.refresh()

    def player_for(self, source: str) -> Toolchain | None:
        """ffplay able to read ``source`` ("srt", "udp" or "omt")."""
        players = [t for t in self.toolchains if t.ffplay]
        if source == "omt":
            return next((t for t in players if t.omt), None)
        if source == "srt":
            return next((t for t in players if t.srt), None)
        # Plain UDP: any build works, prefer a standard one (GPU decoders).
        return next((t for t in players if t.srt), players[0] if players else None)

    def ffmpeg_for(self, need: str) -> Toolchain | None:
        encoders = [t for t in self.toolchains if t.ffmpeg]
        if need == "omt":
            return next((t for t in encoders if t.omt), None)
        return next((t for t in encoders if t.srt), None)

    def gpu_decoders(self) -> frozenset[str]:
        tc = self.player_for("srt")
        return tc.decoders if tc else frozenset()


# ---------------------------------------------------------------------------
# Command builders (pure functions, unit-tested)
# ---------------------------------------------------------------------------


def srt_listener_url(port: int, latency_ms: int) -> str:
    # libsrt expects the latency in microseconds.
    return f"srt://0.0.0.0:{int(port)}?mode=listener&latency={max(0, int(latency_ms)) * 1000}&transtype=live"


def udp_input_url(addr: str, port: int, localaddr: str = "") -> str:
    # Large FIFO + non-fatal overruns: a burst must not kill the player.
    url = f"udp://@{addr}:{int(port)}?fifo_size=100000&overrun_nonfatal=1"
    # Join the group on the same interface the route sends on (multi-NIC hosts:
    # VPN, Hyper-V... would otherwise pick an arbitrary one).
    return url + (f"&localaddr={localaddr}" if localaddr else "")


def video_filter(mode: str, rotate: int, width: int, height: int) -> str | None:
    """Filter chain for the display mode.

    Final scaling is left to ffplay's GPU renderer (it letterboxes to the
    window), so "fit" costs no CPU; "fill" only crops and "stretch" only
    changes the display aspect ratio.
    """
    filters: list[str] = []
    if rotate == 90:
        filters.append("transpose=clock")
    elif rotate == 270:
        filters.append("transpose=cclock")
    elif rotate == 180:
        filters.append("hflip,vflip")

    if mode == "fill":
        filters.append(f"crop='min(iw,ih*{width}/{height})':'min(ih,iw*{height}/{width})',setsar=1")
    elif mode == "stretch":
        filters.append(f"setdar={width}/{height}")
    return ",".join(filters) or None


def build_player_command(
    ffplay: Path,
    *,
    input_args: list[str],
    display: dict,
    mode: str = "fit",
    rotate: int = 0,
    mute: bool = False,
    decoder: str | None = None,
    title: str = "py-multiview",
    live_udp: bool = False,
    raw_video: bool = False,
) -> list[str]:
    args = [str(ffplay), "-hide_banner", "-loglevel", "warning", "-stats"]
    if decoder:
        args += ["-vcodec", decoder]
    args += [
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "500000",
        "-analyzeduration", "500000",
        "-window_title", title,
        "-left", str(int(display["x"])),
        "-top", str(int(display["y"])),
        "-x", str(int(display["width"])),
        "-y", str(int(display["height"])),
        "-noborder",
        "-fs",
        # Live input: when the sender goes away, exit so the supervisor
        # re-arms a fresh listener instead of freezing on the last frame.
        "-autoexit",
    ]
    vf = video_filter(mode, rotate, int(display["width"]), int(display["height"]))
    if vf:
        args += ["-vf", vf]
    if live_udp:
        args += ["-framedrop", "-sync", "ext"]
    if raw_video:
        # OMT carries uncompressed video: one 1080p frame is ~4 MB, one 2160p
        # frame ~16 MB, while ffplay caps all its packet queues at 15 MB
        # combined. The reader thread then pauses after nearly every frame and
        # the audio queue runs dry — continuous crackling. "-infbuf" lifts the
        # cap so audio keeps flowing, "-framedrop" sheds late frames instead of
        # letting video push the audio clock around.
        args += ["-infbuf", "-framedrop", "-sync", "audio"]
    if mute:
        args.append("-an")
    return args + input_args


def build_floating_command(
    ffplay: Path,
    source: str,
    width: int = 640,
    height: int = 360,
    x: int = 80,
    y: int = 80,
    volume: int = 0,
    on_top: bool = True,
) -> list[str]:
    """A small always-on-top window showing an OMT return, with its own volume.

    Unlike a stream card this never takes a whole screen: it is meant to sit
    over the desktop while working.
    """
    args = [
        str(ffplay), "-hide_banner", "-loglevel", "warning", "-stats",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "500000", "-analyzeduration", "500000",
        "-window_title", f"Retour — {source}",
        "-left", str(int(x)), "-top", str(int(y)),
        "-x", str(max(160, int(width))), "-y", str(max(90, int(height))),
        "-volume", str(max(0, min(100, int(volume)))),
        # Uncompressed frames: same packet-queue precaution as a stream card.
        "-infbuf", "-framedrop",
        "-autoexit",
    ]
    if on_top:
        args.append("-alwaysontop")
    return [*args, "-f", "libomt", "-i", source]


def build_monitor_command(ffplay: Path, source: str, volume: int = 100) -> list[str]:
    """Audio-only playback of an OMT source: the master return, no window.

    "-vn" makes ffplay drop the video packets at the demuxer, so the
    uncompressed frames never reach a queue and never delay the sound.
    """
    return [
        str(ffplay), "-hide_banner", "-loglevel", "warning", "-stats",
        "-nodisp", "-vn", "-sn",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "500000", "-analyzeduration", "500000",
        "-sync", "audio",
        "-volume", str(int(volume)),
        # Live input: leave when the source goes away so the supervisor re-arms.
        "-autoexit",
        "-f", "libomt", "-i", source,
    ]


def build_route_command(ffmpeg: Path, route: dict, localaddr: str = "") -> list[str]:
    out_url = (
        f"udp://{route['multicastAddr']}:{int(route['multicastPort'])}"
        f"?pkt_size={int(route['pktSize'])}&ttl={int(route['ttl'])}"
    )
    if localaddr:
        out_url += f"&localaddr={localaddr}"
    return [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-nostats", "-progress", "pipe:1", "-stats_period", "0.5",
        # No "-fflags nobuffer" here: it drops the packets read while probing,
        # which a pure remux needs to keep.
        "-probesize", "1000000", "-analyzeduration", "1000000",
        "-i", srt_listener_url(route["inputPort"], route["inputLatency"]),
        "-map", "0", "-c", "copy",
        "-flush_packets", "1",
        "-mpegts_flags", "+resend_headers",
        "-f", "mpegts", out_url,
    ]


def pipe_audio_input_args(rate: int, channels: int) -> list[str]:
    """Raw float PCM written on stdin by audio.PipeFeeder.

    Raw PCM has nothing to probe, yet FFmpeg would read five seconds of it
    before sending the first picture: the probe is cut to nothing.
    """
    return [
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "f32le", "-ar", str(int(rate)), "-ac", str(int(channels)), "-i", "pipe:0",
    ]


def dshow_audio_input_args(device: str) -> list[str]:
    return ["-f", "dshow", "-audio_buffer_size", "50", "-i", f"audio={device}"]


def build_sender_command(ffmpeg: Path, display: dict, sender: dict, audio_input: list[str] | None = None) -> list[str]:
    if audio_input:
        audio = [
            "-thread_queue_size", "1024", *audio_input,
            "-map", "0:v:0", "-map", "1:a:0",
            # OMT carries 48 kHz audio. Capture timestamps jitter by a few
            # milliseconds (dshow) and drift against the system clock, so the
            # resampler stretches smoothly and only cuts/pads past 100 ms of
            # real drift. A higher "async" would re-time on every jitter spike
            # and audibly warble the sound.
            "-af", "aresample=async=1:min_hard_comp=0.100:first_pts=0",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            # Never hold an audio packet back waiting for the matching video
            # frame: gdigrab stalls would otherwise release the sound in bursts.
            "-max_interleave_delta", "0",
        ]
    else:
        audio = ["-map", "0:v:0", "-an"]
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "warning",
        "-nostats", "-progress", "pipe:1", "-stats_period", "0.5",
        "-thread_queue_size", "512",
        "-f", "gdigrab",
        "-framerate", str(int(sender["fps"])),
        "-offset_x", str(int(display["x"])),
        "-offset_y", str(int(display["y"])),
        "-video_size", f"{int(display['width'])}x{int(display['height'])}",
        "-draw_mouse", "1",
        "-i", "desktop",
        *audio,
        "-vf", f"format={sender['pixelFormat']}",
        "-c:v", "wrapped_avframe",
        # Constant cadence: gdigrab jitter otherwise yields duplicate timestamps.
        "-fps_mode", "cfr",
        "-clock_output", "1" if sender.get("clockOutput") else "0",
        "-reference_level", f"{float(sender.get('referenceLevel', 1.0)):.3f}",
        "-f", "libomt", str(sender["name"]),
    ]


# SDL tweaks for the ffplay windows: physical-pixel coordinates (matches the
# Win32 display geometry on scaled screens) and no auto-minimise when the
# operator clicks on another screen.
PLAYER_ENV = {
    "SDL_WINDOWS_DPI_AWARENESS": "permonitorv2",
    "SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS": "0",
}
