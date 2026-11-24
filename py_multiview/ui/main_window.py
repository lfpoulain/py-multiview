"""Main window: screens, OMT sender, audio return, routes and stream cards."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, audio, omtlib, updater
from ..api import ApiServer
from ..api import dispatch as api_dispatch
from ..config import OMT_PIXEL_FORMATS, default_config, default_stream, load_config, save_config
from ..control import Control
from ..displays import describe, list_displays
from ..engine import Engine
from ..paths import APP_ICON_ICO_PATH, APP_ICON_PNG_PATH
from ..process import Status
from . import theme
from .audio_picker import AudioSourceDialog
from .dialogs import LogDialog, OMTDiscoveryDialog, PreferencesDialog, RoutesDialog, ScreenIdentifier
from .preview import PreviewDialog, grab_display
from .stream_card import StreamCard
from .update_dialog import UpdateDialog
from .widgets import (
    STATUS_TEXT,
    CollapsibleSection,
    ElidedLabel,
    LevelMeter,
    button,
    divider,
    field_label,
    label,
    set_button,
    set_state,
    spin,
)

log = logging.getLogger(__name__)

SIDEBAR_WIDTH = 350
UPDATE_FIRST_CHECK_MS = 5_000
UPDATE_CHECK_EVERY_MS = 6 * 3600 * 1000
CLOCK_TOOLTIP = (
    "libomt remet les trames au récepteur à leur cadence : une trame de son toutes les 20 ms.\n"
    "Décoché, son et image partent par paquets, comme FFmpeg les lit, et un récepteur\n"
    "qui garde peu de son en réserve accroche. À laisser coché."
)
PIXEL_FORMAT_LABELS = {
    "uyvy422": ("UYVY 4:2:2", "Recommandé : bon compromis qualité / bande passante"),
    "bgra": ("BGRA 4:4:4", "Pleine résolution couleur avec alpha, plus gourmand"),
    "yuv422p10le": ("YUV 10 bits", "4:2:2 10 bits pour les chaînes HDR / haute qualité"),
}


class MainWindow(QMainWindow):
    api_call = Signal(object)
    update_found = Signal(object)  # Release, from the background check

    def __init__(self, engine: Engine | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.engine = engine or Engine()
        self.config = load_config()
        self.displays: list[dict] = []
        self._display_signature: tuple = ()
        self.cards: dict[str, StreamCard] = {}
        self._log_windows: dict[str, LogDialog] = {}
        self.host_ip = self.engine.host_ip
        self._previews: dict[str, PreviewDialog] = {}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.commit)

        self._build_ui()
        self.refresh_displays(force=True)
        self.reload_cards()
        self.load_sender_fields()
        self.load_monitor_fields()
        self.refresh_routes_list()
        self._apply_sidebar_state()
        self.refresh_engine_summary()
        self.engine.update(self.config, self.displays)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self.on_tick)
        self._status_timer.start(500)
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self.refresh_displays)
        self._display_timer.start(3000)
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self.refresh_previews)
        self._preview_timer.start(2000)

        # The level meter needs a livelier pace than the status tick.
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._refresh_meter)
        self._meter_timer.start(80)

        # The API answers on its own threads but never touches the engine from
        # them: each request is executed here, on the UI thread.
        self.control = Control(self)
        self.api = ApiServer(self._run_api_request)
        self.api_call.connect(self._run_api_job)
        self.refresh_api()

        QShortcut(QKeySequence.New, self, activated=self.add_stream)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.quit_app)
        self._quitting = False
        self._close_hint_shown = False
        self._build_tray()
        # Windows closing the session must really quit, not minimise.
        QApplication.instance().commitDataRequest.connect(lambda *_: setattr(self, "_quitting", True))
        QTimer.singleShot(300, self.autostart)

        self._latest_release: updater.Release | None = None
        self.update_found.connect(self.on_update_checked)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_updates_quietly)
        self._update_timer.start(UPDATE_CHECK_EVERY_MS)
        QTimer.singleShot(UPDATE_FIRST_CHECK_MS, self.check_updates_quietly)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(14)
        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_streams_panel(), 1)
        outer.addLayout(body, 1)

        self.engine_label = button(variant="ghost")
        self.engine_label.clicked.connect(self.open_preferences)
        self.statusBar().addPermanentWidget(self.engine_label)
        self.statusBar().setSizeGripEnabled(False)

        screen = self.screen().availableGeometry() if self.screen() else None
        width, height = 1320, 860
        if screen is not None:
            width, height = min(width, int(screen.width() * 0.95)), min(height, int(screen.height() * 0.92))
        self.resize(width, height)
        self.setMinimumSize(1000, 620)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(18, 12, 14, 12)
        row.setSpacing(12)

        logo = QLabel()
        if APP_ICON_PNG_PATH.exists():
            pm = QPixmap(str(APP_ICON_PNG_PATH))
            if not pm.isNull():
                logo.setPixmap(pm.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(APP_NAME, "AppTitle"))
        ip = self.host_ip or "IP inconnue"
        self.subtitle = label(f"Version {__version__}  ·  Ce poste : {ip}", "Muted")
        self.subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        titles.addWidget(self.subtitle)
        row.addLayout(titles)
        row.addStretch(1)

        self.streams_chip = label("", "Chip")
        self.sender_chip = label("", "Chip")
        row.addWidget(self.streams_chip)
        row.addWidget(self.sender_chip)
        row.addSpacing(6)

        self.update_btn = button("", variant="primary", icon="download", icon_color="#ffffff")
        self.update_btn.clicked.connect(lambda: self.open_update(self._latest_release))
        self.update_btn.hide()
        row.addWidget(self.update_btn)

        prefs = button("Préférences", variant="ghost", icon="settings", icon_color=theme.TEXT_DIM)
        prefs.clicked.connect(self.open_preferences)
        row.addWidget(prefs)

        self.reception_btn = button("Démarrer la réception", variant="success", icon="play", icon_color="#ffffff", large=True)
        self.reception_btn.setMinimumWidth(220)
        self.reception_btn.setToolTip("Démarre tous les flux qui ont un écran assigné")
        self.reception_btn.clicked.connect(self.toggle_reception)
        row.addWidget(self.reception_btn)
        return header

    def _build_sidebar(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(SIDEBAR_WIDTH)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 6, 0, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel_layout.addWidget(scroll)

        content = QWidget()
        content.setObjectName("Transparent")
        col = QVBoxLayout(content)
        col.setContentsMargins(16, 8, 12, 10)
        col.setSpacing(2)

        # Each block folds to a header that carries its primary action and a
        # one-line summary: the column stays short and nothing running is hidden.
        self.sections: dict[str, CollapsibleSection] = {}
        self._section_defaults: dict[str, bool] = {}

        def section(key: str, title: str, expanded: bool, tip: str = "") -> CollapsibleSection:
            self._section_defaults[key] = expanded
            sec = CollapsibleSection(title, key, expanded)
            if tip:
                sec.head.setToolTip(tip)
            sec.toggled.connect(lambda state, k=key: self._on_section_toggled(k, state))
            self.sections[key] = sec
            return sec

        def footer(sec: CollapsibleSection, detail: QLabel, key: str, title: str) -> None:
            sec.body.addWidget(detail)
            row = QHBoxLayout()
            row.addStretch(1)
            logs = button("Journal", variant="ghost", icon="logs", icon_color=theme.TEXT_DIM, small=True,
                          tooltip=f"Journal : {title.lower()}")
            logs.clicked.connect(lambda: self.show_logs(key, f"Journal — {title.lower()}"))
            row.addWidget(logs)
            sec.body.addLayout(row)

        # Screens.
        screens = section("screens", "Écrans", True)
        identify = button("Identifier", variant="ghost", icon="identify", icon_color=theme.TEXT_DIM, small=True,
                          tooltip="Affiche le nom de chaque écran sur l'écran lui-même")
        identify.clicked.connect(self.identify_screens)
        screens.add_action(identify)
        self.displays_list = QListWidget()
        self.displays_list.setMaximumHeight(150)
        self.displays_list.itemDoubleClicked.connect(lambda _i: self.rename_display())
        self.displays_list.setToolTip("Double-clic pour renommer")
        screens.body.addWidget(self.displays_list)
        screen_btns = QHBoxLayout()
        auto = button("Auto-assigner", icon="wand", tooltip="Assigne un écran libre à chaque flux (crée des flux si besoin)")
        auto.clicked.connect(self.auto_assign)
        screen_btns.addWidget(auto)
        rename = button("Renommer", tooltip="Renommer l'écran sélectionné")
        rename.clicked.connect(self.rename_display)
        screen_btns.addWidget(rename)
        screens.body.addLayout(screen_btns)
        col.addWidget(screens)
        col.addWidget(divider())

        # OMT sender.
        sender = section("sender", "Émission OMT", False,
                         "Publie un écran de ce poste comme source OMT sur le réseau.")
        self.sender_btn = button("Émettre", variant="success", icon="broadcast", icon_color="#ffffff", small=True)
        self.sender_btn.clicked.connect(self.toggle_sender)
        sender.add_action(self.sender_btn)
        self.sender_display = QComboBox()
        self.sender_name = QLineEdit()
        self.sender_name.setPlaceholderText("Nom publié sur le réseau")
        self.sender_fps = spin(1, 60, 30, suffix=" i/s", width=80)
        self.sender_format = QComboBox()
        for key in OMT_PIXEL_FORMATS:
            text, tip = PIXEL_FORMAT_LABELS[key]
            self.sender_format.addItem(text, key)
            self.sender_format.setItemData(self.sender_format.count() - 1, tip, Qt.ToolTipRole)
        # The sound is picked in its own window (categories, search): a combo
        # listing every device and every program twice was unreadable.
        self.sender_audio = button(variant="field", tooltip="Choisir le son envoyé avec l'image")
        audio_inner = QHBoxLayout(self.sender_audio)
        audio_inner.setContentsMargins(8, 0, 6, 0)
        audio_inner.setSpacing(6)
        self.sender_audio_text = ElidedLabel("", "Transparent")
        self.sender_audio_text.setAttribute(Qt.WA_TransparentForMouseEvents)
        audio_inner.addWidget(self.sender_audio_text, 1)
        chevron = QLabel()
        chevron.setPixmap(theme.pixmap("chevron", theme.TEXT_DIM, 12))
        chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        audio_inner.addWidget(chevron)
        self.sender_audio.setMinimumHeight(32)
        self.sender_audio.clicked.connect(self.choose_sender_audio)
        self.sender_meter = LevelMeter()
        self.sender_meter.hide()
        self.sender_clock = QCheckBox("Horloge de sortie (clock_output)")
        self.sender_clock.setToolTip(CLOCK_TOOLTIP)
        for row_label, widget in (("Écran", self.sender_display), ("Nom", self.sender_name)):
            line = QHBoxLayout()
            line.addWidget(field_label(row_label, 52))
            line.addWidget(widget, 1)
            sender.body.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(field_label("Cadence", 52))
        line.addWidget(self.sender_fps)
        line.addWidget(self.sender_format, 1)
        sender.body.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(field_label("Audio", 52))
        line.addWidget(self.sender_audio, 1)
        sender.body.addLayout(line)
        line = QHBoxLayout()
        line.setContentsMargins(58, 0, 0, 0)
        line.addWidget(self.sender_meter, 1)
        sender.body.addLayout(line)
        sender.body.addWidget(self.sender_clock)
        self.sender_display.currentIndexChanged.connect(self.on_sender_changed)
        self.sender_name.editingFinished.connect(self.on_sender_changed)
        self.sender_fps.valueChanged.connect(self.on_sender_changed)
        self.sender_format.currentIndexChanged.connect(self.on_sender_changed)
        self.sender_clock.toggled.connect(self.on_sender_changed)
        self.sender_detail = label("", "Detail", wrap=True)
        self.sender_detail.hide()
        footer(sender, self.sender_detail, "sender", "Émission OMT")
        col.addWidget(sender)
        col.addWidget(divider())

        # Audio-only OMT return.
        monitor = section("monitor", "Retour audio", False,
                          "Écoute le son d'une source OMT sur ce poste, sans image.")
        self.monitor_btn = button("Écouter", variant="success", icon="broadcast", icon_color="#ffffff", small=True)
        self.monitor_btn.clicked.connect(self.toggle_monitor)
        monitor.add_action(self.monitor_btn)
        line = QHBoxLayout()
        self.monitor_source = QLineEdit()
        self.monitor_source.setPlaceholderText("MACHINE (Nom de la source)")
        self.monitor_source.editingFinished.connect(self.on_monitor_changed)
        line.addWidget(self.monitor_source, 1)
        find_monitor = button(variant="icon", icon="search", icon_color=theme.TEXT_DIM,
                              tooltip="Découvrir les sources OMT du réseau")
        find_monitor.clicked.connect(self.discover_monitor_source)
        line.addWidget(find_monitor)
        monitor.body.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(field_label("Volume", 52))
        self.monitor_volume = spin(0, 100, 100, suffix=" %", width=80)
        self.monitor_volume.valueChanged.connect(self.on_monitor_changed)
        line.addWidget(self.monitor_volume)
        line.addStretch(1)
        monitor.body.addLayout(line)
        self.monitor_detail = label("", "Detail", wrap=True)
        self.monitor_detail.hide()
        footer(monitor, self.monitor_detail, "monitor", "Retour audio")
        col.addWidget(monitor)
        col.addWidget(divider())

        # Floating video return: its own block, it used to hide inside the audio one.
        floating = section("floating", "Fenêtre de retour", False,
                           "Petite fenêtre vidéo posée sur le bureau, toujours au-dessus. "
                           "Vide = la même source que l'écoute.")
        self.float_btn = button("Ouvrir", variant="success", icon="play", icon_color="#ffffff", small=True)
        self.float_btn.clicked.connect(self.toggle_floating)
        floating.add_action(self.float_btn)
        line = QHBoxLayout()
        self.float_source = QLineEdit()
        self.float_source.setPlaceholderText("Source (vide = celle de l'écoute)")
        self.float_source.editingFinished.connect(self.on_monitor_changed)
        line.addWidget(self.float_source, 1)
        find_float = button(variant="icon", icon="search", icon_color=theme.TEXT_DIM,
                            tooltip="Découvrir les sources OMT du réseau")
        find_float.clicked.connect(self.discover_float_source)
        line.addWidget(find_float)
        floating.body.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(field_label("Taille", 52))
        self.float_width = spin(160, 7680, 640, suffix=" px", width=86)
        self.float_width.valueChanged.connect(self.on_monitor_changed)
        line.addWidget(self.float_width)
        line.addWidget(label("×", "Muted"))
        self.float_height = spin(90, 4320, 360, suffix=" px", width=86)
        self.float_height.valueChanged.connect(self.on_monitor_changed)
        line.addWidget(self.float_height)
        line.addStretch(1)
        floating.body.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(field_label("Son", 52))
        self.float_volume = spin(0, 100, 0, suffix=" %", width=80)
        self.float_volume.valueChanged.connect(self.on_monitor_changed)
        line.addWidget(self.float_volume)
        self.float_on_top = QCheckBox("Toujours au-dessus")
        self.float_on_top.setChecked(True)
        self.float_on_top.stateChanged.connect(self.on_monitor_changed)
        line.addWidget(self.float_on_top)
        line.addStretch(1)
        floating.body.addLayout(line)
        self.float_detail = label("", "Detail", wrap=True)
        self.float_detail.hide()
        footer(floating, self.float_detail, "floating", "Fenêtre de retour")
        col.addWidget(floating)
        col.addWidget(divider())

        # Routes.
        routes = section("routes", "Routage multicast", False)
        manage = button("Gérer", variant="ghost", icon="route", icon_color=theme.TEXT_DIM, small=True)
        manage.clicked.connect(self.open_routes)
        routes.add_action(manage)
        self.routes_list = QListWidget()
        self.routes_list.setMaximumHeight(130)
        self.routes_list.setSelectionMode(QListWidget.NoSelection)
        self.routes_list.itemDoubleClicked.connect(lambda _i: self.open_routes())
        routes.body.addWidget(self.routes_list)
        col.addWidget(routes)
        col.addStretch(1)

        for combo in (self.sender_display, self.sender_format):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
        scroll.setWidget(content)
        return panel

    def _build_streams_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(18, 16, 18, 16)
        col.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(label("Flux entrants", "PanelTitle"))
        self.streams_count = label("", "Muted")
        head.addWidget(self.streams_count)
        head.addStretch(1)
        add = button("Ajouter un flux", variant="primary", icon="plus", icon_color="#ffffff", tooltip="Ctrl+N")
        add.clicked.connect(self.add_stream)
        head.addWidget(add)
        col.addLayout(head)

        self.streams_stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Transparent")
        self.cards_layout = QVBoxLayout(inner)
        self.cards_layout.setContentsMargins(0, 0, 6, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch(1)
        scroll.setWidget(inner)
        self.streams_stack.addWidget(scroll)

        empty = QWidget()
        empty_col = QVBoxLayout(empty)
        empty_col.addStretch(1)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(theme.pixmap("screen", theme.TEXT_MUTED, 56, stroke=1.5))
        icon_lbl.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(icon_lbl)
        title = label("Aucun flux configuré", "EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(title)
        hint = label("Ajoute un flux puis assigne-lui un écran.\nOu clique « Auto-assigner » pour en créer un par écran.", "Muted")
        hint.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(hint)
        empty_btn = button("Ajouter un flux", variant="primary", icon="plus", icon_color="#ffffff")
        empty_btn.clicked.connect(self.add_stream)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(empty_btn)
        btn_row.addStretch(1)
        empty_col.addLayout(btn_row)
        empty_col.addStretch(2)
        self.streams_stack.addWidget(empty)
        col.addWidget(self.streams_stack, 1)
        return panel

    # ------------------------------------------------------------ helpers

    def notify(self, message: str, timeout: int = 6000) -> None:
        self.statusBar().showMessage(message, timeout)

    def _stream(self, stream_id: str) -> dict | None:
        return next((s for s in self.config["streams"] if s["id"] == stream_id), None)

    def schedule_commit(self) -> None:
        self._save_timer.start()

    def commit(self) -> None:
        """Persist the config and hand it to the engine (applies changes live)."""
        self._save_timer.stop()
        if not save_config(self.config):
            self.notify("⚠ Impossible d'enregistrer la configuration (voir le journal).", 10000)
        self.engine.update(self.config, self.displays)
        self.on_tick()

    def open_path(self, path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, APP_NAME, f"Impossible d'ouvrir :\n{path}")

    # ------------------------------------------------------------ screens

    def refresh_displays(self, force: bool = False) -> None:
        displays = list_displays(self.config["displayNames"])
        signature = tuple((d["id"], d["name"], d["x"], d["y"], d["width"], d["height"], d["isPrimary"]) for d in displays)
        if not force and signature == self._display_signature:
            return
        if not force:
            log.info("Configuration des écrans modifiée: %s", [d["name"] for d in displays])
            self.notify("Configuration des écrans mise à jour.")
        self._display_signature = signature
        self.displays = displays

        selected = self.displays_list.currentItem().data(Qt.UserRole) if self.displays_list.currentItem() else None
        self.displays_list.clear()
        exclude = self.config["excludePrimaryDisplay"]
        for d in displays:
            text = f"{d['name']}\n{describe(d)}"
            if d["isPrimary"]:
                text += "  ·  principal" + (" (exclu)" if exclude else "")
            item = QListWidgetItem(theme.icon("screen", theme.TEXT_MUTED if d["isPrimary"] and exclude else theme.TEXT_DIM), text)
            item.setData(Qt.UserRole, d["id"])
            item.setToolTip(f"{d.get('systemName', d['name'])}\nPosition {d['x']}, {d['y']}\nID {d['id']}")
            self.displays_list.addItem(item)
            if d["id"] == selected:
                self.displays_list.setCurrentItem(item)
        if not displays:
            self.displays_list.addItem("Aucun écran détecté")

        for stream_id, card in self.cards.items():
            card.set_displays(displays, self.config["mapping"].get(stream_id, ""), exclude)
        self._fill_sender_displays()
        if not force:
            self.engine.update(self.config, self.displays)

    def identify_screens(self) -> None:
        ScreenIdentifier(self, self.displays)

    def rename_display(self) -> None:
        item = self.displays_list.currentItem()
        display_id = item.data(Qt.UserRole) if item else None
        display = next((d for d in self.displays if d["id"] == display_id), None)
        if display is None:
            self.notify("Sélectionne un écran à renommer.")
            return
        name, ok = QInputDialog.getText(
            self, "Renommer l'écran", f"Nom de l'écran (vide = « {display['systemName']} ») :", text=display["name"]
        )
        if not ok:
            return
        name = name.strip()
        if name and name != display["systemName"]:
            self.config["displayNames"][display_id] = name
        else:
            self.config["displayNames"].pop(display_id, None)
        self.refresh_displays(force=True)
        self.commit()

    def auto_assign(self) -> None:
        exclude = self.config["excludePrimaryDisplay"]
        targets = [d for d in self.displays if not (exclude and d["isPrimary"])]
        if not targets:
            QMessageBox.information(self, "Auto-assigner", "Aucun écran disponible (l'écran principal est exclu).")
            return
        streams, mapping = self.config["streams"], self.config["mapping"]
        target_ids = [d["id"] for d in targets]
        created = 0
        while len(streams) < len(targets):
            streams.append(default_stream(len(streams) + 1, self._free_port()))
            created += 1
        used = {v for v in mapping.values() if v in target_ids}
        assigned = 0
        for stream in streams:
            if mapping.get(stream["id"]) in target_ids:
                continue
            free = next((t for t in target_ids if t not in used), None)
            if free is None:
                break
            mapping[stream["id"]] = free
            used.add(free)
            assigned += 1
        self.reload_cards()
        self.commit()
        self.notify(f"{assigned} flux assigné(s), {created} flux créé(s).")

    # ------------------------------------------------------------ streams

    def reload_cards(self) -> None:
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        for stream in self.config["streams"]:
            self._add_card(stream)
        self._update_stream_count()

    def _add_card(self, stream: dict) -> StreamCard:
        card = StreamCard(stream)
        card.set_host_ip(self.host_ip)
        card.set_routes(self.config["routes"])
        card.set_displays(self.displays, self.config["mapping"].get(stream["id"], ""), self.config["excludePrimaryDisplay"])
        card.changed.connect(self.on_card_changed)
        card.toggle_requested.connect(self.toggle_stream)
        card.delete_requested.connect(self.delete_stream)
        card.logs_requested.connect(
            lambda sid: self.show_logs(f"player:{sid}", f"Journal — {self._stream(sid)['name']}")
        )
        card.discover_requested.connect(self.discover_omt)
        card.status_message.connect(self.notify)
        card.preview_requested.connect(self.open_preview)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.cards[stream["id"]] = card
        return card

    def _update_stream_count(self) -> None:
        count = len(self.config["streams"])
        self.streams_count.setText(f"{count} flux" if count else "")
        self.streams_stack.setCurrentIndex(0 if count else 1)

    def _free_port(self) -> int:
        used = {s["port"] for s in self.config["streams"]} | {r["inputPort"] for r in self.config["routes"]}
        port = 9001
        while port in used:
            port += 1
        return port

    def add_stream(self) -> None:
        stream = default_stream(len(self.config["streams"]) + 1, self._free_port())
        self.config["streams"].append(stream)
        card = self._add_card(stream)
        self._update_stream_count()
        self.commit()
        card.name_edit.setFocus()
        card.name_edit.selectAll()

    def delete_stream(self, stream_id: str) -> None:
        stream = self._stream(stream_id)
        if stream is None:
            return
        if QMessageBox.question(self, "Supprimer le flux", f"Supprimer le flux « {stream['name']} » ?") != QMessageBox.Yes:
            return
        self.engine.stop_stream(stream_id)
        self.config["streams"].remove(stream)
        self.config["mapping"].pop(stream_id, None)
        card = self.cards.pop(stream_id, None)
        if card:
            card.setParent(None)
            card.deleteLater()
        self._update_stream_count()
        self.commit()

    def on_card_changed(self, stream_id: str) -> None:
        stream, card = self._stream(stream_id), self.cards.get(stream_id)
        if stream is None or card is None:
            return
        display_id = card.read_into(stream)
        if display_id:
            self.config["mapping"][stream_id] = display_id
        else:
            self.config["mapping"].pop(stream_id, None)
        self.schedule_commit()

    def toggle_stream(self, stream_id: str) -> None:
        self.commit()
        if stream_id in self.engine.desired_streams:
            self.engine.stop_stream(stream_id)
        else:
            error = self.engine.start_stream(stream_id)
            if error:
                self.notify(f"⚠ {self._stream(stream_id)['name']} : {error}", 8000)
        self.on_tick()

    def toggle_reception(self) -> None:
        self.commit()
        if self.engine.reception_running:
            self.engine.stop_reception()
            self.notify("Réception arrêtée.")
        else:
            errors = self.engine.start_reception()
            if not self.engine.desired_streams:
                self.notify("Aucun flux n'a d'écran assigné : choisis un écran sur chaque carte ou utilise « Auto-assigner ».", 8000)
            elif errors:
                self.notify(f"⚠ {len(errors)} flux n'ont pas pu démarrer — voir le détail sur les cartes.", 8000)
        self.on_tick()

    def discover_omt(self, stream_id: str) -> None:
        card = self.cards.get(stream_id)
        toolchain = self.engine.tools.ffmpeg_for("omt")
        dlg = OMTDiscoveryDialog(self, toolchain.ffmpeg if toolchain else None, card.omt_edit.text() if card else "")
        if dlg.exec() == QDialog.Accepted and card:
            card.set_omt_source(dlg.selected_source)

    def show_logs(self, key: str, title: str) -> None:
        dlg = self._log_windows.get(key)
        if dlg is None or not dlg.isVisible():
            dlg = LogDialog(self, title, lambda: self.engine.log_lines(key))
            dlg.setAttribute(Qt.WA_DeleteOnClose)
            dlg.destroyed.connect(lambda *_: self._log_windows.pop(key, None))
            self._log_windows[key] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ------------------------------------------------------------ sender

    def _show_sender_audio(self) -> None:
        source = self.config["sender"].get("audioSource", "")
        self.sender_audio_text.set_full_text(audio.describe_source(source))

    def choose_sender_audio(self) -> None:
        toolchain = self.engine.tools.ffmpeg_for("omt")
        native = omtlib.available() and self.config["sender"].get("native", False)
        dialog = AudioSourceDialog(
            self, self.config["sender"].get("audioSource", ""), toolchain.ffmpeg if toolchain else None, native
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.config["sender"]["audioSource"] = dialog.selected_source
        self._show_sender_audio()
        self.commit()  # applied live: the emission restarts with the new sound

    def _refresh_meter(self) -> None:
        level = self.engine.sender_audio_level() if self.engine.sender_desired else None
        self.sender_meter.setVisible(level is not None)
        if level is not None:
            self.sender_meter.set_level(level)

    # ------------------------------------------------------------ previews

    def _stream_display(self, stream_id: str) -> dict | None:
        display_id = self.config["mapping"].get(stream_id)
        return next((d for d in self.displays if d["id"] == display_id), None)

    def refresh_previews(self) -> None:
        size = QSize(160, 90)
        for stream_id, card in self.cards.items():
            display = self._stream_display(stream_id)
            live = self.engine.stream_info(stream_id).status == Status.ACTIVE
            card.thumb.set_preview(grab_display(display, size) if live and display else None)

    def open_preview(self, stream_id: str) -> None:
        stream = self._stream(stream_id)
        if stream is None:
            return
        dlg = self._previews.get(stream_id)
        if dlg is None or not dlg.isVisible():
            dlg = PreviewDialog(self, f"Aperçu — {stream['name']}", lambda: self._stream_display(stream_id))
            dlg.setAttribute(Qt.WA_DeleteOnClose)
            dlg.destroyed.connect(lambda *_: self._previews.pop(stream_id, None))
            self._previews[stream_id] = dlg
        dlg.show()
        dlg.raise_()

    def _fill_sender_displays(self) -> None:
        selected = self.config["sender"]["displayId"]
        self.sender_display.blockSignals(True)
        self.sender_display.clear()
        self.sender_display.addItem("— Choisir un écran —", "")
        for d in self.displays:
            self.sender_display.addItem(d["name"], d["id"])
            self.sender_display.setItemData(self.sender_display.count() - 1, describe(d), Qt.ToolTipRole)
        if selected and self.sender_display.findData(selected) < 0:
            self.sender_display.addItem("⚠ Écran absent", selected)
        self.sender_display.setCurrentIndex(max(0, self.sender_display.findData(selected)))
        self.sender_display.blockSignals(False)

    def load_sender_fields(self) -> None:
        sender = self.config["sender"]
        # libomt captures in BGRA, encodes and paces itself: these two only
        # drive the FFmpeg emission. What counts is the path actually used
        # (Préférences → Émission OMT), not whether the library is there.
        native = omtlib.available() and sender.get("native", False)
        note = "Réglage propre à l'émission FFmpeg : libomt encode et cadence lui-même."
        for widget, tip in ((self.sender_format, ""), (self.sender_clock, CLOCK_TOOLTIP)):
            widget.setEnabled(not native)
            widget.setToolTip(note if native else tip)
        widgets = (self.sender_name, self.sender_fps, self.sender_format, self.sender_clock)
        for w in widgets:
            w.blockSignals(True)
        self.sender_name.setText(sender["name"])
        self.sender_fps.setValue(sender["fps"])
        self.sender_format.setCurrentIndex(max(0, self.sender_format.findData(sender["pixelFormat"])))
        self.sender_clock.setChecked(sender["clockOutput"])
        for w in widgets:
            w.blockSignals(False)
        self._fill_sender_displays()
        self._show_sender_audio()

    def on_sender_changed(self, *_args) -> None:
        sender = self.config["sender"]
        sender["displayId"] = self.sender_display.currentData() or ""
        sender["name"] = self.sender_name.text().strip() or sender["name"]
        sender["fps"] = self.sender_fps.value()
        sender["pixelFormat"] = self.sender_format.currentData()
        sender["clockOutput"] = self.sender_clock.isChecked()
        self.schedule_commit()

    def toggle_sender(self) -> None:
        self.on_sender_changed()
        self.commit()
        if self.engine.sender_desired:
            self.engine.stop_sender()
        else:
            error = self.engine.start_sender()
            if error:
                self.notify(f"⚠ Émission OMT : {error}", 8000)
        self.on_tick()

    # ------------------------------------------------------ audio monitor

    def _monitor_widgets(self) -> tuple:
        return (
            self.monitor_source, self.monitor_volume, self.float_source, self.float_width,
            self.float_height, self.float_volume, self.float_on_top,
        )

    def load_monitor_fields(self) -> None:
        monitor = self.config["monitor"]
        for w in self._monitor_widgets():
            w.blockSignals(True)
        self.monitor_source.setText(monitor["omtSource"])
        self.monitor_volume.setValue(monitor["volume"])
        self.float_source.setText(monitor["floatSource"])
        self.float_width.setValue(monitor["floatWidth"])
        self.float_height.setValue(monitor["floatHeight"])
        self.float_volume.setValue(monitor["floatVolume"])
        self.float_on_top.setChecked(monitor["floatOnTop"])
        for w in self._monitor_widgets():
            w.blockSignals(False)

    def on_monitor_changed(self, *_args) -> None:
        monitor = self.config["monitor"]
        monitor["omtSource"] = self.monitor_source.text().strip()
        monitor["volume"] = self.monitor_volume.value()
        monitor["floatSource"] = self.float_source.text().strip()
        monitor["floatWidth"] = self.float_width.value()
        monitor["floatHeight"] = self.float_height.value()
        monitor["floatVolume"] = self.float_volume.value()
        monitor["floatOnTop"] = self.float_on_top.isChecked()
        self.schedule_commit()

    def discover_float_source(self) -> None:
        toolchain = self.engine.tools.ffmpeg_for("omt")
        dlg = OMTDiscoveryDialog(self, toolchain.ffmpeg if toolchain else None, self.float_source.text().strip())
        if dlg.exec() == QDialog.Accepted:
            self.float_source.setText(dlg.selected_source)
            self.on_monitor_changed()

    def toggle_floating(self) -> None:
        self.on_monitor_changed()
        self.commit()
        if self.engine.floating_desired:
            self.engine.stop_floating()
        else:
            error = self.engine.start_floating()
            if error:
                self.notify(f"⚠ Fenêtre de retour : {error}", 8000)
        self.on_tick()

    def discover_monitor_source(self) -> None:
        toolchain = self.engine.tools.ffmpeg_for("omt")
        dlg = OMTDiscoveryDialog(self, toolchain.ffmpeg if toolchain else None, self.monitor_source.text().strip())
        if dlg.exec() == QDialog.Accepted:
            self.monitor_source.setText(dlg.selected_source)
            self.on_monitor_changed()

    def toggle_monitor(self) -> None:
        self.on_monitor_changed()
        self.commit()
        if self.engine.monitor_desired:
            self.engine.stop_monitor()
        else:
            error = self.engine.start_monitor()
            if error:
                self.notify(f"⚠ Retour audio : {error}", 8000)
        self.on_tick()

    # ------------------------------------------------------- local API

    def refresh_api(self) -> None:
        """Apply the API settings: start, move, or stop the server."""
        settings = self.config.get("api", {})
        self.api.token = settings.get("token", "")
        if not settings.get("enabled"):
            self.api.stop()
            return
        host = "127.0.0.1" if settings.get("localOnly") else "0.0.0.0"  # noqa: S104
        error = self.api.start(host, settings.get("port", 5090))
        if error:
            self.notify(f"⚠ API locale : {error}", 10000)

    def api_url(self) -> str:
        if not self.api.running:
            return ""
        host = "127.0.0.1" if self.api.host.startswith("127.") else self.host_ip
        return f"http://{host}:{self.api.port}"

    def _run_api_request(self, method: str, path: str, body: bytes = b""):
        """Called from an HTTP thread: run the route on the UI thread."""
        done = threading.Event()
        box: dict = {}

        def job() -> None:
            try:
                box["result"] = api_dispatch(method, path, self.control, body)
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                box["error"] = exc
            finally:
                done.set()

        self.api_call.emit(job)
        if not done.wait(timeout=10.0):
            return 503, {"error": {"code": "BUSY", "message": "py-multiview n'a pas répondu à temps"}}
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _run_api_job(self, job) -> None:
        job()

    # ------------------------------------------------------------ routes

    def open_routes(self) -> None:
        RoutesDialog(self).exec()
        self.commit()

    def on_routes_changed(self, reload_cards: bool = False) -> None:
        if reload_cards:
            self.reload_cards()
        else:
            for card in self.cards.values():
                card.set_routes(self.config["routes"])
        self.refresh_routes_list()
        self.schedule_commit()

    def refresh_routes_list(self) -> None:
        routes = self.config["routes"]
        self.routes_list.clear()
        if not routes:
            item = QListWidgetItem("Aucune route — « Gérer » pour en créer")
            item.setForeground(QColor(theme.TEXT_MUTED))
            self.routes_list.addItem(item)
            return
        for route in routes:
            item = QListWidgetItem(f"{route['name']}\nSRT :{route['inputPort']} → {route['multicastAddr']}:{route['multicastPort']}")
            item.setData(Qt.UserRole, route["id"])
            self.routes_list.addItem(item)
        self._update_routes_status()

    def _update_routes_status(self) -> None:
        for i in range(self.routes_list.count()):
            item = self.routes_list.item(i)
            rid = item.data(Qt.UserRole)
            if not rid:
                continue
            info = self.engine.route_info(rid)
            color = theme.STATE_COLORS[info.status.value]
            item.setIcon(theme.icon("route", color))
            item.setToolTip(info.detail or "")

    # ------------------------------------------------------------ status

    def on_tick(self) -> None:
        self.engine.tick()
        active = 0
        for stream_id, card in self.cards.items():
            info = self.engine.stream_info(stream_id)
            card.set_info(info, stream_id in self.engine.desired_streams)
            active += info.status == Status.ACTIVE

        total = len(self.config["streams"])
        self.streams_chip.setText(f"Flux en direct  {active}/{total}")
        set_state(self.streams_chip, "active" if active else "stopped")

        if self.engine.reception_running:
            set_button(self.reception_btn, "Arrêter la réception", "danger", "stop")
        else:
            set_button(self.reception_btn, "Démarrer la réception", "success", "play")

        sender = self.engine.sender_info()
        self.sender_chip.setText("OMT  en émission" if sender.status == Status.ACTIVE else "OMT  arrêté")
        set_state(self.sender_chip, "active" if sender.status == Status.ACTIVE else "stopped")
        set_state(self.sender_detail, sender.status.value)
        self.sender_detail.setText(sender.detail)
        self.sender_detail.setVisible(bool(sender.detail))
        if self.engine.sender_desired:
            set_button(self.sender_btn, "Arrêter", "danger", "stop")
        else:
            set_button(self.sender_btn, "Émettre", "success", "broadcast")

        monitor = self.engine.monitor_info()
        set_state(self.monitor_detail, monitor.status.value)
        self.monitor_detail.setText(monitor.detail)
        self.monitor_detail.setVisible(bool(monitor.detail))
        if self.engine.monitor_desired:
            set_button(self.monitor_btn, "Couper", "danger", "stop")
        else:
            set_button(self.monitor_btn, "Écouter", "success", "broadcast")

        floating = self.engine.floating_info()
        set_state(self.float_detail, floating.status.value)
        self.float_detail.setText(floating.detail)
        self.float_detail.setVisible(bool(floating.detail))
        if self.engine.floating_desired:
            set_button(self.float_btn, "Fermer", "danger", "stop")
        else:
            set_button(self.float_btn, "Ouvrir", "success", "play")
        self._update_routes_status()
        self._refresh_summaries(sender, monitor, floating)

    # --------------------------------------------------------- sidebar

    def _apply_sidebar_state(self) -> None:
        """Restore which blocks were folded; blocks never touched keep their default."""
        saved = self.config.get("sidebar", {})
        for key, sec in self.sections.items():
            sec.set_expanded(saved.get(key, self._section_defaults[key]), notify=False)
        self._refresh_summaries()

    def _on_section_toggled(self, key: str, expanded: bool) -> None:
        self.config.setdefault("sidebar", {})[key] = expanded
        self.schedule_commit()
        self._refresh_summaries()

    def _summary(self, key: str, info, settings: str) -> tuple[str, str]:
        """Folded: say what is wrong, else what is set. Open: the body already says it."""
        sec = self.sections[key]
        status = info.status if info is not None else Status.STOPPED
        if not sec.is_expanded() and info is not None and info.detail:
            return info.detail, status.value
        if status in (Status.WAITING, Status.STARTING, Status.RETRYING, Status.ERROR, Status.ACTIVE):
            return f"{STATUS_TEXT[status]}  ·  {settings}", status.value
        return settings, "stopped"

    def _refresh_summaries(self, sender=None, monitor=None, floating=None) -> None:
        if not hasattr(self, "sections"):
            return
        engine = self.engine
        sender = sender or engine.sender_info()
        monitor = monitor or engine.monitor_info()
        floating = floating or engine.floating_info()
        cfg = self.config

        count = len(self.displays)
        self.sections["screens"].set_summary(f"{count} écran{'s' if count > 1 else ''} détecté{'s' if count > 1 else ''}")

        audio_text = audio.describe_source(cfg["sender"].get("audioSource", ""))
        screen_text = self.sender_display.currentText() if self.sender_display.currentData() else "aucun écran"
        text, state = self._summary(
            "sender", sender,
            f"{screen_text}  ·  {cfg['sender']['fps']} i/s  ·  {audio_text}",
        )
        self.sections["sender"].set_summary(text, state)

        monitor_cfg = cfg["monitor"]
        text, state = self._summary(
            "monitor", monitor,
            f"{monitor_cfg['omtSource'] or 'aucune source'}  ·  {monitor_cfg['volume']} %",
        )
        self.sections["monitor"].set_summary(text, state)

        source = monitor_cfg["floatSource"] or monitor_cfg["omtSource"] or "aucune source"
        text, state = self._summary(
            "floating", floating,
            f"{source}  ·  {monitor_cfg['floatWidth']}×{monitor_cfg['floatHeight']}",
        )
        self.sections["floating"].set_summary(text, state)

        routes = cfg["routes"]
        running = sum(1 for r in routes if r["id"] in engine.active_routes)
        if not routes:
            route_text = "aucune route"
        else:
            route_text = f"{len(routes)} route{'s' if len(routes) > 1 else ''}"
            if running:
                route_text += f"  ·  {running} active{'s' if running > 1 else ''}"
        self.sections["routes"].set_summary(route_text, "active" if running else "stopped")

    def refresh_engine_summary(self) -> None:
        tools = self.engine.tools
        srt = tools.player_for("srt") is not None
        omt_ok = tools.player_for("omt") is not None and tools.ffmpeg_for("omt") is not None
        native_omt = omtlib.available()
        omt_text = "OMT natif ✓" if native_omt else f"OMT {'✓' if omt_ok else '✗'}"
        self.engine_label.setText(f"FFmpeg  ·  SRT {'✓' if srt else '✗'}  ·  {omt_text}")
        tips = [tc.describe() for tc in tools.toolchains] or ["Aucun FFmpeg trouvé"]
        tips.append(
            "libomt : émission et retour audio en direct"
            if native_omt
            else f"libomt indisponible, repli sur FFmpeg ({omtlib.load_error() or 'raison inconnue'})"
        )
        self.engine_label.setToolTip("\n".join(tips))
        if not srt:
            self.notify("⚠ Aucun FFmpeg avec SRT : la réception SRT et le routage sont indisponibles (voir Préférences).", 0)

    # ------------------------------------------------------------ preferences / lifecycle

    def open_preferences(self) -> None:
        PreferencesDialog(self).exec()

    def on_preferences_changed(self) -> None:
        self.refresh_displays(force=True)
        self.refresh_api()
        self.load_sender_fields()  # the emission path decides which fields apply
        self.commit()

    def reset_configuration(self) -> bool:
        answer = QMessageBox.warning(
            self,
            "Réinitialiser",
            "Remettre toute la configuration à zéro ?\n\n"
            "Flux, affectations d'écrans, noms d'écrans, routes et réglages d'émission seront perdus. "
            "Tout ce qui est en cours sera arrêté.",
            QMessageBox.Reset | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Reset:
            return False
        self.engine.shutdown()
        self.config = default_config()
        self.refresh_displays(force=True)
        self.reload_cards()
        self.load_sender_fields()
        self.load_monitor_fields()
        self.refresh_routes_list()
        self._apply_sidebar_state()
        self.commit()
        self.notify("Configuration réinitialisée.")
        return True

    def autostart(self) -> None:
        started = []
        if self.config["autoStartReceiver"]:
            self.engine.start_reception()
            started.append("réception")
        if self.config["autoStartSender"]:
            self.engine.start_sender()
            started.append("émission OMT")
        if started:
            self.notify("Démarrage automatique : " + " et ".join(started) + ". Les sources absentes seront reprises dès leur arrivée.", 10000)
        self.on_tick()

    # ------------------------------------------------------------ updates

    def check_updates_quietly(self) -> None:
        """Background check: only the header button shows up, and only for a new version."""
        if not self.config["updates"]["autoCheck"]:
            return

        def work() -> None:
            updater.cleanup()  # leftovers of the previous update
            try:
                self.update_found.emit(updater.fetch_latest())
            except updater.UpdateError as exc:
                log.info("Recherche de mise à jour : %s", exc)
            except Exception:
                log.exception("Recherche de mise à jour")

        threading.Thread(target=work, daemon=True).start()

    def on_update_checked(self, release: updater.Release) -> None:
        self._latest_release = release
        available = updater.is_newer(release.version) and release.version != self.config["updates"]["skipped"]
        if available:
            self.update_btn.setText(f"Mise à jour {release.version}")
            self.update_btn.setToolTip(f"py-multiview {release.version} est disponible (installée : {__version__})")
        self.update_btn.setVisible(available)

    def skip_update(self, version: str) -> None:
        self.config["updates"]["skipped"] = version
        self.update_btn.hide()
        self.commit()

    def open_update(self, release: updater.Release | None = None) -> None:
        UpdateDialog(self, release).exec()

    def quit_for_update(self) -> None:
        """Leave right away: the installer (or the portable swap) waits for this process to end."""
        self._quit()

    # ------------------------------------------------------------ window / quit

    def _build_tray(self) -> None:
        """Icon near the clock: the way back to a minimised window, and the way out."""
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = APP_ICON_ICO_PATH if APP_ICON_ICO_PATH.exists() else APP_ICON_PNG_PATH
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        show = QAction("Ouvrir py-multiview", self)
        show.triggered.connect(self.bring_to_front)
        menu.addAction(show)
        menu.addSeparator()
        leave = QAction("Quitter", self)
        leave.triggered.connect(self.quit_app)
        menu.addAction(leave)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.bring_to_front()

    def running_summary(self) -> str:
        """What quitting would cut, in words; empty when nothing runs."""
        running = []
        if self.engine.reception_running:
            running.append("la réception")
        if self.engine.sender_desired:
            running.append("l'émission OMT")
        if self.engine.monitor_desired or self.engine.floating_desired:
            running.append("le retour")
        return ", ".join(running[:-1]) + (" et " if len(running) > 1 else "") + running[-1] if running else ""

    def quit_app(self) -> None:
        """Really quit (Ctrl+Q, tray menu), after a word of warning when something is live."""
        running = self.running_summary()
        if running:
            self.bring_to_front()
            answer = QMessageBox.question(
                self, "Quitter py-multiview",
                f"Quitter arrêtera {running}.\n\nQuitter quand même ?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        self._quit()

    def _quit(self) -> None:
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            # Back to what it was before (a maximised window stays maximised).
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if not self._quitting and self.config.get("closeToMinimize", True):
            # The cross only puts the window away: everything keeps running.
            event.ignore()
            self.showMinimized()
            if not self._close_hint_shown:
                self._close_hint_shown = True
                hint = "py-multiview continue de tourner. Pour quitter : Ctrl+Q, ou clic droit sur son icône près de l'horloge."
                if self.tray is not None:
                    self.tray.showMessage(APP_NAME, hint, QSystemTrayIcon.Information, 6000)
                self.notify(hint, 10000)
            return
        if self.tray is not None:
            self.tray.hide()
        self.commit()
        self.api.stop()
        self.engine.shutdown()
        event.accept()
