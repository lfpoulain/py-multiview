import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from py_multiview.config import normalize_config  # noqa: E402
from py_multiview.ui.widgets import Card, ElidedLabel, Switch  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_interface_settings_are_normalized():
    config = normalize_config({"sidebar": {"sender": True}, "appearance": "fluo", "streamView": "x"})
    assert "sidebar" not in config  # folded blocks of the former side column
    assert config["appearance"] == "light"
    assert config["streamView"] == "live"
    assert config["page"] == "streams"
    config = normalize_config({"appearance": "dark", "page": "routes", "streamView": "edit"})
    assert (config["appearance"], config["page"], config["streamView"]) == ("dark", "routes", "edit")


def test_switch_behaves_like_a_checkbox(qapp):
    seen = []
    switch = Switch("Toujours au-dessus")
    switch.toggled.connect(seen.append)
    switch.show()
    switch.click()
    assert switch.isChecked() and seen == [True]
    assert switch.accessibleName() == "Toujours au-dessus" and switch.text() == ""


def test_card_rows_are_separated(qapp):
    card = Card("Émission", "sous-titre", "broadcast")
    card.add_row("Nom", "description", QLabel("x"))
    card.add_row("Cadence", "", QLabel("y"))
    # row, divider, row
    assert card.body.count() == 3


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
