"""The Stream Deck contract shared with orchestream-v2 (``/api/deck``).

py-livedeck speaks one generic protocol to every app of the ecosystem, the
one orchestream-v2 defined (its ``docs/STREAM_DECK.md``):

* ``GET /api/deck/state`` — the whole state, plus ``commands`` and ``keys``;
* ``GET /api/deck/events`` — Server-Sent Events: ``state`` once, then
  ``patch`` (``changed`` sections, modified ``keys``, ``removedKeys``);
* ``GET /api/deck/commands`` — what can be run, with argument values;
* ``POST /api/deck/commands/<id>`` with ``{"args": {...}}`` (or an empty
  body) — answers ``{"ok", "message", "data"}``.

The server describes every key, ready to draw (stable ``id``, ``group``,
``title``, ``subtitle``, ``active``, ``tone``, ``available``/``reason``), so
the deck has nothing hard-coded: a new stream or route shows up on its own.

Everything here is pure (status dict in, dicts out) so it is testable without
Qt; :class:`py_multiview.control.Control` feeds it the live status.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from .api import ApiError

STATUS_LABELS = {
    "stopped": "Arrêté",
    "starting": "Démarrage",
    "waiting": "En attente",
    "active": "Actif",
    "retrying": "Relance",
    "error": "Erreur",
}
STATUS_TONES = {
    "stopped": "neutral",
    "starting": "warning",
    "waiting": "warning",
    "active": "success",
    "retrying": "warning",
    "error": "danger",
}
VOLUME_STEP = 5

# Commands, in the order the deck lists them. ``arg`` names the one argument a
# command takes (a stream or a route), ``verb`` how it maps onto Control.
_ON_OFF = (("start", "on", "Démarrer"), ("stop", "off", "Arrêter"), ("toggle", "toggle", "Basculer"))

COMMANDS: list[dict] = []
for _obj, _label in (("reception", "toute la réception"), ("sender", "l'émission OMT"),
                     ("monitor", "le retour audio"), ("floating", "la fenêtre de retour")):
    for _verb, _mode, _text in _ON_OFF:
        COMMANDS.append({"id": f"{_obj}.{_verb}", "label": f"{_text} {_label}", "argNames": [],
                         "target": _obj, "mode": _mode})
for _verb, _mode, _text in _ON_OFF:
    COMMANDS.append({"id": f"stream.{_verb}", "label": f"{_text} un flux", "argNames": ["stream"],
                     "target": "stream", "mode": _mode})
for _verb, _mode, _text in (("mute", "on", "Couper"), ("unmute", "off", "Rétablir"),
                            ("muteToggle", "toggle", "Basculer")):
    COMMANDS.append({"id": f"stream.{_verb}", "label": f"{_text} le son d'un flux", "argNames": ["stream"],
                     "target": "mute", "mode": _mode})
for _verb, _mode, _text in _ON_OFF:
    COMMANDS.append({"id": f"route.{_verb}", "label": f"{_text} une route", "argNames": ["route"],
                     "target": "route", "mode": _mode})
COMMANDS += [
    {"id": "monitor.volume", "label": "Volume du retour (0 à 100)", "argNames": ["volume"], "target": "volume"},
    {"id": "monitor.volumeUp", "label": f"Volume du retour +{VOLUME_STEP}", "argNames": [], "target": "volume+"},
    {"id": "monitor.volumeDown", "label": f"Volume du retour −{VOLUME_STEP}", "argNames": [], "target": "volume-"},
    {"id": "monitor.source", "label": "Source écoutée", "argNames": ["source"], "target": "source"},
]
_BY_ID = {c["id"]: c for c in COMMANDS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _label(section: dict) -> str:
    return STATUS_LABELS.get(section.get("status", ""), section.get("status", ""))


def _tone(section: dict) -> str:
    if not section.get("running"):
        return "neutral"
    return STATUS_TONES.get(section.get("status", ""), "neutral")


def _key(key_id: str, group: str, command: str | None, args: dict, title: str, subtitle: str | None,
         active: bool, tone: str, reason: str | None = None) -> dict:
    return {"id": key_id, "group": group, "command": command, "args": args, "title": title,
            "subtitle": subtitle or None, "active": bool(active), "tone": tone,
            "available": reason is None, "reason": reason}


# -- availability -----------------------------------------------------------


def _stream_reason(stream: dict) -> str | None:
    if not stream.get("running") and not stream.get("display"):
        return "Aucun écran assigné."
    return None


def _command_reason(command: dict, status: dict) -> str | None:
    """Why a command cannot run right now (for a command without argument)."""
    target = command["target"]
    if target == "reception" and command["mode"] != "off" and not status["reception"]["running"]:
        if not any(s.get("display") for s in status.get("streams", [])):
            return "Aucun flux n'a d'écran assigné."
    if target in ("stream", "mute") and not status.get("streams"):
        return "Aucun flux configuré."
    if target == "route" and not status.get("routes"):
        return "Aucune route configurée."
    volume = status["monitor"].get("volume", 0)
    if target == "volume+" and volume >= 100:
        return "Volume déjà au maximum."
    if target == "volume-" and volume <= 0:
        return "Volume déjà à zéro."
    return None


# -- keys -------------------------------------------------------------------


def build_keys(status: dict) -> list[dict]:
    """Every key the deck can place, in display order."""
    reception, sender = status["reception"], status["sender"]
    monitor, floating = status["monitor"], status["floating"]
    no_display = None
    if not reception["running"] and not any(s.get("display") for s in status["streams"]):
        no_display = "Aucun flux n'a d'écran assigné."

    keys = [
        _key("reception.toggle", "Réception", "reception.toggle", {}, "Réception",
             f"{reception['active']}/{reception['total']} actifs" if reception["running"] else "Arrêtée",
             reception["running"], "success" if reception["running"] else "neutral", no_display),
    ]
    for stream in status["streams"]:
        sid = stream["id"]
        subtitle = _label(stream) if stream["running"] else ("Arrêté" if stream.get("display") else "Sans écran")
        keys.append(_key(f"stream.toggle:{sid}", "Flux", "stream.toggle", {"stream": sid}, stream["name"],
                         subtitle, stream["running"], _tone(stream), _stream_reason(stream)))
    for stream in status["streams"]:
        sid = stream["id"]
        keys.append(_key(f"stream.muteToggle:{sid}", "Son des flux", "stream.muteToggle", {"stream": sid},
                         stream["name"], "Son coupé" if stream["muted"] else "Son actif",
                         stream["muted"], "danger" if stream["muted"] else "neutral"))

    keys.append(_key("sender.toggle", "Émission", "sender.toggle", {}, "Émission OMT",
                     _label(sender) if sender["running"] else (sender.get("name") or "Arrêtée"),
                     sender["running"], "live" if sender.get("status") == "active" else _tone(sender)))

    volume = monitor.get("volume", 0)
    keys += [
        _key("monitor.toggle", "Retour audio", "monitor.toggle", {}, "Retour audio",
             monitor.get("source") or "Aucune source", monitor["running"], _tone(monitor)),
        _key("monitor.volumeDown", "Retour audio", "monitor.volumeDown", {}, "Volume −", f"{volume} %",
             False, "neutral", "Volume déjà à zéro." if volume <= 0 else None),
        _key("monitor.volumeUp", "Retour audio", "monitor.volumeUp", {}, "Volume +", f"{volume} %",
             False, "neutral", "Volume déjà au maximum." if volume >= 100 else None),
        _key("floating.toggle", "Retour audio", "floating.toggle", {}, "Fenêtre de retour",
             floating.get("source") or "Aucune source", floating["running"], _tone(floating)),
    ]
    for route in status["routes"]:
        rid = route["id"]
        keys.append(_key(f"route.toggle:{rid}", "Routes", "route.toggle", {"route": rid}, route["name"],
                         _label(route) if route["running"] else "Arrêtée", route["running"], _tone(route)))
    return keys


# -- state, commands, patches -----------------------------------------------


def _public(command: dict) -> dict:
    return {"id": command["id"], "label": command["label"], "argNames": list(command["argNames"])}


def build_commands(status: dict, with_values: bool = False) -> list[dict]:
    """Commands with their availability; with ``with_values``, the allowed argument values."""
    out = []
    for command in COMMANDS:
        item = _public(command)
        reason = _command_reason(command, status)
        item["available"], item["reason"] = reason is None, reason
        if with_values:
            item["args"] = [_arg_values(name, status) for name in command["argNames"]]
        out.append(item)
    return out


def _arg_values(name: str, status: dict) -> dict:
    if name == "stream":
        values = [{"value": s["id"], "label": s["name"]} for s in status["streams"]]
    elif name == "route":
        values = [{"value": r["id"], "label": r["name"]} for r in status["routes"]]
    elif name == "volume":
        return {"name": name, "type": "integer", "min": 0, "max": 100}
    else:  # source: free text (an OMT name, see GET /sources)
        return {"name": name, "type": "string"}
    return {"name": name, "type": "enum", "values": values}


def build_state(status: dict, token_set: bool) -> dict:
    """``GET /api/deck/state``: the status sections, plus commands and keys."""
    state = {"token": {"name": "jeton API" if token_set else "accès libre", "permissions": []}}
    state.update(copy.deepcopy(status))  # a snapshot: the next patch compares against it
    state["commands"] = build_commands(status)
    state["keys"] = build_keys(status)
    state["updatedAt"] = _now()
    return state


def build_patch(old: dict, new: dict) -> dict | None:
    """What changed between two states, as an SSE ``patch`` (None if nothing did)."""
    changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
    old_keys = {k["id"]: k for k in old.get("keys", [])}
    new_keys = {k["id"]: k for k in new.get("keys", [])}
    keys = [k for kid, k in new_keys.items() if old_keys.get(kid) != k]
    removed = [kid for kid in old_keys if kid not in new_keys]
    if not (changed or keys or removed):
        return None
    return {"changed": changed, "keys": keys, "removedKeys": removed, "updatedAt": new.get("updatedAt") or _now()}


# -- running a command ------------------------------------------------------


def _invalid(arg: str, message: str) -> ApiError:
    error = ApiError(422, "VALIDATION_FAILED", message)
    error.details = {f"args.{arg}": message}
    return error


def _arg(args: dict, name: str) -> str:
    value = args.get(name)
    if value is None or str(value).strip() == "":
        raise _invalid(name, f"argument « {name} » manquant")
    return str(value).strip()


def run_command(control, command_id: str, args: dict) -> dict:
    """``POST /api/deck/commands/<id>``: run it on ``control``, answer like orchestream."""
    command = _BY_ID.get(command_id)
    if command is None:
        raise ApiError(404, "COMMAND_NOT_FOUND", f"commande « {command_id} » inconnue")
    if not isinstance(args, dict):
        raise _invalid("args", "« args » doit être un objet")
    target, mode = command["target"], command.get("mode")

    try:
        if target == "reception":
            data = control.set_reception(mode)
        elif target == "sender":
            data = control.set_sender(mode)
        elif target == "monitor":
            data = control.set_monitor(mode)
        elif target == "floating":
            data = control.set_floating(mode)
        elif target == "stream":
            data = control.set_stream(_arg(args, "stream"), mode)
        elif target == "mute":
            data = control.set_stream_mute(_arg(args, "stream"), mode)
        elif target == "route":
            data = control.set_route(_arg(args, "route"), mode)
        elif target == "volume":
            raw = _arg(args, "volume")
            try:
                value = int(raw)
            except ValueError:
                raise _invalid("volume", f"volume « {raw} » invalide (0 à 100)") from None
            if not 0 <= value <= 100:
                raise _invalid("volume", f"volume {value} hors bornes (0 à 100)")
            data = control.set_monitor_volume(value)
        elif target in ("volume+", "volume-"):
            current = int(control.status()["monitor"].get("volume", 0))
            step = VOLUME_STEP if target == "volume+" else -VOLUME_STEP
            data = control.set_monitor_volume(max(0, min(100, current + step)))
        else:  # source
            data = control.set_monitor_source(_arg(args, "source"))
    except ApiError as exc:
        # An unknown stream or route is a bad argument here, not a missing route:
        # py-livedeck reads 404 as "this server has no /api/deck".
        if exc.status == 404:
            raise _invalid(command["argNames"][0] if command["argNames"] else "args", exc.message) from None
        raise
    return {"ok": True, "message": f"{command['label']} : fait", "data": data}
