import threading
import time
from array import array
from pathlib import Path

from py_multiview import audio
from py_multiview.audio import parse_dshow_audio
from py_multiview.config import normalize_config
from py_multiview.ffmpeg import build_sender_command, dshow_audio_input_args, pipe_audio_input_args

DSHOW_OUTPUT = """
[dshow @ 01] "HD Pro Webcam C920" (video)
[dshow @ 01]   Alternative name "@device_pnp_..."
[dshow @ 01] "Microphone (HD Pro Webcam C920)" (audio)
[dshow @ 01] "CABLE Output (VB-Audio Virtual Cable)" (audio)
[dshow @ 01] "Microphone (HD Pro Webcam C920)" (audio)
"""
DISPLAY = {"x": 0, "y": 0, "width": 1920, "height": 1080}


def test_dshow_audio_devices_are_parsed():
    assert parse_dshow_audio(DSHOW_OUTPUT) == ["Microphone (HD Pro Webcam C920)", "CABLE Output (VB-Audio Virtual Cable)"]


def test_audio_source_is_validated():
    assert normalize_config({"sender": {"audioSource": "loopback"}})["sender"]["audioSource"] == "loopback"
    assert normalize_config({"sender": {"audioSource": "dshow:Micro"}})["sender"]["audioSource"] == "dshow:Micro"
    assert normalize_config({"sender": {"audioSource": "out:Casque"}})["sender"]["audioSource"] == "out:Casque"
    assert normalize_config({"sender": {"audioSource": "nope"}})["sender"]["audioSource"] == ""


def test_sender_without_audio():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"])
    assert "-an" in args and "1:a:0" not in args


def test_sender_with_captured_sound_reads_stdin():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], pipe_audio_input_args(44100, 2))
    assert args[args.index("pipe:0") - 1] == "-i"
    assert args[args.index("-ar") + 1] == "44100"  # input rate, resampled to 48 kHz on output
    # Raw PCM is not probed: ffmpeg would otherwise hold the picture for seconds.
    pipe = args.index("pipe:0")
    assert args[args.index("-probesize") + 1] == "32" and args.index("-probesize") < pipe
    assert args[args.index("-analyzeduration") + 1] == "0"
    assert args[args.index("f32le") - 1] == "-f"
    assert "-an" not in args and "1:a:0" in args
    assert args[-3:-1] == ["-f", "libomt"]


def test_sender_with_dshow_device():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], dshow_audio_input_args("Micro (USB)"))
    assert "audio=Micro (USB)" in args


def test_sender_audio_is_never_held_back():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], dshow_audio_input_args("Micro"))
    # Smooth drift compensation only, and no interleaving delay behind the video.
    assert args[args.index("-af") + 1] == "aresample=async=1:min_hard_comp=0.100:first_pts=0"
    assert args[args.index("-max_interleave_delta") + 1] == "0"


# ---------------------------------------------------------------- capture


class FakeCapture:
    """A capture endpoint that can refuse to open, deliver frames, and die."""

    def __init__(self, name="Carte son", rate=48000, channels=2, fail=""):
        self.name, self.rate, self.channels = name, rate, channels
        self.fail = fail
        self.on_frames = None
        self.is_alive = True
        self.closed = False

    def start(self, on_frames):
        if self.fail:
            raise OSError(self.fail)
        self.on_frames = on_frames

    def alive(self):
        return self.is_alive

    def close(self):
        self.closed = True


def float_bytes(*values):
    return array("f", values).tobytes()


def test_source_labels():
    assert audio.describe_source("") == "Sans son"
    assert audio.describe_source("loopback") == "Son du PC"
    assert audio.describe_source("out:Casque") == "Sortie : Casque"
    assert audio.describe_source("in:Micro (USB)") == "Micro (USB)"
    assert audio.describe_source("app:chrome.exe") == "Son de chrome.exe"
    assert audio.describe_source("notapp:vMix64.exe") == "Son du PC sauf vMix64.exe"
    assert audio.uses_pipe("loopback") and audio.uses_pipe("app:x.exe") and audio.uses_pipe("in:Micro")
    assert not audio.uses_pipe("dshow:vMix Audio") and not audio.uses_pipe("")


def test_peak_level():
    assert audio.peak(b"") == 0.0
    assert audio.peak(float_bytes(0.1, -0.5, 0.25)) == 0.5
    assert audio.peak(float_bytes(3.0)) == 1.0  # clipped for the meter


def test_source_format_falls_back_when_the_device_is_absent(monkeypatch):
    monkeypatch.setattr(audio, "resolve_input", lambda source: (None, "introuvable"))
    assert audio.source_format("in:Absent") == (48000, 2)
    assert audio.source_format("app:chrome.exe") == (48000, 2)
    monkeypatch.setattr(audio, "resolve_input", lambda source: (audio.InputDevice(1, "X", 44100, 6), None))
    assert audio.source_format("out:X") == (44100, 6)


