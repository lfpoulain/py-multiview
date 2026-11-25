"""« Réglages » page: application-wide settings, grouped by tab, applied as you go."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..paths import CONFIG_PATH, LOG_PATH
from . import theme
from .widgets import Card, Switch, button, label, spin

if TYPE_CHECKING:
    from .main_window import MainWindow

CONTENT_WIDTH = 820
APPEARANCES = (("light", "Clair"), ("dark", "Sombre"))


def _scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    return scroll


class SettingsPage(QWidget):
    """Every change is saved at once, like the rest of the window: no Save button to forget."""

    TABS = (
        ("general", "Général"),
        ("reception", "Réception"),
        ("remote", "Pilotage à distance"),
        ("system", "Système"),
    )

    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        self._loading = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(300)
        self._apply_timer.timeout.connect(self._apply)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        for index, (_key, title) in enumerate(self.TABS):
            btn = button(title, variant="underline")
            btn.setCheckable(True)
            self.tab_group.addButton(btn, index)
            tabs.addWidget(btn)
        tabs.addStretch(1)
        tab_bar = QFrame()
        tab_bar.setObjectName("Transparent")
        tab_bar.setLayout(tabs)
        outer.addWidget(tab_bar)

        self.stack = QStackedWidget()
        for builder in (self._general, self._reception, self._remote, self._system):
            column = QWidget()
            column.setObjectName("Transparent")
            col = QVBoxLayout(column)
            col.setContentsMargins(0, 0, 8, 16)
            col.setSpacing(16)
            builder(col)
            col.addStretch(1)
            column.setMaximumWidth(CONTENT_WIDTH)
            holder = QWidget()
            holder.setObjectName("Transparent")
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(column, 1)
            line.addStretch(0)
            self.stack.addWidget(_scroll(holder))
        outer.addWidget(self.stack, 1)

        self.tab_group.idClicked.connect(self.stack.setCurrentIndex)
        self.tab_group.button(0).setChecked(True)
        self.load()

    # ------------------------------------------------------------ tabs

    def _general(self, col: QVBoxLayout) -> None:
        startup = Card("Au lancement", "Ce que py-multiview relance tout seul à l'ouverture de la session.", "power")
        self.auto_rx = Switch("Démarrer la réception")
        startup.add_row("Démarrer la réception", "Chaque flux qui a un écran s'affiche dès l'ouverture.", self.auto_rx)
        self.auto_tx = Switch("Démarrer l'émission OMT")
        startup.add_row("Démarrer l'émission OMT", "Publie l'écran choisi dans la page Émission.", self.auto_tx)
        col.addWidget(startup)

        window = Card("Fenêtre", "", "screen")
        self.close_minimizes = Switch("La croix réduit la fenêtre")
        window.add_row(
            "La croix réduit la fenêtre",
            "Les flux continuent. Pour quitter vraiment : Ctrl+Q, ou clic droit sur l'icône près de l'horloge.",
            self.close_minimizes,
        )
        self.appearance = QComboBox()
        for key, text in APPEARANCES:
            self.appearance.addItem(text, key)
        self.appearance.setFixedWidth(160)
        window.add_row("Apparence", "Thème clair ou sombre. Appliqué au prochain démarrage.", self.appearance)
        col.addWidget(window)

        updates = Card("Mises à jour", f"Version installée : {__version__}", "download")
        check_now = button("Rechercher maintenant", icon="refresh", icon_color=theme.TEXT_DIM)
        check_now.clicked.connect(lambda: self.main.open_update())
        updates.add_action(check_now)
        self.auto_update = Switch("Rechercher automatiquement")
        updates.add_row(
            "Rechercher automatiquement",
            "Au lancement puis toutes les 6 heures. Rien n'est installé sans votre accord.",
            self.auto_update,
        )
        col.addWidget(updates)

        for widget in (self.auto_rx, self.auto_tx, self.close_minimizes, self.auto_update):
            widget.toggled.connect(self._changed)
        self.appearance.currentIndexChanged.connect(self._changed)

    def _reception(self, col: QVBoxLayout) -> None:
        card = Card("Décodage", "Comment les flux SRT reçus sont décodés avant d'être affichés.", "cpu")
        self.decode = QComboBox()
        available = self.main.engine.tools.gpu_decoders()
        for key, text in (
            ("cpu", "Processeur (recommandé)"),
            ("nvidia", "GPU NVIDIA — NVDEC"),
            ("intel", "GPU Intel — Quick Sync"),
            ("amd", "GPU AMD — AMF"),
        ):
            supported = key == "cpu" or key in available
            self.decode.addItem(text if supported else f"{text} (indisponible)", key)
            if not supported:
                self.decode.model().item(self.decode.count() - 1).setEnabled(False)
        self.decode.setMinimumWidth(240)
        card.add_row(
            "Décodage vidéo",
            "Le GPU soulage le processeur avec de nombreux flux H.264. Les flux OMT ne sont pas concernés.",
            self.decode,
        )
        self.decode.currentIndexChanged.connect(self._changed)
        col.addWidget(card)

        hint = Card()
        hint.add_widget(label(
            "L'exclusion de l'écran principal se règle dans la page Écrans ; "
            "le moteur d'émission (FFmpeg ou libomt) dans la page Émission OMT.", "Muted", wrap=True))
        col.addWidget(hint)

    def _remote(self, col: QVBoxLayout) -> None:
        card = Card("API locale", "Pilotage par py-livedeck, Companion, Stream Deck ou la domotique.", "plug")
        self.api_enabled = Switch("Ouvrir l'API locale")
        card.add_row("Ouvrir l'API locale", "Désactivée, aucune commande extérieure n'est acceptée.", self.api_enabled)
        self.api_port = spin(1, 65535, 5090, width=110)
        card.add_row("Port", "Port HTTP d'écoute.", self.api_port)
        self.api_local = Switch("Ce poste uniquement")
        card.add_row("Ce poste uniquement", "Désactivé, l'API est joignable depuis tout le réseau local.", self.api_local)
        token = QHBoxLayout()
        token.setSpacing(8)
        self.api_token = QLineEdit()
        self.api_token.setPlaceholderText("vide = accès libre")
        self.api_token.setMinimumWidth(260)
        token.addWidget(self.api_token, 1)
        generate = button("Générer")
        generate.clicked.connect(self._generate_token)
        token.addWidget(generate)
        card.add_row(
            "Jeton d'accès",
            "Facultatif. Rempli, chaque requête doit porter « Authorization: Bearer <jeton> ».",
            token, stretch_control=True,
        )
        self.api_hint = label("", "Mono", wrap=True)
        self.api_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        callout = QFrame()
        callout.setObjectName("Callout")
        inner = QVBoxLayout(callout)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(6)
        inner.addWidget(self.api_hint)
        inner.addWidget(label("Sans jeton, l'API est ouverte à tout le réseau : à n'ouvrir que sur un réseau "
                              "de confiance.", "RowDesc", wrap=True))
        card.add_widget(callout, separated=False)
        col.addWidget(card)

        self.api_enabled.toggled.connect(self._changed)
        self.api_local.toggled.connect(self._changed)
        self.api_port.valueChanged.connect(self._changed)
        self.api_token.editingFinished.connect(self._changed)

    def _system(self, col: QVBoxLayout) -> None:
        engine = Card("Moteur FFmpeg", "Outils utilisés pour recevoir, router et émettre.", "cpu")
        redetect = button("Re-détecter", icon="refresh", icon_color=theme.TEXT_DIM)
        redetect.clicked.connect(self._redetect)
        engine.add_action(redetect)
        self.diag_checks = QVBoxLayout()
        self.diag_checks.setSpacing(0)
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setLayout(self.diag_checks)
        engine.add_widget(holder, separated=False)
        self.diag_text = label("", "Mono", wrap=True)
        self.diag_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        engine.add_widget(self.diag_text)
        col.addWidget(engine)
        self._fill_diag()

        files = Card("Fichiers", "", "folder")
        open_cfg = button("Ouvrir", icon="folder", icon_color=theme.TEXT_DIM)
        open_cfg.clicked.connect(lambda: self.main.open_path(CONFIG_PATH.parent))
        files.add_row("Configuration", str(CONFIG_PATH), open_cfg)
        open_logs = button("Ouvrir", icon="logs", icon_color=theme.TEXT_DIM)
        open_logs.clicked.connect(lambda: self.main.open_path(LOG_PATH.parent))
        files.add_row("Journaux", str(LOG_PATH), open_logs)
        col.addWidget(files)

        danger = Card("Réinitialiser", "Efface flux, affectations, noms d'écrans, routes et réglages d'émission. "
                                       "Tout ce qui tourne est arrêté.")
        reset = button("Réinitialiser…", variant="danger-ghost")
        reset.clicked.connect(self.main.reset_configuration)
        danger.add_action(reset)
        col.addWidget(danger)

    # ------------------------------------------------------------ data

    def load(self) -> None:
        """Show the current configuration (also after a reset)."""
        config = self.main.config
        self._loading = True
        try:
            self.auto_rx.setChecked(config["autoStartReceiver"])
            self.auto_tx.setChecked(config["autoStartSender"])
            self.close_minimizes.setChecked(config["closeToMinimize"])
            self.auto_update.setChecked(config["updates"]["autoCheck"])
            self.appearance.setCurrentIndex(max(0, self.appearance.findData(config.get("appearance", "light"))))
            self.decode.setCurrentIndex(max(0, self.decode.findData(config["receiver"]["decode"])))
            self.api_enabled.setChecked(config["api"]["enabled"])
            self.api_port.setValue(config["api"]["port"])
            self.api_local.setChecked(config["api"]["localOnly"])
            self.api_token.setText(config["api"].get("token", ""))
        finally:
            self._loading = False
        self._refresh_api_hint()

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._refresh_api_hint()
        self._apply_timer.start()

    def _apply(self) -> None:
        config = self.main.config
        config["autoStartReceiver"] = self.auto_rx.isChecked()
        config["autoStartSender"] = self.auto_tx.isChecked()
        config["closeToMinimize"] = self.close_minimizes.isChecked()
        config["updates"]["autoCheck"] = self.auto_update.isChecked()
        appearance = self.appearance.currentData()
        if appearance != config.get("appearance"):
            config["appearance"] = appearance
            if appearance != theme.MODE:
                self.main.notify("Le nouveau thème s'appliquera au prochain démarrage de py-multiview.", 8000)
        config["receiver"]["decode"] = self.decode.currentData()
        api_before = dict(config["api"])
        config["api"]["enabled"] = self.api_enabled.isChecked()
        config["api"]["port"] = self.api_port.value()
        config["api"]["localOnly"] = self.api_local.isChecked()
        config["api"]["token"] = self.api_token.text().strip()
        if config["api"] != api_before:
            self.main.refresh_api()
        self.main.commit()

    def _generate_token(self) -> None:
        self.api_token.setText(secrets.token_urlsafe(24))
        self._changed()

    def _refresh_api_hint(self) -> None:
        if not self.api_enabled.isChecked():
            self.api_hint.setText("API fermée.")
            return
        host = "127.0.0.1" if self.api_local.isChecked() else (self.main.host_ip or "127.0.0.1")
        base = f"http://{host}:{self.api_port.value()}"
        self.api_hint.setText(f"py-livedeck   {base}\nÉtat Stream Deck   {base}/api/deck/state\n"
                              f"Exemple   POST {base}/sender/toggle")

    def _fill_diag(self) -> None:
        tools = self.main.engine.tools
        while self.diag_checks.count():
            item = self.diag_checks.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        checks = (
            ("Réception SRT", tools.player_for("srt")),
            ("Réception OMT", tools.player_for("omt")),
            ("Routage SRT", tools.ffmpeg_for("srt")),
            ("Émission OMT", tools.ffmpeg_for("omt")),
        )
        line = QHBoxLayout()
        line.setSpacing(18)
        for name, ok in checks:
            glyph = label()
            glyph.setPixmap(theme.pixmap("check" if ok else "close", theme.SUCCESS if ok else theme.DANGER, 14))
            line.addWidget(glyph)
            line.addSpacing(-12)
            line.addWidget(label(name, "Dim"))
        line.addStretch(1)
        row = QWidget()
        row.setObjectName("Transparent")
        row.setLayout(line)
        line.setContentsMargins(0, 0, 0, 4)
        self.diag_checks.addWidget(row)
        lines = [f"{tc.describe()}\n   {tc.directory}" for tc in tools.toolchains] or ["Aucun FFmpeg trouvé."]
        self.diag_text.setText("\n".join(lines))

    def _redetect(self) -> None:
        self.main.engine.tools.refresh()
        self._fill_diag()
        self.main.refresh_engine_summary()
