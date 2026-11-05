"""Capture the sound of one program, not of the whole PC.

Windows can activate a WASAPI capture client scoped to a process tree since
build 20348: ``ActivateAudioInterfaceAsync`` with
``AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK``. Two modes:

* **include** a process and its children — only that program is recorded;
* **exclude** a process — everything the PC plays *except* that program.

The exclusion mode is what keeps an emission from eating its own audio
return: capturing "the PC except py-multiview" cannot loop back on itself.

Everything here is ctypes. The one awkward part is that the activation is
asynchronous and answers through a COM callback, so a tiny COM object with a
hand-built vtable is created below to receive it.

Only inside this module is anything Windows-specific; callers get float32
frames through a callback, exactly like :class:`py_multiview.audio.FloatRecorder`.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import POINTER, Structure, byref, c_int, c_uint32, c_void_p, wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ActivateAudioInterfaceAsync lives here; the virtual device id below is the
# documented pseudo-endpoint for process loopback.
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

PROCESS_LOOPBACK_INCLUDE = 0
PROCESS_LOOPBACK_EXCLUDE = 1
ACTIVATION_TYPE_PROCESS_LOOPBACK = 1

WAVE_FORMAT_IEEE_FLOAT = 0x0003
REFTIMES_PER_SEC = 10_000_000

S_OK = 0


class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


class GUID(Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str = "") -> None:
        super().__init__()
        if text:
            ctypes.oledll.ole32.IIDFromString(ctypes.c_wchar_p(text), byref(self))


class PROPVARIANT(Structure):
    """Only the BLOB case is used, to carry the activation parameters."""

    class _Blob(Structure):
        _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]

    _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("blob", _Blob), ("padding", ctypes.c_byte * 8)]


VT_BLOB = 65

IID_IAudioClient = "{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}"
IID_IActivateAudioInterfaceCompletionHandler = "{41D949AB-9862-444A-80F6-C261334DA5EB}"
IID_IAgileObject = "{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}"
IID_IUnknown = "{00000000-0000-0000-C000-000000000046}"


def _method(index: int, restype, *argtypes):
    """Call slot ``index`` of a COM interface's vtable."""
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)

    def call(interface, *args):
        vtable = ctypes.cast(interface, POINTER(c_void_p))[0]
        slot = ctypes.cast(vtable, POINTER(c_void_p))[index]
        return proto(slot)(interface, *args)

    return call


# IUnknown
_release = _method(2, ctypes.HRESULT)
# IActivateAudioInterfaceAsyncOperation::GetActivateResult
_get_activate_result = _method(3, ctypes.HRESULT, POINTER(ctypes.HRESULT), POINTER(c_void_p))
# IAudioClient: Initialize, GetBufferSize, GetStreamLatency, GetCurrentPadding,
# IsFormatSupported, GetMixFormat, GetDevicePeriod, Start, Stop, Reset,
# SetEventHandle, GetService — in that order after the three IUnknown slots.
_ac_initialize = _method(3, ctypes.HRESULT, c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                         POINTER(WAVEFORMATEX), POINTER(GUID))
_ac_get_buffer_size = _method(4, ctypes.HRESULT, POINTER(c_uint32))
_ac_start = _method(10, ctypes.HRESULT)
_ac_stop = _method(11, ctypes.HRESULT)
_ac_set_event_handle = _method(13, ctypes.HRESULT, wintypes.HANDLE)
_ac_get_service = _method(14, ctypes.HRESULT, POINTER(GUID), POINTER(c_void_p))
# IAudioCaptureClient
_cc_get_buffer = _method(3, ctypes.HRESULT, POINTER(c_void_p), POINTER(c_uint32), POINTER(wintypes.DWORD),
                         POINTER(ctypes.c_ulonglong), POINTER(ctypes.c_ulonglong))
_cc_release_buffer = _method(4, ctypes.HRESULT, c_uint32)
_cc_next_packet_size = _method(5, ctypes.HRESULT, POINTER(c_uint32))

IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")


