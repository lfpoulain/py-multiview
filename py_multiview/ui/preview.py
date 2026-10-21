"""Stream previews: a capture of the output screen itself.

Grabbing the display shows exactly what the audience sees (scaling, rotation,
black bars included) and costs no second decode of the stream.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap, QScreen
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from . import theme


def screen_for(display: dict) -> QScreen | None:
    """QScreen matching a display from ``displays.list_displays``."""
    screens = QGuiApplication.screens()
    name = display.get("deviceName", "")
    for screen in screens:
        if name and screen.name() == name:
            return screen
    index = display.get("index", -1)
    return screens[index] if 0 <= index < len(screens) else None


def grab_display(display: dict, size: QSize) -> QPixmap | None:
    screen = screen_for(display)
    if screen is None:
        return None
    shot = screen.grabWindow(0)
    if shot.isNull():
        return None
    target = size * 2  # crisp on high-DPI
    result = shot.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    result.setDevicePixelRatio(2)
    return result


class Thumbnail(QLabel):
    """Fixed-size preview; shows a placeholder when the stream is not on air."""

    clicked = Signal()

    def __init__(self, width: int = 160, height: int = 90, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Thumbnail")
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Aperçu de l'écran de sortie — clic pour agrandir")
        self._live = True
        self.clear_preview()

    def set_preview(self, pixmap: QPixmap | None) -> None:
        if pixmap is None:
            self.clear_preview()
            return
        self._live = True
        self.setPixmap(pixmap)
        self.setToolTip("Aperçu de l'écran de sortie — clic pour agrandir")

    def clear_preview(self) -> None:
        if self._live:
            self._live = False
            self.setPixmap(theme.pixmap("screen", theme.TEXT_MUTED, 28, stroke=1.5))
            self.setToolTip("Aperçu disponible quand le flux est en direct")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()


class PreviewDialog(QDialog):
    """Larger live preview of one output screen."""

    def __init__(self, parent: QWidget, title: str, display_for) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 580)
        self._display_for = display_for
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.image = QLabel("Écran indisponible")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setObjectName("Thumbnail")
        layout.addWidget(self.image)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)
        self._refresh()

    def _refresh(self) -> None:
        display = self._display_for()
        pixmap = grab_display(display, self.image.size()) if display else None
        if pixmap is None:
            self.image.setText("Écran indisponible")
        else:
            self.image.setPixmap(pixmap)
