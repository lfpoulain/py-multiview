"""Secondary windows: logs, OMT discovery, routes, preferences, screen identification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, omt, omtlib
from ..config import new_id, normalize_route
from ..ffmpeg import udp_input_url
from ..paths import CONFIG_PATH, LOG_PATH
from ..process import Status
from . import theme
from .widgets import Section, StatusPill, button, divider, field_label, label, set_button, spin

if TYPE_CHECKING:
    from .main_window import MainWindow


class LogDialog(QDialog):
    """Live view of a supervised process' output."""

    def __init__(self, parent: QWidget, title: str, fetch) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 520)
        self._fetch = fetch
        self._last: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.text, 1)

        row = QHBoxLayout()
        row.addWidget(label("Mise à jour automatique · journal complet : " + str(LOG_PATH), "Muted"), 1)
        copy_btn = button("Copier", icon="copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        row.addWidget(copy_btn)
        close_btn = button("Fermer", variant="primary")
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _refresh(self) -> None:
        lines = self._fetch()
        if lines == self._last:
            return
        self._last = lines
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.text.setPlainText("\n".join(lines) if lines else "Aucune sortie pour l'instant.")
        if at_bottom:
            bar.setValue(bar.maximum())


class _DiscoveryWorker(QObject):
    finished = Signal(list, str)

    def __init__(self, ffmpeg) -> None:
        super().__init__()
        self._ffmpeg = ffmpeg

    def run(self) -> None:
        sources, error = omt.find_sources(self._ffmpeg)
        self.finished.emit(sources, error or "")


class OMTDiscoveryDialog(QDialog):
    def __init__(self, parent: QWidget, ffmpeg, current: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Sources OMT du réseau")
        self.resize(520, 400)
        self.selected_source = ""
        self._ffmpeg = ffmpeg
        self._thread: QThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        self.status = label("Recherche en cours…", "Dim", wrap=True)
        layout.addWidget(self.status)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.list.currentItemChanged.connect(
            lambda item, _prev: item and self.manual.setText(item.text())
        )
        layout.addWidget(self.list, 1)

        manual_row = QHBoxLayout()
        manual_row.addWidget(field_label("Source"))
        self.manual = QLineEdit(current)
        self.manual.setPlaceholderText("MACHINE (Nom de la source)")
        manual_row.addWidget(self.manual, 1)
        layout.addLayout(manual_row)

        buttons = QHBoxLayout()
        self.rescan = button("Relancer la recherche", icon="refresh")
        self.rescan.clicked.connect(self._scan)
        buttons.addWidget(self.rescan)
        buttons.addStretch(1)
        cancel = button("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = button("Utiliser", variant="primary")
        ok.clicked.connect(self._accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        QTimer.singleShot(0, self._scan)

    def _scan(self) -> None:
        if self._thread is not None:
            return
        self.rescan.setEnabled(False)
        self.status.setText("Recherche des sources OMT annoncées sur le réseau…")
        self.list.clear()
        self._thread = QThread(self)
        self._worker = _DiscoveryWorker(self._ffmpeg)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_finished(self, sources: list, error: str) -> None:
        self._thread = None
        self.rescan.setEnabled(True)
        if error:
            self.status.setText(f"⚠ {error}")
        elif not sources:
            self.status.setText(
                "Aucune source OMT détectée. Vérifie que l'émetteur est actif, sur le même réseau, "
                "et que le pare-feu autorise mDNS (UDP 5353) et TCP 6400-6600."
            )
        else:
            self.status.setText(f"{len(sources)} source(s) trouvée(s). Double-clic pour choisir.")
            for name in sources:
                QListWidgetItem(name, self.list)
            self.list.setCurrentRow(0)

    def _accept(self) -> None:
        chosen = self.manual.text().strip()
        if not chosen:
            self.status.setText("Choisis une source dans la liste ou saisis son nom.")
            return
        self.selected_source = chosen
        self.accept()

    def done(self, result: int) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(9000)
        super().done(result)


class RoutesDialog(QDialog):
    """Create, edit and run SRT -> UDP multicast routes."""

    def __init__(self, main: MainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Routage SRT → multicast")
        self.resize(820, 480)
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        layout = QHBoxLayout()
        layout.setSpacing(16)
        outer.addLayout(layout, 1)

        left = QVBoxLayout()
        left.setSpacing(8)
        intro = label(
            "Une route reçoit un flux SRT une seule fois et le rediffuse en multicast UDP : "
            "plusieurs écrans peuvent alors afficher le même flux.",
            "Dim",
            wrap=True,
        )
        intro.setFixedWidth(260)
        left.addWidget(intro)
        self.list = QListWidget()
        self.list.setFixedWidth(260)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        btns = QHBoxLayout()
        add = button("Nouvelle route", variant="primary", icon="plus", icon_color="#ffffff")
        add.clicked.connect(self._add)
        btns.addWidget(add)
        self.delete_btn = button(variant="icon", icon="trash", icon_color=theme.DANGER, tooltip="Supprimer la route")
        self.delete_btn.clicked.connect(self._delete)
        btns.addWidget(self.delete_btn)
        left.addLayout(btns)
        layout.addLayout(left)

        self.form_panel = QWidget()
        right = QVBoxLayout(self.form_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        head = QHBoxLayout()
        self.pill = StatusPill()
        head.addWidget(self.pill)
        head.addStretch(1)
        self.toggle_btn = button("Démarrer", variant="success", icon="play", icon_color="#ffffff")
        self.toggle_btn.clicked.connect(self._toggle)
        head.addWidget(self.toggle_btn)
        logs = button(variant="icon", icon="logs", icon_color=theme.TEXT_DIM, tooltip="Journal ffmpeg")
        logs.clicked.connect(self._logs)
        head.addWidget(logs)
        right.addLayout(head)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_edit)
        self.in_port = spin(1, 65535, 9001, width=90)
        self.in_latency = spin(0, 10000, 120, suffix=" ms", width=90)
        self.maddr = QLineEdit()
        self.maddr.setPlaceholderText("239.10.10.10")
        self.maddr.editingFinished.connect(self._on_edit)
        self.mport = spin(1, 65535, 1234, width=90)
        self.ttl = spin(1, 255, 1, width=90)
        self.ttl.setToolTip("1 = réseau local uniquement")
        self.pkt = spin(188, 65507, 1316, width=90)
        for w in (self.in_port, self.in_latency, self.mport, self.ttl, self.pkt):
            w.valueChanged.connect(self._on_edit)
        form.addRow(field_label("Nom"), self.name_edit)
        form.addRow(field_label("Port SRT entrant"), self.in_port)
        form.addRow(field_label("Latence SRT"), self.in_latency)
        form.addRow(divider())
        form.addRow(field_label("Groupe multicast"), self.maddr)
        form.addRow(field_label("Port multicast"), self.mport)
        form.addRow(field_label("TTL"), self.ttl)
        form.addRow(field_label("Taille paquet"), self.pkt)
        right.addLayout(form)
        self.detail = label("", "Detail", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.detail)
        right.addStretch(1)
        layout.addWidget(self.form_panel, 1)

        self.empty = label("Aucune route. Crée-en une avec « Nouvelle route ».", "Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close_btn = button("Fermer", variant="primary")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        outer.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)
        self._reload()

    # -- helpers -------------------------------------------------------------

    def _routes(self) -> list[dict]:
        return self.main.config["routes"]

    def _current(self) -> dict | None:
        item = self.list.currentItem()
        rid = item.data(Qt.UserRole) if item else None
        return next((r for r in self._routes() if r["id"] == rid), None)

    def _reload(self, select: str | None = None) -> None:
        current = select or (self._current() or {}).get("id")
        self.list.blockSignals(True)
        self.list.clear()
        for r in self._routes():
            item = QListWidgetItem(r["name"])
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)
        rows = [i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole) == current]
        self.list.setCurrentRow(rows[0] if rows else (0 if self.list.count() else -1))
        has_routes = self.list.count() > 0
        self.form_panel.setVisible(has_routes)
        self.empty.setVisible(not has_routes)
        self.delete_btn.setEnabled(has_routes)
        self._on_select()

    def _on_select(self, *_args) -> None:
        route = self._current()
        if not route:
            return
        self._loading = True
        try:
            self.name_edit.setText(route["name"])
            self.in_port.setValue(route["inputPort"])
            self.in_latency.setValue(route["inputLatency"])
            self.maddr.setText(route["multicastAddr"])
            self.mport.setValue(route["multicastPort"])
            self.ttl.setValue(route["ttl"])
            self.pkt.setValue(route["pktSize"])
        finally:
            self._loading = False
        self._refresh_status()

    def _on_edit(self, *_args) -> None:
        route = self._current()
        if self._loading or not route:
            return
        route.update(
            normalize_route(
                {
                    "id": route["id"],
                    "name": self.name_edit.text(),
                    "inputPort": self.in_port.value(),
                    "inputLatency": self.in_latency.value(),
                    "multicastAddr": self.maddr.text(),
                    "multicastPort": self.mport.value(),
                    "ttl": self.ttl.value(),
                    "pktSize": self.pkt.value(),
                },
                1,
            )
        )
        item = self.list.currentItem()
        if item:
            item.setText(route["name"])
        self.main.on_routes_changed()

    def _refresh_status(self) -> None:
        route = self._current()
        if not route:
            return
        info = self.main.engine.route_info(route["id"])
        running = route["id"] in self.main.engine.active_routes
        self.pill.set_status(info.status)
        if running:
            set_button(self.toggle_btn, "Arrêter", "danger", "stop")
        else:
            set_button(self.toggle_btn, "Démarrer", "success", "play")
        out = udp_input_url(route["multicastAddr"], route["multicastPort"]).split("?")[0]
        host = self.main.engine.host_ip or "<IP>"
        text = f"Entrée srt://{host}:{route['inputPort']}  →  sortie {out}"
        if info.detail:
            text += f"\n{info.detail}"
        elif info.status == Status.WAITING:
            text += "\nEn attente d'un émetteur SRT…"
        self.detail.setText(text)

    # -- actions -------------------------------------------------------------

    def _add(self) -> None:
        used = {r["inputPort"] for r in self._routes()} | {
            s["port"] for s in self.main.config["streams"] if s["source"] == "srt"
        }
        port = 9101
        while port in used:
            port += 1
        mports = {r["multicastPort"] for r in self._routes()}
        mport = 1234
        while mport in mports:
            mport += 1
        route = normalize_route(
            {"id": new_id("route"), "name": f"Route {len(self._routes()) + 1}", "inputPort": port, "multicastPort": mport},
            len(self._routes()) + 1,
        )
        self._routes().append(route)
        self.main.on_routes_changed()
        self._reload(select=route["id"])
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _delete(self) -> None:
        route = self._current()
        if not route:
            return
        users = [s["name"] for s in self.main.config["streams"] if s["source"] == "route" and s["sourceRouteId"] == route["id"]]
        message = f"Supprimer la route « {route['name']} » ?"
        if users:
            message += "\n\nFlux qui l'utilisent (ils repasseront en source SRT) :\n• " + "\n• ".join(users)
        if QMessageBox.question(self, "Supprimer la route", message) != QMessageBox.Yes:
            return
        self.main.engine.stop_route(route["id"])
        self.main.config["routes"] = [r for r in self._routes() if r["id"] != route["id"]]
        for s in self.main.config["streams"]:
            if s["source"] == "route" and s["sourceRouteId"] == route["id"]:
                s["source"], s["sourceRouteId"] = "srt", ""
        self.main.on_routes_changed(reload_cards=True)
        self._reload()

    def _toggle(self) -> None:
        route = self._current()
        if not route:
            return
        if route["id"] in self.main.engine.active_routes:
            self.main.engine.stop_route(route["id"])
        else:
            self.main.engine.start_route(route["id"])
        self._refresh_status()

    def _logs(self) -> None:
        route = self._current()
        if route:
            self.main.show_logs(f"route:{route['id']}", f"Journal — route {route['name']}")


class PreferencesDialog(QDialog):
    def __init__(self, main: MainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Préférences")
        self.resize(640, 560)
        config = main.config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)

        general = Section("Réception")
        self.exclude_primary = QCheckBox("Ne jamais afficher de flux sur l'écran principal")
        self.exclude_primary.setChecked(config["excludePrimaryDisplay"])
        general.body.addWidget(self.exclude_primary)
        decode_row = QHBoxLayout()
        decode_row.addWidget(field_label("Décodage vidéo"))
        self.decode = QComboBox()
        available = main.engine.tools.gpu_decoders()
        for key, text in (
            ("cpu", "Processeur (recommandé, tous codecs)"),
            ("nvidia", "GPU NVIDIA — NVDEC (H.264)"),
            ("intel", "GPU Intel — Quick Sync (H.264)"),
            ("amd", "GPU AMD — AMF (H.264)"),
        ):
            supported = key == "cpu" or key in available
            self.decode.addItem(text if supported else f"{text} — non disponible", key)
            if not supported:
                self.decode.model().item(self.decode.count() - 1).setEnabled(False)
        index = self.decode.findData(config["receiver"]["decode"])
        self.decode.setCurrentIndex(max(0, index))
        self.decode.setToolTip(
            "Le décodage GPU soulage le processeur avec de nombreux flux H.264.\n"
            "Les flux OMT ne sont pas concernés (décodés par libomt)."
        )
        decode_row.addWidget(self.decode, 1)
        general.body.addLayout(decode_row)
        layout.addWidget(general)

        emission = Section("Émission OMT")
        self.sender_native = QCheckBox("Émettre en direct par libomt (expérimental)")
        self.sender_native.setChecked(config["sender"].get("native", False))
        self.sender_native.setEnabled(omtlib.available())
        self.sender_native.setToolTip(
            "Décoché (par défaut), l'écran est capturé et publié par FFmpeg (gdigrab).\n"
            "Coché, libomt émet sans processus intermédiaire : plus léger, mais\n"
            "l'image est noire sur certaines machines."
        )
        emission.body.addWidget(self.sender_native)
        emission.body.addWidget(
            label("FFmpeg est le chemin éprouvé. libomt encode l'image lui-même et coûte moins cher "
                  "en processeur, mais donne une image noire sur certaines machines.", "Muted", wrap=True)
        )
        layout.addWidget(emission)

        api = Section("Pilotage à distance")
        self.api_enabled = QCheckBox("Ouvrir l'API locale (py-livedeck, Companion, domotique…)")
        self.api_enabled.setChecked(config["api"]["enabled"])
        api.body.addWidget(self.api_enabled)
        api_row = QHBoxLayout()
        api_row.addWidget(field_label("Port"))
        self.api_port = spin(1, 65535, config["api"]["port"], width=96)
        api_row.addWidget(self.api_port)
        self.api_local = QCheckBox("Ce poste uniquement")
        self.api_local.setChecked(config["api"]["localOnly"])
        self.api_local.setToolTip("Décoché, l'API est joignable depuis le réseau local.")
        api_row.addWidget(self.api_local)
        api_row.addStretch(1)
        api.body.addLayout(api_row)
        self.api_hint = label("", "Mono", wrap=True)
        self.api_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        api.body.addWidget(self.api_hint)
        api.body.addWidget(
            label("Sans mot de passe, comme les autres services pilotés par py-livedeck : "
                  "à n'ouvrir que sur un réseau de confiance.", "Muted", wrap=True)
        )
        self.api_enabled.stateChanged.connect(self._refresh_api_hint)
        self.api_port.valueChanged.connect(self._refresh_api_hint)
        self.api_local.stateChanged.connect(self._refresh_api_hint)
        self._refresh_api_hint()
        layout.addWidget(api)

        startup = Section("Au lancement")
        self.auto_rx = QCheckBox("Démarrer automatiquement la réception")
        self.auto_rx.setChecked(config["autoStartReceiver"])
        self.auto_tx = QCheckBox("Démarrer automatiquement l'émission OMT")
        self.auto_tx.setChecked(config["autoStartSender"])
        startup.body.addWidget(self.auto_rx)
        startup.body.addWidget(self.auto_tx)
        layout.addWidget(startup)

        diag = Section("Moteur FFmpeg")
        refresh = button("Re-détecter", variant="ghost", icon="refresh", icon_color=theme.TEXT_DIM)
        refresh.clicked.connect(self._redetect)
        diag.add_action(refresh)
        self.diag_text = label("", "Mono", wrap=True)
        self.diag_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diag.body.addWidget(self.diag_text)
        layout.addWidget(diag)
        self._fill_diag()

        files = Section("Fichiers")
        files.body.addWidget(label(f"Configuration : {CONFIG_PATH}\nJournal : {LOG_PATH}", "Mono", wrap=True))
        files_row = QHBoxLayout()
        open_cfg = button("Dossier de configuration", icon="folder")
        open_cfg.clicked.connect(lambda: main.open_path(CONFIG_PATH.parent))
        files_row.addWidget(open_cfg)
        open_logs = button("Journaux", icon="logs")
        open_logs.clicked.connect(lambda: main.open_path(LOG_PATH.parent))
        files_row.addWidget(open_logs)
        files_row.addStretch(1)
        reset = button("Réinitialiser…", variant="danger-ghost")
        reset.clicked.connect(self._reset)
        files_row.addWidget(reset)
        files.body.addLayout(files_row)
        layout.addWidget(files)

        layout.addStretch(1)
        footer = QHBoxLayout()
        footer.addWidget(label(f"py-multiview {__version__}", "Muted"))
        footer.addStretch(1)
        cancel = button("Annuler")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = button("Enregistrer", variant="primary")
        save.clicked.connect(self._save)
        footer.addWidget(save)
        layout.addLayout(footer)

    def _fill_diag(self) -> None:
        tools = self.main.engine.tools
        lines = [f"• {tc.describe()}\n   {tc.directory}" for tc in tools.toolchains] or ["Aucun FFmpeg trouvé."]
        checks = [
            ("Réception SRT", tools.player_for("srt")),
            ("Réception OMT", tools.player_for("omt")),
            ("Routage SRT", tools.ffmpeg_for("srt")),
            ("Émission OMT", tools.ffmpeg_for("omt")),
        ]
        summary = "   ".join(f"{name} {'✓' if tc else '✗'}" for name, tc in checks)
        self.diag_text.setText(summary + "\n\n" + "\n".join(lines))

    def _redetect(self) -> None:
        self.main.engine.tools.refresh()
        self._fill_diag()
        self.main.refresh_engine_summary()

    def _refresh_api_hint(self) -> None:
        if not self.api_enabled.isChecked():
            self.api_hint.setText("API fermée.")
            return
        host = "127.0.0.1" if self.api_local.isChecked() else (self.main.host_ip or "127.0.0.1")
        base = f"http://{host}:{self.api_port.value()}"
        self.api_hint.setText(f"{base}/status\nPOST {base}/sender/toggle")

    def _reset(self) -> None:
        if self.main.reset_configuration():
            self.reject()

    def _save(self) -> None:
        config = self.main.config
        config["excludePrimaryDisplay"] = self.exclude_primary.isChecked()
        config["receiver"]["decode"] = self.decode.currentData()
        config["sender"]["native"] = self.sender_native.isChecked()
        config["api"]["enabled"] = self.api_enabled.isChecked()
        config["api"]["port"] = self.api_port.value()
        config["api"]["localOnly"] = self.api_local.isChecked()
        config["autoStartReceiver"] = self.auto_rx.isChecked()
        config["autoStartSender"] = self.auto_tx.isChecked()
        self.main.on_preferences_changed()
        self.accept()


class _Overlay(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()


class ScreenIdentifier(QObject):
    """Shows each display's name in big letters on the display itself."""

    def __init__(self, parent: QWidget, displays: list[dict]) -> None:
        super().__init__(parent)
        self._closed = False
        self._windows: list[QWidget] = []
        screens = {s.name(): s for s in QGuiApplication.screens()}
        for i, d in enumerate(displays):
            screen = screens.get(d.get("deviceName", "")) or (
                QGuiApplication.screens()[i] if i < len(QGuiApplication.screens()) else None
            )
            if screen is None:
                continue
            win = _Overlay()
            win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            win.setAttribute(Qt.WA_ShowWithoutActivating)
            win.setAlignment(Qt.AlignCenter)
            win.setStyleSheet(
                f"background: rgba(14,16,22,235); color: {theme.TEXT}; border: 6px solid {theme.ACCENT};"
                "font-size: 46px; font-weight: 700;"
            )
            primary = "<br><span style='font-size:22px;color:#a1a9ba'>écran principal</span>" if d.get("isPrimary") else ""
            win.setText(
                f"{d['name']}<br><span style='font-size:26px;color:#a1a9ba'>{d['width']}×{d['height']}</span>{primary}"
            )
            geo = screen.geometry()
            win.setGeometry(geo.x() + geo.width() // 4, geo.y() + geo.height() // 3, geo.width() // 2, geo.height() // 3)
            win.clicked.connect(self.close)
            win.show()
            self._windows.append(win)
        QTimer.singleShot(3500, self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for win in self._windows:
            win.close()
            win.deleteLater()
        self._windows.clear()
        self.deleteLater()
