"""Audio sources for the OMT sender.

Two kinds of sources, named in the configuration as:

* ``loopback`` — what the PC is playing (WASAPI loopback of the default
  output, through PyAudioWPatch);
* ``in:<name>`` — a capture endpoint: microphone, sound card, virtual cable,
  vMix Audio… (``dshow:<name>``, written by older versions, names the same
  hardware and is matched by name).

Which half of this module is used depends on the sender:

* the **native** sender (libomt) captures float samples itself with
  ``FloatRecorder`` and publishes them untouched;
* the **FFmpeg** fallback opens DirectShow devices by name, and needs
  ``LoopbackFeeder`` to push the PC's sound onto ffmpeg's stdin, because
  FFmpeg cannot capture WASAPI loopback on its own.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
import time
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .ffmpeg import creationflags

log = logging.getLogger(__name__)

LOOPBACK = "loopback"
DSHOW_PREFIX = "dshow:"  # written by versions that used the FFmpeg sender
INPUT_PREFIX = "in:"
PROGRAM_PREFIX = "app:"  # app:<exe>  — only that program's sound
EXCEPT_PREFIX = "notapp:"  # notapp:<exe> — the PC's sound except that program

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


@dataclass(frozen=True)
class InputDevice:
    """Any capture endpoint usable by the native sender: loopback or input."""

    index: int
    name: str
    rate: int
    channels: int
    loopback: bool = False


@lru_cache(maxsize=1)
def pyaudio_module():
    import pyaudiowpatch

    return pyaudiowpatch


def list_input_devices() -> list[InputDevice]:
    """WASAPI capture endpoints: microphones, line inputs, virtual cables…

    Loopback endpoints are left out; the PC's own sound has its own entry.
    """
    try:
        pa = pyaudio_module()
    except ImportError:
        return []
    devices: list[InputDevice] = []
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        for i in range(int(wasapi["deviceCount"])):
            info = p.get_device_info_by_host_api_device_index(int(wasapi["index"]), i)
            if int(info.get("maxInputChannels", 0)) < 1 or info.get("isLoopbackDevice"):
                continue
            name = str(info["name"])
            if any(d.name == name for d in devices):
                continue
            devices.append(
                InputDevice(
                    index=int(info["index"]),
                    name=name,
                    rate=int(info.get("defaultSampleRate") or 48000),
                    channels=min(2, max(1, int(info["maxInputChannels"]))),
                )
            )
    except Exception:
        log.exception("Énumération des entrées audio impossible")
    finally:
        p.terminate()
    return devices


def resolve_input(source: str) -> tuple[InputDevice | None, str | None]:
    """Turn a stored ``audioSource`` into a capture endpoint.

    ``dshow:`` entries come from older configurations (the FFmpeg sender named
    DirectShow devices); they are matched against the WASAPI endpoint of the
    same name, which is how Windows exposes the same hardware.
    """
    if not source:
        return None, None
    if source == LOOPBACK:
        device = loopback_device()
        if device is None:
            return None, "Capture du son du PC indisponible (aucune sortie audio par défaut)."
        return InputDevice(device.index, device.name, device.rate, device.channels, loopback=True), None

    wanted = source.split(":", 1)[1] if ":" in source else source
    devices = list_input_devices()
    for match in (
        lambda d: d.name == wanted,
        lambda d: d.name.startswith(wanted) or wanted.startswith(d.name),
        lambda d: wanted.lower() in d.name.lower() or d.name.lower() in wanted.lower(),
    ):
        found = next((d for d in devices if match(d)), None)
        if found is not None:
            return found, None
    return None, f"Entrée audio « {wanted} » introuvable."


def loopback_device() -> LoopbackDevice | None:
    """Loopback endpoint of the default playback device, or None if unavailable."""
    try:
        pa = pyaudio_module()
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


class FloatRecorder:
    """Capture endpoint delivering 32-bit float frames to a callback.

    This is what the native OMT sender uses: float is both what WASAPI hands
    over and what OMT carries, so nothing is converted on the way. The
    callback runs on PortAudio's thread and must stay short.

    A WASAPI loopback endpoint delivers nothing while the PC is silent. The
    sender fills those gaps itself, from the sample count it has already
    published, so silence costs no conversion here.
    """

    CHUNK = 0.02  # seconds per callback

    def __init__(self, device: InputDevice) -> None:
        self.device = device
        self._pa = None
        self._stream = None
        self._on_frames = None

    def start(self, on_frames) -> None:
        """``on_frames(data: bytes, samples: int)`` with interleaved float32."""
        pa = pyaudio_module()
        self._on_frames = on_frames
        self._pa = pa.PyAudio()
        self._stream = self._pa.open(
            format=pa.paFloat32,
            channels=self.device.channels,
            rate=self.device.rate,
            input=True,
            input_device_index=self.device.index,
            frames_per_buffer=int(self.device.rate * self.CHUNK),
            stream_callback=self._callback,
        )

    def _callback(self, data, frames, _time_info, _status):
        try:
            if data and self._on_frames is not None:
                self._on_frames(data, frames)
        except Exception:
            log.exception("Traitement audio interrompu")
        return None, pyaudio_module().paContinue

    def close(self) -> None:
        stream, self._stream = self._stream, None
        pa, self._pa = self._pa, None
        self._on_frames = None
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
        if pa is not None:
            pa.terminate()


def program_capture(source: str):
    """``(ProcessCapture, description)`` for an ``app:``/``notapp:`` source.

    Raises OSError when the program is not running: a deck or a restart must
    get a message naming it, not a silent stream.
    """
    from . import proc_audio

    exclude = source.startswith(EXCEPT_PREFIX)
    name = source.split(":", 1)[1].strip()
    if not name:
        raise OSError("Aucun programme choisi.")
    pids = proc_audio.pids_of(name)
    if not pids:
        raise OSError(f"Le programme « {name} » ne tourne pas.")
    # Windows captures a whole process tree: the first process is enough for
    # a browser that splits itself into many.
    what = f"tout le son sauf « {name} »" if exclude else f"son de « {name} »"
    return proc_audio.ProcessCapture(pids[0], exclude=exclude), what


class ProgramFeeder:
    """Same pipe feed as :class:`LoopbackFeeder`, from one program's sound.

    Windows delivers nothing while the program is silent, exactly like a
    loopback, so the real-time pacing and gap filling are shared.
    """

    def __init__(self, source: str, rate: int = 48000, channels: int = 2) -> None:
        self.source = source
        self.rate = rate
        self.channels = channels
        self.frame_bytes = 2 * channels  # s16le on the pipe
        self._capture = None
        self._buffer: deque[bytes] = deque()
        self._buffered = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _on_frames(self, data: bytes, _frames: int) -> None:
        pcm = _float_to_s16(data)
        with self._lock:
            self._buffer.append(pcm)
            self._buffered += len(pcm)
            while self._buffered > self.rate * self.frame_bytes // 2:  # 0,5 s max
                self._buffered -= len(self._buffer.popleft())

    def _drain(self) -> bytes:
        with self._lock:
            out = b"".join(self._buffer)
            self._buffer.clear()
            self._buffered = 0
        return out

    def __call__(self, pipe) -> Callable[[], None]:
        capture, what = program_capture(self.source)
        self._capture = capture
        log.info("Capture audio : %s", what)
        capture.rate, capture.channels = self.rate, self.channels
        capture.start(self._on_frames)
        self._thread = threading.Thread(target=self._run, args=(pipe,), daemon=True, name="program-audio")
        self._thread.start()
        return self.stop

    def _run(self, pipe) -> None:
        cushion = int(self.rate * LoopbackFeeder.CUSHION)
        start = time.perf_counter()
        written = 0
        try:
            while not self._stop.is_set():
                data = self._drain()
                if data:
                    pipe.write(data)
                    pipe.flush()
                    written += len(data) // self.frame_bytes
                else:
                    due = int((time.perf_counter() - start) * self.rate)
                    gap = due - written - cushion
                    if gap > 0:
                        pipe.write(b"\x00" * (gap * self.frame_bytes))
                        pipe.flush()
                        written += gap
                time.sleep(LoopbackFeeder.CHUNK / 2)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                pipe.close()

    def stop(self) -> None:
        self._stop.set()
        if self._capture is not None:
            self._capture.close()
            self._capture = None
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


def _float_to_s16(data: bytes) -> bytes:
    """float32 interleaved to the s16le PCM ffmpeg reads on its stdin."""
    floats = array("f")
    floats.frombytes(data)
    out = array("h", bytes(2 * len(floats)))
    for i, v in enumerate(floats):
        out[i] = 32767 if v >= 1.0 else -32768 if v <= -1.0 else int(v * 32767.0)
    return out.tobytes()


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
        return None, pyaudio_module().paContinue

    def _drain(self) -> bytes:
        """Everything captured so far, without padding."""
        with self._lock:
            out = b"".join(self._buffer)
            self._buffer.clear()
            self._buffered = 0
        return out

    def _run(self, pipe) -> None:
        pa = pyaudio_module()
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
