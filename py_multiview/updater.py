"""In-app updates from the latest GitHub release.

Three ways the app can be running, three ways to update:

- **installer**: installed by ``py-multiview-setup.exe`` (``unins000.exe`` sits next to
  the exe). The new installer is downloaded and run silently; it closes the app,
  replaces the files and relaunches it (``/RELAUNCH``, see ``scripts/installer.iss``).
- **portable**: the unzipped portable folder. The new zip is downloaded and extracted,
  then a small batch script waits for the app to exit, copies the new files over the
  old ones and relaunches it.
- **source**: a source checkout. Nothing is replaced; the release page is opened.

Everything here is UI-free; network calls are blocking and meant for a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, __version__
from .paths import APP_DIR

log = logging.getLogger(__name__)

REPO = "lfpoulain/py-multiview"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALLER_ASSET = "py-multiview-setup.exe"
PORTABLE_ASSET = "py-multiview-portable.zip"
EXE_NAME = "py-multiview.exe"
UPDATE_DIR = Path(tempfile.gettempdir()) / "py-multiview-update"

INSTALLER, PORTABLE, SOURCE = "installer", "portable", "source"


class UpdateError(Exception):
    """Any failure the user should read: message in French, ready to display."""


class CancelledError(UpdateError):
    pass


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    sha256: str = ""  # GitHub's own digest of the uploaded file, when it gives one


@dataclass
class Release:
    version: str
    tag: str
    page: str
    notes: str = ""
    assets: dict[str, Asset] = field(default_factory=dict)


# ------------------------------------------------------------------ versions


def parse_version(text: str) -> tuple[int, ...]:
    """``"v0.3.12"`` -> ``(0, 3, 12)``. Anything after the numbers (``-beta``) is ignored."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return ()
    parts = [int(n) for n in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 0.3 == 0.3.0
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    remote_v = parse_version(remote)
    return bool(remote_v) and remote_v > parse_version(local)


# ------------------------------------------------------------------ release lookup


def parse_release(data: dict) -> Release:
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("Réponse de GitHub inattendue.")
    assets = {}
    for raw in data.get("assets") or []:
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("browser_download_url"):
            continue
        digest = str(raw.get("digest") or "")
        assets[raw["name"]] = Asset(
            name=raw["name"],
            url=raw["browser_download_url"],
            size=int(raw.get("size") or 0),
            sha256=digest[7:].lower() if digest.startswith("sha256:") else "",
        )
    tag = str(data["tag_name"])
    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        page=str(data.get("html_url") or RELEASES_PAGE),
        notes=str(data.get("body") or ""),
        assets=assets,
    )


def release_notes(body: str) -> str:
    """Release notes without the "Télécharger" block the workflow puts on top (useless in-app)."""
    kept: list[str] = []
    skipping = False
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            skipping = line[3:].strip().lower().startswith("télécharger")
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}", "Accept": accept})


def fetch_latest(timeout: float = 10.0) -> Release:
    try:
        with urllib.request.urlopen(_request(LATEST_URL, "application/vnd.github+json"), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Aucune release publiée trouvée sur GitHub.") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub limite les requêtes pour le moment, réessayez plus tard.") from exc
        raise UpdateError(f"GitHub a répondu {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Impossible de joindre GitHub (pas d'accès Internet ?).") from exc
    except ValueError as exc:
        raise UpdateError("Réponse de GitHub illisible.") from exc
    return parse_release(data)


# ------------------------------------------------------------------ install kind


def install_kind(app_dir: Path = APP_DIR, frozen: bool | None = None) -> str:
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen or sys.platform != "win32":
        return SOURCE
    if any(app_dir.glob("unins*.exe")):
        return INSTALLER
    return PORTABLE


def asset_for(release: Release, kind: str) -> Asset | None:
    name = {INSTALLER: INSTALLER_ASSET, PORTABLE: PORTABLE_ASSET}.get(kind)
    return release.assets.get(name) if name else None


def can_write(directory: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=directory):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------ download


