"""Application entry point."""

from __future__ import annotations

import getpass
import logging
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .logs import setup_logging
from .paths import APP_ICON_ICO_PATH, APP_ICON_PNG_PATH

log = logging.getLogger("py_multiview")

APP_USER_MODEL_ID = "LFPoulain.PyMultiview"


def _instance_key() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"py-multiview-{user}"


def _activate_running_instance(key: str, patience: float = 0.3) -> bool:
    """Ask an already running instance to come to the front.

    ``patience``: how long to keep trying, for an instance that is still
    starting and does not listen yet.
    """
    deadline = time.monotonic() + patience
    while True:
        socket = QLocalSocket()
        socket.connectToServer(key)
        if socket.waitForConnected(300):
            socket.write(b"raise")
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


_instance_mutex = None  # kept for the life of the process: Windows frees it when we exit


def _claim_single_instance(key: str) -> bool:
    """True when this process is the only py-multiview of this user.

    A named mutex is taken before anything slow happens (Qt, FFmpeg
    detection), so two launches a moment apart cannot both get through - the
    local socket alone left that window open, and it is only listened to once
    the first instance is far enough along.
    """
    global _instance_mutex
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, f"Local\\{key}")
    if not handle:
        return True  # cannot tell: let the socket check decide
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _instance_mutex = handle
    return True


def main() -> None:
    setup_logging()
    first = _claim_single_instance(_instance_key())
    log.info("%s %s démarrage (Python %s)", APP_NAME, __version__, sys.version.split()[0])

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon_path = APP_ICON_ICO_PATH if APP_ICON_ICO_PATH.exists() else APP_ICON_PNG_PATH
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    key = _instance_key()
    if not first:
        # The other instance may still be starting: give it time to listen.
        raised = _activate_running_instance(key, patience=8.0)
        log.info("Une instance est déjà ouverte%s : fermeture de celle-ci.",
                 ", elle a été mise au premier plan" if raised else "")
        return
    if _activate_running_instance(key):
        log.info("Une instance est déjà ouverte, elle a été mise au premier plan.")
        return
    QLocalServer.removeServer(key)  # stale socket after a crash
    server = QLocalServer()
    server.listen(key)

    # Imported late so the QApplication exists before any widget code runs.
    from .config import load_config
    from .engine import Engine
    from .ui import theme

    # The palette is read while widgets are built: pick it before the window exists.
    theme.set_mode(load_config().get("appearance", "light"))
    from .ui.main_window import MainWindow

    theme.apply_theme(app)
    engine = Engine()
    engine.tools.refresh()
    window = MainWindow(engine)

    def _on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(window.bring_to_front)
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(_on_connection)
    window.show()
    code = app.exec()
    engine.shutdown()
    log.info("Fermeture (code %s)", code)
    sys.exit(code)
