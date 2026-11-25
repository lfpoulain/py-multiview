"""Small reusable widgets and helpers."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
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
    icon_color: str | None = None,
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
        btn.setIcon(theme.icon(icon, theme.ON_ACCENT if variant == "primary" else icon_color or theme.TEXT))
        btn.setIconSize(QSize(16, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def icon_button(icon: str, tooltip: str, color: str | None = None) -> QPushButton:
    return button(variant="icon", icon=icon, icon_color=color or theme.TEXT_DIM, tooltip=tooltip)


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


class Switch(QCheckBox):
    """On/off toggle drawn as a pill. Same API as a checkbox."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)
        if text:
            self.setAccessibleName(text)
            self.setText("")

    def hitButton(self, pos) -> bool:  # noqa: N802 - the whole pill is clickable
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        on = self.isChecked()
        track = QColor(theme.ACCENT if on else theme.BORDER_HOVER)
        if not self.isEnabled():
            track.setAlphaF(0.45)
        rect = QRectF(1, 2, self.width() - 2, self.height() - 4)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob = rect.height() - 4
        x = rect.right() - knob - 2 if on else rect.left() + 2
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, rect.top() + 2, knob, knob))
        if self.hasFocus():
            painter.setPen(QColor(theme.ACCENT))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), rect.height() / 2 + 1, rect.height() / 2 + 1)
        painter.end()


class IconTile(QFrame):
    """Small rounded square holding an icon, to head a card."""

    def __init__(self, name: str, size: int = 34, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IconTile")
        self.setFixedSize(size, size)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.glyph = QLabel()
        self.glyph.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.glyph)
        self.set_icon(name)

    def set_icon(self, name: str, color: str | None = None) -> None:
        self.glyph.setPixmap(theme.pixmap(name, color or theme.ACCENT, 18))


class Card(QFrame):
    """White block of the page: optional icon, title and subtitle, actions, then rows."""

    def __init__(self, title: str = "", subtitle: str = "", icon: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        self.header = QHBoxLayout()
        self.header.setSpacing(12)
        if icon:
            self.tile = IconTile(icon)
            self.header.addWidget(self.tile, 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label(title, "CardTitle")
        titles.addWidget(self.title)
        self.subtitle = label(subtitle, "CardSub", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        titles.addWidget(self.subtitle)
        self.header.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.header.addLayout(self.actions)
        if title or icon:
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        outer.addLayout(self.body)
        self._rows = 0

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget, 0, Qt.AlignTop)

    def add_row(self, title: str, description: str = "", control: QWidget | QHBoxLayout | None = None,
                *, stretch_control: bool = False) -> QWidget:
        """A setting line: what it is on the left, the control on the right."""
        row = QWidget()
        row.setObjectName("Transparent")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 12, 0, 12)
        line.setSpacing(16)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        texts.addWidget(label(title, "RowTitle"))
        if description:
            texts.addWidget(label(description, "RowDesc", wrap=True))
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setLayout(texts)
        holder.setMinimumWidth(180)
        line.addWidget(holder, 2 if stretch_control else 3)
        if isinstance(control, QHBoxLayout):
            box = QWidget()
            box.setObjectName("Transparent")
            control.setContentsMargins(0, 0, 0, 0)
            box.setLayout(control)
            control = box
        if control is not None:
            align = Qt.AlignVCenter if stretch_control else Qt.AlignVCenter | Qt.AlignRight
            line.addWidget(control, 3 if stretch_control else 0, align)
        self.add_widget(row)
        return row

    def add_widget(self, widget: QWidget | None = None, *, layout: QHBoxLayout | QVBoxLayout | None = None,
                   separated: bool = True) -> None:
        if self._rows and separated:
            self.body.addWidget(divider())
        if widget is not None:
            self.body.addWidget(widget)
        if layout is not None:
            self.body.addLayout(layout)
        self._rows += 1


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
        painter.setBrush(QColor(theme.NEUTRAL_SOFT))
        painter.drawRoundedRect(rect, radius, radius)
        if self._position > 0.0:
            color = theme.DANGER if self._position > 0.97 else theme.WARNING if self._position > 0.85 else theme.SUCCESS
            painter.setBrush(QColor(color))
            fill = QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * self._position), rect.height())
            painter.drawRoundedRect(fill, radius, radius)
        painter.end()
