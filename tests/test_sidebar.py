import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from py_multiview.config import normalize_config  # noqa: E402
from py_multiview.ui.widgets import CollapsibleSection, ElidedLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_sidebar_state_keeps_only_booleans():
    config = normalize_config({"sidebar": {"sender": True, "routes": "oui", "screens": False}})
    assert config["sidebar"] == {"sender": True, "screens": False}  # "oui" is not a state
    assert normalize_config({"sidebar": "nope"})["sidebar"] == {}
    assert normalize_config({})["sidebar"] == {}


def test_section_folds_to_its_header(qapp):
    section = CollapsibleSection("Émission", "sender", expanded=False)
    body = QLabel("contenu")
    section.body.addWidget(body)
    section.show()
    assert not section.content.isVisible()
    section.toggle()
    assert section.is_expanded() and section.content.isVisible()
    section.toggle()
    assert not section.content.isVisible()


def test_section_reports_user_toggles_but_not_restores(qapp):
    section = CollapsibleSection("Retour", "monitor", expanded=False)
    seen = []
    section.toggled.connect(seen.append)
    section.set_expanded(True)  # the user opened it
    section.set_expanded(False, notify=False)  # restoring a saved state must stay silent
    section.set_expanded(False, notify=False)
    assert seen == [True]


def test_header_keeps_the_action_and_summary_when_folded(qapp):
    from PySide6.QtWidgets import QPushButton

    section = CollapsibleSection("Émission", "sender", expanded=False)
    action = QPushButton("Émettre")
    section.add_action(action)
    section.set_summary("Écran 1  ·  25 i/s", "waiting")
    section.show()
    assert action.isVisible()
    assert section.summary.full_text() == "Écran 1  ·  25 i/s"
    assert section.summary.property("state") == "waiting"


def test_summary_is_cut_instead_of_widening_the_column(qapp):
    label = ElidedLabel()
    label.resize(120, 20)
    label.show()
    label.set_full_text("Capture du son du PC indisponible (aucune sortie audio par défaut)")
    assert label.text().endswith("…") and len(label.text()) < len(label.full_text())
    assert label.toolTip() == label.full_text()  # the full message stays reachable
    label.set_full_text("court")
    assert label.text() == "court" and label.toolTip() == ""


def test_switching_stream_views_preserves_settings_and_live_controls(qapp):
    from py_multiview.config import default_stream
    from py_multiview.ui.stream_card import StreamCard

    stream = default_stream(1, 9102)
    card = StreamCard(stream)
    card.set_host_ip("192.0.2.24")
    card.show()
    changes, edits = [], []
    card.changed.connect(changes.append)
    card.edit_requested.connect(edits.append)
    assert card.thumb.isVisible() and not card.delete_btn.isVisible()
    assert "srt://192.0.2.24:9102" in card.source_summary.full_text()
    card.edit_btn.click()
    assert edits == [stream["id"]]
    card.set_editing(True)
    assert card.editor.isVisible() and card.name_edit.isVisible() and not card.thumb.isVisible()
    assert not changes
    card.source_combo.setCurrentIndex(card.source_combo.findData("omt"))
    card.set_omt_source("REGIE (Master)")
    changes.clear()
    card.set_editing(False)
    assert "REGIE (Master)" in card.source_summary.full_text()
    assert card.toggle_btn.isVisible() and card.logs_btn.isVisible()
    assert not changes
    card.close()


def test_output_clock_is_greyed_only_when_libomt_really_emits(qapp, monkeypatch):
    # The bug this guards against: the box was greyed as soon as libomt could
    # be loaded, although FFmpeg -- the default path, the one it drives -- was
    # the one emitting. Nobody could turn the clock on any more.
    from types import SimpleNamespace

    from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox

    from py_multiview.ui import main_window

    monkeypatch.setattr(main_window.omtlib, "available", lambda: True)
    window = SimpleNamespace(
        config=normalize_config({}),
        sender_name=QLineEdit(), sender_fps=QSpinBox(), sender_format=QComboBox(), sender_clock=QCheckBox(),
        _fill_sender_displays=lambda: None, _show_sender_audio=lambda: None,
    )
    main_window.MainWindow.load_sender_fields(window)
    assert window.sender_clock.isEnabled() and window.sender_clock.isChecked()
    assert window.sender_format.isEnabled()
    assert window.sender_clock.toolTip() == main_window.CLOCK_TOOLTIP

    window.config["sender"]["native"] = True  # Préférences → Émettre en direct par libomt
    main_window.MainWindow.load_sender_fields(window)
    assert not window.sender_clock.isEnabled() and not window.sender_format.isEnabled()
    assert "libomt" in window.sender_clock.toolTip()

    window.config["sender"]["native"] = False
    main_window.MainWindow.load_sender_fields(window)
    assert window.sender_clock.isEnabled()
