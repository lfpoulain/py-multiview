import math
import struct
import sys
import threading
import time
from array import array
from pathlib import Path

import pytest

from py_multiview import audio, native
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
    # The WAV header supplies the device format; the demuxer reads at most 20 ms.
    pipe = args.index("pipe:0")
    assert args[args.index("-probesize") + 1] == "32" and args.index("-probesize") < pipe
    assert args[args.index("-analyzeduration") + 1] == "0"
    assert args[pipe - 7:pipe] == ["-f", "wav", "-ignore_length", "1", "-max_size", str(882 * 2 * 4), "-i"]
    assert "-an" not in args and "1:a:0" in args
    assert args[-3:-1] == ["-f", "libomt"]


def test_sender_with_dshow_device():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], dshow_audio_input_args("Micro (USB)"))
    assert "audio=Micro (USB)" in args


def test_sender_limits_interleaving_according_to_the_output_clock():
    sender = normalize_config({})["sender"]
    args = build_sender_command(Path("ffmpeg"), DISPLAY, sender, dshow_audio_input_args("Micro"))
    assert args[args.index("-af") + 1].startswith("aresample=48000:async=1:min_hard_comp=0.100:first_pts=0,")
    assert args[args.index("-max_interleave_delta") + 1] == "500000"
    assert "-audio_preload" not in args
    # The libomt clocks sleep on one thread: keep a full video interval
    # sortable even for a slow picture. With explicit PTS, bound the wait.
    sender["fps"] = 1
    args = build_sender_command(Path("ffmpeg"), DISPLAY, sender, pipe_audio_input_args(48000, 2))
    assert int(args[args.index("-max_interleave_delta") + 1]) >= 1_000_000
    sender["clockOutput"] = False
    args = build_sender_command(Path("ffmpeg"), DISPLAY, sender, pipe_audio_input_args(48000, 2))
    assert args[args.index("-max_interleave_delta") + 1] == "1"
    assert int(args[args.index("-audio_preload") + 1]) >= 1_000_000
    sender["fps"] = 30
    args = build_sender_command(Path("ffmpeg"), DISPLAY, sender, pipe_audio_input_args(48000, 2))
    assert args[args.index("-audio_preload") + 1] == "60000"


def test_sender_audio_leaves_in_20_ms_frames_on_the_omt_clock():
    # FFmpeg reads raw PCM 4096 samples at a time: sent as they are, the
    # receiver gets 85 ms blocks in bursts and runs dry between two of them.
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], pipe_audio_input_args(48000, 2))
    assert args[args.index("-af") + 1] == "aresample=48000,asetnsamples=n=960:p=0"
    assert args[args.index("-clock_output") + 1] == "1"


def test_output_clock_is_on_by_default_and_can_still_be_turned_off():
    assert normalize_config({})["sender"]["clockOutput"] is True
    # Written while the box was greyed out: nobody chose that False.
    old = normalize_config({"sender": {"clockOutput": False}})["sender"]
    assert old["clockOutput"] is True
    old["clockOutput"] = False  # unticked since: remembered
    again = normalize_config({"sender": old})["sender"]
    assert again["clockOutput"] is False
    args = build_sender_command(Path("ffmpeg"), DISPLAY, again)
    assert args[args.index("-clock_output") + 1] == "0"


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

    @property
    def pcm(self):
        return bytes(self.data[44:])

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
        assert struct.unpack("<4sI4s4sIHHIIHH4sI", pipe.data[:44]) == (
            b"RIFF", 0xffffffff, b"WAVE", b"fmt ", 16, 3, 2, 48000, 384000, 8, 32, b"data", 0,
        )
        assert len(pipe.pcm) % (960 * 8) == 0 and set(pipe.pcm) == {0}
        assert len(pipe.pcm) >= int(0.2 * 48000) * 8  # real-time silence, not just the first block
        assert "absent.exe" in feeder.audio_problem and feeder.audio_level() == 0.0
        assert any("absent.exe" in line for line in lines)
    finally:
        stop()
    deadline = time.time() + 2
    while not pipe.closed and time.time() < deadline:
        time.sleep(0.02)
    assert pipe.closed


