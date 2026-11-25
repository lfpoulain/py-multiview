"""Main window: a navigation column on the left, one page per job on the right.

Pages: incoming streams, screens, OMT emission, return (audio and floating
window), multicast routing and settings. A top bar keeps the page title and the
« Démarrer la réception » button in view whatever the page.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
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
from .dialogs import LogDialog, OMTDiscoveryDialog, RoutesPanel, ScreenIdentifier
from .preview import PreviewDialog, grab_display
from .settings_page import SettingsPage
from .stream_card import StreamCard
from .update_dialog import UpdateDialog
from .widgets import (
    STATUS_TEXT,
    Card,
    ElidedLabel,
    IconTile,
    LevelMeter,
    StatusPill,
    Switch,
    button,
    divider,
    label,
    set_button,
    set_state,
    spin,
)

log = logging.getLogger(__name__)

NAV_WIDTH = 232
CONTENT_WIDTH = 860
UPDATE_FIRST_CHECK_MS = 5_000
UPDATE_CHECK_EVERY_MS = 6 * 3600 * 1000
CLOCK_TOOLTIP = (
    "Active le cadencement de la sortie FFmpeg par libomt.\n"
    "Décoché, FFmpeg transmet ses propres horodatages."
)
PIXEL_FORMAT_LABELS = {
    "uyvy422": ("UYVY 4:2:2", "Recommandé : bon compromis qualité / bande passante"),
    "bgra": ("BGRA 4:4:4", "Pleine résolution couleur avec alpha, plus gourmand"),
    "yuv422p10le": ("YUV 10 bits", "4:2:2 10 bits pour les chaînes HDR / haute qualité"),
}
# Most telling state first, to sum up several processes in one badge.
_STATUS_RANK = (Status.ERROR, Status.RETRYING, Status.WAITING, Status.STARTING, Status.ACTIVE, Status.STOPPED)


class NavButton(QPushButton):
    """Entry of the navigation column: icon, label and a status badge on the right."""

    def __init__(self, text: str, icon: str) -> None:
        super().__init__()
        self.setProperty("variant", "nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(text)
        self._icon = icon
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(10)
        self.glyph = QLabel()
        self.glyph.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.glyph)
        self.text_label = label(text, "NavText")
        self.text_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.text_label, 1)
        self.badge = label("", "NavBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.setFixedHeight(20)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()
        row.addWidget(self.badge, 0, Qt.AlignVCenter)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.setChecked(active)
        self.glyph.setPixmap(theme.pixmap(self._icon, theme.ACCENT if active else theme.TEXT_MUTED, 18))
        if self.text_label.property("active") != active:
            self.text_label.setProperty("active", active)
            self.text_label.style().unpolish(self.text_label)
            self.text_label.style().polish(self.text_label)

    def set_badge(self, text: str, state: str = "stopped") -> None:
        self.badge.setText(text)
        self.badge.setVisible(bool(text))
        set_state(self.badge, state)


class MainWindow(QMainWindow):
    api_call = Signal(object)
    update_found = Signal(object)  # Release, from the background check

    PAGES = (
        # key, nav label, icon, page title, page subtitle
        ("streams", "Flux", "grid", "Flux entrants", "Les sources reçues et l'écran où chacune s'affiche."),
        ("screens", "Écrans", "screen", "Écrans", "Les sorties de ce poste, leurs noms et leurs flux."),
        ("sender", "Émission OMT", "broadcast", "Émission OMT", "Publier un écran de ce poste sur le réseau."),
        ("monitor", "Retour", "headphones", "Retour", "Écouter ou regarder une source OMT sur ce poste."),
        ("routes", "Routage", "route", "Routage multicast", "Partager un flux SRT entre plusieurs écrans."),
        ("settings", "Réglages", "gear", "Réglages", "Démarrage, fenêtre, réception, pilotage et système."),
    )

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
        self._latest_release: updater.Release | None = None

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
        self.refresh_engine_summary()
        self.show_page(self.config.get("page", "streams"))
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
        for index, (key, *_rest) in enumerate(self.PAGES, start=1):
            QShortcut(QKeySequence(f"Ctrl+{index}"), self, activated=lambda k=key: self.show_page(k))
        self._quitting = False
        self._close_hint_shown = False
        self._build_tray()
        # Windows closing the session must really quit, not minimise.
        QApplication.instance().commitDataRequest.connect(lambda *_: setattr(self, "_quitting", True))
        QTimer.singleShot(300, self.autostart)

        self.update_found.connect(self.on_update_checked)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_updates_quietly)
        self._update_timer.start(UPDATE_CHECK_EVERY_MS)
        QTimer.singleShot(UPDATE_FIRST_CHECK_MS, self.check_updates_quietly)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_nav())

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_top_bar())
        self.pages = QStackedWidget()
        self._page_index: dict[str, int] = {}
        builders = {
            "streams": self._build_streams_page,
            "screens": self._build_screens_page,
            "sender": self._build_sender_page,
            "monitor": self._build_monitor_page,
            "routes": self._build_routes_page,
            "settings": self._build_settings_page,
        }
        for key, *_rest in self.PAGES:
            wrapper = QWidget()
            wrapper.setObjectName("Page")
            pad = QVBoxLayout(wrapper)
            pad.setContentsMargins(28, 22, 22, 14)
            pad.addWidget(builders[key]())
            self._page_index[key] = self.pages.addWidget(wrapper)
        main.addWidget(self.pages, 1)
        outer.addLayout(main, 1)
        self.statusBar().setSizeGripEnabled(False)

        screen = self.screen().availableGeometry() if self.screen() else None
        width, height = 1360, 880
        if screen is not None:
            width, height = min(width, int(screen.width() * 0.95)), min(height, int(screen.height() * 0.92))
        self.resize(width, height)
        self.setMinimumSize(1040, 640)

    def _build_nav(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("Nav")
        nav.setFixedWidth(NAV_WIDTH)
        col = QVBoxLayout(nav)
        col.setContentsMargins(12, 18, 12, 12)
        col.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        logo = QLabel()
        if APP_ICON_PNG_PATH.exists():
            pm = QPixmap(str(APP_ICON_PNG_PATH))
            if not pm.isNull():
                logo.setPixmap(pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("py-multiview", "Brand"))
        ip = self.host_ip or "IP inconnue"
        self.subtitle = label(f"v{__version__}  ·  {ip}", "BrandSub")
        self.subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle.setToolTip("Version et adresse IP de ce poste")
        names.addWidget(self.subtitle)
        brand.addLayout(names, 1)
        col.addLayout(brand)
        col.addSpacing(22)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, NavButton] = {}
        groups = {"streams": "DIRECT", "sender": "SERVICES"}
        for index, (key, text, icon, *_rest) in enumerate(self.PAGES, start=1):
            if key == "settings":
                col.addStretch(1)
            if key in groups:
                if key != "streams":
                    col.addSpacing(16)
                col.addWidget(label(groups[key], "NavGroup"))
                col.addSpacing(4)
            btn = NavButton(text, icon)
            btn.setToolTip(f"Ctrl+{index}")
            btn.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            col.addWidget(btn)

        col.addSpacing(8)
        self.update_btn = button("", variant="primary", icon="download")
        self.update_btn.clicked.connect(lambda: self.open_update(self._latest_release))
        self.update_btn.hide()
        col.addWidget(self.update_btn)
        col.addSpacing(6)
        col.addWidget(divider())
        col.addSpacing(6)
        self.engine_label = button(variant="ghost", small=True)
        self.engine_label.setStyleSheet("text-align: left;")
        self.engine_label.clicked.connect(self.open_preferences)
        col.addWidget(self.engine_label)
        return nav

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(28, 14, 22, 14)
        row.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.page_title = label("", "PageTitle")
        self.page_sub = label("", "PageSub")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_sub)
        row.addLayout(titles, 1)

        self.live_chip = StatusPill()
        self.live_chip.setToolTip("Flux affichés en ce moment")
        row.addWidget(self.live_chip)
        self.reception_btn = button("Démarrer la réception", variant="success", icon="play", icon_color="#ffffff",
                                    large=True)
        self.reception_btn.setMinimumWidth(210)
        self.reception_btn.setToolTip("Démarre tous les flux qui ont un écran assigné")
        self.reception_btn.clicked.connect(self.toggle_reception)
        row.addWidget(self.reception_btn)
        return bar

    def show_page(self, key: str) -> None:
        if key not in self._page_index:
            key = "streams"
        self.pages.setCurrentIndex(self._page_index[key])
        for page_key, btn in self.nav_buttons.items():
            btn.set_active(page_key == key)
        _key, _nav, _icon, title, sub = next(p for p in self.PAGES if p[0] == key)
        self.page_title.setText(title)
        self.page_sub.setText(sub)
        if key == "settings":
            self.settings_page.load()
        if self.config.get("page") != key:
            self.config["page"] = key
            self.schedule_commit()

    # -- pages ----------------------------------------------------------

    @staticmethod
    def _page_scroll(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _column_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        """A scrolling column of cards, kept to a readable width."""
        column = QWidget()
        column.setObjectName("Transparent")
        column.setMaximumWidth(CONTENT_WIDTH)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 8, 12)
        col.setSpacing(16)
        holder = QWidget()
        holder.setObjectName("Transparent")
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(column, 1)
        line.addStretch(0)
        return self._page_scroll(holder), col

    def _service_card(self, title: str, subtitle: str, icon: str, log_key: str,
                      action: str, action_icon: str) -> tuple[Card, StatusPill, QPushButton, QLabel]:
        """Card of a process that can be started: status, journal, start button, problem line."""
        card = Card(title, subtitle, icon)
        pill = StatusPill()
        card.add_action(pill)
        logs = button(variant="icon", icon="logs", icon_color=theme.TEXT_DIM, tooltip=f"Journal : {title.lower()}")
        logs.clicked.connect(lambda: self.show_logs(log_key, f"Journal — {title.lower()}"))
        card.add_action(logs)
        toggle = button(action, variant="success", icon=action_icon, icon_color="#ffffff")
        toggle.setMinimumWidth(116)
        card.add_action(toggle)
        detail = label("", "Detail", wrap=True)
        detail.hide()
        card.add_widget(detail, separated=False)
        return card, pill, toggle, detail

    def _build_streams_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Transparent")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(10)
        modes = QFrame()
        modes.setObjectName("Segment")
        choices = QHBoxLayout(modes)
        choices.setContentsMargins(3, 3, 3, 3)
        choices.setSpacing(2)
        self.view_group = QButtonGroup(self)
        self.editing = self.config.get("streamView") == "edit"
        for editing, title, tip in ((False, "Mosaïque", "Surveiller les flux et leurs sorties"),
                                    (True, "Configuration", "Régler la source et l'écran de chaque flux")):
            btn = button(title, variant="segment", tooltip=tip)
            btn.setCheckable(True)
            self.view_group.addButton(btn, int(editing))
            btn.setChecked(editing == self.editing)
            choices.addWidget(btn)
        self.view_group.idClicked.connect(lambda index: self._set_editing(bool(index)))
        head.addWidget(modes)
        self.streams_count = label("", "Muted")
        head.addWidget(self.streams_count)
        head.addStretch(1)
        auto = button("Auto-assigner", icon="wand", icon_color=theme.TEXT_DIM,
                      tooltip="Assigne un écran libre à chaque flux (crée des flux si besoin)")
        auto.clicked.connect(self.auto_assign)
        head.addWidget(auto)
        add = button("Ajouter un flux", variant="primary", icon="plus", tooltip="Ctrl+N")
        add.clicked.connect(self.add_stream)
        head.addWidget(add)
        col.addLayout(head)

        self.streams_stack = QStackedWidget()
        inner = QWidget()
        inner.setObjectName("Transparent")
        self.cards_layout = QGridLayout(inner)
        self.cards_layout.setContentsMargins(0, 0, 6, 0)
        self.cards_layout.setSpacing(16)
        self.cards_layout.setAlignment(Qt.AlignTop)
        self._columns = 0
        scroll = self._page_scroll(inner)
        self.cards_scroll = scroll
        scroll.viewport().installEventFilter(self)
        self.streams_stack.addWidget(scroll)

        empty = Card()
        empty_col = empty.body
        empty_col.setSpacing(10)
        empty_col.addStretch(1)
        empty_col.addWidget(IconTile("grid", 56), 0, Qt.AlignHCenter)
        title = label("Aucun flux configuré", "EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(title)
        hint = label("Ajoute un flux puis assigne-lui un écran,\nou « Auto-assigner » pour en créer un par écran.",
                     "Muted")
        hint.setAlignment(Qt.AlignCenter)
        empty_col.addWidget(hint)
        empty_btn = button("Ajouter un flux", variant="primary", icon="plus")
        empty_btn.clicked.connect(self.add_stream)
        empty_col.addWidget(empty_btn, 0, Qt.AlignHCenter)
        empty_col.addStretch(2)
        self.streams_stack.addWidget(empty)
        col.addWidget(self.streams_stack, 1)
        return page

    def _build_screens_page(self) -> QWidget:
        scroll, col = self._column_page()
        card = Card("Écrans détectés", "Double-clic sur un écran pour le renommer.", "screen")
        identify = button("Identifier", icon="identify", icon_color=theme.TEXT_DIM,
                          tooltip="Affiche le nom de chaque écran sur l'écran lui-même")
        identify.clicked.connect(self.identify_screens)
        card.add_action(identify)
        rename = button("Renommer", tooltip="Renommer l'écran sélectionné")
        rename.clicked.connect(self.rename_display)
        card.add_action(rename)
        self.displays_list = QListWidget()
        self.displays_list.setIconSize(QSize(22, 22))
        self.displays_list.setMinimumHeight(240)
        self.displays_list.itemDoubleClicked.connect(lambda _i: self.rename_display())
        card.add_widget(self.displays_list, separated=False)
        col.addWidget(card)

        options = Card("Affectation", "Quel flux s'affiche sur quel écran.", "wand")
        self.exclude_primary = Switch("Exclure l'écran principal")
        self.exclude_primary.toggled.connect(self.on_exclude_primary_changed)
        options.add_row("Exclure l'écran principal",
                        "Aucun flux ne s'affiche sur l'écran du poste opérateur.", self.exclude_primary)
        auto = button("Auto-assigner", icon="wand", icon_color=theme.TEXT_DIM)
        auto.clicked.connect(self.auto_assign)
        options.add_row("Auto-assigner", "Donne un écran libre à chaque flux et crée les flux qui manquent.", auto)
        col.addWidget(options)
        col.addStretch(1)
        return scroll

    def _build_sender_page(self) -> QWidget:
        scroll, col = self._column_page()

        status, self.sender_pill, self.sender_btn, self.sender_detail = self._service_card(
            "Émission", "Publie un écran de ce poste comme source OMT.", "broadcast", "sender",
            "Émettre", "broadcast",
        )
        self.sender_btn.clicked.connect(self.toggle_sender)
        col.addWidget(status)

        source = Card("Source", "Ce qui est envoyé sur le réseau.")
        self.sender_display = QComboBox()
        self.sender_display.setMinimumWidth(280)
        source.add_row("Écran", "L'écran de ce poste qui est capturé.", self.sender_display)
        # The sound is picked in its own window (categories, search): a combo
        # listing every device and every program twice was unreadable.
        self.sender_audio = button(variant="field", tooltip="Choisir le son envoyé avec l'image")
        audio_inner = QHBoxLayout(self.sender_audio)
        audio_inner.setContentsMargins(9, 0, 8, 0)
        audio_inner.setSpacing(6)
        self.sender_audio_text = ElidedLabel("", "Transparent")
        self.sender_audio_text.setAttribute(Qt.WA_TransparentForMouseEvents)
        audio_inner.addWidget(self.sender_audio_text, 1)
        chevron = QLabel()
        chevron.setPixmap(theme.pixmap("chevron", theme.TEXT_MUTED, 12))
        chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        audio_inner.addWidget(chevron)
        self.sender_audio.setFixedHeight(32)
        self.sender_audio.setFixedWidth(280)
        self.sender_audio.clicked.connect(self.choose_sender_audio)
        self.sender_meter = LevelMeter()
        self.sender_meter.hide()
        audio_box = QVBoxLayout()
        audio_box.setContentsMargins(0, 0, 0, 0)
        audio_box.setSpacing(6)
        audio_box.addWidget(self.sender_audio)
        audio_box.addWidget(self.sender_meter)
        audio_holder = QWidget()
        audio_holder.setObjectName("Transparent")
        audio_holder.setLayout(audio_box)
        source.add_row("Son", "Le son du PC, d'un programme ou d'une entrée, ou aucun.", audio_holder)
        col.addWidget(source)

        publish = Card("Publication", "Comment la source apparaît sur le réseau.")
        self.sender_name = QLineEdit()
        self.sender_name.setPlaceholderText("Nom publié sur le réseau")
        self.sender_name.setFixedWidth(280)
        publish.add_row("Nom", "Le nom que verront les récepteurs OMT.", self.sender_name)
        self.sender_fps = spin(1, 60, 30, suffix=" i/s", width=120)
        publish.add_row("Cadence", "Images par seconde envoyées.", self.sender_fps)
        self.sender_format = QComboBox()
        for key in OMT_PIXEL_FORMATS:
            text, tip = PIXEL_FORMAT_LABELS[key]
            self.sender_format.addItem(text, key)
            self.sender_format.setItemData(self.sender_format.count() - 1, tip, Qt.ToolTipRole)
        self.sender_format.setFixedWidth(180)
        publish.add_row("Format d'image", "UYVY 4:2:2 convient à la plupart des usages.", self.sender_format)
        col.addWidget(publish)

        advanced = Card("Avancé", "Moteur d'émission et horloge.")
        self.sender_native = Switch("Émettre par libomt")
        self.sender_native.setEnabled(omtlib.available())
        self.sender_native.toggled.connect(self.on_sender_engine_changed)
        advanced.add_row(
            "Émettre en direct par libomt (expérimental)",
            "Plus léger que FFmpeg, mais l'image est noire sur certaines machines. "
            "Désactivé, FFmpeg capture et publie l'écran (chemin éprouvé).",
            self.sender_native,
        )
        self.sender_clock = Switch("Horloge de sortie")
        advanced.add_row("Horloge de sortie (clock_output)", CLOCK_TOOLTIP.replace("\n", " "), self.sender_clock)
        col.addWidget(advanced)
        col.addStretch(1)

        self.sender_display.currentIndexChanged.connect(self.on_sender_changed)
        self.sender_name.editingFinished.connect(self.on_sender_changed)
        self.sender_fps.valueChanged.connect(self.on_sender_changed)
        self.sender_format.currentIndexChanged.connect(self.on_sender_changed)
        self.sender_clock.toggled.connect(self.on_sender_changed)
        return scroll

    def _source_field(self, placeholder: str, discover) -> tuple[QHBoxLayout, QLineEdit]:
        line = QHBoxLayout()
        line.setSpacing(6)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(self.on_monitor_changed)
        line.addWidget(edit, 1)
        find = button("Rechercher", icon="search", icon_color=theme.TEXT_DIM,
                      tooltip="Découvrir les sources OMT du réseau")
        find.clicked.connect(discover)
        line.addWidget(find)
        return line, edit

    def _build_monitor_page(self) -> QWidget:
        scroll, col = self._column_page()

        monitor, self.monitor_pill, self.monitor_btn, self.monitor_detail = self._service_card(
            "Retour audio", "Écoute le son d'une source OMT sur ce poste, sans image.", "headphones", "monitor",
            "Écouter", "volume",
        )
        self.monitor_btn.clicked.connect(self.toggle_monitor)
        line, self.monitor_source = self._source_field("MACHINE (Nom de la source)", self.discover_monitor_source)
        monitor.add_row("Source OMT", "La source à écouter.", line, stretch_control=True)
        self.monitor_volume = spin(0, 100, 100, suffix=" %", width=120)
        self.monitor_volume.valueChanged.connect(self.on_monitor_changed)
        monitor.add_row("Volume", "", self.monitor_volume)
        col.addWidget(monitor)

        # Floating video return: a small window posed on the desktop.
        floating, self.float_pill, self.float_btn, self.float_detail = self._service_card(
            "Fenêtre de retour", "Petite fenêtre vidéo posée sur le bureau.", "pip", "floating",
            "Ouvrir", "play",
        )
        self.float_btn.clicked.connect(self.toggle_floating)
        line, self.float_source = self._source_field("Vide = celle du retour audio", self.discover_float_source)
        floating.add_row("Source OMT", "Vide : la source du retour audio.", line, stretch_control=True)
        size = QHBoxLayout()
        size.setSpacing(8)
        self.float_width = spin(160, 7680, 640, suffix=" px", width=100)
        self.float_width.valueChanged.connect(self.on_monitor_changed)
        size.addWidget(self.float_width)
        size.addWidget(label("×", "Muted"))
        self.float_height = spin(90, 4320, 360, suffix=" px", width=100)
        self.float_height.valueChanged.connect(self.on_monitor_changed)
        size.addWidget(self.float_height)
        floating.add_row("Taille", "Largeur × hauteur de la fenêtre.", size)
        self.float_volume = spin(0, 100, 0, suffix=" %", width=120)
        self.float_volume.valueChanged.connect(self.on_monitor_changed)
        floating.add_row("Son", "0 % : fenêtre muette, le retour audio s'en charge.", self.float_volume)
        self.float_on_top = Switch("Toujours au-dessus")
        self.float_on_top.setChecked(True)
        self.float_on_top.toggled.connect(self.on_monitor_changed)
        floating.add_row("Toujours au-dessus", "La fenêtre reste devant les autres applications.", self.float_on_top)
        col.addWidget(floating)
        col.addStretch(1)
        return scroll

    def _build_routes_page(self) -> QWidget:
        self.routes_panel = RoutesPanel(self)
        return self._page_scroll(self.routes_panel)

    def _build_settings_page(self) -> QWidget:
        self.settings_page = SettingsPage(self)
        return self.settings_page

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
        self._fill_displays_list()

        exclude = self.config["excludePrimaryDisplay"]
        self.exclude_primary.blockSignals(True)
        self.exclude_primary.setChecked(exclude)
        self.exclude_primary.blockSignals(False)
        for stream_id, card in self.cards.items():
            card.set_displays(displays, self.config["mapping"].get(stream_id, ""), exclude)
        self._fill_sender_displays()
        if not force:
            self.engine.update(self.config, self.displays)

    def _fill_displays_list(self) -> None:
        """One line per screen: name, mode, and the streams shown on it."""
        selected = self.displays_list.currentItem().data(Qt.UserRole) if self.displays_list.currentItem() else None
        self.displays_list.clear()
        exclude = self.config["excludePrimaryDisplay"]
        names = {s["id"]: s["name"] for s in self.config["streams"]}
        for d in self.displays:
            shown = [names[sid] for sid, did in self.config["mapping"].items() if did == d["id"] and sid in names]
            details = [describe(d)]
            if d["isPrimary"]:
                details.append("principal" + (", exclu" if exclude else ""))
            details.append("Flux : " + ", ".join(shown) if shown else "aucun flux")
            text = f"{d['name']}\n" + "  ·  ".join(details)
            dim = d["isPrimary"] and exclude
            item = QListWidgetItem(theme.icon("screen", theme.TEXT_MUTED if dim else theme.ACCENT), text)
            item.setData(Qt.UserRole, d["id"])
            item.setToolTip(f"{d.get('systemName', d['name'])}\nPosition {d['x']}, {d['y']}\nID {d['id']}")
            self.displays_list.addItem(item)
            if d["id"] == selected:
                self.displays_list.setCurrentItem(item)
        if not self.displays:
            self.displays_list.addItem("Aucun écran détecté")

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

    def on_exclude_primary_changed(self, exclude: bool) -> None:
        self.config["excludePrimaryDisplay"] = bool(exclude)
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
        self._fill_displays_list()
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
        card.edit_requested.connect(self.edit_stream)
        card.set_editing(self.editing)
        self.cards[stream["id"]] = card
        return card

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self.cards_scroll.viewport():
            self._layout_cards()
        return super().eventFilter(obj, event)

    def _layout_cards(self, force: bool = False) -> None:
        width = self.cards_scroll.viewport().width()
        if self.editing:
            columns = max(1, min(2, width // 560, len(self.cards)))
        else:
            columns = max(1, min(4, width // 320, len(self.cards)))
        if columns == self._columns and not force:
            return
        for i in range(max(columns, self._columns)):
            self.cards_layout.setColumnStretch(i, int(i < columns))
        self._columns = columns
        for index, card in enumerate(self.cards.values()):
            self.cards_layout.removeWidget(card)
            self.cards_layout.addWidget(card, index // columns, index % columns)

    def _set_editing(self, editing: bool) -> None:
        if self.editing == editing:
            return
        self.editing = editing
        self.view_group.button(int(editing)).setChecked(True)
        for card in self.cards.values():
            card.set_editing(editing)
        self._layout_cards(force=True)
        self.config["streamView"] = "edit" if editing else "live"
        if not save_config(self.config):
            self.notify("Impossible d’enregistrer la vue choisie.")

    def _update_stream_count(self) -> None:
        count = len(self.config["streams"])
        self.streams_count.setText(f"{count} flux")
        self._layout_cards(force=True)
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
        self.edit_stream(card.stream_id)

    def edit_stream(self, stream_id: str) -> None:
        self.show_page("streams")
        self._set_editing(True)
        card = self.cards[stream_id]
        card.name_edit.setFocus()
        card.name_edit.selectAll()
        self.cards_scroll.ensureWidgetVisible(card.name_edit)

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
        self._fill_displays_list()
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
        self._fill_displays_list()
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
        for stream_id, card in self.cards.items():
            display = self._stream_display(stream_id)
            live = self.engine.stream_info(stream_id).status == Status.ACTIVE
            if card.thumb.isVisible():
                card.thumb.set_preview(grab_display(display, card.thumb.size()) if live and display else None)

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
        # (the « libomt » switch), not whether the library is there.
        native = omtlib.available() and sender.get("native", False)
        note = "Réglage propre à l'émission FFmpeg : libomt encode et cadence lui-même."
        for widget, tip in ((self.sender_format, ""), (self.sender_clock, CLOCK_TOOLTIP)):
            widget.setEnabled(not native)
            widget.setToolTip(note if native else tip)
        widgets = [self.sender_name, self.sender_fps, self.sender_format, self.sender_clock]
        native_switch = getattr(self, "sender_native", None)
        if native_switch is not None:
            widgets.append(native_switch)
        for w in widgets:
            w.blockSignals(True)
        self.sender_name.setText(sender["name"])
        self.sender_fps.setValue(sender["fps"])
        self.sender_format.setCurrentIndex(max(0, self.sender_format.findData(sender["pixelFormat"])))
        self.sender_clock.setChecked(sender["clockOutput"])
        if native_switch is not None:
            native_switch.setChecked(bool(sender.get("native", False)))
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

    def on_sender_engine_changed(self, native: bool) -> None:
        self.config["sender"]["native"] = bool(native)
        self.load_sender_fields()  # the emission path decides which fields apply
        self.commit()

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
        self.show_page("routes")

    def on_routes_changed(self, reload_cards: bool = False) -> None:
        if reload_cards:
            self.reload_cards()
        else:
            for card in self.cards.values():
                card.set_routes(self.config["routes"])
        self.schedule_commit()

    def refresh_routes_list(self) -> None:
        self.routes_panel.reload()

    # ------------------------------------------------------------ status

    def on_tick(self) -> None:
        self.engine.tick()
        active = 0
        statuses = []
        for stream_id, card in self.cards.items():
            info = self.engine.stream_info(stream_id)
            card.set_info(info, stream_id in self.engine.desired_streams)
            active += info.status == Status.ACTIVE
            statuses.append(info.status)

        total = len(self.config["streams"])
        streams_state = _worst(statuses)
        self.live_chip.set_status(Status.ACTIVE if active else Status.STOPPED, f"{active}/{total} en direct")
        self.nav_buttons["streams"].set_badge(f"{active}/{total}" if total else "", streams_state.value)
        count = len(self.displays)
        self.nav_buttons["screens"].set_badge(str(count) if count else "", "stopped")

        if self.engine.reception_running:
            set_button(self.reception_btn, "Arrêter la réception", "danger", "stop")
        else:
            set_button(self.reception_btn, "Démarrer la réception", "success", "play")

        sender = self.engine.sender_info()
        self._show_service(self.sender_pill, self.sender_detail, sender)
        if self.engine.sender_desired:
            set_button(self.sender_btn, "Arrêter", "danger", "stop")
        else:
            set_button(self.sender_btn, "Émettre", "success", "broadcast")
        self._badge("sender", [sender])

        monitor = self.engine.monitor_info()
        self._show_service(self.monitor_pill, self.monitor_detail, monitor)
        if self.engine.monitor_desired:
            set_button(self.monitor_btn, "Couper", "danger", "stop")
        else:
            set_button(self.monitor_btn, "Écouter", "success", "volume")

        floating = self.engine.floating_info()
        self._show_service(self.float_pill, self.float_detail, floating)
        if self.engine.floating_desired:
            set_button(self.float_btn, "Fermer", "danger", "stop")
        else:
            set_button(self.float_btn, "Ouvrir", "success", "play")
        self._badge("monitor", [monitor, floating])

        routes = self.config["routes"]
        route_infos = [self.engine.route_info(r["id"]) for r in routes]
        running = sum(1 for r in routes if r["id"] in self.engine.active_routes)
        if running:
            state = _worst([i.status for i in route_infos])
            self.nav_buttons["routes"].set_badge(f"{running}/{len(routes)}", state.value)
        else:
            self.nav_buttons["routes"].set_badge(str(len(routes)) if routes else "", "stopped")

    @staticmethod
    def _show_service(pill: StatusPill, detail: QLabel, info) -> None:
        pill.set_status(info.status)
        set_state(detail, info.status.value)
        detail.setText(info.detail)
        detail.setVisible(bool(info.detail))

    def _badge(self, key: str, infos: list) -> None:
        state = _worst([i.status for i in infos])
        text = "" if state == Status.STOPPED else STATUS_TEXT[state]
        self.nav_buttons[key].set_badge(text, state.value)

    def refresh_engine_summary(self) -> None:
        tools = self.engine.tools
        srt = tools.player_for("srt") is not None
        omt_ok = tools.player_for("omt") is not None and tools.ffmpeg_for("omt") is not None
        native_omt = omtlib.available()
        omt_text = "OMT ✓ (libomt)" if native_omt else f"OMT {'✓' if omt_ok else '✗'}"
        self.engine_label.setText(f"SRT {'✓' if srt else '✗'}   ·   {omt_text}")
        tips = [tc.describe() for tc in tools.toolchains] or ["Aucun FFmpeg trouvé"]
        tips.append(
            "libomt : émission et retour audio en direct"
            if native_omt
            else f"libomt indisponible, repli sur FFmpeg ({omtlib.load_error() or 'raison inconnue'})"
        )
        self.engine_label.setToolTip("\n".join(tips))
        if not srt:
            self.notify("⚠ Aucun FFmpeg avec SRT : la réception SRT et le routage sont indisponibles (voir Réglages).", 0)

    # ------------------------------------------------------------ settings / lifecycle

    def open_preferences(self) -> None:
        self.show_page("settings")
        self.settings_page.stack.setCurrentIndex(len(self.settings_page.TABS) - 1)
        self.settings_page.tab_group.button(len(self.settings_page.TABS) - 1).setChecked(True)

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
        self._set_editing(False)
        self.refresh_displays(force=True)
        self.reload_cards()
        self.load_sender_fields()
        self.load_monitor_fields()
        self.refresh_routes_list()
        self.settings_page.load()
        self.refresh_api()
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
        """Background check: only the navigation button shows up, and only for a new version."""
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


def _worst(statuses: list[Status]) -> Status:
    """The state that most deserves attention among several."""
    for status in _STATUS_RANK:
        if status in statuses:
            return status
    return Status.STOPPED
