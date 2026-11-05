"""Screen capture for the native OMT sender.

A plain GDI BitBlt into a DIB section, which is what ``ffmpeg -f gdigrab``
does under the hood. The DIB is created once and reused, so a frame costs
one blit: the bytes are already BGRA, the pixel format OMT takes directly.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import c_int, wintypes

log = logging.getLogger(__name__)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
CURSOR_SHOWING = 0x00000001
DI_NORMAL = 0x0003

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


def _prototypes(user32: ctypes.WinDLL, gdi32: ctypes.WinDLL) -> None:
    """Declare every call we make.

    Without this, ctypes assumes a 32-bit int for handles and either
    truncates them or raises "int too long to convert" on a 64-bit build.
    """
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.restype = c_int
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.DrawIconEx.restype = wintypes.BOOL
    user32.DrawIconEx.argtypes = [
        wintypes.HDC, c_int, c_int, wintypes.HANDLE, c_int, c_int,
        wintypes.UINT, wintypes.HANDLE, wintypes.UINT,
    ]

    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        wintypes.HDC, c_int, c_int, c_int, c_int, wintypes.HDC, c_int, c_int, wintypes.DWORD,
    ]


class ScreenCapture:
    """Repeated capture of one screen rectangle as BGRA (top-down)."""

    def __init__(self, x: int, y: int, width: int, height: int, draw_mouse: bool = True) -> None:
        self.x, self.y = int(x), int(y)
        self.width, self.height = int(width), int(height)
        self.draw_mouse = draw_mouse
        self.stride = self.width * 4
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _prototypes(self._user32, self._gdi32)
        # Physical pixels, like the Win32 screen enumeration the geometry comes from.
        self._user32.SetProcessDPIAware()
        self.clamped = self._clamp_to_desktop()

        self._screen_dc = self._user32.GetDC(None)
        if not self._screen_dc:
            raise OSError("GetDC a échoué")
        self._mem_dc = self._gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._mem_dc:
            self._release()
            raise OSError("CreateCompatibleDC a échoué")

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self.width
        # Negative height: top-down rows, the order OMT expects.
        info.bmiHeader.biHeight = -self.height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        self._bits = ctypes.c_void_p()
        self._bitmap = self._gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(self._bits), None, 0
        )
        if not self._bitmap or not self._bits:
            self._release()
            raise OSError("CreateDIBSection a échoué")
        self._old = self._gdi32.SelectObject(self._mem_dc, self._bitmap)
        self.size = self.stride * self.height

    def _clamp_to_desktop(self) -> str:
        """Keep the rectangle inside the virtual desktop.

        With mixed DPI scaling, the position reported for a screen can land
        slightly outside it; BitBlt then copies nothing and every frame comes
        out black. Returns a description when the rectangle had to move.
        """
        vx = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            return ""
        before = (self.x, self.y, self.width, self.height)
        self.width = max(2, min(self.width, vw))
        self.height = max(2, min(self.height, vh))
        self.x = max(vx, min(self.x, vx + vw - self.width))
        self.y = max(vy, min(self.y, vy + vh - self.height))
        self.stride = self.width * 4
        if before == (self.x, self.y, self.width, self.height):
            return ""
        return (
            f"zone {before[2]}x{before[3]}+{before[0]}+{before[1]} hors du bureau "
            f"({vw}x{vh}+{vx}+{vy}), ramenée à {self.width}x{self.height}+{self.x}+{self.y}"
        )

    def looks_black(self, samples: int = 400) -> bool:
        """True when the last grabbed frame carries no visible light.

        GDI cannot read back content drawn by some hardware-accelerated or
        protected windows, which comes out as a black rectangle.
        """
        if not self._bits:
            return True
        step_y = max(1, self.height // 20)
        step_x = max(1, self.width // max(1, samples // 20))
        data = ctypes.cast(self._bits, ctypes.POINTER(ctypes.c_ubyte))
        for y in range(0, self.height, step_y):
            row = y * self.stride
            for x in range(0, self.width, step_x):
                o = row + x * 4
                if data[o] > 8 or data[o + 1] > 8 or data[o + 2] > 8:
                    return False
        return True

    def grab(self) -> ctypes.c_void_p | None:
        """Blit the screen into the DIB; returns the pixel pointer (not a copy)."""
        ok = self._gdi32.BitBlt(
            self._mem_dc, 0, 0, self.width, self.height,
            self._screen_dc, self.x, self.y, SRCCOPY | CAPTUREBLT,
        )
        if not ok:
            return None
        if self.draw_mouse:
            self._blit_cursor()
        return self._bits

    def _blit_cursor(self) -> None:
        cursor = CURSORINFO()
        cursor.cbSize = ctypes.sizeof(CURSORINFO)
        if not self._user32.GetCursorInfo(ctypes.byref(cursor)):
            return
        if not (cursor.flags & CURSOR_SHOWING) or not cursor.hCursor:
            return
        icon = ICONINFO()
        if not self._user32.GetIconInfo(cursor.hCursor, ctypes.byref(icon)):
            return
        try:
            left = cursor.ptScreenPos.x - self.x - icon.xHotspot
            top = cursor.ptScreenPos.y - self.y - icon.yHotspot
            if -64 <= left <= self.width and -64 <= top <= self.height:
                self._user32.DrawIconEx(self._mem_dc, left, top, cursor.hCursor, 0, 0, 0, None, DI_NORMAL)
        finally:
            for handle in (icon.hbmMask, icon.hbmColor):
                if handle:
                    self._gdi32.DeleteObject(handle)

    def _release(self) -> None:
        if getattr(self, "_mem_dc", None):
            if getattr(self, "_old", None):
                self._gdi32.SelectObject(self._mem_dc, self._old)
                self._old = None
            if getattr(self, "_bitmap", None):
                self._gdi32.DeleteObject(self._bitmap)
                self._bitmap = None
            self._gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        if getattr(self, "_screen_dc", None):
            self._user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None

    def close(self) -> None:
        self._release()

    def __enter__(self) -> ScreenCapture:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
