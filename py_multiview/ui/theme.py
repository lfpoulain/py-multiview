"""Dark theme (Fusion + palette + QSS) and vector icons.

Same visual language as py-livedeck and py-intercom: near-black surfaces, a
single teal accent, flat full-width header, pill status chips and switch-style
toggles. Colour names are kept stable (``SURFACE``, ``TEXT_DIM``...) because
the widgets and dialogs refer to them.
"""

from __future__ import annotations

import tempfile
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

BG = "#0d0f13"
SURFACE = "#14171d"
SURFACE_2 = "#1a1e26"
RAISED = "#222733"
FIELD = "#101319"
BORDER = "#272c37"
BORDER_HOVER = "#384053"
TEXT = "#e7eaf0"
TEXT_DIM = "#8b93a5"
TEXT_MUTED = "#667085"
ACCENT = "#2dd4bf"
ACCENT_HOVER = "#5eead4"
ACCENT_PRESSED = "#14b8a6"
ACCENT_TEXT = "#04211d"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
DANGER = "#f87171"
INFO = "#60a5fa"

STATE_COLORS = {
    "stopped": TEXT_MUTED,
    "starting": INFO,
    "waiting": WARNING,
    "active": SUCCESS,
    "retrying": WARNING,
    "error": DANGER,
}

# Which icon colour reads on each button variant (the filled ones are dark on teal).
VARIANT_ICON = {
    "primary": ACCENT_TEXT,
    "success": ACCENT_TEXT,
    "danger": DANGER,
    "danger-ghost": DANGER,
}

# 24x24 stroke icons.
_ICONS = {
    "play": '<path d="M7 4.5v15l12-7.5z" fill="{c}" stroke="{c}" stroke-linejoin="round"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{c}"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "logs": '<path d="M5 4h14v16H5zM8 9h8M8 13h8M8 17h5"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5"/>',
    "settings": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "identify": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4M10 8.5l2-1.5v6"/>',
    "wand": '<path d="M5 19L15 9M14 4v2M19 9h2M17.5 5.5L19 4M18 13l1.5 1.5M9 4l1 1.5"/>',
    "route": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
    "broadcast": '<circle cx="12" cy="12" r="2"/><path d="M8 8a5.6 5.6 0 0 0 0 8M16 8a5.6 5.6 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}


def _svg(name: str, color: str, stroke: float = 2.0) -> bytes:
    body = _ICONS[name].replace("{c}", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode()


def pixmap(name: str, color: str = TEXT, size: int = 18, ratio: float = 2.0, stroke: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, pm.width(), pm.height()))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@cache
def icon(name: str, color: str = TEXT) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color), QIcon.Normal)
    result.addPixmap(pixmap(name, TEXT_MUTED), QIcon.Disabled)
    return result


def _write_asset(filename: str, data: bytes) -> str:
    """Write an SVG used by the stylesheet and return its URL-friendly path."""
    folder = Path(tempfile.gettempdir()) / "py-multiview-ui"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path.as_posix()


def _asset(name: str, color: str, stroke: float = 2.0) -> str:
    return _write_asset(f"{name}-{color.strip('#')}.svg", _svg(name, color, stroke))


def _switch_asset(checked: bool, hover: bool = False) -> str:
    """A 30x16 switch with its knob: QSS alone cannot draw the knob."""
    if checked:
        track, edge, knob, cx = ACCENT, ACCENT, ACCENT_TEXT, 22
    else:
        track, edge, knob, cx = RAISED, BORDER_HOVER if hover else BORDER, TEXT_DIM, 8
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="16" viewBox="0 0 30 16">'
        f'<rect x="0.5" y="0.5" width="29" height="15" rx="7.5" fill="{track}" stroke="{edge}"/>'
        f'<circle cx="{cx}" cy="8" r="5" fill="{knob}"/></svg>'
    ).encode()
    return _write_asset(f"switch-{int(checked)}{int(hover)}.svg", svg)


def _stylesheet() -> str:
    chevron = _asset("chevron", TEXT_DIM)
    sw_off, sw_off_hover = _switch_asset(False), _switch_asset(False, hover=True)
    sw_on = _switch_asset(True)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px;
}}

QFrame#Header {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER}; border-radius: 0; }}
QFrame#Panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLabel#Brand {{ font-size: 17px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#BrandAccent {{ font-size: 17px; font-weight: 700; color: {ACCENT}; }}
QLabel#AppTitle {{ font-size: 17px; font-weight: 700; }}
QLabel#Muted {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Hint {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#SectionTitle {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }}
QLabel#PanelTitle {{ font-size: 18px; font-weight: 650; }}
QLabel#FieldLabel {{ color: {TEXT_DIM}; }}
QLabel#Mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {TEXT_DIM}; }}
QLabel#Detail {{ color: {TEXT_DIM}; }}
QLabel#Detail[state="error"] {{ color: {DANGER}; }}
QLabel#Detail[state="waiting"], QLabel#Detail[state="retrying"] {{ color: {WARNING}; }}
QLabel#EmptyTitle {{ font-size: 16px; font-weight: 650; }}