def test_pipe_forwards_captured_samples(monkeypatch):
    capture = FakeCapture("Carte son")
    monkeypatch.setattr(audio, "open_capture", lambda source: capture)
    pipe = FakePipe()
    feeder = audio.PipeFeeder("in:Carte son", 48000, 2)
    stop = feeder(pipe)
    try:
        deadline = time.time() + 2
        while capture.on_frames is None and time.time() < deadline:
            time.sleep(0.01)
        capture.on_frames(float_bytes(0.5, -0.5) * 480, 480)
        deadline = time.time() + 2
        while audio.peak(pipe.pcm) < 0.5 and time.time() < deadline:
            time.sleep(0.01)
        loud = [v for v in array("f", pipe.pcm) if v]
        assert loud == [0.5, -0.5] * 480  # a short isolated sound survives priming
        assert feeder.audio_level() == 0.5 and feeder.audio_problem == ""
    finally:
        stop()


# ---------------------------------------------------------- steady stream


class Card:
    """A sound card feeding a SteadyStream, on a clock of its own."""

    def __init__(self, drift_ppm=0.0, rate=48000, chunk=960):
        self.now = 1000.0
        self.stream = audio.SteadyStream(rate, 1, clock=lambda: self.now)
        self.rate, self.chunk = rate, chunk
        self.card_rate = rate * (1 + drift_ppm * 1e-6)
        self.phase = 0
        self.owed = 0.0
        self.out = array("f")

    def run(self, seconds, playing=True, step=0.005):
        for _ in range(int(seconds / step)):
            self.now += step
            if playing:
                self.owed += step * self.card_rate
                while self.owed >= self.chunk:
                    self.owed -= self.chunk
                    ramp = array("f", [((self.phase + i) % 4800) / 4800 for i in range(self.chunk)])
                    self.phase += self.chunk
                    self.stream.push(ramp.tobytes())
            self.out.frombytes(self.stream.pull())

    def breaks(self):
        """Places where the ramp does not go on: a click to the ear."""
        step = 1 / 4800
        found = 0
        for i in range(1, len(self.out)):
            a, b = self.out[i - 1], self.out[i]
            if a == 0.0 or b == 0.0:
                continue  # silence, or the ramp starting again from zero
            delta = b - a
            if not (0 <= delta <= 2.5 * step) and abs(delta + 1) > 3 * step:
                found += 1
        return found


def test_stretch_keeps_both_ends_and_the_shape():
    ramp = array("f", [i / 100 for i in range(100)]).tobytes()
    for delta in (-2, -1, 1, 3):
        out = array("f", audio.stretch(ramp, 1, delta))
        assert len(out) == 100 + delta
        assert out[0] == 0.0 and abs(out[-1] - 0.99) < 1e-6
        assert all(out[i + 1] > out[i] for i in range(len(out) - 1))
    assert audio.stretch(ramp, 1, 0) is ramp
    stereo = array("f", [v for i in range(50) for v in (i / 50, -i / 50)]).tobytes()
    out = array("f", audio.stretch(stereo, 2, -1))
    assert len(out) == 2 * 49 and all(abs(out[i] + out[i + 1]) < 1e-6 for i in range(0, len(out), 2))


@pytest.mark.parametrize("delta", [-4, -1, 1, 4])
def test_clock_correction_preserves_treble_throughout_the_packet(delta):
    # A constant marker survives even linear interpolation. Independent
    # high-frequency tones reveal the gain dips within a corrected packet.
    def stereo(t):
        return (0.5 * math.sin(2 * math.pi * 12000 / 48000 * t),
                0.4 * math.cos(2 * math.pi * 18000 / 48000 * t))

    original = array("f", (v for i in range(960) for v in stereo(i)))
    output = array("f", audio.stretch(original.tobytes(), 2, delta))
    frames = 960 + delta
    assert len(output) == frames * 2
    for i in range(16, frames - 16):
        expected = stereo(i * 959 / (frames - 1))
        assert max(abs(output[2 * i + c] - expected[c]) for c in range(2)) < 0.002
    assert output[:2] == original[:2] and output[-2:] == original[-2:]


def test_steady_stream_follows_the_clock_without_a_click():
    # The bug this guards against: with a sound card slower than the PC's
    # clock, silence was slipped between real samples as soon as the stream
    # fell 60 ms behind -- a click every few seconds, for ever.
    for drift in (-800.0, 0.0, 800.0):
        card = Card(drift)
        card.run(120)
        assert card.breaks() == 0, drift
        assert card.stream.resyncs == 0 and card.stream.dropped == 0
        written, due = card.stream.written, 120 * 48000
        assert abs(written - (due - audio.SteadyStream.PREFILL * 48000)) <= card.chunk
        if drift:
            # What was added or removed is the drift itself (4608 frames
            # here), give or take the few tens of ms of settling at the start.
            assert abs(card.stream.adjusted + drift * 1e-6 * due) < 0.05 * 48000


