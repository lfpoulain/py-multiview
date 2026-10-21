"""Application entry point."""

from __future__ import annotations

import getpass
import logging
import sys

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


def _activate_running_instance(key: str) -> bool:
    """Ask an already running instance to come to the front."""
    socket = QLocalSocket()
    socket.connectToServer(key)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"raise")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def main() -> None:
    setup_logging()
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
    if _activate_running_instance(key):
        log.info("Une instance est déjà ouverte, elle a été mise au premier plan.")
        return
    QLocalServer.removeServer(key)  # stale socket after a crash
    server = QLocalServer()
    server.listen(key)

    # Imported late so the QApplication exists before any widget code runs.
    from .engine import Engine
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    apply_theme(app)
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
