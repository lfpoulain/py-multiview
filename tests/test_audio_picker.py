import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from py_multiview.config import normalize_config  # noqa: E402
from py_multiview.ui import audio_picker as ap  # noqa: E402
from py_multiview.ui.widgets import LevelMeter  # noqa: E402

CATALOG = ap.Catalog(
    outputs=["Haut-parleurs (Focusrite)", "Casque (USB)"],
    inputs=["Microphone (C920)", "CABLE Output (VB-Audio)"],
    dshow=["Microphone (C920)", "vMix Audio - Bus A"],
    programs=[("chrome.exe", "Google Chrome"), ("vMix64.exe", "vMix64.exe")],
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp, monkeypatch):
    # No device enumeration in tests: the catalog is handed over.
    monkeypatch.setattr(ap.AudioSourceDialog, "_load", lambda self: None)

    def make(current):
        dlg = ap.AudioSourceDialog(None, current, None, False)
        dlg.set_catalog(CATALOG)
        return dlg

    return make


def test_choices_are_sorted_by_kind():
    choices = ap.build_choices(CATALOG)
    assert [c.source for c in choices[ap.OUTPUTS]] == [
        "loopback", "out:Haut-parleurs (Focusrite)", "out:Casque (USB)",
    ]
    # A DirectShow device Windows also lists as an input appears once.
    assert [c.source for c in choices[ap.INPUTS]] == [
        "in:Microphone (C920)", "in:CABLE Output (VB-Audio)", "dshow:vMix Audio - Bus A",
    ]
    programs = choices[ap.PROGRAMS]
    assert [c.program for c in programs] == ["chrome.exe", "vMix64.exe"]
    assert programs[0].label == "chrome.exe  \u2014  Google Chrome" and programs[1].label == "vMix64.exe"


def test_saved_source_finds_its_row():
    choices = ap.build_choices(CATALOG)
    mic = choices[ap.INPUTS][0]
    assert ap.matches(mic, "in:Microphone (C920)")
    assert ap.matches(mic, "dshow:Microphone (C920)")  # saved by an older version
    assert not ap.matches(mic, "out:Microphone (C920)")
    chrome = choices[ap.PROGRAMS][0]
    assert ap.matches(chrome, "app:chrome.exe") and ap.matches(chrome, "notapp:Chrome.exe")
    assert ap.category_of("notapp:x.exe") == ap.PROGRAMS
    assert ap.category_of("dshow:vMix Audio") == ap.INPUTS
    assert ap.category_of("loopback") == ap.category_of("out:Casque") == ap.category_of("") == ap.OUTPUTS


def test_dialog_opens_on_the_current_source(dialog):
    dlg = dialog("notapp:vMix64.exe")
    assert not dlg.mode_row.isHidden() and dlg.except_btn.isChecked()
    assert dlg.chosen_source() == "notapp:vMix64.exe"
    dlg.only_btn.click()
    assert dlg.chosen_source() == "app:vMix64.exe"
    dlg._accept()
    assert dlg.selected_source == "app:vMix64.exe"


def test_dialog_search_jumps_to_the_matching_kind(dialog):
    dlg = dialog("loopback")
    assert dlg.mode_row.isHidden() and dlg.chosen_source() == "loopback"
    dlg.search.setText("vmix audio")
    assert dlg._category == ap.INPUTS
    assert dlg.chosen_source() == "dshow:vMix Audio - Bus A"
    dlg.search.setText("rien de tel")
    assert dlg.chosen_source() == "" and not dlg.ok.isEnabled()


def test_dialog_can_mute(dialog):
    dlg = dialog("in:Microphone (C920)")
    assert dlg.chosen_source() == "in:Microphone (C920)"
    dlg._mute()
    assert dlg.selected_source == ""


def test_level_meter_scale():
    assert LevelMeter.position(0.0) == 0.0
    assert LevelMeter.position(1.0) == 1.0
    assert LevelMeter.position(2.0) == 1.0
    assert abs(LevelMeter.position(0.001) - 0.0) < 1e-9  # -60 dB is the floor
    assert abs(LevelMeter.position(0.1) - (1 - 20 / 60)) < 1e-9  # -20 dB


def test_close_button_minimises_by_default():
    assert normalize_config({})["closeToMinimize"] is True
    assert normalize_config({"closeToMinimize": False})["closeToMinimize"] is False
