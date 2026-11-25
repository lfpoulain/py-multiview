"""Secondary windows and panels: logs, OMT discovery, routes, screen identification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
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

from .. import omt
from ..config import new_id, normalize_route
from ..ffmpeg import udp_input_url
from ..paths import LOG_PATH
from ..process import Status
from . import theme
from .widgets import Card, IconTile, StatusPill, button, field_label, label, set_button, spin

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


class RoutesPanel(QWidget):
    """Create, edit and run SRT -> UDP multicast routes (the « Routage » page)."""

    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        side = Card("Routes", "Un flux SRT reçu une fois, rediffusé en multicast UDP : "
                              "plusieurs écrans peuvent alors l'afficher.")
        side.setFixedWidth(320)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._on_select)
        side.add_widget(self.list, separated=False)
        side.body.setSpacing(10)
        btns = QHBoxLayout()
        add = button("Nouvelle route", variant="primary", icon="plus")
        add.clicked.connect(self._add)
        btns.addWidget(add, 1)
        self.delete_btn = button(variant="icon", icon="trash", icon_color=theme.DANGER, tooltip="Supprimer la route")
        self.delete_btn.clicked.connect(self._delete)
        btns.addWidget(self.delete_btn)
        side.add_widget(layout=btns, separated=False)
        layout.addWidget(side, 0, Qt.AlignTop)

        self.form_panel = Card("Route", "", icon="route")
        self.pill = StatusPill()
        self.form_panel.add_action(self.pill)
        logs = button(variant="icon", icon="logs", icon_color=theme.TEXT_DIM, tooltip="Journal ffmpeg")
        logs.clicked.connect(self._logs)
        self.form_panel.add_action(logs)
        self.toggle_btn = button("Démarrer", variant="success", icon="play", icon_color="#ffffff")
        self.toggle_btn.clicked.connect(self._toggle)
        self.form_panel.add_action(self.toggle_btn)

        self.name_edit = QLineEdit()
        self.name_edit.setMinimumWidth(240)
        self.name_edit.editingFinished.connect(self._on_edit)
        self.in_port = spin(1, 65535, 9001, width=110)
        self.in_latency = spin(0, 10000, 120, suffix=" ms", width=110)
        self.maddr = QLineEdit()
        self.maddr.setPlaceholderText("239.10.10.10")
        self.maddr.setFixedWidth(180)
        self.maddr.editingFinished.connect(self._on_edit)
        self.mport = spin(1, 65535, 1234, width=110)
        self.ttl = spin(1, 255, 1, width=110)
        self.pkt = spin(188, 65507, 1316, width=110)
        for w in (self.in_port, self.in_latency, self.mport, self.ttl, self.pkt):
            w.valueChanged.connect(self._on_edit)
        card = self.form_panel
        card.add_row("Nom", "Affiché dans la liste des sources des flux.", self.name_edit)
        card.add_row("Port SRT entrant", "La régie envoie vers ce port (mode caller).", self.in_port)
        card.add_row("Latence SRT", "Identique ou supérieure à celle de l'émetteur.", self.in_latency)
        card.add_row("Groupe multicast", "Adresse de diffusion sur le réseau local.", self.maddr)
        card.add_row("Port multicast", "", self.mport)
        card.add_row("TTL", "1 = réseau local uniquement.", self.ttl)
        card.add_row("Taille de paquet", "1316 convient à la plupart des réseaux.", self.pkt)
        self.detail = label("", "Mono", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        callout = QFrame()
        callout.setObjectName("Callout")
        inner = QVBoxLayout(callout)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.addWidget(self.detail)
        card.add_widget(callout, separated=False)

        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self.form_panel)
        self.empty = Card()
        empty_col = self.empty.body
        empty_col.setSpacing(8)
        tile = IconTile("route", 48)
        empty_col.addWidget(tile, 0, Qt.AlignHCenter)
        title = label("Aucune route", "EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(title)
        hint = label("Crée une route pour partager un même flux SRT entre plusieurs écrans.", "Muted", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(hint)
        self.empty.body.setContentsMargins(0, 30, 0, 30)
        right.addWidget(self.empty)
        right.addStretch(1)
        layout.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)
        self._reload()

    def reload(self) -> None:
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
            item = QListWidgetItem(theme.icon("route", theme.TEXT_DIM), r["name"])
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
            self.form_panel.title.setText(route["name"])
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
        self.form_panel.title.setText(route["name"])
        self.main.on_routes_changed()

    def _refresh_status(self) -> None:
        route = self._current()
        if not route:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            state = self.main.engine.route_info(item.data(Qt.UserRole)).status.value
            item.setIcon(theme.icon("route", theme.STATE_COLORS[state]))
        info = self.main.engine.route_info(route["id"])
        running = route["id"] in self.main.engine.active_routes
        self.pill.set_status(info.status)
        if running:
            set_button(self.toggle_btn, "Arrêter", "danger", "stop")
        else:
            set_button(self.toggle_btn, "Démarrer", "success", "play")
        out = udp_input_url(route["multicastAddr"], route["multicastPort"]).split("?")[0]
        host = self.main.engine.host_ip or "<IP>"
        text = f"Entrée  srt://{host}:{route['inputPort']}\nSortie  {out}"
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
                f"background: rgba(18,18,23,240); color: #ffffff; border: 6px solid {theme.ACCENT}; border-radius: 18px;"
                "font-size: 46px; font-weight: 700;"
            )
            primary = "<br><span style='font-size:22px;color:#a9a9bb'>écran principal</span>" if d.get("isPrimary") else ""
            win.setText(
                f"{d['name']}<br><span style='font-size:26px;color:#a9a9bb'>{d['width']}×{d['height']}</span>{primary}"
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
