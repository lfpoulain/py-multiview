"""Display enumeration with ids that stay stable across reboots and re-plugs.

On Windows the ids are derived from the monitor/adapter device ids reported by
``EnumDisplayDevicesW``, so a screen keeps its id even if its position or the
enumeration order changes. Coordinates are physical pixels of the virtual desktop.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import sys

log = logging.getLogger(__name__)

ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
DISPLAY_DEVICE_MIRRORING_DRIVER = 0x00000008


if sys.platform == "win32":
    from ctypes import wintypes

    class _POINTL(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _DEVMODE_DISPLAY(ctypes.Structure):
        _fields_ = [
            ("dmPosition", _POINTL),
            ("dmDisplayOrientation", wintypes.DWORD),
            ("dmDisplayFixedOutput", wintypes.DWORD),
        ]

    class _DEVMODE_UNION_1(ctypes.Union):
        _fields_ = [("dmOrientation", wintypes.SHORT), ("_display", _DEVMODE_DISPLAY)]

    class _DEVMODE_UNION_2(ctypes.Union):
        _fields_ = [("dmDisplayFlags", wintypes.DWORD), ("dmNup", wintypes.DWORD)]

    class _DEVMODEW(ctypes.Structure):
        _anonymous_ = ("u1", "u2")
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("u1", _DEVMODE_UNION_1),
            ("dmColor", wintypes.SHORT),
            ("dmDuplex", wintypes.SHORT),
            ("dmYResolution", wintypes.SHORT),
            ("dmTTOption", wintypes.SHORT),
            ("dmCollate", wintypes.SHORT),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("u2", _DEVMODE_UNION_2),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]


def _clean(value) -> str:
    return str(value or "").split("\x00", 1)[0].strip()


def stable_display_id(identity: str) -> str:
    raw = str(identity or "").strip() or "unknown"
    return "disp-" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _sorted(displays: list[dict]) -> list[dict]:
    ordered = sorted(
        displays,
        key=lambda d: (int(d.get("y") or 0), int(d.get("x") or 0), not d.get("isPrimary"), str(d.get("id"))),
    )
    for i, display in enumerate(ordered):
        display["index"] = i
    return ordered


def _enumerate_windows() -> list[dict]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_devices = user32.EnumDisplayDevicesW
    enum_devices.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wintypes.DWORD]
    enum_devices.restype = wintypes.BOOL
    enum_settings = user32.EnumDisplaySettingsExW
    enum_settings.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DEVMODEW), wintypes.DWORD]
    enum_settings.restype = wintypes.BOOL

    displays: list[dict] = []
    adapter_index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not enum_devices(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1

        flags = int(adapter.StateFlags or 0)
        if not flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP or flags & DISPLAY_DEVICE_MIRRORING_DRIVER:
            continue
        device_name = _clean(adapter.DeviceName)
        if not device_name:
            continue
        devmode = _DEVMODEW()
        devmode.dmSize = ctypes.sizeof(_DEVMODEW)
        if not enum_settings(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode), 0):
            continue

        is_primary = bool(flags & DISPLAY_DEVICE_PRIMARY_DEVICE)

        # First monitor attached to this adapter output that exposes an id.
        monitor_label = monitor_id = monitor_key = ""
        monitor_index = 0
        while True:
            monitor = _DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not enum_devices(device_name, monitor_index, ctypes.byref(monitor), 0):
                break
            monitor_index += 1
            label = _clean(monitor.DeviceString)
            if label and not monitor_label:
                monitor_label = label
            if _clean(monitor.DeviceID) or _clean(monitor.DeviceKey):
                monitor_id = _clean(monitor.DeviceID)
                monitor_key = _clean(monitor.DeviceKey)
                monitor_label = label or monitor_label
                break

        position = devmode._display.dmPosition
        width, height = int(devmode.dmPelsWidth), int(devmode.dmPelsHeight)
        parts = [monitor_id, monitor_key, _clean(adapter.DeviceID), _clean(adapter.DeviceKey)]
        identity = "|".join(p for p in parts if p) or (
            f"geom|{int(position.x)}|{int(position.y)}|{width}|{height}|{1 if is_primary else 0}"
        )

        displays.append(
            {
                "id": stable_display_id(identity),
                "name": monitor_label or _clean(adapter.DeviceString) or device_name,
                "width": width,
                "height": height,
                "x": int(position.x),
                "y": int(position.y),
                "refresh": int(devmode.dmDisplayFrequency or 0),
                "isPrimary": is_primary,
                "deviceName": device_name,
            }
        )
    return displays


def _enumerate_screeninfo() -> list[dict]:
    from screeninfo import get_monitors

    displays: list[dict] = []
    counts: dict[str, int] = {}
    for i, m in enumerate(get_monitors()):
        base = str(getattr(m, "name", "") or "").strip() or f"geom-{m.x}-{m.y}-{m.width}x{m.height}"
        counts[base] = counts.get(base, 0) + 1
        display_id = base if counts[base] == 1 else f"{base}-{counts[base]}"
        displays.append(
            {
                "id": display_id,
                "name": base if not base.startswith("geom-") else f"Écran {i + 1}",
                "width": int(m.width),
                "height": int(m.height),
                "x": int(m.x),
                "y": int(m.y),
                "refresh": 0,
                "isPrimary": bool(getattr(m, "is_primary", False)),
                "deviceName": str(getattr(m, "name", "") or ""),
            }
        )
    return displays


def list_displays(name_overrides: dict[str, str] | None = None) -> list[dict]:
    """All attached displays, sorted top-to-bottom then left-to-right.

    Each entry carries ``id``, ``name`` (user override applied), ``systemName``,
    geometry (``x``, ``y``, ``width``, ``height``), ``isPrimary`` and ``index``.
    """
    displays: list[dict] = []
    if sys.platform == "win32":
        try:
            displays = _enumerate_windows()
        except Exception:
            log.exception("Énumération Win32 des écrans impossible, repli sur screeninfo")
    if not displays:
        try:
            displays = _enumerate_screeninfo()
        except Exception:
            log.exception("Énumération des écrans impossible")
            displays = []

    overrides = name_overrides or {}
    for display in displays:
        display["systemName"] = display["name"]
        custom = overrides.get(display["id"])
        if custom:
            display["name"] = custom
    return _sorted(displays)


def describe(display: dict) -> str:
    text = f"{display['width']}×{display['height']}"
    if display.get("refresh"):
        text += f" @{display['refresh']} Hz"
    return text