def test_live_capture_retries_and_reports_once(monkeypatch):
    attempts = []

    def opener(source):
        attempts.append(source)
        if len(attempts) < 3:
            raise OSError("Entrée audio « Micro » introuvable.")
        return FakeCapture("Micro")

    monkeypatch.setattr(audio, "open_capture", opener)
    clock = [100.0]
    monkeypatch.setattr(audio.time, "monotonic", lambda: clock[0])
    lines, got = [], []
    live = audio.LiveCapture("in:Micro", lambda data, frames: got.append(frames), lines.append)

    live.poll()
    live.poll()  # too early: no second attempt
    assert len(attempts) == 1 and "introuvable" in live.problem
    clock[0] += audio.LiveCapture.RETRY
    live.poll()
    assert len(attempts) == 2 and len(lines) == 1  # same problem: said once
    clock[0] += audio.LiveCapture.RETRY
    live.poll()
    assert live.problem == "" and live.format == (48000, 2) and live.generation == 1
    assert lines[-1] == "Son : Micro (48000 Hz, 2 canaux)"

    live._capture.on_frames(float_bytes(0.0, 0.25), 1)
    assert got == [1] and live.level() == 0.25


def test_live_capture_reopens_a_dead_source(monkeypatch):
    captures = []

    def opener(source):
        captures.append(FakeCapture("Chrome"))
        return captures[-1]

    monkeypatch.setattr(audio, "open_capture", opener)
    clock = [50.0]
    monkeypatch.setattr(audio.time, "monotonic", lambda: clock[0])
    live = audio.LiveCapture("app:chrome.exe", lambda data, frames: None)
    live.poll()
    captures[0].is_alive = False  # the program quit
    clock[0] += audio.LiveCapture.RETRY
    live.poll()
    assert captures[0].closed and "interrompue" in live.problem and live.level() == 0.0
    clock[0] += audio.LiveCapture.RETRY
    live.poll()
    assert len(captures) == 2 and live.problem == "" and live.generation == 2


def test_live_capture_refuses_another_format(monkeypatch):
    monkeypatch.setattr(audio, "open_capture", lambda source: FakeCapture("Casque", 44100, 2))
    live = audio.LiveCapture("loopback", lambda data, frames: None, expected=(48000, 2))
    live.poll()
    assert "relancer" in live.problem and live.format is None


class FakePipe:
    def __init__(self):
        self.data = bytearray()
        self.closed = False
        self.first_write = threading.Event()

    def write(self, chunk):
        self.data += chunk
        self.first_write.set()

    def flush(self):
        pass

    def close(self):
        self.closed = True


def test_pipe_is_fed_even_when_nothing_can_be_captured(monkeypatch):
    # The bug this guards against: ffmpeg waits for its audio input before
    # sending the picture, so an empty pipe froze the whole emission.
    def opener(source):
        raise OSError("Le programme « absent.exe » ne tourne pas.")

    monkeypatch.setattr(audio, "open_capture", opener)
    pipe = FakePipe()
    feeder = audio.PipeFeeder("app:absent.exe", 48000, 2)
    lines = []
    stop = feeder(pipe, lines.append)
    try:
        assert pipe.first_write.wait(2.0)
        time.sleep(0.4)
        assert len(pipe.data) % 8 == 0 and set(pipe.data) == {0}
        assert len(pipe.data) >= int(0.2 * 48000) * 8  # real-time silence, not just the first block
        assert "absent.exe" in feeder.audio_problem and feeder.audio_level() == 0.0
        assert any("absent.exe" in line for line in lines)
    finally:
        stop()
    deadline = time.time() + 2
    while not pipe.closed and time.time() < deadline:
        time.sleep(0.02)
    assert pipe.closed


def test_pipe_forwards_captured_samples_untouched(monkeypatch):
    capture = FakeCapture("Carte son")
    monkeypatch.setattr(audio, "open_capture", lambda source: capture)
    pipe = FakePipe()
    feeder = audio.PipeFeeder("in:Carte son", 48000, 2)
    stop = feeder(pipe)
    try:
        deadline = time.time() + 2
        while capture.on_frames is None and time.time() < deadline:
            time.sleep(0.01)
        payload = float_bytes(0.5, -0.5) * 480
        capture.on_frames(payload, 480)
        deadline = time.time() + 2
        while payload not in bytes(pipe.data) and time.time() < deadline:
            time.sleep(0.01)
        assert payload in bytes(pipe.data)
        assert feeder.audio_level() == 0.5 and feeder.audio_problem == ""
    finally:
        stop()
