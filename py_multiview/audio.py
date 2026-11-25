"""Audio sources for the OMT sender.

A source is named in the configuration as:

* ``loopback`` - what the PC is playing (WASAPI loopback of the default output);
* ``out:<name>`` - what one given playback device is playing;
* ``in:<name>`` - a capture endpoint: microphone, sound card, virtual cable...;
* ``app:<exe>`` / ``notapp:<exe>`` - one program's sound, or the PC's sound
  without it (Windows process loopback, see ``proc_audio``);
* ``dshow:<name>`` - a DirectShow device, for the ones that are nothing else
  (vMix Audio...). FFmpeg opens these itself.

Everything but ``dshow:`` is captured here, in float, and handed to the
sender: straight to libomt for the native sender, on FFmpeg's stdin
(:class:`PipeFeeder`) otherwise. In both cases the capture is kept alive by
:class:`LiveCapture`, so that no audio problem can hold the picture back.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import struct
import subprocess
import threading
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .ffmpeg import audio_packet_frames, creationflags

log = logging.getLogger(__name__)

LOOPBACK = "loopback"
DSHOW_PREFIX = "dshow:"  # written by versions that used the FFmpeg sender
INPUT_PREFIX = "in:"
PROGRAM_PREFIX = "app:"  # app:<exe>  — only that program's sound
EXCEPT_PREFIX = "notapp:"  # notapp:<exe> — the PC's sound except that program
OUTPUT_PREFIX = "out:"  # out:<name> — what that playback device plays
SOURCE_PREFIXES = (INPUT_PREFIX, DSHOW_PREFIX, PROGRAM_PREFIX, EXCEPT_PREFIX, OUTPUT_PREFIX)

PROGRAM_FORMAT = (48000, 2)  # imposed on a process loopback; also the fallback format

# PortAudio is initialised and terminated per PyAudio() instance, which is not
# safe from two threads at once (device list in the UI, capture in a worker).
_PA_LOCK = threading.RLock()

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
    with _PA_LOCK:
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


def _loopback_endpoints(p) -> list[dict]:
    return list(p.get_loopback_device_info_generator())


def _output_name(loopback_name: str) -> str:
    """PyAudioWPatch names a loopback endpoint « <output> [Loopback] »."""
    return loopback_name.replace("[Loopback]", "").strip()


def list_output_devices() -> list[str]:
    """Playback devices whose sound can be captured (each has a loopback endpoint)."""
    try:
        pa = pyaudio_module()
    except ImportError:
        return []
    names: list[str] = []
    with _PA_LOCK:
        p = pa.PyAudio()
        try:
            for dev in _loopback_endpoints(p):
                name = _output_name(str(dev["name"]))
                if name and name not in names:
                    names.append(name)
        except Exception:
            log.exception("Énumération des sorties audio impossible")
        finally:
            p.terminate()
    return names


def _loopback_of(wanted: str | None) -> InputDevice | None:
    """Loopback endpoint of the output called ``wanted``, or of the default output."""
    try:
        pa = pyaudio_module()
    except ImportError:
        return None
    with _PA_LOCK:
        p = pa.PyAudio()
        try:
            if wanted is None:
                wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
                wanted = str(p.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"])
            endpoints = _loopback_endpoints(p)
            for match in (
                lambda name: name == wanted,
                lambda name: wanted in name or name in wanted,
                lambda name: wanted.lower() in name.lower() or name.lower() in wanted.lower(),
            ):
                for dev in endpoints:
                    name = _output_name(str(dev["name"]))
                    if name and match(name):
                        return InputDevice(
                            int(dev["index"]), name, int(dev["defaultSampleRate"]),
                            max(1, int(dev["maxInputChannels"])), loopback=True,
                        )
        except Exception:
            log.exception("Périphérique loopback WASAPI introuvable")
        finally:
            p.terminate()
    return None


def loopback_device() -> InputDevice | None:
    """Loopback endpoint of the default playback device, or None if unavailable."""
    return _loopback_of(None)


def is_program(source: str) -> bool:
    return source.startswith((PROGRAM_PREFIX, EXCEPT_PREFIX))


def uses_pipe(source: str) -> bool:
    """True when the FFmpeg sender gets this source on its stdin (everything but DirectShow)."""
    return bool(source) and not source.startswith(DSHOW_PREFIX)


def resolve_input(source: str) -> tuple[InputDevice | None, str | None]:
    """Turn a stored ``audioSource`` into a WASAPI capture endpoint.

    ``dshow:`` entries name DirectShow devices; they are matched against the
    WASAPI endpoint of the same name, which is how Windows exposes the same
    hardware.
    """
    if not source:
        return None, None
    if source == LOOPBACK:
        device = loopback_device()
        if device is None:
            return None, "Capture du son du PC indisponible (aucune sortie audio par défaut)."
        return device, None
    wanted = source.split(":", 1)[1] if ":" in source else source
    if source.startswith(OUTPUT_PREFIX):
        device = _loopback_of(wanted)
        if device is None:
            return None, f"Sortie audio « {wanted} » introuvable."
        return device, None

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


def source_format(source: str) -> tuple[int, int]:
    """``(rate, channels)`` a source delivers; 48 kHz stereo when it cannot be asked.

    The FFmpeg sender needs the format on its command line before any sample
    exists, so an absent device gets the usual format rather than an error:
    the picture must go out anyway.
    """
    if not source or is_program(source):
        return PROGRAM_FORMAT  # Windows hands a process loopback over in the format asked for
    device, _error = resolve_input(source)
    return (device.rate, device.channels) if device is not None else PROGRAM_FORMAT


def describe_source(source: str) -> str:
    """Short French label of a stored ``audioSource``, for buttons and summaries."""
    if not source:
        return "Sans son"
    if source == LOOPBACK:
        return "Son du PC"
    name = source.split(":", 1)[1] if ":" in source else source
    if source.startswith(PROGRAM_PREFIX):
        return f"Son de {name}"
    if source.startswith(EXCEPT_PREFIX):
        return f"Son du PC sauf {name}"
    if source.startswith(OUTPUT_PREFIX):
        return f"Sortie : {name}"
    return name


class FloatRecorder:
    """Capture endpoint delivering 32-bit float frames to a callback.

    Float is what WASAPI hands over, what OMT carries and what the FFmpeg
    sender reads on its pipe, so nothing is converted on the way. The
    callback runs on PortAudio's thread and must stay short.

    A WASAPI loopback endpoint delivers nothing while the PC is silent;
    :class:`SteadyStream` fills those gaps.
    """

    CHUNK = 0.02  # seconds per callback

    def __init__(self, device: InputDevice) -> None:
        self.device = device
        self.name = device.name
        self.rate = device.rate
        self.channels = device.channels
        self._pa = None
        self._stream = None
        self._on_frames = None

    def start(self, on_frames) -> None:
        """``on_frames(data: bytes, samples: int)`` with interleaved float32."""
        pa = pyaudio_module()
        self._on_frames = on_frames
        with _PA_LOCK:
            self._pa = pa.PyAudio()
            try:
                self._stream = self._pa.open(
                    format=pa.paFloat32,
                    channels=self.device.channels,
                    rate=self.device.rate,
                    input=True,
                    input_device_index=self.device.index,
                    frames_per_buffer=int(self.device.rate * self.CHUNK),
                    stream_callback=self._callback,
                )
            except Exception:
                self._pa.terminate()
                self._pa = None
                raise

    def _callback(self, data, frames, _time_info, _status):
        try:
            if data and self._on_frames is not None:
                self._on_frames(data, frames)
        except Exception:
            log.exception("Traitement audio interrompu")
        return None, pyaudio_module().paContinue

    def alive(self) -> bool:
        """False once the endpoint is gone (unplugged, disabled)."""
        try:
            return self._stream is not None and self._stream.is_active()
        except Exception:
            return False

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
            with _PA_LOCK:
                pa.terminate()


class _ProgramRecorder:
    """One program's sound (or the PC's sound without it), as a capture endpoint.

    Excluding a program that is not running would leave nothing to capture
    from, so py-multiview itself is excluded instead: the result is the whole
    PC's sound, minus what this application plays — which also keeps an
    emission from eating its own return.
    """

    def __init__(self, source: str) -> None:
        from . import proc_audio

        self._proc_audio = proc_audio
        self.exclude = source.startswith(EXCEPT_PREFIX)
        self.program = source.split(":", 1)[1].strip()
        if not self.program:
            raise OSError("Aucun programme choisi.")
        self.rate, self.channels = PROGRAM_FORMAT
        pids = proc_audio.pids_of(self.program)
        self.attached = bool(pids)
        if pids:
            # Windows captures a whole process tree: the first process is
            # enough for a browser that splits itself into many.
            self.pid = pids[0]
            self.name = f"tout le son sauf « {self.program} »" if self.exclude else f"son de « {self.program} »"
        elif self.exclude:
            self.pid = os.getpid()
            self.name = f"tout le son du PC (« {self.program} » ne tourne pas)"
        else:
            raise OSError(f"Le programme « {self.program} » ne tourne pas (le son reprendra à son lancement).")
        self._capture = proc_audio.ProcessCapture(self.pid, exclude=self.exclude, rate=self.rate, channels=self.channels)

    def start(self, on_frames) -> None:
        self._capture.start(on_frames)

    def alive(self) -> bool:
        """False when the program quit, or came back under another process."""
        if not self._capture.alive():
            return False
        pids = self._proc_audio.pids_of(self.program)
        if not self.attached:
            return not pids  # it started: attach to it for real
        return self.pid in pids

    def close(self) -> None:
        self._capture.close()


def open_capture(source: str):
    """Capture object for ``source`` (not started). Raises OSError with a message to show."""
    if is_program(source):
        return _ProgramRecorder(source)
    device, error = resolve_input(source)
    if device is None:
        raise OSError(error or "Source audio indisponible.")
    return FloatRecorder(device)


def peak(data: bytes) -> float:
    """Peak level (0..1) of interleaved float32 samples."""
    samples = array("f")
    samples.frombytes(data[: len(data) - len(data) % 4])
    if not samples:
        return 0.0
    return min(1.0, max(max(samples), -min(samples)))


class LiveCapture:
    """Keeps a source captured, whatever happens to it.

    The emission must never depend on the sound: a missing device, a program
    that is not started yet or a refusal from Windows is reported and retried
    every couple of seconds. Activation and process enumeration can block,
    so the capture supervisor runs independently of the outgoing audio clock.
    """

    RETRY = 2.0  # seconds between attempts, and between health checks
    HOLD = 0.3  # seconds without samples before the level reads zero
    DECAY = 0.8  # per chunk: the level falls back in a fraction of a second

    def __init__(self, source: str, on_frames, report=None, expected: tuple[int, int] | None = None,
                 on_format=None) -> None:
        self.source = source
        self.expected = expected
        self.problem = ""  # what prevents the capture right now, "" when it runs
        self.name = ""
        self.format: tuple[int, int] | None = None
        self.generation = 0  # bumped at every (re)opening
        self._on_frames = on_frames
        self._on_format = on_format
        self._report = report or (lambda _line: None)
        self._capture = None
        self._next_try = 0.0
        self._next_check = 0.0
        self._peak = 0.0
        self._peak_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._watch, daemon=True, name="audio-source")
        self._thread.start()

    def _watch(self) -> None:
        try:
            while not self._stop.is_set():
                self.poll()
                deadline = self._next_check if self._capture is not None else self._next_try
                self._stop.wait(max(0.0, deadline - time.monotonic()))
        finally:
            self._drop()

    def poll(self) -> None:
        now = time.monotonic()
        if self._capture is not None:
            if now >= self._next_check:
                self._next_check = now + self.RETRY
                if not self._capture.alive():
                    self._drop()
                    self._set_problem(f"Capture de « {self.name} » interrompue, reprise…")
            return
        if now < self._next_try:
            return
        self._next_try = now + self.RETRY
        capture = None
        try:
            capture = open_capture(self.source)
            fmt = (capture.rate, capture.channels)
            if self.expected and fmt != self.expected:
                raise OSError(
                    f"« {capture.name} » est passé en {fmt[0]} Hz / {fmt[1]} canaux : "
                    "arrêter puis relancer l'émission pour retrouver le son."
                )
            if self._on_format is not None:
                self._on_format(*fmt)  # start() may deliver samples before it returns
            capture.start(self._frames)
        except Exception as exc:
            if capture is not None:
                with contextlib.suppress(Exception):
                    capture.close()
            self._set_problem(str(exc) or exc.__class__.__name__)
            return
        self._capture = capture
        self.name, self.format = capture.name, fmt
        self.generation += 1
        self.problem = ""
        self._next_check = now + self.RETRY
        self._report(f"Son : {capture.name} ({fmt[0]} Hz, {fmt[1]} canaux)")

    def _frames(self, data: bytes, frames: int) -> None:
        if self._stop.is_set():
            return
        self._on_frames(data, frames)
        self._peak = max(peak(data), self._peak * self.DECAY)
        self._peak_at = time.monotonic()

    def level(self) -> float:
        """Recent peak, 0..1; 0 when nothing is captured."""
        if self._capture is None or time.monotonic() - self._peak_at > self.HOLD:
            return 0.0
        return self._peak

    def _set_problem(self, text: str) -> None:
        if text != self.problem:
            self.problem = text
            self._report(f"⚠ Son : {text}")
            log.warning("Capture audio (%s) : %s", self.source, text)

    def _drop(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            with contextlib.suppress(Exception):
                capture.close()

    def close(self) -> None:
        self._stop.set()
        if self._thread is None:
            self._drop()
        else:
            # A pending WASAPI activation can take seconds. Its owner closes
            # it in _watch even if the sender has already stopped.
            self._thread.join(timeout=1.0)


@lru_cache(maxsize=1)
def _sinc_filters():
    """Unit-gain fractional-delay filters, shared by all sample rates and channels."""
    offsets = np.arange(-15, 17)
    phase = np.arange(1025)[:, None] / 1024
    distance = phase - offsets
    window = np.i0(8 * np.sqrt(np.maximum(0, 1 - (distance / 16) ** 2))) / np.i0(8)
    weights = np.sinc(distance) * window
    return offsets, weights / weights.sum(axis=1, keepdims=True)


def stretch(data: bytes, channels: int, delta: int) -> bytes:
    """Apply a small clock correction without attenuating treble in corrected blocks."""
    count = len(data) // (4 * channels)
    wanted = count + delta
    if not delta or count < 2 or wanted < 2:
        return data
    samples = np.frombuffer(data, dtype="<f4", count=count * channels).reshape(count, channels)
    offsets, filters = _sinc_filters()
    positions = np.linspace(0, count - 1, wanted)
    base = positions.astype(np.intp)
    phase = np.rint((positions - base) * (len(filters) - 1)).astype(np.intp)
    indices = np.clip(base[:, None] + offsets, 0, count - 1)
    out = np.einsum("nt,ntc->nc", filters[phase], samples[indices])
    out[0], out[-1] = samples[0], samples[-1]
    return out.astype("<f4").tobytes()


class SteadyStream:
    """Play a bounded capture FIFO in 20 ms packets on the picture's clock."""

    PREFILL = 0.10  # the outgoing packet plus 80 ms for batched WASAPI wake-ups
    MAX_BUFFER = 0.5
    RESYNC = 0.25  # skip stale audio after suspension or a blocked output
    SMOOTHING = 2.5  # seconds over which to measure sustained clock drift
    DEADBAND = 0.004  # ignore a few ms of timer jitter
    GAIN = 0.1
    MAX_CORRECTION = 0.005

    def __init__(self, rate: int, channels: int, origin: float | None = None, clock=time.perf_counter) -> None:
        self.rate = int(rate)
        self.channels = int(channels)
        self.frame_bytes = 4 * self.channels
        self.packet_frames = audio_packet_frames(self.rate)
        self._period = self.packet_frames / self.rate
        self._silence = bytes(self.packet_frames * self.frame_bytes)
        self._clock = clock
        # Packet zero describes the samples captured at the A/V origin. Send
        # it after the reserve has had time to fill, retaining that timestamp.
        self._start = (clock() if origin is None else origin) + self.PREFILL
        self._deadline = self._start
        self.written = 0  # position after the last emitted packet, in frames
        self.adjusted = 0  # frames added (+) or removed (-) for clock drift
        self.resyncs = 0
        self.dropped = 0
        self._starvations = 0
        self._reported = (0, 0)
        self._next_report = 0.0
        self._buffer = bytearray()
        self._pending = b""  # resampled frames left over for the next packet
        self._limit = int(self.rate * self.MAX_BUFFER) * self.frame_bytes
        self._lock = threading.Lock()
        self._last_push = 0.0
        self._ready_at: float | None = None
        self._playing = False
        self._empty_packet = False
        self._target = self.PREFILL
        self._error = self._debt = 0.0

    def push(self, data: bytes) -> None:
        size = len(data) - len(data) % self.frame_bytes
        if not size:
            return
        with self._lock:
            now = self._clock()
            # Keep the newest complete sample frames even for a single
            # oversized callback. The capture thread never waits on the sender.
            self._buffer.extend(memoryview(data)[max(0, size - self._limit):size])
            excess = max(0, len(self._buffer) - self._limit)
            del self._buffer[:excess]
            self.dropped += (max(0, size - self._limit) + excess) // self.frame_bytes
            self._last_push = now
            if not self._playing:
                # A short isolated sound must also play if no more data arrives.
                duration = len(self._buffer) / (self.rate * self.frame_bytes)
                ready = now + self.PREFILL - duration
                self._ready_at = ready if self._ready_at is None else min(self._ready_at, ready)

    def delay(self) -> float:
        """Time left to the next packet, including time already spent sending."""
        return max(0.0, self._deadline - self._clock())

    def continue_from(self, previous: SteadyStream) -> None:
        """Continue the same A/V timeline after the capture format changes."""
        self.written = (previous.written * self.rate + previous.rate - 1) // previous.rate
        self._deadline = self._start + self.written / self.rate

    def _resync(self, now: float) -> None:
        """Skip stale capture after an output stall. Called with the FIFO locked."""
        slot = int((now - self._start) / self._period)
        self.written = slot * self.packet_frames
        keep = int(self.PREFILL * self.rate) if now - self._last_push < self.RESYNC else 0
        excess = max(0, len(self._buffer) - keep * self.frame_bytes)
        del self._buffer[:excess]
        self.dropped += (excess + len(self._pending)) // self.frame_bytes
        self._pending = b""
        self._playing = False
        self._ready_at = None
        if self._buffer:
            duration = len(self._buffer) / (self.rate * self.frame_bytes)
            self._ready_at = self._last_push + self.PREFILL - duration
        self.resyncs += 1

    def pull(self) -> bytes:
        now = self._clock()
        if now + 1e-9 < self._deadline:
            return b""
        late = now - self._deadline
        if late > self.RESYNC:
            with self._lock:
                self._resync(now)
            log.warning("Envoi audio retardé de %.0f ms, reprise à la position courante", late * 1000)
        with self._lock:
            self.written += self.packet_frames
            # Deadlines are absolute. Recover a small scheduling overrun over
            # several packets instead of flushing them all in one burst.
            self._deadline = max(self._start + self.written / self.rate, now + self._period / 2)
            level = (len(self._buffer) + len(self._pending)) / (self.rate * self.frame_bytes) + (now - self._last_push)
            if not self._playing:
                if self._ready_at is None or now + 1e-9 < self._ready_at:
                    return self._silence
                self._playing = True
                self._empty_packet = False
                self._target = min(self.PREFILL + self._period, max(self.PREFILL, level))
                self._error = self._debt = 0.0

            # Add the age of the newest capture to the FIFO duration: six
            # callbacks delivered together must not look like a faster clock.
            self._error += (level - self._target - self._error) * self._period / (self.SMOOTHING + self._period)
            error = self._error - max(-self.DEADBAND, min(self.DEADBAND, self._error))
            correction = max(-self.MAX_CORRECTION, min(self.MAX_CORRECTION, -self.GAIN * error))
            needed = self.packet_frames - len(self._pending) // self.frame_bytes
            blocks = []
            while needed > 0 and self._buffer:
                # Work on whole input blocks and carry the fractional packet
                # across sends. Both stereo channels use the same phase.
                size = min(len(self._silence), len(self._buffer))
                count = size // self.frame_bytes
                self._debt += count * correction
                delta = int(self._debt) if count >= 3 else 0
                self._debt -= delta
                self.adjusted += delta
                blocks.append((bytes(self._buffer[:size]), delta))
                del self._buffer[:size]
                needed -= count + delta
            if needed > 0:
                self._starvations += 1
                # A callback can straddle the deadline by just a few samples.
                # Fit its tail into the packet within the usual stretch limit.
                if blocks:
                    block, delta = blocks[-1]
                    count = len(block) // self.frame_bytes
                    limit = int(count * self.MAX_CORRECTION)
                    if needed <= limit and abs(delta + needed) <= limit:
                        blocks[-1] = (block, delta + needed)
                        self.adjusted += needed
                        needed = 0
            # One late callback must not trigger 100 ms of startup silence.
            # A second wholly empty deadline signals a sustained interruption.
            empty = needed == self.packet_frames
            if empty and self._empty_packet:
                self._playing = False
                self._ready_at = None
            self._empty_packet = empty
            counts = (self._starvations, self.dropped)
        if counts != self._reported and now >= self._next_report:
            log.info("Émission audio depuis le démarrage : tampon épuisé %d fois (silence ou retard "
                     "de capture), %d échantillons par canal écartés", *counts)
            self._reported = counts
            self._next_report = now + 5
        data = self._pending + b"".join(
            stretch(block, self.channels, delta) if delta else block for block, delta in blocks
        )
        size = len(self._silence)
        self._pending = data[size:]
        return data[:size].ljust(size, b"\x00")


