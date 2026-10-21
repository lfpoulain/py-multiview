"""Filesystem locations used at runtime (source checkout or frozen PyInstaller build)."""

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    """Directory holding bundled resources (``bin/``, ``img/``)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _app_dir() -> Path:
    """Directory of the executable (frozen) or of the source checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _user_config_dir(name: str = "py-multiview") -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / name


RESOURCE_DIR = _resource_root()
APP_DIR = _app_dir()
BIN_DIR = RESOURCE_DIR / "bin"
IMG_DIR = RESOURCE_DIR / "img"

# Two FFmpeg toolchains live side by side:
#   bin/omt/       FFmpeg built with libomt (OMT send/receive) - versioned in the repo
#   bin/standard/  Regular FFmpeg build with libsrt + GPU decoders - fetched by scripts/fetch-ffmpeg.ps1
OMT_BIN_DIR = BIN_DIR / "omt"
STANDARD_BIN_DIR = BIN_DIR / "standard"

CONFIG_DIR = _user_config_dir()
LEGACY_CONFIG_DIR = _user_config_dir("srt-multiview")  # before the rename to py-multiview
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"
LOG_PATH = LOG_DIR / "py-multiview.log"

APP_ICON_ICO_PATH = IMG_DIR / "icon.ico"
APP_ICON_PNG_PATH = IMG_DIR / "icon.png"
