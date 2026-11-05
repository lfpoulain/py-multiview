"""Audio sources for the OMT sender.

Two kinds of sources:

* ``loopback`` — what the PC is playing (WASAPI loopback of the default output,
  through PyAudioWPatch), streamed as raw PCM on ffmpeg's stdin;
* ``dshow:<name>`` — a DirectShow capture device (microphone, sound card,
  virtual cable, vMix Audio…) opened by ffmpeg itself.

WASAPI loopback delivers nothing while the PC is silent, which would stall
ffmpeg. The feeder therefore writes at a steady real-time pace and fills the
gaps with silence.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .ffmpeg import creationflags

log = logging.getLogger(__name__)

LOOPBACK = "loopback"
DSHOW_PREFIX = "dshow:"

_DSHOW_AUDIO_RE = re.compile(r'"(.+)"\s+\(audio\)')


def list_dshow_audio(ffmpeg: Path | None) -> list[str]:
    """Names of the DirectShow audio capture devices."""
    if ffmpeg is None:
        return []
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Liste des entrées audio impossible: %s", exc)
        return []
    return parse_dshow_audio(proc.stdout.decode("utf-8", errors="replace"))


def parse_dshow_audio(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        match = _DSHOW_AUDIO_RE.search(line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


@dataclass(frozen=True)
class LoopbackDevice:
    index: int
    name: str
    rate: int
    channels: int


@lru_cache(maxsize=1)
def _pyaudio():
    import pyaudiowpatch

    return pyaudiowpatch


def loopback_device() -> LoopbackDevice | None:
    """Loopback endpoint of the default playback device, or None if unavailable."""
    try:
        pa = _pyaudio()
    except ImportError:
        return None
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for dev in p.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return LoopbackDevice(
                    int(dev["index"]), str(speakers["name"]), int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
                )
    except Exception:
        log.exception("Périphérique loopback WASAPI introuvable")
    finally:
        p.terminate()
    return None


class LoopbackFeeder:
    """Streams the PC's audio as s16le PCM to a writable pipe, in real time.

    Captured samples are forwarded as soon as WASAPI delivers them: the wall
    clock only decides *when to invent silence*, never how much real audio to
    hand over. Pacing the pipe on the wall clock instead would make every late
    callback inject silence, pile up the samples that arrive just after, and
    end up discarding them at the buffer cap — a click each time.
    """

    CHUNK = 0.01  # seconds
    CUSHION = 0.06  # seconds of play-out kept in hand before padding a gap

    def __init__(self, device: LoopbackDevice) -> None:
        self.device = device
        self.frame_bytes = 2 * device.channels
        self._buffer: deque[bytes] = deque()
        self._buffered = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def __call__(self, pipe):
        """Start feeding ``pipe`` (one fresh feed per process launch); returns its stop function."""
        feed = LoopbackFeeder(self.device)
        threading.Thread(target=feed._run, args=(pipe,), daemon=True, name="loopback-feed").start()
        return feed._stop.set

    def _on_audio(self, data, _frames, _time_info, _status):
        with self._lock:
            self._buffer.append(data)
            self._buffered += len(data)
            # Never keep more than ~0.5 s: latency beats completeness.
            limit = int(self.device.rate * 0.5) * self.frame_bytes
            while self._buffered > limit and self._buffer:
                self._buffered -= len(self._buffer.popleft())
        return None, _pyaudio().paContinue

    def _drain(self) -> bytes:
        """Everything captured so far, without padding."""
        with self._lock:
            out = b"".join(self._buffer)
            self._buffer.clear()
            self._buffered = 0
        return out

    def _run(self, pipe) -> None:
        pa = _pyaudio()
        p = pa.PyAudio()
        stream = None
        try:
            stream = p.open(
                format=pa.paInt16,
                channels=self.device.channels,
                rate=self.device.rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=int(self.device.rate * self.CHUNK),
                stream_callback=self._on_audio,
            )
            start = time.perf_counter()
            cushion = int(self.device.rate * self.CUSHION)
            written = 0  # frames handed to ffmpeg
            while not self._stop.is_set():
                data = self._drain()
                if data:
                    pipe.write(data)
                    pipe.flush()
                    written += len(data) // self.frame_bytes
                else:
                    # WASAPI loopback stays silent while the PC plays nothing:
                    # keep ffmpeg fed, but leave a cushion so the samples that
                    # resume right after are not pushed back by our own silence.
                    due = int((time.perf_counter() - start) * self.device.rate)
                    gap = due - written - cushion
                    if gap > 0:
                        pipe.write(b"\x00" * (gap * self.frame_bytes))
                        pipe.flush()
                        written += gap
                time.sleep(self.CHUNK / 2)
        except (BrokenPipeError, OSError, ValueError):
            pass  # ffmpeg exited
        except Exception:
            log.exception("Capture audio loopback interrompue")
        finally:
            if stream is not None:
                stream.close()
            p.terminate()
            try:
                pipe.close()
            except OSError:
                pass
