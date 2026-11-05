"""Desired-state engine.

The UI only says *what* should run (streams, routes, sender). ``reconcile()``
turns that intent plus the current config and displays into running
processes: it launches what is missing, restarts what changed (settings are
applied live), stops what is no longer wanted and reports why something
cannot run (no screen, port conflict, missing FFmpeg build...).

Because reconcile runs periodically, an unplugged screen or a sender that
is not there yet simply resolves itself once the situation is back to normal.
"""

from __future__ import annotations

import logging

from . import audio
from .ffmpeg import (
    GPU_DECODERS,
    PLAYER_ENV,
    ToolchainRegistry,
    build_monitor_command,
    build_player_command,
    build_route_command,
    build_sender_command,
    dshow_audio_input_args,
    loopback_input_args,
    srt_listener_url,
    udp_input_url,
)
from .network import local_ip
from .process import ProcessInfo, ProcessSpec, Status, Supervisor

log = logging.getLogger(__name__)

PLAYER = "player:"
ROUTE = "route:"
SENDER = "sender"
MONITOR = "monitor"

NO_SRT_PLAYER = (
    "Aucun ffplay avec support SRT. Installe le FFmpeg standard dans bin/standard "
    "(scripts/fetch-ffmpeg.ps1) ou utilise la version packagée."
)
NO_SRT_FFMPEG = "Aucun ffmpeg avec support SRT pour le routage (bin/standard manquant)."
NO_OMT_PLAYER = "Aucun ffplay compatible OMT (bin/omt manquant)."
NO_OMT_FFMPEG = "Aucun ffmpeg compatible OMT (bin/omt manquant)."


