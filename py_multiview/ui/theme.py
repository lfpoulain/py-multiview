"""Dark theme (Fusion + palette + QSS) and vector icons."""

from __future__ import annotations

import tempfile
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

BG = "#0e1016"
SURFACE = "#161922"
SURFACE_2 = "#1c202b"
FIELD = "#11141b"
BORDER = "#272c3a"
BORDER_HOVER = "#394054"
TEXT = "#e7e9f0"
TEXT_DIM = "#a1a9ba"
TEXT_MUTED = "#6b7386"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#8e80ff"
ACCENT_PRESSED = "#6a59f2"
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
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
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


def _asset(name: str, color: str, stroke: float = 2.0) -> str:
    """Write an SVG used by the stylesheet and return its URL-friendly path."""
    folder = Path(tempfile.gettempdir()) / "py-multiview-ui"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{color.strip('#')}.svg"
    if not path.exists():
        path.write_bytes(_svg(name, color, stroke))
    return path.as_posix()


def _stylesheet() -> str:
    chevron = _asset("chevron", TEXT_DIM)
    check = _asset("check", "#ffffff", 3.0)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_HOVER};
    border-radius: 6px; padding: 6px 8px;
}}

QFrame#Header {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLabel#AppTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_MUTED}; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#SectionTitle {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#PanelTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#FieldLabel {{ color: {TEXT_MUTED}; }}
QLabel#Mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {TEXT_DIM}; }}
QLabel#Detail {{ color: {TEXT_DIM}; }}
QLabel#Detail[state="error"] {{ color: {DANGER}; }}
QLabel#Detail[state="waiting"], QLabel#Detail[state="retrying"] {{ color: {WARNING}; }}
QLabel#EmptyTitle {{ font-size: 15px; font-weight: 600; }}

QLabel#Pill {{
    border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600;
    background: #232837; color: {TEXT_MUTED};
}}
QLabel#Pill[state="active"] {{ background: #12342a; color: {SUCCESS}; }}
QLabel#Pill[state="waiting"], QLabel#Pill[state="retrying"] {{ background: #3a2f12; color: {WARNING}; }}
QLabel#Pill[state="starting"] {{ background: #13284a; color: {INFO}; }}
QLabel#Pill[state="error"] {{ background: #3d1c1f; color: {DANGER}; }}

QLabel#Chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 3px 12px; color: {TEXT_DIM};
}}
QLabel#Chip[state="active"] {{ color: {SUCCESS}; border-color: #1f5b47; }}

QLabel#Thumbnail {{ background: #07080c; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#StreamCard {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px;
}}
QFrame#StreamCard:hover {{ border-color: {BORDER_HOVER}; }}
QFrame#StreamCard[state="active"] {{ border-color: #1f5b47; }}
QFrame#StreamCard[state="waiting"], QFrame#StreamCard[state="retrying"] {{ border-color: #5a4718; }}
QFrame#StreamCard[state="error"] {{ border-color: #5c2a2e; }}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 14px; color: {TEXT}; min-height: 20px;
}}
QPushButton:hover {{ border-color: {BORDER_HOVER}; background: #222736; }}
QPushButton:pressed {{ background: #1a1e29; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background: {SURFACE}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[variant="success"] {{ background: #1f9d6f; border-color: #1f9d6f; color: white; font-weight: 600; }}
QPushButton[variant="success"]:hover {{ background: #25b07d; border-color: #25b07d; }}
QPushButton[variant="danger"] {{ background: #c2414b; border-color: #c2414b; color: white; font-weight: 600; }}
QPushButton[variant="danger"]:hover {{ background: #d24f59; border-color: #d24f59; }}
QPushButton[variant="danger-ghost"] {{ background: transparent; border-color: #5c2a2e; color: {DANGER}; }}
QPushButton[variant="danger-ghost"]:hover {{ background: #2a1619; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton[variant="ghost"]:hover {{ background: #222736; color: {TEXT}; }}
QPushButton[variant="icon"] {{
    background: transparent; border-color: transparent; padding: 0; min-width: 30px; max-width: 30px;
    min-height: 30px; max-height: 30px; border-radius: 8px;
}}
QPushButton[variant="icon"]:hover {{ background: #262b3a; }}
QPushButton[size="large"] {{ padding: 9px 20px; font-size: 13px; }}
QPushButton[size="small"] {{ padding: 3px 10px; min-height: 18px; font-size: 12px; }}
QFrame#SectionHead {{ background: transparent; border: none; }}
QFrame#SectionHead:hover QLabel#SectionTitle {{ color: {TEXT}; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 4px 8px; min-height: 22px; selection-background-color: {ACCENT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {BORDER_HOVER}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_MUTED}; }}
QLineEdit#NameEdit {{ background: transparent; border-color: transparent; font-size: 13px; font-weight: 600; }}
QLineEdit#NameEdit:hover {{ border-color: {BORDER}; }}
QLineEdit#NameEdit:focus {{ background: {FIELD}; border-color: {ACCENT}; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER_HOVER}; border-radius: 6px;
    selection-background-color: #2d2a5c; padding: 4px;
}}

QTextBrowser {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 8px;
}}
QProgressBar {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 6px;
    min-height: 12px; max-height: 12px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER_HOVER}; background: {FIELD};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}

QListWidget {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background: #1b1f2b; }}
QListWidget::item:selected {{ background: #2a2752; color: {TEXT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3142; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3b4258; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2c3142; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_HOVER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: #2a2752; }}
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
        QPalette.Button: SURFACE_2,
        QPalette.ButtonText: TEXT,
        QPalette.ToolTipBase: SURFACE_2,
        QPalette.ToolTipText: TEXT,
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: TEXT_MUTED,
        QPalette.Link: ACCENT_HOVER,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet())
