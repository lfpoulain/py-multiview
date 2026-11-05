from pathlib import Path

from py_multiview import audio
from py_multiview.config import normalize_config
from py_multiview.engine import Engine
from py_multiview.ffmpeg import Toolchain
from py_multiview.process import ProcessInfo, Status

DISPLAYS = [
    {"id": "main", "name": "Main", "x": 0, "y": 0, "width": 1920, "height": 1080, "isPrimary": True},
    {"id": "side", "name": "Side", "x": 1920, "y": 0, "width": 1920, "height": 1080, "isPrimary": False},
]


class FakeTools:
    def __init__(self, srt=True, omt=True):
        self.std = Toolchain("std", "", Path("ffmpeg"), Path("ffplay"), srt=True) if srt else None
        self.omt = Toolchain("omt", "", Path("ffmpeg-omt"), Path("ffplay-omt"), omt=True) if omt else None

    def player_for(self, source):
        return self.omt if source == "omt" else self.std

    def ffmpeg_for(self, need):
        return self.omt if need == "omt" else self.std


class FakeSupervisor:
    """Records what the engine wants running instead of spawning processes."""

    def __init__(self):
        self.running = {}

    def ensure(self, key, spec):
        self.running[key] = spec

    def stop(self, key):
        self.running.pop(key, None)

    def stop_all(self):
        self.running.clear()

    def keys(self, prefix=""):
        return [k for k in self.running if k.startswith(prefix)]

    def tick(self):
        pass

    def info(self, key):
        return ProcessInfo(Status.ACTIVE if key in self.running else Status.STOPPED)


class FakeWorker:
    """Stands in for a libomt worker: records what the engine asks of it."""

    def __init__(self, key):
        self.key = key
        self.params = None
        self.volume = None
        self.running = False
        self.log = []

    def ensure(self, params):
        self.params = params
        self.running = True

    def set_volume(self, percent):
        self.volume = percent

    def stop(self):
        self.running = False

    def alive(self):
        return self.running

    def info(self):
        return ProcessInfo(Status.ACTIVE if self.running else Status.STOPPED)


def make_engine(config, native=False, **tools):
    """``native=True`` exercises the libomt path, otherwise the FFmpeg fallback."""
    workers: dict[str, FakeWorker] = {}

    def factory(key):
        if not native:
            return None
        workers[key] = FakeWorker(key)
        return workers[key]

    engine = Engine(FakeTools(**tools), FakeSupervisor(), worker_factory=factory)
    engine.host_ip = "10.0.0.5"
    engine.fake_workers = workers
    engine.update(normalize_config({"displayIdSchemaVersion": 2, **config}), DISPLAYS)
    return engine


def test_stream_without_screen_reports_error():
    engine = make_engine({"streams": [{"id": "a", "name": "A"}]})
    assert engine.start_stream("a") == "Aucun écran assigné."
    assert engine.stream_info("a").status == Status.ERROR


def test_primary_screen_excluded():
    engine = make_engine({"streams": [{"id": "a"}], "mapping": {"a": "main"}, "excludePrimaryDisplay": True})
    assert "exclu" in engine.start_stream("a")


def test_port_conflict_second_stream_fails():
    engine = make_engine(
        {
            "streams": [{"id": "a", "name": "A", "port": 9001}, {"id": "b", "name": "B", "port": 9001}],
            "mapping": {"a": "side", "b": "side"},
        }
    )
    errors = engine.start_reception()
    assert "player:a" in engine.sup.running
    assert "« A »" in errors["b"]


def test_route_stream_starts_route_and_stop_cascades():
    engine = make_engine(
        {
            "streams": [{"id": "a", "source": "route", "sourceRouteId": "r"}],
            "routes": [{"id": "r", "name": "R"}],
            "mapping": {"a": "side"},
        }
    )
    assert engine.start_stream("a") is None
    assert {"route:r", "player:a"} <= set(engine.sup.running)
    assert engine.sup.running["player:a"].args[-1].endswith("&localaddr=10.0.0.5")
    engine.stop_route("r")
    assert not engine.sup.running


def test_missing_srt_build_is_explained():
    engine = make_engine({"streams": [{"id": "a"}], "mapping": {"a": "side"}}, srt=False)
    assert "SRT" in engine.start_stream("a")


def test_live_reconfiguration_and_unplug():
    engine = make_engine({"streams": [{"id": "a", "port": 9001}], "mapping": {"a": "side"}})
    engine.start_stream("a")
    first = engine.sup.running["player:a"].args
    engine.config["streams"][0]["port"] = 9005
    engine.reconcile()
    assert engine.sup.running["player:a"].args != first
    engine.update(engine.config, DISPLAYS[:1])  # screen unplugged
    assert "player:a" not in engine.sup.running
    assert engine.stream_info("a").status == Status.ERROR
    engine.update(engine.config, DISPLAYS)  # plugged back: resumes on its own
    assert "player:a" in engine.sup.running


def test_omt_stream_ignores_gpu_decoder():
    engine = make_engine(
        {
            "streams": [{"id": "a", "source": "omt", "omtSource": "PC (Cam)"}],
            "mapping": {"a": "side"},
            "receiver": {"decode": "nvidia"},
        }
    )
    engine.tools.std = Toolchain("std", "", Path("ffmpeg"), Path("ffplay"), srt=True, decoders=frozenset({"nvidia"}))
    engine.start_stream("a")
    args = engine.sup.running["player:a"].args
    assert "-vcodec" not in args
    assert args[-4:] == ["-f", "libomt", "-i", "PC (Cam)"]
    assert args[0] == "ffplay-omt"


