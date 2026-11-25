"""Theme (Fusion + palette + QSS) and vector icons.

Two palettes share one stylesheet: a light one in the spirit of analytics
dashboards (white cards on a soft grey page, violet accent) and a dark one for
dimmed control rooms. ``set_mode`` must run before any widget is built: the
colours below are read when widgets and icons are created.
"""

from __future__ import annotations

import tempfile
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

MODES = ("light", "dark")

_PALETTES = {
    "light": {
        "BG": "#f5f5f8",
        "SURFACE": "#ffffff",
        "SURFACE_2": "#f4f4f7",
        "HOVER": "#efeff4",
        "FIELD": "#ffffff",
        "BORDER": "#e3e3ea",
        "BORDER_HOVER": "#c6c6d2",
        "TEXT": "#1d1d27",
        "TEXT_DIM": "#555566",
        "TEXT_MUTED": "#8b8b9b",
        "ACCENT": "#7856ff",
        "ACCENT_HOVER": "#6644f5",
        "ACCENT_PRESSED": "#5534e0",
        "ACCENT_SOFT": "#f0ecff",
        "ACCENT_TEXT": "#5a37f0",
        "ON_ACCENT": "#ffffff",
        "SUCCESS": "#12a150",
        "SUCCESS_SOFT": "#e4f6ec",
        "WARNING": "#c97a06",
        "WARNING_SOFT": "#fcf1dc",
        "DANGER": "#e2474d",
        "DANGER_SOFT": "#fdeaea",
        "INFO": "#2f7cf6",
        "INFO_SOFT": "#e6effe",
        "NEUTRAL_SOFT": "#efeff3",
        "SCROLL": "#d4d4de",
    },
    "dark": {
        "BG": "#111116",
        "SURFACE": "#18181e",
        "SURFACE_2": "#202028",
        "HOVER": "#25252e",
        "FIELD": "#141419",
        "BORDER": "#2b2b35",
        "BORDER_HOVER": "#3f3f4c",
        "TEXT": "#ececf2",
        "TEXT_DIM": "#b1b1c0",
        "TEXT_MUTED": "#7f7f90",
        "ACCENT": "#8a6dff",
        "ACCENT_HOVER": "#9c84ff",
        "ACCENT_PRESSED": "#7856ff",
        "ACCENT_SOFT": "#28213f",
        "ACCENT_TEXT": "#b4a2ff",
        "ON_ACCENT": "#ffffff",
        "SUCCESS": "#3fcf7c",
        "SUCCESS_SOFT": "#15301f",
        "WARNING": "#f2a73a",
        "WARNING_SOFT": "#33270f",
        "DANGER": "#f2585d",
        "DANGER_SOFT": "#3a1a1c",
        "INFO": "#5d9cff",
        "INFO_SOFT": "#172640",
        "NEUTRAL_SOFT": "#24242c",
        "SCROLL": "#34343f",
    },
}

# Video tiles stay dark in both modes: a picture reads best on black.
SLATE = "#121217"
SLATE_LINE = "#1d1d25"
SLATE_TEXT = "#9a9aab"

MODE = "light"
BG = SURFACE = SURFACE_2 = HOVER = FIELD = BORDER = BORDER_HOVER = ""
TEXT = TEXT_DIM = TEXT_MUTED = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ACCENT_SOFT = ACCENT_TEXT = ON_ACCENT = ""
SUCCESS = SUCCESS_SOFT = WARNING = WARNING_SOFT = DANGER = DANGER_SOFT = INFO = INFO_SOFT = ""
NEUTRAL_SOFT = SCROLL = ""
STATE_COLORS: dict[str, str] = {}


def set_mode(mode: str) -> None:
    """Pick the palette. Call before building widgets (icons are cached)."""
    global MODE, STATE_COLORS
    MODE = mode if mode in _PALETTES else "light"
    globals().update(_PALETTES[MODE])
    STATE_COLORS = {
        "stopped": TEXT_MUTED,
        "starting": INFO,
        "waiting": WARNING,
        "active": SUCCESS,
        "retrying": WARNING,
        "error": DANGER,
    }
    icon.cache_clear()


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
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6'
            'M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6"/><circle cx="12" cy="12" r="6.5"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "grid": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/>'
            '<rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    "identify": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4M10 8.5l2-1.5v6"/>',
    "wand": '<path d="M5 19L15 9M14 4v2M19 9h2M17.5 5.5L19 4M18 13l1.5 1.5M9 4l1 1.5"/>',
    "route": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
    "broadcast": '<circle cx="12" cy="12" r="2"/><path d="M8 8a5.6 5.6 0 0 0 0 8M16 8a5.6 5.6 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3.5" y="14" width="4" height="6" rx="1.5"/>'
                  '<rect x="16.5" y="14" width="4" height="6" rx="1.5"/>',
    "pip": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><rect x="12" y="12" width="6.5" height="5" rx="1" fill="{c}"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "volume": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "plug": '<path d="M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0zM12 16.5V21"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 2.5v3M14.5 2.5v3M9.5 18.5v3M14.5 18.5v3'
           'M2.5 9.5h3M2.5 14.5h3M18.5 9.5h3M18.5 14.5h3"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    "power": '<path d="M12 3v8M6.4 6.6a8 8 0 1 0 11.2 0"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
}


