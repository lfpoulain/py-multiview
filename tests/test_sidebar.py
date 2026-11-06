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
