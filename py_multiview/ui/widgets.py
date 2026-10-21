"""Small reusable widgets and helpers."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..process import Status
from . import theme

STATUS_TEXT = {
    Status.STOPPED: "Arrêté",
    Status.STARTING: "Démarrage",
    Status.WAITING: "En attente",
    Status.ACTIVE: "En direct",
    Status.RETRYING: "Reconnexion",
    Status.ERROR: "Erreur",
}


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_state(widget: QWidget, state: str) -> None:
    """Set the ``state`` dynamic property used by the stylesheet."""
    if widget.property("state") != state:
        widget.setProperty("state", state)
        repolish(widget)


def button(
    text: str = "",
    *,
    variant: str | None = None,
    icon: str | None = None,
    icon_color: str = theme.TEXT,
    tooltip: str = "",
    large: bool = False,
) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if variant:
        btn.setProperty("variant", variant)
    if large:
        btn.setProperty("size", "large")
    if icon:
        btn.setIcon(theme.icon(icon, icon_color))
        btn.setIconSize(QSize(16, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def icon_button(icon: str, tooltip: str, color: str = theme.TEXT_DIM) -> QPushButton:
    return button(variant="icon", icon=icon, icon_color=color, tooltip=tooltip)


def set_button(btn: QPushButton, text: str, variant: str, icon: str | None = None, icon_color: str = "#ffffff") -> None:
    btn.setText(text)
    if icon:
        btn.setIcon(theme.icon(icon, icon_color))
    if btn.property("variant") != variant:
        btn.setProperty("variant", variant)
        repolish(btn)


def label(text: str = "", name: str | None = None, *, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def field_label(text: str, width: int | None = None) -> QLabel:
    lbl = label(text, "FieldLabel")
    if width:
        lbl.setFixedWidth(width)
    return lbl


def spin(lo: int, hi: int, value: int, *, suffix: str = "", width: int | None = None) -> QSpinBox:
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setValue(value)
    box.setButtonSymbols(QSpinBox.NoButtons)
    box.setKeyboardTracking(False)  # emit valueChanged on commit, not on every keystroke
    box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if suffix:
        box.setSuffix(suffix)
    if width:
        box.setFixedWidth(width)
    return box


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


class StatusPill(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Pill")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_status(Status.STOPPED)

    def set_status(self, status: Status, text: str | None = None) -> None:
        self.setText("●  " + (text or STATUS_TEXT[status]))
        set_state(self, status.value)


class Section(QWidget):
    """Titled block: an uppercase title row (with optional actions) and a body."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        self.title = label(title.upper(), "SectionTitle")
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def add_action(self, widget: QWidget) -> None:
        self.header.addWidget(widget)
