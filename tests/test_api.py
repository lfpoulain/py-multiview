from py_multiview.api import dispatch
from py_multiview.config import normalize_config
from py_multiview.control import _match, _wanted


class FakeControl:
    """Records the verbs the router resolves, and answers like the real one."""

    def __init__(self):
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, *args))
        return {"ok": name}

    def status(self):
        return self._record("status")

    def sources(self):
        self.calls.append(("sources",))
        return ["PC (Master)"]

    def set_reception(self, mode):
        return self._record("reception", mode)

    def set_stream(self, ref, mode):
        return self._record("stream", ref, mode)

    def set_stream_mute(self, ref, mode):
        return self._record("mute", ref, mode)

    def set_sender(self, mode):
        return self._record("sender", mode)

    def set_monitor(self, mode):
        return self._record("monitor", mode)

    def set_monitor_volume(self, value):
        return self._record("volume", value)

    def set_monitor_source(self, name):
        return self._record("source", name)

    def set_floating(self, mode):
        return self._record("floating", mode)

    def set_route(self, ref, mode):
        return self._record("route", ref, mode)


def test_status_and_sources_are_read_only():
    control = FakeControl()
    assert dispatch("GET", "/status", control)[0] == 200
    assert dispatch("GET", "/sources", control) == (200, {"sources": ["PC (Master)"]})
    status, body = dispatch("POST", "/status", control)
    assert status == 405 and "GET /status" in body["error"]["message"]


def test_commands_follow_the_livedeck_shape():
    control = FakeControl()
    for path, expected in [
        ("/reception/on", ("reception", "on")),
        ("/sender/toggle", ("sender", "toggle")),
        ("/monitor/off", ("monitor", "off")),
        ("/monitor/volume/40", ("volume", 40)),
        ("/floating/on", ("floating", "on")),
        ("/stream/2/on", ("stream", "2", "on")),
        ("/stream/Camera 1/mute/toggle", ("mute", "Camera 1", "toggle")),
        ("/route/Multicast/off", ("route", "Multicast", "off")),
    ]:
        status, _ = dispatch("POST", path, control)
        assert status == 200, path
        assert control.calls[-1] == expected


def test_commands_refuse_get():
    status, body = dispatch("GET", "/sender/on", FakeControl())
    assert status == 405 and body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_percent_encoded_source_name_is_decoded():
    control = FakeControl()
    dispatch("POST", "/monitor/source/R%C3%89GIE%20(Master)", control)
    assert control.calls[-1] == ("source", "RÉGIE (Master)")


def test_bad_values_are_reported_as_json():
    status, body = dispatch("POST", "/sender/blink", FakeControl())
    assert status == 422 and body["error"]["code"] == "BAD_VALUE"
    status, body = dispatch("POST", "/monitor/volume/150", FakeControl())
    assert status == 422 and "0 à 100" in body["error"]["message"]
    status, body = dispatch("POST", "/nope", FakeControl())
    assert status == 404 and "GET /status" in body["error"]["message"]


def test_query_string_is_ignored():
    control = FakeControl()
    assert dispatch("POST", "/sender/on?from=deck", control)[0] == 200
    assert control.calls[-1] == ("sender", "on")


def test_items_resolve_by_position_name_or_id():
    items = [{"id": "s-1", "name": "Caméra 1"}, {"id": "s-2", "name": "Plateau"}]
    assert _match(items, "1")["id"] == "s-1"
    assert _match(items, "2")["id"] == "s-2"
    assert _match(items, "plateau")["id"] == "s-2"
    assert _match(items, "s-1")["id"] == "s-1"
    assert _match(items, "3") is None
    assert _match(items, "") is None


def test_toggle_is_resolved_against_the_current_state():
    assert _wanted("on", False) and _wanted("on", True)
    assert not _wanted("off", True)
    assert _wanted("toggle", False) and not _wanted("toggle", True)


def test_api_settings_have_safe_defaults():
    api = normalize_config({})["api"]
    assert api == {"enabled": False, "port": 5090, "localOnly": False}
    assert normalize_config({"api": {"port": 99999}})["api"]["port"] == 65535  # borné
    assert normalize_config({"api": {"port": "nope"}})["api"]["port"] == 5090  # défaut
