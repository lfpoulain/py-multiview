"""Local network API, shaped like the services py-livedeck already speaks to.

Two contracts live side by side:

* ``/api/deck/…`` — the Stream Deck contract shared with orchestream-v2,
  where the server describes every key (see :mod:`py_multiview.deck`). This is
  the one py-livedeck should use;
* the original routes below, kept for existing buttons, Companion and scripts.

An optional token protects both: when one is set, every request must carry
``Authorization: Bearer <token>`` (never in the URL), else 401 ``INVALID_TOKEN``.

The original routes come straight from py-intercom and Tally32:

* plain HTTP on the LAN, JSON in and out;
* one ``GET /status`` returning the **whole** state, with ``connected`` so the
  generic "network device" check lights up, and ``streams`` as the signature
  key telling the deck it really reached py-multiview;
* commands are ``POST /<noun>/<id>/<verb>`` with an **empty body** and no
  ``Content-Type``, because that is exactly what LiveDeck sends;
* verbs are absolute (``on``/``off``), never relative — a deck resolves its
  own toggle — though ``toggle`` is accepted too;
* every command answers with the new status, so a button can refresh its
  feedback without a second request;
* errors are JSON ``{"error": {"code", "message"}}``: 404 unknown route or
  stream, 409 refused with a reason, 422 bad value, 405 wrong verb.

Routing is a pure function so it can be tested without opening a socket.
Commands never run on the HTTP thread: they are handed to the UI thread,
which owns the engine.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

log = logging.getLogger(__name__)

MODES = ("on", "off", "toggle")
DECK_HELP = ("GET /api/deck/state, GET /api/deck/events, GET /api/deck/commands, "
             "POST /api/deck/commands/<id> {\"args\": {...}}")
ROUTES_HELP = (
    "GET /status, GET /sources, "
    "POST /reception/<on|off|toggle>, POST /stream/<n>/<on|off|toggle>, "
    "POST /stream/<n>/mute/<on|off|toggle>, POST /sender/<on|off|toggle>, "
    "POST /monitor/<on|off|toggle>, POST /monitor/volume/<0-100>, "
    "POST /monitor/source/<nom>, POST /floating/<on|off|toggle>, "
    "POST /route/<n>/<on|off|toggle>"
)


class ApiError(Exception):
    """An answer the deck can act on: HTTP status, machine code, message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details: dict | None = None

    def body(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def bad_value(message: str) -> ApiError:
    return ApiError(422, "BAD_VALUE", message)


def dispatch(method: str, path: str, control, body: bytes = b"") -> tuple[int, dict]:
    """Route one request. ``control`` implements the verbs below."""
    parts = [unquote(p) for p in path.split("?", 1)[0].strip("/").split("/") if p]
    try:
        if parts[:2] == ["api", "deck"]:
            return 200, _deck_route(method.upper(), parts[2:], control, body)
        return 200, _route(method.upper(), parts, control)
    except ApiError as exc:
        return exc.status, exc.body()


def check_token(expected: str, authorization: str | None) -> ApiError | None:
    """None when the request may go through: no token set, or the right one."""
    if not expected:
        return None
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), expected.encode()):
        return None
    return ApiError(401, "INVALID_TOKEN", "jeton API absent ou incorrect : py-multiview › Préférences › "
                                          "Pilotage à distance, en-tête « Authorization: Bearer <jeton> »")


def _json_body(body: bytes) -> dict:
    """``{}`` for an empty body, the object otherwise."""
    if not body or not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        error = ApiError(422, "VALIDATION_FAILED", "corps JSON illisible")
        error.details = {"body": "JSON attendu, ou corps vide"}
        raise error from None
    if not isinstance(data, dict):
        raise ApiError(422, "VALIDATION_FAILED", "le corps doit être un objet JSON")
    return data


def _deck_route(method: str, parts: list[str], control, body: bytes) -> dict:
    """``/api/deck/…`` (the orchestream-v2 contract). Never a bare NOT_FOUND:
    py-livedeck reads that code as "public address refused"."""
    from . import deck

    if parts in (["state"], ["commands"]):
        if method != "GET":
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"utilisez GET /api/deck/{parts[0]}")
        return control.deck_state() if parts == ["state"] else {"commands": control.deck_commands()}
    if len(parts) == 2 and parts[0] == "commands":
        _post_only(method, "une commande")
        args = _json_body(body).get("args", {})
        return deck.run_command(control, parts[1], args if args is not None else {})
    raise ApiError(404, "ROUTE_NOT_FOUND", f"route inconnue. Routes : {DECK_HELP}")


def _mode(value: str) -> str:
    if value not in MODES:
        raise bad_value(f"mode « {value} » inconnu (attendu : {', '.join(MODES)})")
    return value


def _post_only(method: str, what: str) -> None:
    if method != "POST":
        raise ApiError(405, "METHOD_NOT_ALLOWED", f"{what} exige POST")