QLabel#Pill {{
    border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600;
    background: {RAISED}; color: {TEXT_DIM};
}}
QLabel#Pill[state="active"] {{ background: #0f2f2a; color: {ACCENT}; }}
QLabel#Pill[state="waiting"], QLabel#Pill[state="retrying"] {{ background: #3a2f12; color: {WARNING}; }}
QLabel#Pill[state="starting"] {{ background: #13284a; color: {INFO}; }}
QLabel#Pill[state="error"] {{ background: #3d1c1f; color: {DANGER}; }}

QLabel#Chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 4px 12px; font-size: 12px; font-weight: 600; color: {TEXT_DIM};
}}
QLabel#Chip[state="active"] {{ color: {TEXT}; border-color: #1c5a52; }}

QLabel#Thumbnail {{ background: #07080c; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#StreamCard {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#StreamCard:hover {{ border-color: {BORDER_HOVER}; }}
QFrame#StreamCard[state="active"] {{ border-color: #1c5a52; }}
QFrame#StreamCard[state="waiting"], QFrame#StreamCard[state="retrying"] {{ border-color: #5a4718; }}
QFrame#StreamCard[state="error"] {{ border-color: #5c2a2e; }}

QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 14px; color: {TEXT}; font-weight: 600; min-height: 20px;
}}
QPushButton:hover {{ border-color: #343b4a; background: #2a3040; }}
QPushButton:pressed {{ background: #1c2029; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background: {SURFACE}; }}
QPushButton[variant="primary"], QPushButton[variant="success"] {{
    background: {ACCENT}; border: none; color: {ACCENT_TEXT}; font-weight: 700;
}}
QPushButton[variant="primary"]:hover, QPushButton[variant="success"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:pressed, QPushButton[variant="success"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[variant="primary"]:disabled, QPushButton[variant="success"]:disabled {{
    background: {RAISED}; color: {TEXT_MUTED};
}}
QPushButton[variant="danger"], QPushButton[variant="danger-ghost"] {{
    background: transparent; border: 1px solid #4a2a2e; color: {DANGER};
}}
QPushButton[variant="danger"]:hover, QPushButton[variant="danger-ghost"]:hover {{ background: #2a1a1d; }}
QPushButton[variant="ghost"] {{ background: transparent; border: none; color: {TEXT_DIM}; padding: 6px 10px; }}
QPushButton[variant="ghost"]:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton[variant="icon"] {{
    background: transparent; border: none; padding: 0; min-width: 30px; max-width: 30px;
    min-height: 30px; max-height: 30px; border-radius: 8px;
}}
QPushButton[variant="icon"]:hover {{ background: {SURFACE_2}; }}
QPushButton[size="large"] {{ padding: 9px 20px; font-size: 13px; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 4px 10px; min-height: 22px;
    selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: {BORDER_HOVER}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_MUTED}; }}
QLineEdit#NameEdit {{ background: transparent; border-color: transparent; font-size: 13px; font-weight: 600; }}
QLineEdit#NameEdit:hover {{ border-color: {BORDER}; }}
QLineEdit#NameEdit:focus {{ background: {FIELD}; border-color: {ACCENT}; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; color: {TEXT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {RAISED}; selection-color: {TEXT}; padding: 4px; outline: none;
}}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 6px; }}

QCheckBox {{ spacing: 8px; color: {TEXT_DIM}; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{ width: 30px; height: 16px; border: none; background: transparent; image: url("{sw_off}"); }}
QCheckBox::indicator:hover {{ image: url("{sw_off_hover}"); }}
QCheckBox::indicator:checked {{ image: url("{sw_on}"); }}
QCheckBox::indicator:disabled {{ image: url("{sw_off}"); }}

QListWidget {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 7px 8px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {RAISED}; color: {TEXT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2c3345; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {RAISED}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)

    palette = QPalette()
    roles = {
        QPalette.Window: BG,
        QPalette.WindowText: TEXT,
        QPalette.Base: FIELD,
        QPalette.AlternateBase: SURFACE,
        QPalette.Text: TEXT,
        QPalette.Button: RAISED,
        QPalette.ButtonText: TEXT,
        QPalette.ToolTipBase: RAISED,
        QPalette.ToolTipText: TEXT,
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: ACCENT_TEXT,
        QPalette.PlaceholderText: TEXT_MUTED,
        QPalette.Link: ACCENT_HOVER,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet())
