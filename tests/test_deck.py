"""The /api/deck contract shared with orchestream-v2 (what py-livedeck reads)."""

import copy
import json
import threading
import urllib.error
import urllib.request

import pytest

from py_multiview import deck
from py_multiview.api import ApiError, ApiServer, check_token, dispatch


def make_status():
    return {
        "app": "py-multiview",
        "version": "0.0.0",
        "connected": True,
        "reception": {"running": True, "active": 1, "total": 2},
        "streams": [
            {"index": 1, "id": "s-1", "name": "Caméra 1", "source": "srt", "running": True,
             "status": "active", "detail": "", "muted": False, "display": "Écran 2"},
            {"index": 2, "id": "s-2", "name": "Plateau", "source": "omt", "running": False,
             "status": "stopped", "detail": "", "muted": True, "display": ""},
        ],
        "sender": {"running": False, "status": "stopped", "detail": "", "name": "Régie", "audio": "", "fps": 25},
        "monitor": {"running": True, "status": "active", "detail": "", "source": "RÉGIE", "volume": 100},
        "floating": {"running": False, "status": "stopped", "detail": "", "source": "RÉGIE"},
        "routes": [{"index": 1, "id": "r-1", "name": "Multicast", "running": True, "status": "waiting"}],
    }


class FakeControl:
    """Verbs as Control exposes them, recording what the deck asked."""

    def __init__(self, status=None, token=False):
        self._status = status or make_status()
        self.token = token
        self.calls = []

    def status(self):
        return self._status

    def deck_state(self):
        return deck.build_state(self._status, token_set=self.token)

    def deck_commands(self):
        return deck.build_commands(self._status, with_values=True)

    def _rec(self, *call):
        self.calls.append(call)
        return self._status

    def set_reception(self, mode):
        return self._rec("reception", mode)

    def set_sender(self, mode):
        return self._rec("sender", mode)

    def set_monitor(self, mode):
        return self._rec("monitor", mode)

    def set_floating(self, mode):
        return self._rec("floating", mode)

    def set_stream(self, ref, mode):
        if ref not in ("s-1", "s-2", "1", "2"):
            raise ApiError(404, "NOT_FOUND", f"flux « {ref} » inconnu")
        return self._rec("stream", ref, mode)

    def set_stream_mute(self, ref, mode):
        return self._rec("mute", ref, mode)

    def set_route(self, ref, mode):
        return self._rec("route", ref, mode)

    def set_monitor_volume(self, value):
        return self._rec("volume", value)

    def set_monitor_source(self, name):
        return self._rec("source", name)


def keys_by_id(state):
    return {k["id"]: k for k in state["keys"]}


# -- state and keys ------------------------------------------------------------


def test_state_has_what_livedeck_checks():
    state = deck.build_state(make_status(), token_set=False)
    assert isinstance(state["commands"], list) and isinstance(state["keys"], list)
    assert state["token"]["name"] and state["connected"] is True
    assert state["streams"][0]["name"] == "Caméra 1"  # the /status sections are kept
    assert state["updatedAt"].endswith("Z")


def test_keys_are_described_by_the_server():
    keys = keys_by_id(deck.build_state(make_status(), token_set=False))
    for key in keys.values():
        assert set(key) == {"id", "group", "command", "args", "title", "subtitle", "active", "tone",
                            "available", "reason"}
    cam = keys["stream.toggle:s-1"]
    assert (cam["command"], cam["args"], cam["title"]) == ("stream.toggle", {"stream": "s-1"}, "Caméra 1")
    assert cam["active"] and cam["tone"] == "success" and cam["subtitle"] == "Actif" and cam["available"]

    plateau = keys["stream.toggle:s-2"]
    assert not plateau["available"] and plateau["reason"] == "Aucun écran assigné."
    assert keys["stream.muteToggle:s-2"]["active"] and keys["stream.muteToggle:s-2"]["tone"] == "danger"
    assert keys["route.toggle:r-1"]["tone"] == "warning"
    assert keys["monitor.volumeUp"]["available"] is False  # already at 100
    assert keys["monitor.volumeDown"]["available"] is True
    assert {"reception.toggle", "sender.toggle", "monitor.toggle", "floating.toggle"} <= set(keys)


def test_every_key_runs_a_known_command():
    ids = {c["id"] for c in deck.COMMANDS}
    for key in deck.build_keys(make_status()):
        assert key["command"] in ids


def test_commands_list_argument_values():
    commands = {c["id"]: c for c in deck.build_commands(make_status(), with_values=True)}
    values = commands["stream.toggle"]["args"][0]["values"]
    assert values == [{"value": "s-1", "label": "Caméra 1"}, {"value": "s-2", "label": "Plateau"}]
    assert commands["monitor.volume"]["args"][0]["max"] == 100
    assert commands["monitor.volumeUp"]["available"] is False


def test_patch_carries_only_what_changed():
    old = deck.build_state(make_status(), token_set=False)
    assert deck.build_patch(old, copy.deepcopy(old)) is None

    status = make_status()
    status["sender"].update(running=True, status="active")
    status["routes"] = []
    new = deck.build_state(status, token_set=False)
    patch = deck.build_patch(old, new)
    assert set(patch["changed"]) >= {"sender", "routes"}
    assert "streams" not in patch["changed"]
    assert [k["id"] for k in patch["keys"]] == ["sender.toggle"]
    assert patch["keys"][0]["tone"] == "live"
    assert patch["removedKeys"] == ["route.toggle:r-1"]