def _route(method: str, parts: list[str], control) -> dict:
    if not parts:
        return {"app": "py-multiview", "deck": DECK_HELP, "routes": ROUTES_HELP}

    head = parts[0]
    if head == "status":
        if method != "GET":
            raise ApiError(405, "METHOD_NOT_ALLOWED", "utilisez GET /status")
        return control.status()
    if head == "sources":
        if method != "GET":
            raise ApiError(405, "METHOD_NOT_ALLOWED", "utilisez GET /sources")
        return {"sources": control.sources()}

    if head == "reception" and len(parts) == 2:
        _post_only(method, "la réception")
        return control.set_reception(_mode(parts[1]))

    if head == "sender" and len(parts) == 2:
        _post_only(method, "l'émission")
        return control.set_sender(_mode(parts[1]))

    if head == "floating" and len(parts) == 2:
        _post_only(method, "la fenêtre de retour")
        return control.set_floating(_mode(parts[1]))

    if head == "monitor" and len(parts) == 2:
        _post_only(method, "le retour audio")
        return control.set_monitor(_mode(parts[1]))
    if head == "monitor" and len(parts) == 3 and parts[1] == "volume":
        _post_only(method, "le volume")
        try:
            value = int(parts[2])
        except ValueError:
            raise bad_value(f"volume « {parts[2]} » invalide (0 à 100)") from None
        if not 0 <= value <= 100:
            raise bad_value(f"volume {value} hors bornes (0 à 100)")
        return control.set_monitor_volume(value)
    if head == "monitor" and len(parts) >= 3 and parts[1] == "source":
        _post_only(method, "le choix de la source")
        return control.set_monitor_source("/".join(parts[2:]))

    if head == "stream" and len(parts) == 3:
        _post_only(method, "un flux")
        return control.set_stream(parts[1], _mode(parts[2]))
    if head == "stream" and len(parts) == 4 and parts[2] == "mute":
        _post_only(method, "la coupure du son")
        return control.set_stream_mute(parts[1], _mode(parts[3]))

    if head == "route" and len(parts) == 3:
        _post_only(method, "une route")
        return control.set_route(parts[1], _mode(parts[2]))

    raise not_found(f"route inconnue. Routes : {ROUTES_HELP}")


class _Handler(BaseHTTPRequestHandler):
    server_version = "py-multiview"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        if self._refused():
            return
        if self.path.split("?", 1)[0].rstrip("/") == "/api/deck/events":
            self._events()
            return
        self._answer("GET")

    def do_POST(self) -> None:  # noqa: N802
        # LiveDeck posts an empty body on the original routes, JSON args on /api/deck.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(min(length, 65536)) if length > 0 else b""
        if self._refused():
            return
        self._answer("POST", body)
        self.server.changed()  # a command ran: live streams look again right away

    def _refused(self) -> bool:
        error = check_token(self.server.token(), self.headers.get("Authorization"))
        if error is not None:
            self._send_json(error.status, error.body())
            return True
        return False

    def _answer(self, method: str, body: bytes = b"") -> None:
        try:
            status, answer = self.server.call(method, self.path, body)
        except Exception as exc:  # never leave the deck hanging
            log.exception("Erreur API")
            status, answer = 500, {"error": {"code": "INTERNAL", "message": str(exc)}}
        self._send_json(status, answer)

    def _events(self) -> None:
        """``GET /api/deck/events``: ``state``, then a ``patch`` on each change.

        The state is read every second, and at once after a command; a comment
        line every 25 s keeps the connection (and its proxies) alive.
        """
        from . import deck

        server = self.server
        if not server.open_stream():
            self._send_json(429, {"error": {"code": "TOO_MANY_STREAMS",
                                            "message": f"déjà {MAX_STREAMS} flux en direct ouverts"}})
            return
        try:
            status, state = server.call("GET", "/api/deck/state", b"")
            if status != 200:
                self._send_json(status, state)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._event("state", state)
            last, last_write, seen = state, time.monotonic(), server.generation
            while not server.closing.is_set():
                seen = server.wait_change(seen, POLL_SECONDS)
                if server.closing.is_set():
                    break
                status, state = server.call("GET", "/api/deck/state", b"")
                if status == 200:
                    patch = deck.build_patch(last, state)
                    if patch is not None:
                        self._event("patch", patch)
                        last, last_write = state, time.monotonic()
                        continue
                if time.monotonic() - last_write >= KEEPALIVE_SECONDS:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_write = time.monotonic()
        except OSError:
            pass  # the deck went away
        finally:
            server.close_stream()

    def _event(self, name: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    def _send_json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def log_message(self, fmt: str, *args) -> None:
        log.debug("API %s", fmt % args)


MAX_STREAMS = 16
POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 25.0


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, call, token) -> None:
        super().__init__(address, _Handler)
        self.call = call
        self.token = token
        self.closing = threading.Event()
        self.generation = 0
        self._cond = threading.Condition()
        self._streams = 0

    def changed(self) -> None:
        with self._cond:
            self.generation += 1
            self._cond.notify_all()

    def wait_change(self, seen: int, timeout: float) -> int:
        """Wait for a change after ``seen`` (or ``timeout``); returns the new generation."""
        with self._cond:
            if self.generation == seen and not self.closing.is_set():
                self._cond.wait(timeout)
            return self.generation

    def open_stream(self) -> bool:
        with self._cond:
            if self._streams >= MAX_STREAMS:
                return False
            self._streams += 1
            return True

    def close_stream(self) -> None:
        with self._cond:
            self._streams -= 1

    def shutdown(self) -> None:
        self.closing.set()
        self.changed()  # wake the live streams so they end
        super().shutdown()


class ApiServer:
    """Runs the HTTP API on a background thread."""

    def __init__(self, call) -> None:
        self._call = call
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.error = ""
        self.host = ""
        self.port = 0
        self.token = ""  # optional; read on each request, so a change applies at once

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, host: str, port: int) -> str:
        """Start (or restart on a new address). Returns an error message, or ""."""
        if self.running and (host, port) == (self.host, self.port):
            return self.error
        self.stop()
        try:
            self._server = _Server((host, int(port)), self._call, lambda: self.token)
        except OSError as exc:
            self.error = f"port {port} indisponible ({exc.strerror or exc})"
            log.warning("API non démarrée: %s", self.error)
            return self.error
        self.host, self.port, self.error = host, int(port), ""
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        daemon=True, name="api")
        self._thread.start()
        log.info("API locale sur http://%s:%s", host, port)
        return ""

    def stop(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        self.host, self.port = "", 0