def test_steady_stream_makes_up_silence_only_when_the_source_stops():
    card = Card()
    card.run(1.0, playing=False)  # nothing captured yet: silence, a cushion behind
    assert len(card.out) and not any(card.out)
    assert 0.9 * 48000 <= card.stream.written <= 0.95 * 48000
    card.run(2.0)
    card.run(0.08, playing=False)  # a late callback is not a silence
    silent = card.stream.written
    card.run(1.0, playing=False)  # the PC plays nothing: a loopback goes quiet
    assert card.stream.written - silent > 0.8 * 48000
    card.run(2.0)
    assert card.breaks() == 0 and card.stream.resyncs == 0
    due = (card.now - 1000.0 - audio.SteadyStream.PREFILL) * 48000
    assert abs(card.stream.written - due) <= card.chunk + 1


def test_steady_stream_jumps_once_after_a_long_hole():
    card = Card()
    card.run(2.0)
    card.now += 3600.0  # the PC slept
    card.stream.push(array("f", [0.5] * 960).tobytes())
    data = card.stream.pull()
    # The hour is skipped, not sent as silence; the stream is on time again.
    assert len(data) == 4 * card.chunk and card.stream.resyncs == 1
    due = (card.now - 1000.0 - audio.SteadyStream.PREFILL) * 48000
    assert abs(card.stream.written - due) <= card.chunk


@pytest.mark.parametrize(("late_frames", "silent_frames"), [(1, 0), (240, 240), (960, 960)])
def test_brief_capture_shortfall_does_not_restart_playout(late_frames, silent_frames):
    # 80 ms of callback delay drains the reserve at t=4 s. The missing
    # samples arrive one callback later, while the output clock keeps running.
    frame = float_bytes(0.25, -0.5)
    stream = audio.SteadyStream(48000, 2, origin=0.0, clock=lambda: ms / 1000)
    for ms in range(4501):
        if 0 < ms < 4000 and ms % 20 == 0:
            stream.push(frame * 960)
        elif ms >= 4080 and ms % 20 == 0:
            frames = 960 - late_frames if ms == 4080 else 960
            stream.push(frame * frames)
        elif ms == 4090:
            stream.push(frame * late_frames)
        data = stream.pull()
        if ms == 4080:
            assert data == frame * (960 - silent_frames) + bytes(8 * silent_frames)
        elif ms >= 4000 and ms % 20 == 0:
            assert data == frame * 960, ms
    assert stream.resyncs == stream.dropped == 0


def test_steady_stream_keeps_half_a_second_when_nobody_pulls():
    now = 0.0
    stream = audio.SteadyStream(48000, 2, clock=lambda: now)
    for _ in range(100):
        stream.push(bytes(8 * 960))
    assert stream.dropped == 100 * 960 - 24000
    now = stream.PREFILL
    assert len(stream.pull()) == 8 * 960


def test_oversized_callback_and_late_consumer_keep_only_recent_audio():
    now = 0.0
    stream = audio.SteadyStream(48000, 2, clock=lambda: now)
    now = 2.0
    recent_frames = round(stream.PREFILL * 48000)
    recent = float_bytes(0.25, -0.25) * recent_frames
    stream.push(bytes(8 * 48000) + recent)
    assert stream.dropped == 48000 + recent_frames - 24000
    assert stream.pull() == recent[:960 * 8]
    assert stream.resyncs == 1 and stream.dropped == 48000
    assert not stream.pull()  # no catch-up burst
    assert 0 < stream.delay() <= 0.021


def test_batched_capture_preserves_every_stereo_sample_at_regular_deadlines():
    rate, n = 48000, 960
    now = 0.0
    stream = audio.SteadyStream(rate, 2, clock=lambda: now)
    # An asymmetric waveform reveals inserted silence, reordered frames and
    # crossed channels. Six 10 ms capture callbacks wake up together, with
    # another 0..15 ms of scheduler jitter across successive batches.
    pcm = array("f", (v for i in range(60 * 48 * 20) for v in (i + 1, -i - 1))).tobytes()
    arrivals = {60 * (i + 1) + (0, 15, 5, 10)[i % 4]: i for i in range(20)}
    received, times = bytearray(), []
    start_ms = round(stream.PREFILL * 1000)
    deadlines = list(range(start_ms, start_ms + 1200, 20))
    for ms in range(deadlines[-1] + 1):
        now = ms / 1000
        if ms in arrivals:
            start = arrivals[ms] * 6 * 480 * 8
            for j in range(6):
                offset = start + j * 480 * 8
                stream.push(pcm[offset:offset + 480 * 8])
        data = stream.pull()
        if data:
            assert len(data) == n * 8
            times.append(ms)
            received.extend(data)
    assert times == deadlines
    assert received == pcm
    assert stream.adjusted == stream.resyncs == stream.dropped == 0