def _svg(name: str, color: str, stroke: float = 2.0) -> bytes:
    body = _ICONS[name].replace("{c}", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode()


def pixmap(name: str, color: str | None = None, size: int = 18, ratio: float = 2.0, stroke: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color or TEXT, stroke)))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, pm.width(), pm.height()))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@cache
def icon(name: str, color: str | None = None) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color or TEXT), QIcon.Normal)
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
    chevron = _asset("chevron", TEXT_MUTED)
    check = _asset("check", ON_ACCENT, 3.0)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{
    background: {TEXT}; color: {SURFACE}; border: none; border-radius: 6px; padding: 6px 9px;
}}

/* ---- shell: navigation, top bar, pages ---- */
QFrame#Nav {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#Brand {{ font-size: 15px; font-weight: 700; }}
QLabel#BrandSub {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#NavGroup {{ color: {TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; padding: 0 12px; }}
QPushButton[variant="nav"] {{
    background: transparent; border: none; border-radius: 8px; padding: 0; min-height: 36px; text-align: left;
}}
QPushButton[variant="nav"]:hover {{ background: {HOVER}; }}
QPushButton[variant="nav"]:checked {{ background: {ACCENT_SOFT}; }}
QLabel#NavText {{ color: {TEXT_DIM}; font-weight: 500; }}
QLabel#NavText[active="true"] {{ color: {ACCENT_TEXT}; font-weight: 650; }}
QLabel#NavBadge {{
    border-radius: 9px; padding: 1px 7px; font-size: 11px; font-weight: 650;
    background: {NEUTRAL_SOFT}; color: {TEXT_MUTED};
}}
QLabel#NavBadge[state="active"] {{ background: {SUCCESS_SOFT}; color: {SUCCESS}; }}
QLabel#NavBadge[state="waiting"], QLabel#NavBadge[state="retrying"] {{ background: {WARNING_SOFT}; color: {WARNING}; }}
QLabel#NavBadge[state="starting"] {{ background: {INFO_SOFT}; color: {INFO}; }}
QLabel#NavBadge[state="error"] {{ background: {DANGER_SOFT}; color: {DANGER}; }}
QLabel#NavBadge[state="empty"] {{ background: transparent; }}

QFrame#TopBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#PageTitle {{ font-size: 20px; font-weight: 700; }}
QLabel#PageSub {{ color: {TEXT_MUTED}; }}
QWidget#Page, QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; }}

/* ---- cards and setting rows ---- */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 650; }}
QLabel#CardSub {{ color: {TEXT_MUTED}; }}
QLabel#RowTitle {{ font-weight: 600; }}
QLabel#RowDesc {{ color: {TEXT_MUTED}; font-size: 12px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#Callout {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#IconTile {{ background: {ACCENT_SOFT}; border: none; border-radius: 9px; }}

QLabel#Muted {{ color: {TEXT_MUTED}; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#SectionTitle {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#PanelTitle {{ font-size: 16px; font-weight: 650; }}
QLabel#FieldLabel {{ color: {TEXT_MUTED}; }}
QLabel#Mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {TEXT_DIM}; }}
QLabel#Detail {{ color: {TEXT_DIM}; }}
QLabel#Detail[state="error"] {{ color: {DANGER}; }}
QLabel#Detail[state="waiting"], QLabel#Detail[state="retrying"] {{ color: {WARNING}; }}
QLabel#EmptyTitle {{ font-size: 16px; font-weight: 650; }}

QLabel#Pill {{
    border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 650;
    background: {NEUTRAL_SOFT}; color: {TEXT_MUTED};
}}
QLabel#Pill[state="active"] {{ background: {SUCCESS_SOFT}; color: {SUCCESS}; }}
QLabel#Pill[state="waiting"], QLabel#Pill[state="retrying"] {{ background: {WARNING_SOFT}; color: {WARNING}; }}
QLabel#Pill[state="starting"] {{ background: {INFO_SOFT}; color: {INFO}; }}
QLabel#Pill[state="error"] {{ background: {DANGER_SOFT}; color: {DANGER}; }}

QLabel#StreamTitle {{ font-size: 15px; font-weight: 650; }}
QFrame#Segment {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton[variant="segment"] {{ background: transparent; border: 1px solid transparent; color: {TEXT_DIM};
    padding: 4px 14px; border-radius: 6px; font-weight: 600; }}