class _CompletionHandler(Structure):
    """Minimal COM object receiving the end of the asynchronous activation.

    A real vtable is built by hand: three IUnknown slots plus
    ActivateCompleted. The object only has to stay alive and set an event.
    """

    _fields_ = [("lpVtbl", c_void_p)]

    _QueryInterface = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
    _AddRef = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)
    _Release = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)
    _ActivateCompleted = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_void_p)

    def __init__(self) -> None:
        super().__init__()
        self.done = threading.Event()
        known = {IID_IUnknown.lower(), IID_IActivateAudioInterfaceCompletionHandler.lower(),
                 IID_IAgileObject.lower()}

        def query(this, riid, out):
            text = ctypes.c_wchar_p()
            ctypes.oledll.ole32.StringFromCLSID(riid, byref(text))
            if (text.value or "").lower() in known:
                ctypes.cast(out, POINTER(c_void_p))[0] = this
                return S_OK
            ctypes.cast(out, POINTER(c_void_p))[0] = None
            return -2147467262  # E_NOINTERFACE

        def addref(_this):
            return 2

        def release(_this):
            return 1

        def completed(_this, _operation):
            self.done.set()
            return S_OK

        # Keep references: ctypes callbacks are freed with their Python object.
        self._fns = [self._QueryInterface(query), self._AddRef(addref), self._Release(release),
                     self._ActivateCompleted(completed)]
        self._vtbl = (c_void_p * 4)(*[ctypes.cast(f, c_void_p).value for f in self._fns])
        self.lpVtbl = ctypes.cast(self._vtbl, c_void_p)


@dataclass(frozen=True)
class Program:
    """A running program that can be captured."""

    pid: int
    name: str  # executable, e.g. "chrome.exe"
    title: str  # main window title, when it has one


def _supported() -> bool:
    import sys

    return sys.platform == "win32" and ctypes.windll.kernel32.GetVersion() is not None


def list_programs() -> list[Program]:
    """Programs with a visible window, the ones an operator means by « a program »."""
    import sys

    if sys.platform != "win32":
        return []
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found: dict[int, Program] = {}

    enum_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    buffer = ctypes.create_unicode_buffer(512)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if not pid.value or pid.value in found:
            return True
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value
        # PROCESS_QUERY_LIMITED_INFORMATION: enough to read the executable name
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        name = ""
        if handle:
            size = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)):
                name = buffer.value.rsplit("\\", 1)[-1]
            kernel32.CloseHandle(handle)
        if name:
            found[pid.value] = Program(pid.value, name, title)
        return True

    user32.EnumWindows(enum_proto(visit), 0)
    return sorted(found.values(), key=lambda p: (p.name.lower(), p.pid))


