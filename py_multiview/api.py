"""Local network API, shaped like the services py-livedeck already speaks to.

The conventions come straight from py-intercom and Tally32, the other
services a LiveDeck button drives:

* plain HTTP on the LAN, JSON in and out, no authentication;
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

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

log = logging.getLogger(__name__)

MODES = ("on", "off", "toggle")
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

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def bad_value(message: str) -> ApiError:
    return ApiError(422, "BAD_VALUE", message)


def dispatch(method: str, path: str, control) -> tuple[int, dict]:
    """Route one request. ``control`` implements the verbs below."""
    parts = [unquote(p) for p in path.split("?", 1)[0].strip("/").split("/") if p]
    try:
        return 200, _route(method.upper(), parts, control)
    except ApiError as exc:
        return exc.status, exc.body()


def _mode(value: str) -> str:
    if value not in MODES:
        raise bad_value(f"mode « {value} » inconnu (attendu : {', '.join(MODES)})")
    return value


def _post_only(method: str, what: str) -> None:
    if method != "POST":
        raise ApiError(405, "METHOD_NOT_ALLOWED", f"{what} exige POST")


def _route(method: str, parts: list[str], control) -> dict:
    if not parts:
        return {"app": "py-multiview", "routes": ROUTES_HELP}

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
        self._answer("GET")

    def do_POST(self) -> None:  # noqa: N802
        # LiveDeck posts an empty body; read and discard anything sent anyway.
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            self.rfile.read(min(length, 65536))
        self._answer("POST")

    def _answer(self, method: str) -> None:
        try:
            status, body = self.server.call(method, self.path)
        except Exception as exc:  # never leave the deck hanging
            log.exception("Erreur API")
            status, body = 500, {"error": {"code": "INTERNAL", "message": str(exc)}}
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


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, call) -> None:
        super().__init__(address, _Handler)
        self.call = call


class ApiServer:
    """Runs the HTTP API on a background thread."""

    def __init__(self, call) -> None:
        self._call = call
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.error = ""
        self.host = ""
        self.port = 0

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, host: str, port: int) -> str:
        """Start (or restart on a new address). Returns an error message, or ""."""
        if self.running and (host, port) == (self.host, self.port):
            return self.error
        self.stop()
        try:
            self._server = _Server((host, int(port)), self._call)
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