class PipeFeeder:
    """Feeds the FFmpeg sender's stdin with a streaming float32 WAV.

    FFmpeg waits for both inputs. The header is written immediately and
    :class:`SteadyStream` supplies timed silence whenever capture is unavailable.
    """

    def __init__(self, source: str, rate: int = 48000, channels: int = 2) -> None:
        self.source = source
        self.rate = int(rate)
        self.channels = int(channels)
        self._feed: _PipeFeed | None = None

    def __call__(self, pipe, report=None) -> Callable[[], None]:
        """Start feeding ``pipe`` (one fresh feed per process launch); returns its stop function."""
        feed = _PipeFeed(self, pipe, report)
        self._feed = feed
        feed.start()
        return feed.stop

    @property
    def audio_problem(self) -> str:
        return self._feed.live.problem if self._feed is not None else ""

    def audio_level(self) -> float:
        return self._feed.live.level() if self._feed is not None else 0.0


class _PipeFeed:
    def __init__(self, feeder: PipeFeeder, pipe, report) -> None:
        self.rate, self.channels = feeder.rate, feeder.channels
        self._pipe = pipe
        self._stop = threading.Event()
        self.stream = SteadyStream(self.rate, self.channels)
        self.live = LiveCapture(feeder.source, self._on_frames, report, expected=(self.rate, self.channels))
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio-feed")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _on_frames(self, data: bytes, _frames: int) -> None:
        self.stream.push(data)

    def _run(self) -> None:
        pipe, stream = self._pipe, self.stream
        try:
            # An empty data length with WAV's ignore_length input option
            # streams until EOF. Format 3 is WAVE_FORMAT_IEEE_FLOAT.
            pipe.write(struct.pack(
                "<4sI4s4sIHHIIHH4sI", b"RIFF", 0xffffffff, b"WAVE", b"fmt ", 16,
                3, self.channels, self.rate, self.rate * stream.frame_bytes,
                stream.frame_bytes, 32, b"data", 0,
            ))
            pipe.flush()
            self.live.start()
            while not self._stop.is_set():
                data = stream.pull()
                if data:
                    pipe.write(data)
                    pipe.flush()
                self._stop.wait(stream.delay())
        except (BrokenPipeError, OSError, ValueError):
            pass  # ffmpeg exited
        except Exception:
            log.exception("Alimentation audio de l'émission interrompue")
        finally:
            self.live.close()
            with contextlib.suppress(Exception):
                pipe.close()
