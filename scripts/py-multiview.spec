# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — build with:  python -m PyInstaller scripts/py-multiview.spec
# Produces a folder build (dist/py-multiview/): starts instantly, unlike a
# one-file exe that would unpack ~300 MB of FFmpeg on every launch.

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

root = Path(SPECPATH).resolve().parent
version = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "py_multiview" / "__init__.py").read_text(encoding="utf-8-sig")).group(1)
numbers = [int(n) for n in re.findall(r"\d+", version)[:3]]
numbers = tuple((numbers + [0, 0, 0, 0])[:4])

required = [root / "bin" / "omt" / name for name in ("ffmpeg.exe", "ffplay.exe", "libomt.dll", "libvmx.dll")]
required += [root / "bin" / "standard" / name for name in ("ffmpeg.exe", "ffplay.exe")]
missing = [str(p.relative_to(root)) for p in required if not p.exists()]
if missing:
    raise SystemExit("Binaires manquants : " + ", ".join(missing) + " (voir scripts/fetch-ffmpeg.ps1)")

datas = [
    (str(root / "img" / "icon.ico"), "img"),
    (str(root / "img" / "icon.png"), "img"),
    (str(root / "bin" / "omt"), "bin/omt"),
    (str(root / "bin" / "standard"), "bin/standard"),
]

a = Analysis(
    [str(root / "py_multiview" / "__main__.py")],
    pathex=[str(root)],
    datas=datas,
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040C04B0",
                    [
                        StringStruct("CompanyName", "LFPoulain"),
                        StringStruct("FileDescription", "py-multiview"),
                        StringStruct("FileVersion", version),
                        StringStruct("InternalName", "py-multiview"),
                        StringStruct("LegalCopyright", "© LFPoulain — CC BY-NC-SA 4.0"),
                        StringStruct("OriginalFilename", "py-multiview.exe"),
                        StringStruct("ProductName", "py-multiview"),
                        StringStruct("ProductVersion", version),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="py-multiview",
    console=False,
    upx=False,  # UPX breaks Qt DLLs and triggers antivirus false positives
    icon=str(root / "img" / "icon.ico"),
    version=version_info,
)

coll = COLLECT(exe, a.binaries, a.datas, name="py-multiview", upx=False)