class Engine:
    def __init__(self, tools: ToolchainRegistry | None = None, supervisor: Supervisor | None = None) -> None:
        self.tools = tools or ToolchainRegistry()
        self.sup = supervisor or Supervisor()
        self.host_ip = local_ip()
        self._loopback: audio.LoopbackDevice | bool | None = None
        self.config: dict = {}
        self.displays: list[dict] = []
        self.desired_streams: set[str] = set()
        self.desired_routes: set[str] = set()
        self.sender_desired = False
        self.monitor_desired = False
        self.active_routes: set[str] = set()  # explicitly started or used by a started stream
        self.errors: dict[str, str] = {}

    # -- inputs ------------------------------------------------------------

    def update(self, config: dict, displays: list[dict]) -> None:
        self.config = config
        self.displays = displays
        self.reconcile()

    def start_stream(self, stream_id: str) -> str | None:
        self.desired_streams.add(stream_id)
        self.reconcile()
        return self.errors.get(PLAYER + stream_id)

    def stop_stream(self, stream_id: str) -> None:
        self.desired_streams.discard(stream_id)
        self.reconcile()

    def start_reception(self) -> dict[str, str]:
        """Start every stream that has a screen; returns ``{stream_id: error}``."""
        mapping = self.config.get("mapping", {})
        for stream in self.config.get("streams", []):
            if mapping.get(stream["id"]):
                self.desired_streams.add(stream["id"])
        self.reconcile()
        return {sid: self.errors[PLAYER + sid] for sid in self.desired_streams if PLAYER + sid in self.errors}

    def stop_reception(self) -> None:
        self.desired_streams.clear()
        self.desired_routes.clear()
        self.reconcile()

    def start_route(self, route_id: str) -> str | None:
        self.desired_routes.add(route_id)
        self.reconcile()
        return self.errors.get(ROUTE + route_id)

    def stop_route(self, route_id: str) -> None:
        """Stop a route and the streams that read from it."""
        self.desired_routes.discard(route_id)
        for stream in self.config.get("streams", []):
            if stream["source"] == "route" and stream["sourceRouteId"] == route_id:
                self.desired_streams.discard(stream["id"])
        self.reconcile()

    def start_sender(self) -> str | None:
        self._loopback = None  # the default playback device may have changed
        self.sender_desired = True
        self.reconcile()
        return self.errors.get(SENDER)

    def stop_sender(self) -> None:
        self.sender_desired = False
        self.reconcile()

    def start_monitor(self) -> str | None:
        self.monitor_desired = True
        self.reconcile()
        return self.errors.get(MONITOR)

    def stop_monitor(self) -> None:
        self.monitor_desired = False
        self.reconcile()

    def shutdown(self) -> None:
        self.desired_streams.clear()
        self.desired_routes.clear()
        self.sender_desired = False
        self.monitor_desired = False
        self.sup.stop_all()

    def tick(self) -> None:
        self.sup.tick()
        self.reconcile()

    # -- state -------------------------------------------------------------

    @property
    def reception_running(self) -> bool:
        return bool(self.desired_streams)

    def stream_info(self, stream_id: str) -> ProcessInfo:
        return self._info(PLAYER + stream_id, stream_id in self.desired_streams)

    def route_info(self, route_id: str) -> ProcessInfo:
        return self._info(ROUTE + route_id, route_id in self.active_routes)

    def sender_info(self) -> ProcessInfo:
        return self._info(SENDER, self.sender_desired)

    def monitor_info(self) -> ProcessInfo:
        return self._info(MONITOR, self.monitor_desired)

    def _info(self, key: str, desired: bool) -> ProcessInfo:
        if not desired:
            return ProcessInfo(Status.STOPPED)
        if key in self.errors:
            return ProcessInfo(Status.ERROR, self.errors[key])
        return self.sup.info(key)

    def log_lines(self, key: str) -> list[str]:
        return self.sup.log_lines(key)

    # -- reconciliation ----------------------------------------------------

    def reconcile(self) -> None:
        config = self.config
        if not config:
            return
        streams = {s["id"]: s for s in config.get("streams", [])}
        routes = {r["id"]: r for r in config.get("routes", [])}
        self.desired_streams &= set(streams)
        self.desired_routes &= set(routes)

        # SRT listening ports already taken: port -> owner label.
        claimed: dict[int, str] = {}
        new_errors: dict[str, str] = {}

        needed_routes = set(self.desired_routes)
        for sid in self.desired_streams:
            stream = streams[sid]
            if stream["source"] == "route" and stream["sourceRouteId"] in routes:
                needed_routes.add(stream["sourceRouteId"])

        self.active_routes = needed_routes
        for rid, route in routes.items():
            key = ROUTE + rid
            if rid not in needed_routes:
                self.sup.stop(key)
                continue
            spec, error = self._route_spec(route, claimed)
            self._apply(key, spec, error, new_errors)

        for key in self.sup.keys(ROUTE):
            if key[len(ROUTE):] not in routes:
                self.sup.stop(key)

        for sid, stream in streams.items():
            key = PLAYER + sid
            if sid not in self.desired_streams:
                self.sup.stop(key)
                continue
            spec, error = self._player_spec(stream, routes, claimed, new_errors)
            self._apply(key, spec, error, new_errors)

        for key in self.sup.keys(PLAYER):
            if key[len(PLAYER):] not in streams:
                self.sup.stop(key)

        if self.sender_desired:
            spec, error = self._sender_spec()
            self._apply(SENDER, spec, error, new_errors)
        else:
            self.sup.stop(SENDER)

        if self.monitor_desired:
            spec, error = self._monitor_spec()
            self._apply(MONITOR, spec, error, new_errors)
        else:
            self.sup.stop(MONITOR)

        self.errors = new_errors

    def _apply(self, key: str, spec: ProcessSpec | None, error: str | None, errors: dict[str, str]) -> None:
        if error or spec is None:
            if key not in self.errors or self.errors[key] != error:
                log.warning("[%s] non démarré: %s", key, error)
            self.sup.stop(key)
            errors[key] = error or "Erreur inconnue"
            return
        self.sup.ensure(key, spec)

    def _display(self, display_id: str) -> dict | None:
        return next((d for d in self.displays if d["id"] == display_id), None)

    def _claim(self, port: int, owner: str, claimed: dict[int, str]) -> str | None:
        if port in claimed:
            return f"Port SRT {port} déjà utilisé par « {claimed[port]} »."
        claimed[port] = owner
        return None

    def _player_spec(
        self, stream: dict, routes: dict[str, dict], claimed: dict[int, str], errors: dict[str, str]
    ) -> tuple[ProcessSpec | None, str | None]:
        display_id = self.config.get("mapping", {}).get(stream["id"])
        if not display_id:
            return None, "Aucun écran assigné."
        display = self._display(display_id)
        if display is None:
            return None, "Écran assigné absent ou débranché."
        if display.get("isPrimary") and self.config.get("excludePrimaryDisplay"):
            return None, "L'écran principal est exclu (voir Préférences)."

        source = stream["source"]
        live_udp = False
        raw_video = source == "omt"
        if source == "srt":
            toolchain = self.tools.player_for("srt")
            if toolchain is None:
                return None, NO_SRT_PLAYER
            error = self._claim(stream["port"], stream["name"], claimed)
            if error:
                return None, error
            input_args = ["-i", srt_listener_url(stream["port"], stream["latency"])]
        elif source == "omt":
            if not stream["omtSource"]:
                return None, "Aucune source OMT renseignée."
            toolchain = self.tools.player_for("omt")
            if toolchain is None:
                return None, NO_OMT_PLAYER
            input_args = ["-f", "libomt", "-i", stream["omtSource"]]
        else:
            route = routes.get(stream["sourceRouteId"])
            if route is None:
                return None, "Aucune route sélectionnée."
            if ROUTE + route["id"] in errors:
                return None, f"Route « {route['name']} » indisponible : {errors[ROUTE + route['id']]}"
            toolchain = self.tools.player_for("udp")
            if toolchain is None:
                return None, NO_SRT_PLAYER
            input_args = ["-i", udp_input_url(route["multicastAddr"], route["multicastPort"], self.host_ip)]
            live_udp = True

        decode = self.config.get("receiver", {}).get("decode", "cpu")
        decoder = None
        if source != "omt" and decode in toolchain.decoders:
            decoder = GPU_DECODERS[decode]

        args = build_player_command(
            toolchain.ffplay,
            input_args=input_args,
            display=display,
            mode=stream["displayMode"],
            rotate=stream["rotate"],
            mute=stream["muteAudio"],
            decoder=decoder,
            title=f"{stream['name']} — py-multiview",
            live_udp=live_udp,
            raw_video=raw_video,
        )
        return ProcessSpec(args, stream["name"], progress="ffplay", env=dict(PLAYER_ENV)), None

    def _route_spec(self, route: dict, claimed: dict[int, str]) -> tuple[ProcessSpec | None, str | None]:
        toolchain = self.tools.ffmpeg_for("srt")
        if toolchain is None:
            return None, NO_SRT_FFMPEG
        error = self._claim(route["inputPort"], route["name"], claimed)
        if error:
            return None, error
        args = build_route_command(toolchain.ffmpeg, route, self.host_ip)
        return ProcessSpec(args, route["name"], progress="ffmpeg"), None

    def _sender_spec(self) -> tuple[ProcessSpec | None, str | None]:
        sender = self.config.get("sender", {})
        if not sender.get("displayId"):
            return None, "Aucun écran à émettre sélectionné."
        display = self._display(sender["displayId"])
        if display is None:
            return None, "L'écran à émettre est absent ou débranché."
        toolchain = self.tools.ffmpeg_for("omt")
        if toolchain is None:
            return None, NO_OMT_FFMPEG
        audio_input, feeder = None, None
        source = sender.get("audioSource", "")
        if source == audio.LOOPBACK:
            device = self.loopback_device()
            if device is None:
                return None, "Capture du son du PC indisponible (aucune sortie audio par défaut)."
            audio_input = loopback_input_args(device.rate, device.channels)
            feeder = audio.LoopbackFeeder(device)
        elif source.startswith(audio.DSHOW_PREFIX):
            audio_input = dshow_audio_input_args(source[len(audio.DSHOW_PREFIX):])
        args = build_sender_command(toolchain.ffmpeg, display, sender, audio_input)
        spec = ProcessSpec(args, "Émission OMT", progress="ffmpeg", graceful_quit=feeder is None, stdin_feeder=feeder)
        return spec, None

    def _monitor_spec(self) -> tuple[ProcessSpec | None, str | None]:
        monitor = self.config.get("monitor", {})
        source = monitor.get("omtSource", "")
        if not source:
            return None, "Aucune source OMT à écouter."
        toolchain = self.tools.player_for("omt")
        if toolchain is None:
            return None, NO_OMT_PLAYER
        args = build_monitor_command(toolchain.ffplay, source, monitor.get("volume", 100))
        return ProcessSpec(args, "Retour audio", progress="ffplay", env=dict(PLAYER_ENV)), None

    def loopback_device(self) -> audio.LoopbackDevice | None:
        """Default playback device for loopback capture (cached: probing opens PortAudio)."""
        if self._loopback is None:
            self._loopback = audio.loopback_device() or False
        return self._loopback or None
