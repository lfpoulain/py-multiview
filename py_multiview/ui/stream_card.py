"""Card editing and monitoring a single incoming stream."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..displays import describe
from ..ffmpeg import udp_input_url
from ..process import ProcessInfo, Status
from . import theme
from .preview import Thumbnail
from .widgets import ElidedLabel, StatusPill, button, field_label, icon_button, label, set_button, set_state, spin

LABEL_W = 58


def _combo(items: list[tuple[str, object]]) -> QComboBox:
    combo = QComboBox()
    for text, data in items:
        combo.addItem(text, data)
    return combo


def _select(combo: QComboBox, data) -> None:
    index = combo.findData(data)
    combo.setCurrentIndex(index if index >= 0 else 0)


class StreamCard(QFrame):
    changed = Signal(str)
    toggle_requested = Signal(str)
    delete_requested = Signal(str)
    logs_requested = Signal(str)
    discover_requested = Signal(str)
    status_message = Signal(str)
    preview_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(self, stream: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stream_id = stream["id"]
        self.setObjectName("StreamCard")
        self._loading = False
        self._host_ip = ""
        self._running = False
        self._route_id = ""
        self._routes: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)
        self.thumb = Thumbnail()
        self.thumb.clicked.connect(lambda: self.preview_requested.emit(self.stream_id))

        # Header: status, name, actions.
        head = QHBoxLayout()
        head.setSpacing(8)
        self.pill = StatusPill()
        self.live_name = ElidedLabel("", "StreamTitle")
        head.addWidget(self.live_name, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("NameEdit")
        self.name_edit.setPlaceholderText("Nom du flux")
        self.name_edit.editingFinished.connect(self._emit_changed)
        head.addWidget(self.name_edit, 1)
        head.addWidget(self.pill)
        self.edit_btn = icon_button("settings", "Configurer ce flux")
        self.edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.stream_id))
        head.addWidget(self.edit_btn)
        self.toggle_btn = button("Démarrer", icon="play", icon_color=theme.SUCCESS)
        self.toggle_btn.setMinimumWidth(110)
        self.toggle_btn.clicked.connect(lambda: self.toggle_requested.emit(self.stream_id))
        self.logs_btn = icon_button("logs", "Journal ffplay de ce flux")
        self.logs_btn.clicked.connect(lambda: self.logs_requested.emit(self.stream_id))
        self.delete_btn = icon_button("trash", "Supprimer ce flux", theme.DANGER)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.stream_id))
        head.addWidget(self.delete_btn)
        root.addLayout(head)
        root.addWidget(self.thumb)
        self.editor = QWidget()
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        root.addWidget(self.editor)

        # Source row: kind + source-specific fields.
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(field_label("Source", LABEL_W))
        self.source_combo = _combo([("SRT", "srt"), ("OMT", "omt"), ("Route", "route")])
        self.source_combo.setFixedWidth(96)
        self.source_combo.setToolTip(
            "SRT : l'app écoute un port (la régie envoie en caller)\n"
            "OMT : source Open Media Transport du réseau\n"
            "Route : lit une route SRT → multicast partagée"
        )
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo)

        self.source_stack = QStackedWidget()
        self.source_stack.addWidget(self._build_srt_page())
        self.source_stack.addWidget(self._build_omt_page())
        self.source_stack.addWidget(self._build_route_page())
        source_row.addWidget(self.source_stack, 1)
        editor_layout.addLayout(source_row)

        # Output row: screen + display options.
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(field_label("Écran", LABEL_W))
        self.display_combo = QComboBox()
        self.display_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.display_combo.setMinimumContentsLength(14)
        self.display_combo.currentIndexChanged.connect(self._emit_changed)
        out_row.addWidget(self.display_combo, 1)
        editor_layout.addLayout(out_row)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(field_label("Affichage"))
        self.mode_combo = _combo([("Adapter", "fit"), ("Remplir", "fill"), ("Étirer", "stretch")])
        self.mode_combo.setToolTip(
            "Adapter : image entière, bandes noires si besoin\n"
            "Remplir : plein écran, bords rognés\n"
            "Étirer : plein écran, proportions déformées"
        )
        self.mode_combo.currentIndexChanged.connect(self._emit_changed)
        out_row.addWidget(self.mode_combo)
        out_row.addWidget(field_label("Rotation"))
        self.rot_combo = _combo([("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)])
        self.rot_combo.currentIndexChanged.connect(self._emit_changed)
        out_row.addWidget(self.rot_combo)
        self.mute_chk = QCheckBox("Muet")
        self.mute_chk.setToolTip("Ne pas jouer l'audio de ce flux")
        self.mute_chk.toggled.connect(self._emit_changed)
        out_row.addWidget(self.mute_chk)
        out_row.addStretch(1)
        editor_layout.addLayout(out_row)

        self.source_summary = ElidedLabel("", "Mono")
        root.addWidget(self.source_summary)
        foot = QHBoxLayout()
        self.preview_btn = button("Aperçu", variant="ghost", icon="screen", small=True)
        self.preview_btn.clicked.connect(lambda: self.preview_requested.emit(self.stream_id))
        foot.addWidget(self.preview_btn, 1, Qt.AlignLeft)
        self.output_summary = ElidedLabel("", "Detail")
        foot.addWidget(self.output_summary, 1)
        foot.addWidget(self.logs_btn)
        foot.addWidget(self.toggle_btn)
        root.addLayout(foot)

        self.detail = label("", "Detail", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.hide()
        root.addWidget(self.detail)

        self.load(stream)
        self.set_editing(False)

    def set_editing(self, editing: bool) -> None:
        for widget in (self.editor, self.name_edit, self.delete_btn, self.preview_btn):
            widget.setVisible(editing)
        for widget in (self.thumb, self.live_name, self.source_summary, self.output_summary, self.edit_btn):
            widget.setVisible(not editing)
        self._update_summary()

    def _update_summary(self) -> None:
        self.live_name.set_full_text(self.name_edit.text() or "Flux sans nom")
        source = self.source_combo.currentData()
        if source == "srt":
            address = self.srt_url.full_text()
        elif source == "omt":
            address = self.omt_edit.text().strip() or "Source à choisir"
        else:
            address = self.route_combo.currentText()
        self.source_summary.set_full_text(f"{self.source_combo.currentText()}  ·  {address}")
        output = self.display_combo.currentText() if self.display_combo.currentData() else "Aucun écran assigné"
        self.output_summary.set_full_text(output)

    # -- source pages --------------------------------------------------------

    def _build_srt_page(self) -> QWidget:
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field_label("Port"))
        self.port_spin = spin(1, 65535, 9001, width=74)
        self.port_spin.valueChanged.connect(self._emit_changed)
        row.addWidget(self.port_spin)
        row.addWidget(field_label("Latence"))
        self.latency_spin = spin(0, 10000, 120, suffix=" ms", width=86)
        self.latency_spin.setToolTip("Latence SRT : à régler identique (ou supérieure) côté émetteur")
        self.latency_spin.valueChanged.connect(self._emit_changed)
        row.addWidget(self.latency_spin)
        row.addStretch(1)
        form.addLayout(row)
        row = QHBoxLayout()
        self.srt_url = ElidedLabel("", "Mono")
        self.srt_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.srt_url, 1)
        copy_btn = icon_button("copy", "Copier l'adresse SRT pour l'émetteur")
        copy_btn.clicked.connect(self._copy_srt_url)
        row.addWidget(copy_btn)
        form.addLayout(row)
        return page

    def _build_omt_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.omt_edit = QLineEdit()
        self.omt_edit.setPlaceholderText("MACHINE (Nom de la source)")
        self.omt_edit.editingFinished.connect(self._emit_changed)
        row.addWidget(self.omt_edit, 1)
        find_btn = button("Rechercher", icon="search", tooltip="Découvrir les sources OMT du réseau")
        find_btn.clicked.connect(lambda: self.discover_requested.emit(self.stream_id))
        row.addWidget(find_btn)
        return page

    def _build_route_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.route_combo = QComboBox()
        self.route_combo.currentIndexChanged.connect(self._emit_changed)
        row.addWidget(self.route_combo, 1)
        self.route_url = label("", "Mono")
        row.addWidget(self.route_url)
        return page

    # -- data binding --------------------------------------------------------

    def load(self, stream: dict) -> None:
        self._loading = True
        try:
            self.name_edit.setText(stream["name"])
            _select(self.source_combo, stream["source"])
            self.source_stack.setCurrentIndex(self.source_combo.currentIndex())
            self.port_spin.setValue(stream["port"])
            self.latency_spin.setValue(stream["latency"])
            self.omt_edit.setText(stream["omtSource"])
            self._route_id = stream["sourceRouteId"]
            _select(self.route_combo, stream["sourceRouteId"])
            _select(self.mode_combo, stream["displayMode"])
            _select(self.rot_combo, stream["rotate"])
            self.mute_chk.setChecked(stream["muteAudio"])
        finally:
            self._loading = False
        self._update_urls()

    def set_displays(self, displays: list[dict], selected: str, exclude_primary: bool) -> None:
        self._loading = True
        try:
            self.display_combo.clear()
            self.display_combo.addItem("— Aucun écran —", "")
            found = False
            for d in displays:
                excluded = exclude_primary and d.get("isPrimary")
                if excluded and d["id"] != selected:
                    continue
                text = f"{d['name']}  ·  {describe(d)}"
                if excluded:
                    text += "  (principal, exclu)"
                self.display_combo.addItem(text, d["id"])
                found = found or d["id"] == selected
            if selected and not found:
                # Keep the binding of a screen that is asleep or unplugged.
                self.display_combo.addItem("⚠ Écran absent (débranché ou en veille)", selected)
            _select(self.display_combo, selected)
        finally:
            self._loading = False
        self._update_summary()

    def set_routes(self, routes: list[dict]) -> None:
        self._loading = True
        try:
            self.route_combo.clear()
            self.route_combo.addItem("— Choisir une route —" if routes else "Aucune route (Routage → Gérer)", "")
            for r in routes:
                self.route_combo.addItem(f"{r['name']}  ·  SRT :{r['inputPort']}", r["id"])
            _select(self.route_combo, self._route_id)
        finally:
            self._loading = False
        self._routes = {r["id"]: r for r in routes}
        self._update_urls()

    def set_host_ip(self, ip: str) -> None:
        self._host_ip = ip
        self._update_urls()

    def read_into(self, stream: dict) -> str:
        """Write the card's values into ``stream``; returns the selected display id."""
        stream["name"] = self.name_edit.text().strip() or stream["name"]
        stream["source"] = self.source_combo.currentData()
        stream["port"] = self.port_spin.value()
        stream["latency"] = self.latency_spin.value()
        stream["omtSource"] = self.omt_edit.text().strip()
        stream["sourceRouteId"] = self.route_combo.currentData() or ""
        stream["displayMode"] = self.mode_combo.currentData()
        stream["rotate"] = self.rot_combo.currentData()
        stream["muteAudio"] = self.mute_chk.isChecked()
        self._route_id = stream["sourceRouteId"]
        return self.display_combo.currentData() or ""

    def set_omt_source(self, name: str) -> None:
        self.omt_edit.setText(name)
        self._emit_changed()

    # -- status --------------------------------------------------------------

    def set_info(self, info: ProcessInfo, running: bool) -> None:
        source = self.source_combo.currentData()
        detail = info.detail
        if info.status == Status.WAITING and not detail:
            if source == "srt":
                detail = f"En attente d'un émetteur SRT sur le port {self.port_spin.value()}…"
            elif source == "omt":
                detail = f"Connexion à la source OMT « {self.omt_edit.text().strip()} »…"
            else:
                detail = "En attente de données sur la route multicast…"
        elif info.status == Status.ACTIVE:
            detail = ""

        self.pill.set_status(info.status)
        set_state(self, info.status.value)
        set_state(self.detail, info.status.value)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))

        if running != self._running:
            self._running = running
            if running:
                set_button(self.toggle_btn, "Arrêter", "danger-ghost", "stop", theme.DANGER)
            else:
                set_button(self.toggle_btn, "Démarrer", "", "play", theme.SUCCESS)

    # -- internals -----------------------------------------------------------

    def _on_source_changed(self) -> None:
        self.source_stack.setCurrentIndex(self.source_combo.currentIndex())
        self._emit_changed()

    def _emit_changed(self, *_args) -> None:
        self._update_urls()
        if not self._loading:
            self.changed.emit(self.stream_id)

    def _update_urls(self) -> None:
        host = self._host_ip or "IP_DE_CE_PC"
        self.srt_url.set_full_text(f"srt://{host}:{self.port_spin.value()}")
        route = self._routes.get(self.route_combo.currentData() or "")
        self.route_url.setText(udp_input_url(route["multicastAddr"], route["multicastPort"]).split("?")[0] if route else "")
        self._update_summary()

    def _copy_srt_url(self) -> None:
        host = self._host_ip or "IP_DE_CE_PC"
        url = f"srt://{host}:{self.port_spin.value()}?mode=caller&latency={self.latency_spin.value() * 1000}"
        QApplication.clipboard().setText(url)
        self.status_message.emit(f"Adresse copiée : {url}")