class PROCESSENTRY32W(Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", POINTER(ctypes.c_ulong)), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = -1


def pids_of(executable: str) -> list[int]:
    """Every running process of that executable, window or not.

    A media player can be hidden in the tray and a browser splits itself in
    many processes, so this looks at the whole process list rather than at
    the windows.
    """
    import sys

    wanted = (executable or "").strip().lower()
    if not wanted or sys.platform != "win32":
        return []
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE or not snapshot:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids: list[int] = []
    try:
        if not kernel32.Process32FirstW(snapshot, byref(entry)):
            return []
        while True:
            if entry.szExeFile.lower() == wanted:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snapshot, byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return pids


class ProcessCapture:
    """Captures what one program plays, or everything except it.

    Windows gives no mix format for a process loopback client, so the format
    is imposed: 32-bit float, the format WASAPI works in internally.
    """

    CHUNK = 0.02  # seconds

    def __init__(self, pid: int, exclude: bool = False, rate: int = 48000, channels: int = 2) -> None:
        self.pid = int(pid)
        self.exclude = bool(exclude)
        self.rate = int(rate)
        self.channels = int(channels)
        self._client: c_void_p | None = None
        self._capture: c_void_p | None = None
        self._event = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_frames = None

    # -- activation --------------------------------------------------------

    def _activate(self) -> c_void_p:
        params = AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = self.pid
        params.ProcessLoopbackParams.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_EXCLUDE if self.exclude else PROCESS_LOOPBACK_INCLUDE
        )
        prop = PROPVARIANT()
        prop.vt = VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()
        operation = c_void_p()
        hr = ctypes.oledll.mmdevapi.ActivateAudioInterfaceAsync(
            ctypes.c_wchar_p(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK),
            byref(GUID(IID_IAudioClient)),
            byref(prop),
            ctypes.byref(handler),
            byref(operation),
        )
        if hr != S_OK or not operation:
            raise OSError(f"ActivateAudioInterfaceAsync a échoué (0x{hr & 0xFFFFFFFF:08x})")
        if not handler.done.wait(5.0):
            raise OSError("Windows n'a pas répondu à la demande de capture du programme")
        result = ctypes.HRESULT()
        client = c_void_p()
        _get_activate_result(operation, byref(result), byref(client))
        _release(operation)
        code = result.value & 0xFFFFFFFF
        if code or not client:
            if code == 0x80070057:  # E_INVALIDARG : process gone, or build too old
                raise OSError("Capture par programme refusée : le programme s'est arrêté, "
                              "ou Windows est trop ancien (build 20348 minimum)")
            raise OSError(f"Capture du programme refusée par Windows (0x{code:08x})")
        return client

    def start(self, on_frames) -> None:
        """``on_frames(data: bytes, samples: int)`` with interleaved float32."""
        ctypes.oledll.ole32.CoInitializeEx(None, 0)  # STA not required; MTA is fine
        self._on_frames = on_frames
        client = self._activate()

        fmt = WAVEFORMATEX()
        fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
        fmt.nChannels = self.channels
        fmt.nSamplesPerSec = self.rate
        fmt.wBitsPerSample = 32
        fmt.nBlockAlign = self.channels * 4
        fmt.nAvgBytesPerSec = self.rate * fmt.nBlockAlign
        fmt.cbSize = 0

        duration = int(REFTIMES_PER_SEC * 0.2)
        hr = _ac_initialize(client, AUDCLNT_SHAREMODE_SHARED,
                            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
                            duration, 0, byref(fmt), None)
        if hr:
            _release(client)
            raise OSError(f"Initialisation de la capture impossible (0x{hr & 0xFFFFFFFF:08x})")

        self._event = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
        _ac_set_event_handle(client, self._event)
        capture = c_void_p()
        hr = _ac_get_service(client, byref(IID_IAudioCaptureClient), byref(capture))
        if hr or not capture:
            _release(client)
            raise OSError(f"Service de capture indisponible (0x{hr & 0xFFFFFFFF:08x})")
        _ac_start(client)
        self._client, self._capture = client, capture
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="proc-audio", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        ctypes.oledll.ole32.CoInitializeEx(None, 0)
        frame_bytes = self.channels * 4
        data = c_void_p()
        frames = c_uint32()
        flags = wintypes.DWORD()
        pos = ctypes.c_ulonglong()
        qpc = ctypes.c_ulonglong()
        while not self._stop.is_set():
            # 100 ms: a program that plays nothing simply wakes us up for nothing
            ctypes.windll.kernel32.WaitForSingleObject(self._event, 100)
            while not self._stop.is_set():
                available = c_uint32()
                if _cc_next_packet_size(self._capture, byref(available)) or not available.value:
                    break
                if _cc_get_buffer(self._capture, byref(data), byref(frames), byref(flags),
                                  byref(pos), byref(qpc)):
                    break
                count = frames.value
                if count:
                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                        chunk = b"\x00" * (count * frame_bytes)
                    else:
                        chunk = ctypes.string_at(data, count * frame_bytes)
                    try:
                        if self._on_frames is not None:
                            self._on_frames(chunk, count)
                    except Exception:
                        log.exception("Traitement du son du programme interrompu")
                _cc_release_buffer(self._capture, count)

    def close(self) -> None:
        self._stop.set()
        if self._event:
            ctypes.windll.kernel32.SetEvent(self._event)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if self._client:
            _ac_stop(self._client)
        for ref in ("_capture", "_client"):
            handle = getattr(self, ref, None)
            if handle:
                _release(handle)
            setattr(self, ref, None)
        if self._event:
            ctypes.windll.kernel32.CloseHandle(self._event)
            self._event = None
        self._on_frames = None
