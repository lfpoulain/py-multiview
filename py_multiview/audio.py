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
    every couple of seconds, while the caller goes on (with silence). ``poll``
    is cheap and meant to be called from the consumer's own loop.
    """

    RETRY = 2.0  # seconds between attempts, and between health checks
    HOLD = 0.3  # seconds without samples before the level reads zero
    DECAY = 0.8  # per chunk: the level falls back in a fraction of a second

    def __init__(self, source: str, on_frames, report=None, expected: tuple[int, int] | None = None) -> None:
        self.source = source
        self.expected = expected
        self.problem = ""  # what prevents the capture right now, "" when it runs
        self.name = ""
        self.format: tuple[int, int] | None = None
        self.generation = 0  # bumped at every (re)opening
        self._on_frames = on_frames
        self._report = report or (lambda _line: None)
        self._capture = None
        self._next_try = 0.0
        self._next_check = 0.0
        self._peak = 0.0
        self._peak_at = 0.0

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
        self._peak = max(peak(data), self._peak * self.DECAY)
        self._peak_at = time.monotonic()
        self._on_frames(data, frames)

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
        self._drop()


def stretch(data: bytes, channels: int, delta: int) -> bytes:
    """Interleaved float32 resampled to ``delta`` frames more (or fewer).

    Linear interpolation over the whole chunk: its first and last frames are
    kept as they are, so the chunk still joins its neighbours without a step.
    Meant for a frame or two at a time, which is inaudible.
    """
    samples = array("f")
    samples.frombytes(data[: len(data) - len(data) % 4])
    count = len(samples) // channels
    wanted = count + delta
    if not delta or count < 2 or wanted < 2:
        return data
    step = (count - 1) / (wanted - 1)
    out = array("f", bytes(4 * wanted * channels))
    for c in range(channels):
        plane = samples[c::channels]
        plane.append(plane[-1])
        resampled = array("f", bytes(4 * wanted))
        for j in range(wanted):
            position = j * step
            i = int(position)
            left = plane[i]
            resampled[j] = left + (plane[i + 1] - left) * (position - i)
        out[c::channels] = resampled
    return out.tobytes()


class SteadyStream:
    """Captured chunks in, a continuous stream locked to this PC's clock out.

    A sound card counts its samples on its own crystal, the picture is paced
    by the system clock, and the two never agree: a card that is 100 ppm off
    gains or loses 6 ms a minute. Left alone, that difference ends up as
    silence slipped between real samples (a click each time), or as sound
    piling up behind the picture. So the stream is steered instead: the
    number of samples handed over is compared with the time elapsed, and a
    frame is added to or removed from a chunk now and then, by interpolation.

    Three rules:

    * what is captured is forwarded at once, never paced, never held back;
    * silence is made up only when the source has really stopped delivering
      (a WASAPI loopback goes quiet when the PC plays nothing), and stays a
      cushion behind the clock so that the sound coming back is not pushed
      late;
    * a jump is made only when the stream is off by more than ``RESYNC``
      (the PC was suspended, the consumer blocked): one cut, then steady.

    ``push`` is called from the capture thread, ``pull`` from the consumer's
    loop every few milliseconds.
    """

    CUSHION = 0.06  # seconds the made-up silence stays behind the clock
    SILENCE_AFTER = 0.12  # seconds without samples before making up silence
    TARGET = 0.03  # seconds the stream trails the clock while sound flows
    MAX_BUFFER = 0.5  # seconds kept when the consumer does not pull
    SMOOTHING = 2.5  # seconds: capture callbacks jitter, the drift does not
    GAIN = 0.1  # correction per second of error
    MAX_CORRECTION = 0.005  # 0.5 %: far below what an ear can tell
    RESYNC = 0.25  # seconds of error beyond which the stream jumps

    def __init__(self, rate: int, channels: int, origin: float | None = None, clock=time.perf_counter) -> None:
        self.rate = int(rate)
        self.channels = int(channels)
        self.frame_bytes = 4 * self.channels
        self.written = 0  # frames handed over since the origin, silence included
        self.adjusted = 0  # frames added (+) or removed (-) to follow the clock
        self.resyncs = 0
        self.dropped = 0  # frames lost because nobody pulled
        self._clock = clock
        self._origin = origin
        self._started = False
        self._chunks: deque[bytes] = deque()
        self._buffered = 0
        self._lock = threading.Lock()
        self._last_data: float | None = None
        self._error = 0.0  # smoothed, in seconds; positive when ahead of the clock
        self._error_at: float | None = None
        self._debt = 0.0  # fraction of a frame still to add or remove

    def push(self, data: bytes) -> None:
        limit = int(self.rate * self.MAX_BUFFER) * self.frame_bytes
        with self._lock:
            self._chunks.append(data)
            self._buffered += len(data)
            # Latency beats completeness on a live feed.
            while self._buffered > limit and len(self._chunks) > 1:
                lost = self._chunks.popleft()
                self._buffered -= len(lost)
                self.dropped += len(lost) // self.frame_bytes

    def pull(self) -> bytes:
        """What to send now: captured sound, silence, or nothing."""
        now = self._clock()
        if self._origin is None:
            self._origin = now
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks.clear()
            self._buffered = 0
        data = data[: len(data) - len(data) % self.frame_bytes]
        due = (now - self._origin) * self.rate
        if not self._started:
            # The stream begins now, wherever the clock already is.
            self._started = True
            self.written = max(self.written, int(due - self.CUSHION * self.rate))
        if not data:
            if self._last_data is not None and now - self._last_data <= self.SILENCE_AFTER:
                return b""
            gap = int(due - self.CUSHION * self.rate) - self.written
            if gap <= 0:
                return b""
            self.written += gap
            self._error_at = None  # the sound that comes back starts a new measure
            # After a long hole (PC suspended, consumer blocked) the time is
            # skipped rather than filled.
            return bytes(min(gap, self.rate) * self.frame_bytes)

        self._last_data = now
        frames = len(data) // self.frame_bytes
        error = (self.written + frames - due) / self.rate + self.TARGET
        head = b""
        if error > self.RESYNC:  # far too much sound for the time elapsed: cut
            cut = min(frames, int(error * self.rate))
            data = data[cut * self.frame_bytes:]
            frames -= cut
            error -= cut / self.rate
            self.resyncs += 1
            self._error_at = None
        elif error < -self.RESYNC:  # far behind: jump forward over a silence
            pad = int(-error * self.rate)
            head = bytes(min(pad, self.rate) * self.frame_bytes)
            self.written += pad
            error += pad / self.rate
            self.resyncs += 1
            self._error_at = None

        if self._error_at is None:
            self._error = error
        else:
            elapsed = max(0.0, now - self._error_at)
            self._error += (error - self._error) * elapsed / (self.SMOOTHING + elapsed)
        self._error_at = now

        correction = max(-self.MAX_CORRECTION, min(self.MAX_CORRECTION, -self.GAIN * self._error))
        self._debt += frames * correction
        delta = int(self._debt)
        if delta and frames >= 2:
            delta = max(-(frames // 2), min(frames, delta))
            data = stretch(data, self.channels, delta)
            self._debt -= delta
            self.adjusted += delta
            frames += delta
        self.written += frames
        return head + data


class PipeFeeder:
    """Feeds the FFmpeg sender's stdin with a source's sound, as raw float32.

    FFmpeg opens all its inputs before sending anything: while its audio
    input stays empty, the *picture* does not go out either. So the pipe is
    fed from the very first instant, with silence until the capture runs and
    whenever it stops, and the capture itself is opened, watched and retried
    beside it (:class:`LiveCapture`). Whatever happens to the sound, FFmpeg
    always has something to read.

    The sound itself goes through a :class:`SteadyStream`: forwarded as soon
    as it is captured, locked to this PC's clock so that it neither drifts
    away from the picture nor gets silence slipped between its samples.
    """

    CHUNK = 0.01  # seconds

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
        self.stream = SteadyStream(self.rate, self.channels)  # f32le on the pipe
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
            # Something to read at once: FFmpeg starts the picture as soon as
            # its audio input has shown a first packet.
            first = int(self.rate * PipeFeeder.CHUNK * 2)
            pipe.write(bytes(first * stream.frame_bytes))
            pipe.flush()
            stream.written = first
            while not self._stop.is_set():
                self.live.poll()  # may take a moment when it (re)opens a device
                data = stream.pull()
                if data:
                    pipe.write(data)
                    pipe.flush()
                time.sleep(PipeFeeder.CHUNK / 2)
        except (BrokenPipeError, OSError, ValueError):
            pass  # ffmpeg exited
        except Exception:
            log.exception("Alimentation audio de l'émission interrompue")
        finally:
            self.live.close()
            with contextlib.suppress(Exception):
                pipe.close()
