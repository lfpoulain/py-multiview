"""Small reusable widgets and helpers."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
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
    small: bool = False,
) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if variant:
        btn.setProperty("variant", variant)
    if large:
        btn.setProperty("size", "large")
    elif small:
        btn.setProperty("size", "small")
    if icon:
        btn.setIcon(theme.icon(icon, "#04211d" if variant == "primary" else icon_color))
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


class ElidedLabel(QLabel):
    """One-line label that cuts its text with « … » instead of widening the layout."""

    def __init__(self, text: str = "", name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = ""
        # Ignored: the text length must never decide the width of a column.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self._fit()

    def full_text(self) -> str:
        return self._full

    def _fit(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(8, self.width() - 2))
        if shown != self.text():
            super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class _SectionHead(QFrame):
    """Clickable header row of a collapsible section."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SectionHead")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CollapsibleSection(QWidget):
    """A section that folds to its header.

    The header stays useful when folded: it carries the section's status and
    primary action (``add_action``) and a one-line summary of the current
    settings or of the problem, so a long sidebar can be reduced to a short
    list of lines without hiding what is running.
    """

    toggled = Signal(bool)

    def __init__(self, title: str, key: str, expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._expanded = expanded
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Two rows: title and action on top, then the summary on the full width
        # (beside the button it would be cut after a few words).
        self.head = _SectionHead()
        self.head.setAccessibleName(title)
        stack = QVBoxLayout(self.head)
        stack.setContentsMargins(0, 6, 0, 6)
        stack.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._chevron = QLabel()
        self._chevron.setFixedSize(14, 14)
        self._chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self._chevron)
        self.title = label(title.upper(), "SectionTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.title)
        row.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        row.addLayout(self.actions)
        stack.addLayout(row)

        under = QHBoxLayout()
        under.setContentsMargins(22, 0, 0, 0)
        self.summary = ElidedLabel("", "Detail")
        self.summary.setAttribute(Qt.WA_TransparentForMouseEvents)
        under.addWidget(self.summary)
        stack.addLayout(under)
        outer.addWidget(self.head)

        self.content = QWidget()
        self.content.setObjectName("Transparent")
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(22, 2, 0, 8)
        self.body.setSpacing(8)
        outer.addWidget(self.content)

        self.head.clicked.connect(self.toggle)
        self._apply()

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def set_summary(self, text: str, state: str = "") -> None:
        self.summary.set_full_text(text)
        set_state(self.summary, state or "stopped")

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, *, notify: bool = True) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply()
        if notify:
            self.toggled.emit(expanded)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _apply(self) -> None:
        self.content.setVisible(self._expanded)
        self._chevron.setPixmap(theme.pixmap("chevron" if self._expanded else "chevron-right",
                                             theme.TEXT_DIM, 14))


class LevelMeter(QWidget):
    """Thin peak meter on a dB scale: -60 dB at the left, 0 dB at the right."""

    FLOOR_DB = -60.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position = 0.0
        self._shown = 0.0
        self.setFixedHeight(6)
        self.setToolTip("Niveau du son envoyé")

    @classmethod
    def position(cls, level: float) -> float:
        """0..1 along the bar for a linear peak level (0..1)."""
        if level <= 0.0:
            return 0.0
        db = 20.0 * math.log10(min(1.0, level))
        return max(0.0, min(1.0, 1.0 - db / cls.FLOOR_DB))

    def set_level(self, level: float) -> None:
        position = self.position(level)
        # Fall back smoothly, rise at once: readable without flicker.
        self._shown = position if position >= self._shown else max(position, self._shown - 0.08)
        if abs(self._shown - self._position) > 0.002:
            self._position = self._shown
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setBrush(QColor(theme.FIELD))
        painter.drawRoundedRect(rect, radius, radius)
        if self._position > 0.0:
            color = theme.DANGER if self._position > 0.97 else theme.WARNING if self._position > 0.85 else theme.SUCCESS
            painter.setBrush(QColor(color))
            fill = QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * self._position), rect.height())
            painter.drawRoundedRect(fill, radius, radius)
        painter.end()