# -- commands ------------------------------------------------------------------


def test_run_command_maps_onto_control():
    control = FakeControl()
    result = deck.run_command(control, "stream.toggle", {"stream": "s-1"})
    assert result["ok"] is True and result["data"]["app"] == "py-multiview"
    deck.run_command(control, "sender.start", {})
    deck.run_command(control, "stream.mute", {"stream": "2"})
    deck.run_command(control, "route.stop", {"route": "r-1"})
    deck.run_command(control, "monitor.volumeDown", {})
    deck.run_command(control, "monitor.volume", {"volume": "40"})
    assert control.calls == [("stream", "s-1", "toggle"), ("sender", "on"), ("mute", "2", "on"),
                             ("route", "r-1", "off"), ("volume", 95), ("volume", 40)]


def test_run_command_errors_follow_orchestream():
    control = FakeControl()
    with pytest.raises(ApiError) as exc:
        deck.run_command(control, "nope", {})
    assert (exc.value.status, exc.value.code) == (404, "COMMAND_NOT_FOUND")
    with pytest.raises(ApiError) as exc:
        deck.run_command(control, "stream.toggle", {})
    assert (exc.value.status, exc.value.code) == (422, "VALIDATION_FAILED")
    assert "args.stream" in exc.value.details
    # An unknown stream is a bad argument, never a 404 NOT_FOUND (py-livedeck would
    # read that as "this server refuses its public address").
    with pytest.raises(ApiError) as exc:
        deck.run_command(control, "stream.toggle", {"stream": "s-9"})
    assert (exc.value.status, exc.value.code) == (422, "VALIDATION_FAILED")
    with pytest.raises(ApiError) as exc:
        deck.run_command(control, "monitor.volume", {"volume": 150})
    assert exc.value.status == 422


def test_dispatch_deck_routes():
    control = FakeControl()
    status, body = dispatch("GET", "/api/deck/state", control)
    assert status == 200 and "keys" in body
    status, body = dispatch("GET", "/api/deck/commands", control)
    assert status == 200 and body["commands"][0]["id"] == "reception.start"
    status, body = dispatch("POST", "/api/deck/commands/stream.toggle", control,
                            json.dumps({"args": {"stream": "s-1"}}).encode())
    assert status == 200 and body["ok"]
    status, body = dispatch("POST", "/api/deck/commands/sender.toggle", control, b"")
    assert status == 200
    status, body = dispatch("POST", "/api/deck/commands/sender.toggle", control, b"{oops")
    assert status == 422 and body["error"]["code"] == "VALIDATION_FAILED"
    status, body = dispatch("GET", "/api/deck/commands/sender.toggle", control)
    assert status == 405
    status, body = dispatch("GET", "/api/deck/nope", control)
    assert status == 404 and body["error"]["code"] != "NOT_FOUND"


def test_token_is_optional():
    assert check_token("", None) is None
    assert check_token("", "Bearer whatever") is None
    assert check_token("abc", "Bearer abc") is None
    assert check_token("abc", "bearer abc") is None
    for header in (None, "", "Bearer nope", "abc", "Basic abc"):
        error = check_token("abc", header)
        assert error.status == 401 and error.code == "INVALID_TOKEN"


# -- over a real socket ----------------------------------------------------------


@pytest.fixture
def server():
    control = FakeControl()

    def call(method, path, body=b""):
        return dispatch(method, path, control, body)

    api = ApiServer(call)
    assert api.start("127.0.0.1", 0) == "" or api.running
    yield api, control
    api.stop()


def _port(api):
    return api._server.server_address[1]


def _request(api, path, method="GET", body=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{_port(api)}{path}", data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_token_guards_every_route(server):
    api, _ = server
    assert _request(api, "/status")[0] == 200
    api.token = "secret"
    status, body = _request(api, "/status")
    assert status == 401 and body["error"]["code"] == "INVALID_TOKEN"
    assert _request(api, "/api/deck/state", token="wrong")[0] == 401
    assert _request(api, "/api/deck/state", token="secret")[0] == 200
    status, body = _request(api, "/api/deck/commands/sender.toggle", "POST", b"{}", token="secret")
    assert status == 200 and body["ok"]


def _read_event(resp):
    lines = []
    while True:
        line = resp.readline().decode("utf-8").rstrip("\n")
        if line == "":
            if lines:
                break
            continue
        lines.append(line)
    if lines[0].startswith(":"):
        return "comment", None
    name = lines[0].split(": ", 1)[1]
    data = json.loads(lines[1].split(": ", 1)[1])
    return name, data


def test_events_stream_state_then_patch(server):
    api, control = server
    req = urllib.request.Request(f"http://127.0.0.1:{_port(api)}/api/deck/events",
                                 headers={"Accept": "text/event-stream"})
    resp = urllib.request.urlopen(req, timeout=5)
    try:
        assert "text/event-stream" in resp.headers["Content-Type"]
        name, state = _read_event(resp)
        assert name == "state" and "keys" in state

        control._status["sender"].update(running=True, status="active")
        done = threading.Event()

        def press():
            _request(api, "/api/deck/commands/sender.start", "POST", b"")
            done.set()

        threading.Thread(target=press, daemon=True).start()
        name, patch = _read_event(resp)
        assert name == "patch"
        assert "sender" in patch["changed"]
        assert [k["id"] for k in patch["keys"]] == ["sender.toggle"]
        assert done.wait(5)
    finally:
        resp.close()