QPushButton[variant="segment"]:hover {{ color: {TEXT}; }}
QPushButton[variant="segment"]:checked {{ background: {SURFACE}; border-color: {BORDER}; color: {TEXT}; }}
QPushButton[variant="underline"] {{
    background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
    color: {TEXT_MUTED}; padding: 8px 2px; margin-right: 18px; font-weight: 600; min-width: 20px;
}}
QPushButton[variant="underline"]:hover {{ color: {TEXT}; }}
QPushButton[variant="underline"]:checked {{ color: {ACCENT_TEXT}; border-bottom-color: {ACCENT}; }}

QLabel#Thumbnail {{ background: {SLATE}; border: 1px solid {BORDER}; border-radius: 8px; color: {SLATE_TEXT}; }}
QLabel#Thumbnail:focus {{ border-color: {ACCENT}; }}
QFrame#StreamCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#StreamCard:hover {{ border-color: {BORDER_HOVER}; }}
QFrame#StreamCard[state="active"] {{ border-color: {SUCCESS}; }}
QFrame#StreamCard[state="waiting"], QFrame#StreamCard[state="retrying"] {{ border-color: {WARNING}; }}
QFrame#StreamCard[state="error"] {{ border-color: {DANGER}; }}

/* ---- buttons ---- */
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 14px; color: {TEXT}; min-height: 20px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {BORDER_HOVER}; background: {HOVER}; }}
QPushButton:pressed {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background: {SURFACE_2}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; font-weight: 650; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[variant="success"] {{ background: {SUCCESS}; border-color: {SUCCESS}; color: white; font-weight: 650; }}
QPushButton[variant="success"]:hover {{ background: {SUCCESS}; border-color: {TEXT}; }}
QPushButton[variant="danger"] {{ background: {DANGER}; border-color: {DANGER}; color: white; font-weight: 650; }}
QPushButton[variant="danger"]:hover {{ background: {DANGER}; border-color: {TEXT}; }}
QPushButton[variant="danger-ghost"] {{ background: transparent; border-color: {DANGER}; color: {DANGER}; }}
QPushButton[variant="danger-ghost"]:hover {{ background: {DANGER_SOFT}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton[variant="ghost"]:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton[variant="icon"] {{
    background: transparent; border-color: transparent; padding: 0; min-width: 30px; max-width: 30px;
    min-height: 30px; max-height: 30px; border-radius: 8px;
}}
QPushButton[variant="icon"]:hover {{ background: {HOVER}; }}
QPushButton[variant="tab"] {{ background: transparent; border-color: {BORDER}; color: {TEXT_DIM}; border-radius: 14px; padding: 5px 14px; }}
QPushButton[variant="tab"]:hover {{ border-color: {BORDER_HOVER}; color: {TEXT}; }}
QPushButton[variant="tab"]:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT_TEXT}; font-weight: 650; }}
QPushButton[variant="field"] {{ background: {FIELD}; text-align: left; padding: 4px 8px; font-weight: 400; }}
QPushButton[variant="field"]:hover {{ border-color: {BORDER_HOVER}; background: {FIELD}; }}
QPushButton[size="large"] {{ padding: 8px 18px; font-size: 13px; }}
QPushButton[size="small"] {{ padding: 3px 10px; min-height: 18px; font-size: 12px; }}
QFrame#SectionHead {{ background: transparent; border: none; }}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 4px 9px; min-height: 22px; selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {BORDER_HOVER}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_MUTED}; background: {SURFACE_2}; }}
QLineEdit#NameEdit {{ background: transparent; border-color: transparent; font-size: 14px; font-weight: 650; }}
QLineEdit#NameEdit:hover {{ border-color: {BORDER}; }}
QLineEdit#NameEdit:focus {{ background: {FIELD}; border-color: {ACCENT}; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; padding: 4px;
}}

QTextBrowser {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
}}
QProgressBar {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
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
QListWidget::item {{ padding: 8px 10px; border-radius: 7px; color: {TEXT}; }}
QListWidget::item:hover {{ background: {HOVER}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QPushButton:focus, QPushButton[variant="segment"]:focus, QCheckBox::indicator:focus {{ border: 1px solid {ACCENT}; }}
QPushButton[variant="nav"]:focus, QPushButton[variant="underline"]:focus {{ border: none; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI Variable Text", 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)

    palette = QPalette()
    roles = {
        QPalette.Window: BG,
        QPalette.WindowText: TEXT,
        QPalette.Base: FIELD,
        QPalette.AlternateBase: SURFACE_2,
        QPalette.Text: TEXT,
        QPalette.Button: SURFACE,
        QPalette.ButtonText: TEXT,
        QPalette.ToolTipBase: TEXT,
        QPalette.ToolTipText: SURFACE,
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: ON_ACCENT,
        QPalette.PlaceholderText: TEXT_MUTED,
        QPalette.Link: ACCENT_TEXT,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet())


set_mode("light")