def download(
    asset: Asset,
    dest_dir: Path = UPDATE_DIR,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download ``asset`` into ``dest_dir`` and check its SHA-256 when GitHub gave one."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / asset.name
    partial = target.with_name(target.name + ".part")
    hasher = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(asset.url, "application/octet-stream"), timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or asset.size or 0)
            with open(partial, "wb") as out:
                while chunk := resp.read(256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise CancelledError("Téléchargement annulé.")
                    out.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement interrompu : {exc}") from exc

    if asset.size and done != asset.size:
        partial.unlink(missing_ok=True)
        raise UpdateError("Fichier téléchargé incomplet.")
    if asset.sha256 and hasher.hexdigest() != asset.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("Le fichier téléchargé est corrompu (empreinte SHA-256 différente).")
    os.replace(partial, target)
    return target


# ------------------------------------------------------------------ install


def installer_arguments() -> list[str]:
    # /RELAUNCH is ours: the installer restarts the app once the files are in place.
    return ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH"]


def launch_installer(path: Path) -> None:
    """Start the installer through the shell so Windows can show the UAC prompt."""
    import ctypes

    params = " ".join(installer_arguments())
    result = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), params, str(path.parent), 1)
    if result <= 32:
        raise UpdateError(f"Impossible de lancer l'installeur (code {result}).")


def extract_portable(zip_path: Path, dest_dir: Path) -> Path:
    """Extract the portable zip and return the folder holding ``py-multiview.exe``."""
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            root = dest_dir.resolve()
            for member in archive.namelist():
                if not (root / member).resolve().is_relative_to(root):
                    raise UpdateError("Archive invalide.")
            archive.extractall(dest_dir)
    except zipfile.BadZipFile as exc:
        raise UpdateError("Archive portable illisible.") from exc
    found = next(iter(sorted(dest_dir.rglob(EXE_NAME), key=lambda p: len(p.parts))), None)
    if found is None:
        raise UpdateError(f"{EXE_NAME} introuvable dans l'archive.")
    return found.parent


def portable_script(pid: int, source: Path, target: Path, staging: Path) -> str:
    """Batch script that swaps the portable folder once the app has exited.

    ``_internal`` (Python + Qt + FFmpeg) is mirrored so stale files do not linger;
    the top level is only copied over, leaving anything the user put there alone.
    """
    exe = target / EXE_NAME
    # Full System32 paths: a Git or MSYS "find" earlier on the PATH would break the wait loop.
    sys32 = "%SystemRoot%\\System32\\"
    # /IS /IT: copy even when size and date match, a file is never trusted to be the same.
    copy = "/R:5 /W:1 /IS /IT /NFL /NDL /NJH /NJS >nul"
    return "\r\n".join(
        [
            "@echo off",
            f"{sys32}chcp.com 65001 >nul",
            ":wait",
            f'{sys32}tasklist.exe /FI "PID eq {pid}" /NH | {sys32}find.exe "{pid}" >nul'
            f" && ({sys32}ping.exe -n 2 127.0.0.1 >nul & goto wait)",
            f'{sys32}robocopy.exe "{source / "_internal"}" "{target / "_internal"}" /MIR {copy}',
            f'{sys32}robocopy.exe "{source}" "{target}" /E /XD "{source / "_internal"}" {copy}',
            f'start "" "{exe}"',
            f'rmdir /S /Q "{staging}"',
            '(goto) 2>nul & del "%~f0"',
            "",
        ]
    )


def launch_portable_swap(zip_path: Path, target: Path = APP_DIR) -> None:
    staging = UPDATE_DIR / "portable"
    source = extract_portable(zip_path, staging)
    script = UPDATE_DIR / "apply-update.cmd"
    script.write_text(portable_script(os.getpid(), source, target, staging), encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        ["cmd.exe", "/C", str(script)],
        cwd=str(UPDATE_DIR),
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cleanup() -> None:
    """Remove what a previous update left behind (called at startup)."""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
