"""The actions the local API exposes, on top of the engine.

Everything here runs on the UI thread: the HTTP thread hands a callable over
and waits for it, so the engine is never touched from two threads at once.

Streams and routes are addressed the way an operator thinks of them — by
position (``1``, ``2``…), by name, or by internal id — because a deck button
holds a fixed label, not a generated identifier.
"""

from __future__ import annotations

from . import __version__, omtlib
from .api import ApiError, bad_value, not_found
from .process import Status


def _match(items: list[dict], ref: str) -> dict | None:
    """A stream or route by 1-based position, name (case-insensitive), or id."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        index = int(ref) - 1
        if 0 <= index < len(items):
            return items[index]
        return None
    lowered = ref.casefold()
    for item in items:
        if item.get("id") == ref:
            return item
    for item in items:
        if str(item.get("name", "")).casefold() == lowered:
            return item
    return None


def _wanted(mode: str, currently: bool) -> bool:
    return not currently if mode == "toggle" else mode == "on"


class Control:
    """Implements the verbs of :mod:`py_multiview.api` against the window."""

    def __init__(self, window) -> None:
        self.window = window

    # -- helpers -----------------------------------------------------------

    @property
    def engine(self):
        return self.window.engine

    @property
    def config(self) -> dict:
        return self.window.config

    def _streams(self) -> list[dict]:
        return list(self.config.get("streams", []))

    def _routes(self) -> list[dict]:
        return list(self.config.get("routes", []))

    def _stream(self, ref: str) -> dict:
        found = _match(self._streams(), ref)
        if found is None:
            known = ", ".join(f"{i + 1}={s['name']}" for i, s in enumerate(self._streams())) or "aucun"
            raise not_found(f"flux « {ref} » inconnu (flux : {known})")
        return found

    def _route(self, ref: str) -> dict:
        found = _match(self._routes(), ref)
        if found is None:
            known = ", ".join(f"{i + 1}={r['name']}" for i, r in enumerate(self._routes())) or "aucune"
            raise not_found(f"route « {ref} » inconnue (routes : {known})")
        return found

    def _apply(self) -> dict:
        """Persist, push to the engine, refresh the window, and report back."""
        self.window.commit()
        return self.status()

    # -- state -------------------------------------------------------------

    def status(self) -> dict:
        engine = self.engine
        streams = []
        active = 0
        for index, stream in enumerate(self._streams(), start=1):
            info = engine.stream_info(stream["id"])
            running = stream["id"] in engine.desired_streams
            active += info.status == Status.ACTIVE
            display = self.window._stream_display(stream["id"])
            streams.append({
                "index": index,
                "id": stream["id"],
                "name": stream["name"],
                "source": stream["source"],
                "running": running,
                "status": info.status.value,
                "detail": info.detail,
                "muted": bool(stream.get("muteAudio")),
                "display": display["name"] if display else "",
            })

        sender_info = engine.sender_info()
        sender_cfg = self.config.get("sender", {})
        monitor_info = engine.monitor_info()
        monitor_cfg = self.config.get("monitor", {})
        floating_info = engine.floating_info()

        return {
            "app": "py-multiview",
            "version": __version__,
            # Read by py-livedeck's generic network-device check.
            "connected": True,
            "reception": {
                "running": engine.reception_running,
                "active": active,
                "total": len(streams),
            },
            "streams": streams,
            "sender": {
                "running": engine.sender_desired,
                "status": sender_info.status.value,
                "detail": sender_info.detail,
                "name": sender_cfg.get("name", ""),
                "audio": sender_cfg.get("audioSource", ""),
                "fps": sender_cfg.get("fps", 0),
            },
            "monitor": {
                "running": engine.monitor_desired,
                "status": monitor_info.status.value,
                "detail": monitor_info.detail,
                "source": monitor_cfg.get("omtSource", ""),
                "volume": monitor_cfg.get("volume", 100),
            },
            "floating": {
                "running": engine.floating_desired,
                "status": floating_info.status.value,
                "detail": floating_info.detail,
                "source": monitor_cfg.get("floatSource") or monitor_cfg.get("omtSource", ""),
            },
            "routes": [
                {
                    "index": i,
                    "id": route["id"],
                    "name": route["name"],
                    "running": route["id"] in engine.active_routes,
                    "status": engine.route_info(route["id"]).status.value,
                }
                for i, route in enumerate(self._routes(), start=1)
            ],
        }

    def sources(self) -> list[str]:
        return omtlib.discover()

    # -- commands ----------------------------------------------------------

    def set_reception(self, mode: str) -> dict:
        if _wanted(mode, self.engine.reception_running):
            self.engine.start_reception()
            if not self.engine.desired_streams:
                raise ApiError(409, "NO_DISPLAY", "aucun flux n'a d'écran assigné")
        else:
            self.engine.stop_reception()
        return self._apply()

    def set_stream(self, ref: str, mode: str) -> dict:
        stream = self._stream(ref)
        if _wanted(mode, stream["id"] in self.engine.desired_streams):
            error = self.engine.start_stream(stream["id"])
            if error:
                raise ApiError(409, "REFUSED", f"{stream['name']} : {error}")
        else:
            self.engine.stop_stream(stream["id"])
        return self._apply()

    def set_stream_mute(self, ref: str, mode: str) -> dict:
        stream = self._stream(ref)
        target = _wanted(mode, bool(stream.get("muteAudio")))
        for item in self.config["streams"]:
            if item["id"] == stream["id"]:
                item["muteAudio"] = target
        self.window.reload_cards()
        return self._apply()

    def set_sender(self, mode: str) -> dict:
        if _wanted(mode, self.engine.sender_desired):
            error = self.engine.start_sender()
            if error:
                raise ApiError(409, "REFUSED", error)
        else:
            self.engine.stop_sender()
        return self._apply()

    def set_monitor(self, mode: str) -> dict:
        if _wanted(mode, self.engine.monitor_desired):
            error = self.engine.start_monitor()
            if error:
                raise ApiError(409, "REFUSED", error)
        else:
            self.engine.stop_monitor()
        return self._apply()

    def set_monitor_volume(self, value: int) -> dict:
        self.config["monitor"]["volume"] = int(value)
        self.window.load_monitor_fields()
        return self._apply()

    def set_monitor_source(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise bad_value("nom de source vide")
        self.config["monitor"]["omtSource"] = name
        self.window.load_monitor_fields()
        return self._apply()

    def set_floating(self, mode: str) -> dict:
        if _wanted(mode, self.engine.floating_desired):
            error = self.engine.start_floating()
            if error:
                raise ApiError(409, "REFUSED", error)
        else:
            self.engine.stop_floating()
        return self._apply()

    def set_route(self, ref: str, mode: str) -> dict:
        route = self._route(ref)
        if _wanted(mode, route["id"] in self.engine.active_routes):
            error = self.engine.start_route(route["id"])
            if error:
                raise ApiError(409, "REFUSED", f"{route['name']} : {error}")
        else:
            self.engine.stop_route(route["id"])
        return self._apply()
