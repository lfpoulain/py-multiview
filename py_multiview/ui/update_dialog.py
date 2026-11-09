"""Update window: check the latest GitHub release, download it, install it and restart."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QTextBrowser, QVBoxLayout

from .. import __version__, updater
from ..paths import APP_DIR
from .widgets import button, label

if TYPE_CHECKING:
    from .main_window import MainWindow

log = logging.getLogger(__name__)


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.0f} Mo"


class UpdateDialog(QDialog):
    checked = Signal(object, str)  # Release | None, error
    progressed = Signal(int, int)  # done, total
    downloaded = Signal(object, str)  # Path | None, error

    def __init__(self, main: MainWindow, release: updater.Release | None = None) -> None:
        super().__init__(main)
        self.main = main
        self.release = release
        self.kind = updater.install_kind()
        self._cancel = threading.Event()
        self._busy = False
        self.setWindowTitle("Mise à jour")
        self.resize(560, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        self.title = label("", "PanelTitle", wrap=True)
        layout.addWidget(self.title)
        self.info = label("", "Muted", wrap=True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.info)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.progress_text = label("", "Muted")
        layout.addWidget(self.progress_text)
        layout.addStretch(0)

        self.buttons = QHBoxLayout()
        layout.addLayout(self.buttons)

        self.checked.connect(self._on_checked)
        self.progressed.connect(self._on_progress)
        self.downloaded.connect(self._on_downloaded)

        if release is None:
            self._check()
        else:
            self._show_release(release)

    # ------------------------------------------------------------ layout helpers

    def _set_buttons(self, *specs) -> None:
        """``specs``: (text, slot, variant) from left to right, after a stretch."""
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # deleteLater alone leaves it on screen until the next loop turn
                widget.deleteLater()
        self.buttons.addWidget(label(f"Version installée : {__version__}", "Muted"))
        self.buttons.addStretch(1)
        for text, slot, variant in specs:
            btn = button(text, variant=variant)
            btn.clicked.connect(slot)
            self.buttons.addWidget(btn)
            if variant == "primary":
                btn.setDefault(True)
                btn.setFocus()

    def _state(self, title: str, info: str = "", *, notes: bool = False, progress: bool = False) -> None:
        self.title.setText(title)
        self.info.setText(info)
        self.info.setVisible(bool(info))
        self.notes.setVisible(notes)
        self.progress.setVisible(progress)
        self.progress_text.setVisible(progress)

    # ------------------------------------------------------------ check

    def _check(self) -> None:
        self._state("Recherche de mise à jour…", progress=True)
        self.progress.setRange(0, 0)
        self.progress_text.setText("Interrogation de GitHub")
        self._set_buttons(("Fermer", self.reject, None))

        def work() -> None:
            try:
                self.checked.emit(updater.fetch_latest(), "")
            except updater.UpdateError as exc:
                self.checked.emit(None, str(exc))
            except Exception as exc:  # never leave the dialog spinning
                log.exception("Recherche de mise à jour")
                self.checked.emit(None, f"Erreur inattendue : {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_checked(self, release, error: str) -> None:
        if error:
            self._state("Recherche impossible", error)
            self._set_buttons(("Réessayer", self._check, None), ("Fermer", self.reject, "primary"))
            return
        self.release = release
        self.main.on_update_checked(release)
        if not updater.is_newer(release.version):
            self._state("py-multiview est à jour", f"Dernière version publiée : {release.version}.")
            self._set_buttons(("Fermer", self.accept, "primary"))
            return
        self._show_release(release)

    # ------------------------------------------------------------ release

    def _show_release(self, release: updater.Release) -> None:
        notes = updater.release_notes(release.notes)
        self.notes.setMarkdown(notes or "_Pas de notes pour cette version._")
        asset = updater.asset_for(release, self.kind)
        title = f"py-multiview {release.version} est disponible"
        later = ("Plus tard", self.reject, None)
        skip = ("Ignorer cette version", self._skip, "ghost")
        page = ("Ouvrir la page de la release", self._open_page, "primary")

        if self.kind == updater.SOURCE:
            self._state(title, "Lancée depuis les sources : mettez à jour avec git pull.", notes=True)
            self._set_buttons(skip, later, page)
        elif asset is None:
            self._state(title, "Le fichier de cette version est introuvable dans la release.", notes=True)
            self._set_buttons(skip, later, page)
        elif self.kind == updater.PORTABLE and not updater.can_write(APP_DIR):
            self._state(title, f"Le dossier de l'application n'est pas modifiable :\n{APP_DIR}\n"
                               "Téléchargez la version portable depuis la page de la release.", notes=True)
            self._set_buttons(skip, later, page)
        else:
            how = ("L'installeur sera lancé ; Windows peut demander une autorisation."
                   if self.kind == updater.INSTALLER else "Les fichiers du dossier portable seront remplacés.")
            self._state(
                title,
                f"Téléchargement de {_mb(asset.size)}. {how} py-multiview se fermera puis redémarrera : "
                "les flux et l'émission en cours seront coupés quelques secondes.",
                notes=True,
            )
            self._set_buttons(skip, later, ("Installer et redémarrer", self._install, "primary"))

    def _skip(self) -> None:
        if self.release is not None:
            self.main.skip_update(self.release.version)
        self.reject()

    def _open_page(self) -> None:
        url = self.release.page if self.release else updater.RELEASES_PAGE
        QDesktopServices.openUrl(QUrl(url))
        self.accept()

    # ------------------------------------------------------------ install

    def _install(self) -> None:
        asset = updater.asset_for(self.release, self.kind)
        if asset is None:
            return
        self._cancel.clear()
        self._busy = True
        self._state(f"Téléchargement de py-multiview {self.release.version}…", progress=True)
        self.progress.setRange(0, max(asset.size, 1))
        self.progress.setValue(0)
        self.progress_text.setText(f"0 / {_mb(asset.size)}")
        self._set_buttons(("Annuler", self._abort, None))

        def work() -> None:
            try:
                path = updater.download(asset, progress=self.progressed.emit, cancel=self._cancel)
                self.downloaded.emit(path, "")
            except updater.UpdateError as exc:
                self.downloaded.emit(None, str(exc))
            except Exception as exc:
                log.exception("Téléchargement de la mise à jour")
                self.downloaded.emit(None, f"Erreur inattendue : {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.progress_text.setText(f"{_mb(done)} / {_mb(total)}" if total else _mb(done))

    def _abort(self) -> None:
        self._cancel.set()
        self.progress_text.setText("Annulation…")

    def _on_downloaded(self, path, error: str) -> None:
        self._busy = False
        if error:
            if self._cancel.is_set():
                self._show_release(self.release)
                return
            self._state("Mise à jour impossible", error)
            self._set_buttons(("Réessayer", self._install, None), ("Fermer", self.reject, "primary"))
            return
        self.progress.setRange(0, 0)
        self.progress_text.setText("Installation…")
        try:
            if self.kind == updater.INSTALLER:
                updater.launch_installer(path)
            else:
                updater.launch_portable_swap(path)
        except (updater.UpdateError, OSError) as exc:
            log.exception("Lancement de la mise à jour")
            self._state("Mise à jour impossible", str(exc))
            self._set_buttons(("Réessayer", self._install, None), ("Fermer", self.reject, "primary"))
            return
        log.info("Mise à jour vers %s lancée (%s), fermeture.", self.release.version, self.kind)
        self.accept()
        self.main.quit_for_update()

    def reject(self) -> None:
        if self._busy:
            self._abort()
        super().reject()
