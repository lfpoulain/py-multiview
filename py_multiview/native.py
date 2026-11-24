"""OMT workers that talk to libomt directly, without an FFmpeg process.

Two jobs run this way:

* :class:`AudioMonitor` — the master return. It asks libomt for audio frames
  only, so the sender never opens its video connection, and plays them
  straight out: OMT carries float samples and PortAudio takes float samples.
* :class:`ScreenSender` — screen capture published as an OMT source. Video
  goes out as BGRA (libomt encodes it), audio as the planar float OMT uses
  natively. No sample format conversion, no drift filter, no muxer holding
  the sound behind a late frame.

Both are background threads rather than child processes, so they are given
the same surface as a supervised process — status, restart with back-off,
rolling log — through :class:`NativeWorker`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from . import audio, omtlib
from .capture import ScreenCapture
from .process import (
    ACTIVITY_TIMEOUT,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    STABLE_UPTIME,
    STARTUP_GRACE,
    ProcessInfo,
    Status,
)

log = logging.getLogger(__name__)

LOG_LINES = 300
AUDIO_PUMP = 0.01  # seconds between audio flushes


class NativeWorker:
    """A restartable loop on a background thread."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.params: tuple = ()
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.started_at = 0.0
        self.last_activity = 0.0
        self.restarts = 0
        self.error = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._backoff = INITIAL_BACKOFF

    # -- lifecycle ---------------------------------------------------------

    def ensure(self, params: tuple) -> None:
        """Start the loop, or restart it when ``params`` changed."""
        if self.alive():
            if params == self.params:
                return
            self.stop()
        self.params = params
        self._stop.clear()
        self.error = ""
        self._backoff = INITIAL_BACKOFF
        self.started_at = time.monotonic()
        self.last_activity = 0.0
        self.append(f"Démarrage — {self.describe()}")
        self._thread = threading.Thread(target=self._guarded, daemon=True, name=self.label)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self.started_at = 0.0
        self.last_activity = 0.0

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- for subclasses ----------------------------------------------------

    def run(self) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return self.label

    def beat(self) -> None:
        """Media is flowing right now."""
        self.last_activity = time.monotonic()

    def append(self, line: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {line}")

    def sleep(self, seconds: float) -> bool:
        """Interruptible wait; returns True when asked to stop."""
        return self._stop.wait(seconds)

    # -- internals ---------------------------------------------------------

    def _guarded(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.run()
                if not self._stop.is_set():
                    self.append("Source interrompue, reprise…")
            except Exception as exc:
                self.error = str(exc) or exc.__class__.__name__
                self.append(self.error)
                log.warning("[%s] %s", self.label, self.error)
            if self._stop.is_set():
                return
            if time.monotonic() - started >= STABLE_UPTIME:
                self._backoff = INITIAL_BACKOFF
            if self.sleep(self._backoff):
                return
            self._backoff = min(self._backoff * 2, MAX_BACKOFF)
            self.restarts += 1
            self.started_at = time.monotonic()
            self.last_activity = 0.0

    def info(self) -> ProcessInfo:
        now = time.monotonic()
        if not self.alive():
            return ProcessInfo(Status.STOPPED, restarts=self.restarts)
        if self.last_activity and now - self.last_activity < ACTIVITY_TIMEOUT:
            return ProcessInfo(Status.ACTIVE, restarts=self.restarts, uptime=now - self.started_at)
        if self.error:
            return ProcessInfo(Status.RETRYING, self.error, restarts=self.restarts)
        if now - self.started_at < STARTUP_GRACE:
            return ProcessInfo(Status.STARTING, restarts=self.restarts)
        return ProcessInfo(Status.WAITING, restarts=self.restarts, uptime=now - self.started_at)


class AudioMonitor(NativeWorker):
    """Plays the audio of an OMT source on this PC's default output."""

    def __init__(self) -> None:
        super().__init__("Retour audio")
        self.gain = 1.0

    def describe(self) -> str:
        return f"écoute de « {self.params[0]} »" if self.params else self.label

    def set_volume(self, percent: int) -> None:
        """Applied to the next frames; no need to restart the receiver."""
        self.gain = max(0, min(100, int(percent))) / 100.0

    def run(self) -> None:
        source = self.params[0] if self.params else ""
        if not source:
            raise RuntimeError("Aucune source OMT à écouter.")
        player = _AudioOutput()
        try:
            with omtlib.Receiver(source, omtlib.FRAME_AUDIO) as receiver:
                self.append(f"Connecté à « {source} »")
                while not self.stopping():
                    frame = receiver.receive_audio(500, self.gain)
                    if frame is None:
                        continue
                    player.write(frame)
                    self.beat()
        finally:
            player.close()


class _AudioOutput:
    """PortAudio output stream, opened on the first frame received."""

    def __init__(self) -> None:
        self._pa = None
        self._stream = None
        self._format: tuple[int, int] = (0, 0)

    def write(self, frame: omtlib.AudioFrame) -> None:
        if self._format != (frame.rate, frame.channels):
            self.close()
            pa = audio.pyaudio_module()
            # PortAudio is not safe to initialise from two threads at once,
            # and a capture may be opening at this very moment.
            with audio._PA_LOCK:
                self._pa = pa.PyAudio()
                self._stream = self._pa.open(
                    format=pa.paFloat32,
                    channels=frame.channels,
                    rate=frame.rate,
                    output=True,
                    frames_per_buffer=max(256, frame.samples),
                )
            self._format = (frame.rate, frame.channels)
        self._stream.write(frame.data)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        pa, self._pa = self._pa, None
        self._format = (0, 0)
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
        if pa is not None:
            with audio._PA_LOCK:
                pa.terminate()


class ScreenSender(NativeWorker):
    """Publishes a screen (and optionally sound) as an OMT source."""

    def __init__(self) -> None:
        super().__init__("Émission OMT")
        self.address = ""
        self._pump: _AudioPump | None = None

    @property
    def audio_problem(self) -> str:
        pump = self._pump
        return pump.live.problem if pump is not None else ""

    def audio_level(self) -> float:
        pump = self._pump
        return pump.live.level() if pump is not None else 0.0

    def describe(self) -> str:
        return f"publication de « {self.params[0]} »" if self.params else self.label

    def run(self) -> None:
        name, x, y, width, height, fps, audio_source = self.params
        fps = max(1, int(fps))
        with omtlib.Sender(name) as sender:
            self.address = sender.address
            self.append(f"Publié sous « {self.address or name} »")
            with ScreenCapture(x, y, width, height) as screen:
                self.append(f"Capture {screen.width}x{screen.height} en {x},{y} à {fps} i/s")
                if screen.clamped:
                    self.append(f"⚠ {screen.clamped}")
                # One origin for both timestamps: sound and picture are dated
                # on the same clock.
                origin = time.perf_counter()
                pump = _AudioPump(sender, audio_source, self.append, origin) if audio_source else None
                self._pump = pump
                try:
                    if pump is not None:
                        pump.start()
                    self._send_frames(sender, screen, fps, origin)
                finally:
                    self._pump = None
                    if pump is not None:
                        pump.close()

    def _send_frames(self, sender, screen: ScreenCapture, fps: int, start: float) -> None:
        period = 1.0 / fps
        index = 0
        misses = 0
        black_since = 0.0
        reported_black = False
        connections = -1
        while not self.stopping():
            # Constant cadence from a fixed origin, so a slow frame does not
            # shift every frame that follows.
            index += 1
            due = start + index * period
            now = time.perf_counter()
            if due < now - period:  # fell behind: skip to the next slot
                index = int((now - start) / period) + 1
                due = start + index * period
            if self.sleep(max(0.0, due - time.perf_counter())):
                return
            pixels = screen.grab()
            if pixels is None:
                misses += 1
                if misses in (1, 50):
                    self.append("Capture d'écran momentanément indisponible")
                continue
            misses = 0
            timestamp = int((time.perf_counter() - start) * omtlib.TICKS_PER_SECOND)
            sender.send_video(
                pixels, screen.width, screen.height, screen.stride, omtlib.CODEC_BGRA, fps, timestamp
            )
            self.beat()

            # Say out loud what is being captured: a black picture on the
            # receiving end is either black here already (nothing to capture,
            # or a window GDI cannot read back) or a problem further down.
            if index % max(1, fps) == 0:
                now = time.perf_counter()
                if screen.looks_black():
                    black_since = black_since or now
                    if not reported_black and now - black_since > 3.0:
                        self.append("⚠ L'écran capturé est entièrement noir (la source est noire, "
                                    "ou son contenu est rendu par le GPU et illisible par GDI).")
                        reported_black = True
                else:
                    if reported_black:
                        self.append("Image de nouveau visible.")
                    black_since, reported_black = 0.0, False
                live = sender.connections
                if live != connections:
                    connections = live
                    self.append(f"{live} connexion(s) de récepteurs")


class _AudioPump:
    """Captures an audio source and publishes it on the same OMT sender.

    Audio timestamps come from the number of samples already published, which
    keeps the stream continuous for the receiver, and that count is locked to
    this PC's clock by :class:`audio.SteadyStream`, from the same origin as
    the picture's timestamps: sound and picture stay together for hours even
    though the sound card runs on its own crystal.

    The source is held by :class:`audio.LiveCapture`: when it cannot be
    captured (device missing, program not started), the picture goes out
    without sound and the capture is retried until it comes.
    """

    def __init__(self, sender: omtlib.Sender, source: str, report, origin: float | None = None) -> None:
        self._sender = sender
        self._origin = origin
        self._stream: audio.SteadyStream | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.live = audio.LiveCapture(source, self._on_frames, report)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="omt-audio")
        self._thread.start()

    def _on_frames(self, data: bytes, _frames: int) -> None:
        stream = self._stream
        if stream is not None:
            stream.push(data)

    def _run(self) -> None:
        generation = 0
        stream = None
        while not self._stop.is_set():
            self.live.poll()
            if self.live.format is not None and self.live.generation != generation:
                # A capture just (re)opened, maybe in another format. The new
                # stream shares the picture's origin, so it resumes where the
                # clock is now.
                generation = self.live.generation
                rate, channels = self.live.format
                if stream is None or (stream.rate, stream.channels) != (rate, channels):
                    stream = self._stream = audio.SteadyStream(rate, channels, origin=self._origin)
            if stream is not None:
                data = stream.pull()
                if data:
                    self._publish(stream, data)
            self._stop.wait(AUDIO_PUMP)

    def _publish(self, stream: audio.SteadyStream, interleaved: bytes) -> None:
        samples = len(interleaved) // stream.frame_bytes
        timestamp = int((stream.written - samples) * omtlib.TICKS_PER_SECOND / stream.rate)
        planar = omtlib.interleaved_to_planar(interleaved, stream.channels)
        self._sender.send_audio(planar, stream.rate, stream.channels, timestamp)

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.live.close()
