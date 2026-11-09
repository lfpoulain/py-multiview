import hashlib
import io
import threading
import zipfile

import pytest

from py_multiview import updater
from py_multiview.config import normalize_config

RELEASE_JSON = {
    "tag_name": "v0.3.12",
    "html_url": "https://github.com/lfpoulain/py-multiview/releases/tag/v0.3.12",
    "body": "## Télécharger\n\n- Installeur : [setup](x)\n\n## What's Changed\n* fix by someone",
    "assets": [
        {
            "name": "py-multiview-setup.exe",
            "browser_download_url": "https://example.invalid/setup.exe",
            "size": 10,
            "digest": "sha256:ABCDEF",
        },
        {"name": "py-multiview-portable.zip", "browser_download_url": "https://example.invalid/p.zip", "size": 20},
        {"name": "broken"},
    ],
}


def test_versions():
    assert updater.parse_version("v0.3.12") == (0, 3, 12)
    assert updater.parse_version("0.3") == updater.parse_version("0.3.0")
    assert updater.parse_version("1.2.3-beta") == (1, 2, 3)
    assert updater.parse_version("nope") == ()
    assert updater.is_newer("0.3.12", "0.3.9")
    assert updater.is_newer("0.4.1", "0.3.99")
    assert not updater.is_newer("0.3.9", "0.3.9")
    assert not updater.is_newer("0.3.8", "0.3.9")
    assert not updater.is_newer("garbage", "0.3.9")


def test_parse_release():
    release = updater.parse_release(RELEASE_JSON)
    assert release.version == "0.3.12"
    assert set(release.assets) == {"py-multiview-setup.exe", "py-multiview-portable.zip"}
    assert release.assets["py-multiview-setup.exe"].sha256 == "abcdef"
    assert release.assets["py-multiview-portable.zip"].sha256 == ""
    assert updater.asset_for(release, updater.INSTALLER).name == "py-multiview-setup.exe"
    assert updater.asset_for(release, updater.PORTABLE).name == "py-multiview-portable.zip"
    assert updater.asset_for(release, updater.SOURCE) is None
    with pytest.raises(updater.UpdateError):
        updater.parse_release({"message": "Not Found"})


def test_release_notes_drop_download_block():
    notes = updater.release_notes(RELEASE_JSON["body"])
    assert "Télécharger" not in notes
    assert notes.startswith("## What's Changed")


def test_install_kind(tmp_path):
    assert updater.install_kind(tmp_path, frozen=False) == updater.SOURCE
    assert updater.install_kind(tmp_path, frozen=True) == updater.PORTABLE
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert updater.install_kind(tmp_path, frozen=True) == updater.INSTALLER


def test_update_settings_are_normalized():
    assert normalize_config({})["updates"] == {"autoCheck": True, "skipped": ""}
    assert normalize_config({"updates": {"autoCheck": 0, "skipped": " 0.3.4 "}})["updates"] == {
        "autoCheck": False,
        "skipped": "0.3.4",
    }


class _FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _serve(monkeypatch, payload: bytes):
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload))


def test_download_checks_digest(tmp_path, monkeypatch):
    payload = b"x" * 1000
    _serve(monkeypatch, payload)
    good = updater.Asset("a.exe", "https://example.invalid/f", len(payload), hashlib.sha256(payload).hexdigest())
    seen = []
    path = updater.download(good, tmp_path, progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == payload and seen[-1] == len(payload)

    bad = updater.Asset("b.exe", "https://example.invalid/f", len(payload), "0" * 64)
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download(bad, tmp_path)
    assert not (tmp_path / "b.exe").exists() and not (tmp_path / "b.exe.part").exists()

    short = updater.Asset("c.exe", "https://example.invalid/f", 5000)
    with pytest.raises(updater.UpdateError, match="incomplet"):
        updater.download(short, tmp_path)


def test_download_can_be_cancelled(tmp_path, monkeypatch):
    _serve(monkeypatch, b"x" * 10)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(updater.CancelledError):
        updater.download(updater.Asset("d.exe", "https://example.invalid/f", 10), tmp_path, cancel=cancel)
    assert list(tmp_path.iterdir()) == []


def test_extract_portable(tmp_path):
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("py-multiview/py-multiview.exe", b"exe")
        zf.writestr("py-multiview/_internal/x.dll", b"dll")
    folder = updater.extract_portable(archive, tmp_path / "out")
    assert folder == tmp_path / "out" / "py-multiview"

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", b"no")
    with pytest.raises(updater.UpdateError):
        updater.extract_portable(evil, tmp_path / "out2")
    assert not (tmp_path / "escape.txt").exists()


def test_portable_script(tmp_path):
    script = updater.portable_script(1234, tmp_path / "new", tmp_path / "app", tmp_path / "stage")
    assert '"PID eq 1234"' in script
    assert f'"{tmp_path / "new" / "_internal"}" "{tmp_path / "app" / "_internal"}" /MIR' in script
    assert f'start "" "{tmp_path / "app" / "py-multiview.exe"}"' in script
    assert script.index("/MIR") < script.index("start ")