def test_sender_requires_screen():
    engine = make_engine({})
    assert "écran" in engine.start_sender()
    engine.config["sender"]["displayId"] = "main"
    engine.reconcile()
    assert "sender" in engine.sup.running


def test_sender_with_dshow_audio():
    engine = make_engine({"sender": {"displayId": "main", "audioSource": "dshow:Micro"}})
    assert engine.start_sender() is None
    spec = engine.sup.running["sender"]
    assert "audio=Micro" in spec.args and spec.stdin_feeder is None and spec.graceful_quit


def test_sender_loopback_unavailable_is_explained():
    engine = make_engine({"sender": {"displayId": "main", "audioSource": "loopback"}})
    engine.loopback_device = lambda: None
    assert "son du PC" in engine.start_sender()


def test_audio_monitor_needs_a_source():
    engine = make_engine({})
    assert "Aucune source OMT" in engine.start_monitor()
    engine.config["monitor"]["omtSource"] = "RÉGIE (Master)"
    engine.reconcile()
    args = engine.sup.running["monitor"].args
    assert args[0] == "ffplay-omt"
    assert args[-4:] == ["-f", "libomt", "-i", "RÉGIE (Master)"]
    # No window and no video decoding: only the sound of the master return.
    assert "-nodisp" in args and "-vn" in args


def test_audio_monitor_follows_volume_and_stops():
    engine = make_engine({"monitor": {"omtSource": "RÉGIE (Master)", "volume": 40}})
    assert engine.start_monitor() is None
    args = engine.sup.running["monitor"].args
    assert args[args.index("-volume") + 1] == "40"
    engine.stop_monitor()
    assert "monitor" not in engine.sup.running


def test_native_monitor_is_preferred_over_ffplay():
    engine = make_engine({"monitor": {"omtSource": "RÉGIE (Master)", "volume": 40}}, native=True)
    assert engine.start_monitor() is None
    worker = engine.fake_workers["monitor"]
    assert worker.params == ("RÉGIE (Master)",)
    assert worker.volume == 40
    assert "monitor" not in engine.sup.running  # no ffplay alongside it
    # Volume is applied live: it must not restart the receiver.
    engine.config["monitor"]["volume"] = 70
    engine.reconcile()
    assert worker.volume == 70 and worker.params == ("RÉGIE (Master)",)
    engine.stop_monitor()
    assert not worker.running


def test_native_sender_publishes_the_screen_geometry():
    engine = make_engine(
        {"sender": {"displayId": "side", "name": "Régie", "fps": 50, "native": True}}, native=True
    )
    assert engine.start_sender() is None
    worker = engine.fake_workers["sender"]
    assert worker.params == ("Régie", 1920, 0, 1920, 1080, 50, "")
    assert "sender" not in engine.sup.running


def test_ffmpeg_sender_is_the_default():
    # Le sender libomt donne une image noire sur certaines machines : même
    # quand il est disponible, c'est gdigrab qui émet sauf demande explicite.
    engine = make_engine({"sender": {"displayId": "side", "name": "Régie"}}, native=True)
    assert engine.config["sender"]["native"] is False
    assert engine.start_sender() is None
    assert not engine.fake_workers.get("sender", FakeWorker("sender")).running
    args = engine.sup.running["sender"].args
    assert "gdigrab" in args and "libomt" in args


def test_program_audio_reaches_ffmpeg_through_a_pipe():
    engine = make_engine({"sender": {"displayId": "side", "audioSource": "app:chrome.exe"}})
    assert engine.start_sender() is None
    spec = engine.sup.running["sender"]
    # Windows livre le son du programme en float : il arrive par stdin, comme
    # le son du PC, et non par un périphérique DirectShow.
    assert "-f" in spec.args and "pipe:0" in spec.args
    assert "dshow" not in spec.args
    assert isinstance(spec.stdin_feeder, audio.ProgramFeeder)
    assert spec.stdin_feeder.source == "app:chrome.exe"


def test_floating_window_uses_the_listening_source_by_default():
    engine = make_engine({"monitor": {"omtSource": "RÉGIE (Master)", "floatWidth": 800}})
    assert engine.start_floating() is None
    args = engine.sup.running["floating"].args
    assert args[-4:] == ["-f", "libomt", "-i", "RÉGIE (Master)"]
    assert args[args.index("-x") + 1] == "800"
    assert "-alwaysontop" in args and "-fs" not in args


def test_floating_window_can_watch_another_source():
    engine = make_engine(
        {"monitor": {"omtSource": "RÉGIE (Master)", "floatSource": "CAM (Plateau)",
                     "floatOnTop": False, "floatVolume": 25}}
    )
    assert engine.start_floating() is None
    args = engine.sup.running["floating"].args
    assert args[-1] == "CAM (Plateau)"
    assert args[args.index("-volume") + 1] == "25"
    assert "-alwaysontop" not in args
    engine.stop_floating()
    assert "floating" not in engine.sup.running


def test_native_sender_reports_a_missing_audio_input():
    engine = make_engine(
        {"sender": {"displayId": "side", "audioSource": "dshow:Absent", "native": True}}, native=True
    )
    error = engine.start_sender()
    assert error and "Absent" in error
    assert not engine.fake_workers["sender"].running
