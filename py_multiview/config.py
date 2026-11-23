"""Configuration model: defaults, normalisation and atomic persistence."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

from .paths import CONFIG_PATH, LEGACY_CONFIG_DIR

log = logging.getLogger(__name__)

# Bumping this invalidates stored display ids (mapping, sender display, names).
DISPLAY_ID_SCHEMA_VERSION = 2

STREAM_SOURCES = ("srt", "omt", "route")
DISPLAY_MODES = ("fit", "fill", "stretch")
ROTATIONS = (0, 90, 180, 270)
DECODERS = ("cpu", "nvidia", "intel", "amd")
OMT_PIXEL_FORMATS = ("uyvy422", "bgra", "yuv422p10le")

DEFAULT_LATENCY_MS = 120
DEFAULT_SENDER_NAME = "py-multiview"
DEFAULT_MULTICAST_ADDR = "239.10.10.10"
DEFAULT_MULTICAST_PORT = 1234
# Local control API. 5080 is py-intercom's, so stay next door without clashing.
DEFAULT_API_PORT = 5090

# Legacy decode values (ffmpeg -hwaccel names) -> current decoder family.
_LEGACY_DECODERS = {
    "h264_cuvid": "nvidia",
    "h264_qsv": "intel",
    "h264_amf": "amd",
    "auto": "cpu",
    "gpu": "cpu",
    "dxva2": "cpu",
}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _int(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None and result < lo:
        result = default
    if hi is not None and result > hi:
        result = hi
    return result


def _str(value, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _schema_version(config: dict) -> int:
    try:
        return int(config.get("displayIdSchemaVersion") or 0)
    except (TypeError, ValueError):
        return 0


def default_stream(index: int, port: int | None = None) -> dict:
    return normalize_stream({"id": new_id("stream"), "port": port or 9000 + index}, index)


def default_config() -> dict:
    return normalize_config(
        {
            "displayIdSchemaVersion": DISPLAY_ID_SCHEMA_VERSION,
            "streams": [default_stream(i) for i in (1, 2, 3)],
        }
    )


def normalize_stream(raw: dict, index: int) -> dict:
    stream = dict(raw)
    stream["id"] = _str(stream.get("id"), f"stream-{index}")
    stream["name"] = _str(stream.get("name"), f"Flux {index}")
    source = _str(stream.get("source"), "srt").lower()
    stream["source"] = source if source in STREAM_SOURCES else "srt"
    stream["port"] = _int(stream.get("port"), 9000 + index, 1, 65535)
    stream["latency"] = _int(stream.get("latency"), DEFAULT_LATENCY_MS, 0, 10000)
    stream["omtSource"] = _str(stream.get("omtSource"))
    stream["sourceRouteId"] = _str(stream.get("sourceRouteId"))
    mode = _str(stream.get("displayMode"), "fit").lower()
    stream["displayMode"] = mode if mode in DISPLAY_MODES else "fit"
    rotate = _int(stream.get("rotate"), 0)
    stream["rotate"] = rotate if rotate in ROTATIONS else 0
    stream["muteAudio"] = bool(stream.get("muteAudio", False))
    # "udp" used to be an internal pseudo-source; it is resolved at launch time now.
    for legacy in ("udpAddr", "udpPort"):
        stream.pop(legacy, None)
    return stream


def normalize_route(raw: dict, index: int) -> dict:
    route = dict(raw)
    route["id"] = _str(route.get("id"), f"route-{index}")
    route["name"] = _str(route.get("name"), f"Route {index}")
    route["inputPort"] = _int(route.get("inputPort"), 9001, 1, 65535)
    route["inputLatency"] = _int(route.get("inputLatency"), DEFAULT_LATENCY_MS, 0, 10000)
    route["multicastAddr"] = _str(route.get("multicastAddr"), DEFAULT_MULTICAST_ADDR)
    route["multicastPort"] = _int(route.get("multicastPort"), DEFAULT_MULTICAST_PORT, 1, 65535)
    route["pktSize"] = _int(route.get("pktSize"), 1316, 188, 65507)
    route["ttl"] = _int(route.get("ttl"), 1, 1, 255)
    return route


def normalize_sender(raw: dict, reset_display: bool) -> dict:
    sender = dict(raw)
    sender["displayId"] = "" if reset_display else _str(sender.get("displayId"))
    sender["name"] = _str(sender.get("name"), DEFAULT_SENDER_NAME)
    sender["fps"] = _int(sender.get("fps"), 30, 1, 60)
    pixel_format = _str(sender.get("pixelFormat"), "uyvy422").lower()
    sender["pixelFormat"] = pixel_format if pixel_format in OMT_PIXEL_FORMATS else "uyvy422"
    sender["clockOutput"] = bool(sender.get("clockOutput", False))
    # FFmpeg/gdigrab by default: the libomt sender gives a black picture on
    # some machines. True switches the emission back to libomt.
    sender["native"] = bool(sender.get("native", False))
    # "" | "loopback" (what the PC plays) | "out:<device>" (what one output plays)
    # | "in:<device>" | "dshow:<device>" (DirectShow-only devices)
    # | "app:<exe>" (one program) | "notapp:<exe>" (the PC except that program)
    audio = _str(sender.get("audioSource"))
    valid = audio in ("", "loopback") or audio.startswith(("in:", "out:", "dshow:", "app:", "notapp:"))
    sender["audioSource"] = audio if valid else ""
    try:
        sender["referenceLevel"] = max(0.0, min(20.0, float(sender.get("referenceLevel", 1.0))))
    except (TypeError, ValueError):
        sender["referenceLevel"] = 1.0
    # Fields from the former SRT sender / unfinished audio support.
    for legacy in (
        "host", "port", "latency", "bitrateK", "encoder", "includeSystemAudio",
        "noAudio", "audioDevice", "audioSampleRate", "audioChannels",
    ):
        sender.pop(legacy, None)
    return sender


def normalize_monitor(raw: dict) -> dict:
    """The OMT return: heard on this PC, and optionally shown in a small window."""
    monitor = dict(raw)
    monitor["omtSource"] = _str(monitor.get("omtSource"))
    monitor["volume"] = _int(monitor.get("volume"), 100, 0, 100)
    monitor["floatSource"] = _str(monitor.get("floatSource"))
    monitor["floatWidth"] = _int(monitor.get("floatWidth"), 640, 160, 7680)
    monitor["floatHeight"] = _int(monitor.get("floatHeight"), 360, 90, 4320)
    monitor["floatX"] = _int(monitor.get("floatX"), 80)
    monitor["floatY"] = _int(monitor.get("floatY"), 80)
    monitor["floatVolume"] = _int(monitor.get("floatVolume"), 0, 0, 100)
    monitor["floatOnTop"] = bool(monitor.get("floatOnTop", True))
    return monitor


def normalize_config(config) -> dict:
    """Return a fully-populated, type-safe copy of ``config``.

    Tolerates any garbage (wrong types, missing keys, legacy fields) so the UI
    never has to defend against malformed data.
    """
    config = dict(config) if isinstance(config, dict) else {}
    reset_displays = _schema_version(config) != DISPLAY_ID_SCHEMA_VERSION
    config["displayIdSchemaVersion"] = DISPLAY_ID_SCHEMA_VERSION

    streams: list[dict] = []
    seen_ids: set[str] = set()
    raw_streams = config.get("streams")
    for i, raw in enumerate(raw_streams if isinstance(raw_streams, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        stream = normalize_stream(raw, i)
        if stream["id"] in seen_ids:
            stream["id"] = new_id("stream")
        seen_ids.add(stream["id"])
        streams.append(stream)
    config["streams"] = streams

    routes: list[dict] = []
    seen_routes: set[str] = set()
    raw_routes = config.get("routes")
    for i, raw in enumerate(raw_routes if isinstance(raw_routes, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        route = normalize_route(raw, i)
        if route["id"] in seen_routes:
            route["id"] = new_id("route")
        seen_routes.add(route["id"])
        routes.append(route)
    config["routes"] = routes

    route_ids = {r["id"] for r in routes}
    for stream in streams:
        if stream["source"] == "route" and stream["sourceRouteId"] not in route_ids:
            stream["sourceRouteId"] = ""

    mapping = config.get("mapping")
    if reset_displays or not isinstance(mapping, dict):
        mapping = {}
    config["mapping"] = {
        str(k): str(v) for k, v in mapping.items() if v and str(k) in seen_ids
    }

    names = config.get("displayNames")
    if reset_displays or not isinstance(names, dict):
        names = {}
    config["displayNames"] = {str(k): str(v).strip() for k, v in names.items() if v and str(v).strip()}

    sender = config.get("sender")
    config["sender"] = normalize_sender(sender if isinstance(sender, dict) else {}, reset_displays)

    monitor = config.get("monitor")
    config["monitor"] = normalize_monitor(monitor if isinstance(monitor, dict) else {})

    # Which sidebar blocks are open; a block that was never toggled keeps its default.
    sidebar = config.get("sidebar")
    config["sidebar"] = (
        {str(k): bool(v) for k, v in sidebar.items() if isinstance(v, bool)} if isinstance(sidebar, dict) else {}
    )

    api = config.get("api")
    api = dict(api) if isinstance(api, dict) else {}
    api["enabled"] = bool(api.get("enabled", False))
    api["port"] = _int(api.get("port"), DEFAULT_API_PORT, 1, 65535)
    api["localOnly"] = bool(api.get("localOnly", False))
    api["token"] = _str(api.get("token")).strip()  # optional: empty = open, like py-intercom
    config["api"] = api

    updates = config.get("updates")
    updates = dict(updates) if isinstance(updates, dict) else {}
    updates["autoCheck"] = bool(updates.get("autoCheck", True))
    updates["skipped"] = _str(updates.get("skipped"))  # version the user chose to ignore
    config["updates"] = updates

    receiver = config.get("receiver")
    receiver = dict(receiver) if isinstance(receiver, dict) else {}
    decode = _str(receiver.get("decode"), "cpu").lower()
    decode = _LEGACY_DECODERS.get(decode, decode)
    receiver["decode"] = decode if decode in DECODERS else "cpu"
    config["receiver"] = receiver

    config["excludePrimaryDisplay"] = bool(config.get("excludePrimaryDisplay", True))
    config["autoStartReceiver"] = bool(config.get("autoStartReceiver", False))
    config["autoStartSender"] = bool(config.get("autoStartSender", False))
    # The window's close button minimises instead of quitting: closing by
    # reflex must not cut a live emission.
    config["closeToMinimize"] = bool(config.get("closeToMinimize", True))
    return config


def _quarantine(path: Path) -> None:
    """Move an unreadable config aside instead of silently overwriting it."""
    target = path.with_name(f"{path.stem}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
    try:
        path.replace(target)
        log.warning("Configuration illisible déplacée vers %s", target)
    except OSError:
        log.exception("Impossible de mettre de côté la configuration corrompue")


def _migrate_legacy(path: Path) -> None:
    """Bring over the configuration saved under the former "srt-multiview" name."""
    legacy = LEGACY_CONFIG_DIR / "config.json"
    if path != CONFIG_PATH or path.exists() or not legacy.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(legacy.read_bytes())
        log.info("Configuration reprise depuis %s", legacy)
    except OSError:
        log.exception("Reprise de l'ancienne configuration impossible")


def load_config(path: Path = CONFIG_PATH) -> dict:
    _migrate_legacy(path)
    if not path.exists():
        return default_config()
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("la racine JSON n'est pas un objet")
    except (OSError, ValueError) as exc:
        log.error("Lecture de %s impossible: %s", path, exc)
        _quarantine(path)
        return default_config()

    migrated = _schema_version(loaded) != DISPLAY_ID_SCHEMA_VERSION
    config = normalize_config(loaded)
    if migrated:
        save_config(config, path)
    return config


def save_config(config: dict, path: Path = CONFIG_PATH) -> bool:
    """Atomically write ``config``. Returns False (and logs) on I/O failure."""
    data = normalize_config(config)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except OSError:
        log.exception("Échec de l'écriture de la configuration %s", path)
        return False