@pytest.mark.parametrize("ppm", [200, -800])
def test_batched_capture_with_drift_keeps_a_whole_packet_in_reserve(ppm):
    # At +200 ppm the six-callback batch eventually arrives just AFTER
    # a playout deadline. With only 80 ms including the outgoing packet,
    # the stream was two frames short at 66.72 s, then re-primed in silence.
    packet = float_bytes(0.25, -0.25) * 960
    chunk = packet[:480 * 8]
    stream = audio.SteadyStream(48000, 2, origin=0.0, clock=lambda: ms / 1000)
    card_rate = 48000 * (1 + ppm * 1e-6)
    batch = 1
    arrival = 2880 / card_rate
    jitter_ms = (0, 15, 5, 10, 2)
    for ms in range(0, 70001, 5):
        if ms / 1000 >= arrival:
            for _ in range(6):
                stream.push(chunk)
            batch += 1
            arrival = batch * 2880 / card_rate + jitter_ms[(batch - 1) % len(jitter_ms)] / 1000
        data = stream.pull()
        if ms >= 200 and ms % 20 == 0:
            assert data == packet, ms
    assert stream.dropped == stream.resyncs == 0


def test_native_audio_keeps_sending_during_slow_open_and_accepts_first_new_format_packet(monkeypatch):
    opening, release, silent_packets, heard = (threading.Event() for _ in range(4))

    class Capture(FakeCapture):
        def start(self, on_frames):
            on_frames(float_bytes(0.5) * 882, 882)  # delivered before start returns

    def open_capture(_source):
        opening.set()
        assert release.wait(3)
        return Capture("Carte", 44100, 1)

    packets = []

    class Sender:
        def send_audio(self, data, rate, channels, timestamp):
            packets.append((rate, channels, timestamp, len(data)))
            if len(packets) == 4 and not any(data):
                silent_packets.set()
            if any(data):
                assert data == float_bytes(0.5) * 882
                heard.set()

    monkeypatch.setattr(audio, "open_capture", open_capture)
    pump = native._AudioPump(Sender(), "in:Carte", lambda _line: None)
    pump.start()
    try:
        assert opening.wait(2) and silent_packets.wait(2)
        release.set()
        assert heard.wait(2)
    finally:
        release.set()
        pump.close()
    assert packets[0][:2] == (48000, 2) and packets[-1][:2] == (44100, 1)
    for i, (rate, channels, timestamp, size) in enumerate(packets):
        assert timestamp == i * 200000
        assert size == (rate // 50) * channels * 4


@pytest.mark.skipif(sys.platform != "win32", reason="WASAPI is a Windows interface")
def test_wasapi_releases_its_packet_before_calling_the_audio_pipeline(monkeypatch):
    import ctypes

    from py_multiview import proc_audio

    capture = proc_audio.ProcessCapture(1)
    payload = float_bytes(0.25, -0.25)
    buffer = ctypes.create_string_buffer(payload)
    events = []

    def next_packet(_capture, size):
        size._obj.value = 1
        return 0

    def get_buffer(_capture, data, frames, flags, pos, qpc):
        data._obj.value = ctypes.addressof(buffer)
        frames._obj.value = 1
        events.append("acquire")
        return 0

    def release(_capture, count):
        assert count == 1
        events.append("release")
        buffer.raw = bytes(len(buffer))  # driver may reuse the memory immediately

    def delivered(data, count):
        capture._stop.set()
        events.append((data, count))

    monkeypatch.setattr(ctypes.windll.kernel32, "WaitForSingleObject", lambda *_args: 0)
    monkeypatch.setattr(proc_audio, "_cc_next_packet_size", next_packet)
    monkeypatch.setattr(proc_audio, "_cc_get_buffer", get_buffer)
    monkeypatch.setattr(proc_audio, "_cc_release_buffer", release)
    capture._on_frames = delivered
    capture._read_packets()
    assert events == ["acquire", "release", (payload, 1)]
