"""Picker for the sound of the OMT emission.

One flat list of every device and every program, twice over, was long and
hard to read. Here the sources are sorted by what the operator is thinking
of — what the PC plays, one program, an input — with a search box, and the
choice says in plain words what will be sent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import audio, proc_audio
from . import theme
from .widgets import button, label

OUTPUTS, PROGRAMS, INPUTS = "outputs", "programs", "inputs"
CATEGORIES = (
    (OUTPUTS, "Son du PC", "screen"),
    (PROGRAMS, "Un programme", "wand"),
    (INPUTS, "Une entrée", "broadcast"),
)


@dataclass(frozen=True)
class Choice:
    label: str
    source: str  # value stored in the configuration; "" for a program row (see ``program``)
    hint: str
    program: str = ""  # executable, for the rows of the "program" category


@dataclass
class Catalog:
    outputs: list[str]
    inputs: list[str]
    dshow: list[str]
    programs: list[tuple[str, str]]  # (executable, window title)


def load_catalog(ffmpeg, native: bool) -> Catalog:
    """Everything that can be picked (blocking: PortAudio and one ffmpeg call)."""
    programs: dict[str, str] = {}
    for program in proc_audio.list_programs():
        programs.setdefault(program.name, program.title)
    return Catalog(
        outputs=audio.list_output_devices(),
        inputs=[d.name for d in audio.list_input_devices()],
        # DirectShow devices only matter to the FFmpeg sender, and only the
        # ones Windows does not also expose as a regular input.
        dshow=[] if native else audio.list_dshow_audio(ffmpeg),
        programs=sorted(programs.items(), key=lambda item: item[0].lower()),
    )


def build_choices(catalog: Catalog) -> dict[str, list[Choice]]:
    outputs = [Choice(
        "Tout ce que joue le PC",
        audio.LOOPBACK,
        "La sortie audio par défaut de Windows, quelle qu'elle soit au moment d'émettre.",
    )]
    outputs += [
        Choice(name, audio.OUTPUT_PREFIX + name, f"Uniquement ce qui sort sur « {name} », même si ce n'est pas la sortie par défaut.")
        for name in catalog.outputs
    ]
    programs = [
        Choice(f"{exe}  —  {title}" if title and title.lower() != exe.lower() else exe, "", "", program=exe)
        for exe, title in catalog.programs
    ]
    inputs = [
        Choice(name, audio.INPUT_PREFIX + name, "Micro, carte son ou câble virtuel : ce que cette entrée reçoit.")
        for name in catalog.inputs
    ]
    known = {name.lower() for name in catalog.inputs}
    inputs += [
        Choice(f"{name}  (DirectShow)", audio.DSHOW_PREFIX + name,
               "Périphérique DirectShow (vMix Audio, capture virtuelle…), ouvert directement par FFmpeg.")
        for name in catalog.dshow
        if name.lower() not in known
    ]
    return {OUTPUTS: outputs, PROGRAMS: programs, INPUTS: inputs}


def category_of(source: str) -> str:
    if audio.is_program(source):
        return PROGRAMS
    if source.startswith((audio.INPUT_PREFIX, audio.DSHOW_PREFIX)):
        return INPUTS
    return OUTPUTS


def program_source(executable: str, exclude: bool) -> str:
    return (audio.EXCEPT_PREFIX if exclude else audio.PROGRAM_PREFIX) + executable


def matches(choice: Choice, source: str) -> bool:
    """True when ``choice`` is what ``source`` names (a saved ``dshow:`` input matches its ``in:`` twin)."""
    if choice.program:
        return audio.is_program(source) and source.split(":", 1)[1].lower() == choice.program.lower()
    if choice.source == source:
        return True
    both_inputs = all(s.startswith((audio.INPUT_PREFIX, audio.DSHOW_PREFIX)) for s in (choice.source, source))
    return both_inputs and choice.source.split(":", 1)[1] == source.split(":", 1)[1]


class AudioSourceDialog(QDialog):
    loaded = Signal(object)

    def __init__(self, parent: QWidget, current: str, ffmpeg, native: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Son de l'émission")
        self.resize(640, 480)
        self.selected_source = current
        self._current = current
        self._ffmpeg = ffmpeg
        self._native = native
        self._choices: dict[str, list[Choice]] = {OUTPUTS: [], PROGRAMS: [], INPUTS: []}
        self._category = category_of(current)
        self._exclude = current.startswith(audio.EXCEPT_PREFIX)
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un périphérique ou un programme…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)
        self.refresh_btn = button(variant="icon", icon="refresh", icon_color=theme.TEXT_DIM, tooltip="Actualiser la liste")
        self.refresh_btn.clicked.connect(self._load)
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self._tabs = QButtonGroup(self)
        self._tab_buttons: dict[str, object] = {}
        for key, text, icon in CATEGORIES:
            tab = button(text, variant="tab", icon=icon, icon_color=theme.TEXT_DIM)
            tab.setCheckable(True)
            tab.clicked.connect(lambda _checked=False, k=key: self._set_category(k))
            self._tabs.addButton(tab)
            self._tab_buttons[key] = tab
            tabs.addWidget(tab)
        tabs.addStretch(1)
        layout.addLayout(tabs)

        # Only for programs: send that program alone, or everything but it.
        self.mode_row = QWidget()
        self.mode_row.setObjectName("Transparent")
        mode = QHBoxLayout(self.mode_row)
        mode.setContentsMargins(0, 0, 0, 0)
        mode.setSpacing(6)
        mode.addWidget(label("Envoyer", "FieldLabel"))
        self._modes = QButtonGroup(self)
        self.only_btn = button("Ce programme seul", variant="tab")
        self.except_btn = button("Tout le PC sauf lui", variant="tab")
        for btn, exclude in ((self.only_btn, False), (self.except_btn, True)):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, e=exclude: self._set_exclude(e))
            self._modes.addButton(btn)
            mode.addWidget(btn)
        self.except_btn.setToolTip("Pratique pour ne pas renvoyer à la régie son propre retour, ou le son d'un outil.")
        mode.addStretch(1)
        layout.addWidget(self.mode_row)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.list.currentItemChanged.connect(lambda *_: self._refresh_hint())
        layout.addWidget(self.list, 1)

        self.hint = label("", "Muted", wrap=True)
        self.hint.setMinimumHeight(34)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        mute = button("Émettre sans son", variant="ghost")
        mute.clicked.connect(self._mute)
        buttons.addWidget(mute)
        buttons.addStretch(1)
        cancel = button("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok = button("Choisir", variant="primary")
        self.ok.clicked.connect(self._accept)
        buttons.addWidget(self.ok)
        layout.addLayout(buttons)

        self.loaded.connect(self._on_loaded)
        self._sync_controls()
        self._load()

    # ------------------------------------------------------------ loading

    def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_btn.setEnabled(False)
        self._fill()
        ffmpeg, native = self._ffmpeg, self._native

        def work() -> None:
            try:
                catalog = load_catalog(ffmpeg, native)
            except Exception:
                catalog = Catalog([], [], [], [])
            self.loaded.emit(catalog)

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, catalog: Catalog) -> None:
        self._loading = False
        self.refresh_btn.setEnabled(True)
        self.set_catalog(catalog)

    def set_catalog(self, catalog: Catalog) -> None:
        self._choices = build_choices(catalog)
        self._sync_controls()
        self._fill()

    # ------------------------------------------------------------ state

    def _set_category(self, key: str) -> None:
        self._category = key
        self._sync_controls()
        self._fill()

    def _set_exclude(self, exclude: bool) -> None:
        self._exclude = exclude
        self._sync_controls()
        self._refresh_hint()

    def _on_search(self, text: str) -> None:
        # A search that finds nothing here but something elsewhere moves there.
        if text.strip() and not self._visible(self._category):
            for key, _text, _icon in CATEGORIES:
                if self._visible(key):
                    self._category = key
                    break
        self._sync_controls()
        self._fill()

    def _visible(self, key: str) -> list[Choice]:
        needle = self.search.text().strip().lower()
        return [c for c in self._choices[key] if needle in c.label.lower()]

    def _sync_controls(self) -> None:
        for key, text, _icon in CATEGORIES:
            tab = self._tab_buttons[key]
            tab.setChecked(key == self._category)
            count = len(self._visible(key))
            tab.setText(f"{text}  {count}" if count else text)
        self.mode_row.setVisible(self._category == PROGRAMS)
        self.only_btn.setChecked(not self._exclude)
        self.except_btn.setChecked(self._exclude)

    def _fill(self) -> None:
        self.list.clear()
        choices = self._visible(self._category)
        if self._loading and not choices:
            self._placeholder("Recherche des sources audio…")
        elif not choices:
            self._placeholder({
                OUTPUTS: "Aucune sortie audio trouvée.",
                PROGRAMS: "Aucun programme avec une fenêtre ouverte." if not self.search.text().strip()
                else "Aucun programme ne correspond.",
                INPUTS: "Aucune entrée audio trouvée.",
            }[self._category])
        for choice in choices:
            item = QListWidgetItem(choice.label, self.list)
            item.setData(Qt.UserRole, choice)
            if matches(choice, self._current):
                item.setIcon(theme.icon("check", theme.SUCCESS))
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and choices:
            self.list.setCurrentRow(0)
        self._refresh_hint()

    def _placeholder(self, text: str) -> None:
        item = QListWidgetItem(text, self.list)
        item.setFlags(Qt.NoItemFlags)

    def _choice(self) -> Choice | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def chosen_source(self) -> str:
        choice = self._choice()
        if choice is None:
            return ""
        return program_source(choice.program, self._exclude) if choice.program else choice.source

    def _refresh_hint(self) -> None:
        choice = self._choice()
        self.ok.setEnabled(choice is not None)
        if choice is None:
            self.hint.setText("")
        elif choice.program:
            self.hint.setText(
                f"Tout ce que joue le PC, sauf « {choice.program} ». S'il ne tourne pas, tout le son du PC est envoyé."
                if self._exclude else
                f"Uniquement le son de « {choice.program} ». S'il est fermé, l'image part sans son "
                "et le son reprend dès qu'il est relancé."
            )
        else:
            self.hint.setText(choice.hint)

    # ------------------------------------------------------------ result

    def _accept(self) -> None:
        if self._choice() is None:
            return
        self.selected_source = self.chosen_source()
        self.accept()

    def _mute(self) -> None:
        self.selected_source = ""
        self.accept()
