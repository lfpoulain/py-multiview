from pathlib import Path

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


def make_engine(config, **tools):
    engine = Engine(FakeTools(**tools), FakeSupervisor())
    engine.host_ip = "10.0.0.5"
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
