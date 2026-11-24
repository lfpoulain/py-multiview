"""Direct access to libomt, the Open Media Transport library.

The FFmpeg build in ``bin/omt`` already ships ``libomt.dll`` and its
dependencies, so talking to it over ctypes costs no extra dependency and
gives what the FFmpeg wrappers cannot:

* **audio-only reception** — a receiver asks for ``OMTFrameType_Audio`` and
  the sender never opens the video connection, instead of decoding every
  frame just to throw it away;
* **native audio** — OMT carries 32-bit float planar audio (``FPA1``).
  Sending and receiving it as-is removes the sample format conversion, the
  drift compensation filter and the audio/video interleaving of a muxer;
* **instant discovery** — the library keeps the mDNS list in memory, where
  ``ffmpeg -find_sources`` costs a process launch and several seconds.

Everything here degrades gracefully: when the library cannot be loaded,
``available()`` returns False and the callers fall back to FFmpeg.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from array import array
from ctypes import c_char_p, c_float, c_int, c_int64, c_void_p
from dataclasses import dataclass

from .paths import OMT_BIN_DIR

log = logging.getLogger(__name__)

# OMTFrameType
FRAME_NONE = 0
FRAME_METADATA = 1
FRAME_VIDEO = 2
FRAME_AUDIO = 4

# OMTCodec (four-character codes)
CODEC_FPA1 = 0x31415046  # planar float32 audio
CODEC_UYVY = 0x59565955
CODEC_BGRA = 0x41524742

# OMTQuality
QUALITY_DEFAULT = 0
QUALITY_MEDIUM = 50
QUALITY_HIGH = 100

# OMTPreferredVideoFormat
VIDEO_FORMAT_UYVY = 0

# Timestamps are in 100 ns units.
TICKS_PER_SECOND = 10_000_000


class OMTMediaFrame(ctypes.Structure):
    _fields_ = [
        ("Type", c_int),
        ("Timestamp", c_int64),
        ("Codec", c_int),
        ("Width", c_int),
        ("Height", c_int),
        ("Stride", c_int),
        ("Flags", c_int),
        ("FrameRateN", c_int),
        ("FrameRateD", c_int),
        ("AspectRatio", c_float),
        ("ColorSpace", c_int),
        ("SampleRate", c_int),
        ("Channels", c_int),
        ("SamplesPerChannel", c_int),
        ("Data", c_void_p),
        ("DataLength", c_int),
        ("CompressedData", c_void_p),
        ("CompressedLength", c_int),
        ("FrameMetadata", c_void_p),
        ("FrameMetadataLength", c_int),
    ]


_lib: ctypes.CDLL | None = None
_vmx: ctypes.CDLL | None = None
_load_error = ""
_load_lock = threading.Lock()


def _bind(lib: ctypes.CDLL) -> None:
    lib.omt_discovery_getaddresses.restype = ctypes.POINTER(c_char_p)
    lib.omt_discovery_getaddresses.argtypes = [ctypes.POINTER(c_int)]

    lib.omt_receive_create.restype = c_void_p
    lib.omt_receive_create.argtypes = [c_char_p, c_int, c_int, c_int]
    lib.omt_receive.restype = ctypes.POINTER(OMTMediaFrame)
    lib.omt_receive.argtypes = [c_void_p, c_int, c_int]
    lib.omt_receive_destroy.restype = None
    lib.omt_receive_destroy.argtypes = [c_void_p]

    lib.omt_send_create.restype = c_void_p
    lib.omt_send_create.argtypes = [c_char_p, c_int]
    lib.omt_send.restype = c_int
    lib.omt_send.argtypes = [c_void_p, ctypes.POINTER(OMTMediaFrame)]
    lib.omt_send_getaddress.restype = c_int
    lib.omt_send_getaddress.argtypes = [c_void_p, c_char_p, c_int]
    lib.omt_send_connections.restype = c_int
    lib.omt_send_connections.argtypes = [c_void_p]
    lib.omt_send_destroy.restype = None
    lib.omt_send_destroy.argtypes = [c_void_p]


def load() -> ctypes.CDLL | None:
    """Load libomt once. Returns None (and remembers why) when unavailable."""
    global _lib, _vmx, _load_error
    with _load_lock:
        if _lib is not None or _load_error:
            return _lib
        dll = OMT_BIN_DIR / "libomt.dll"
        if not dll.exists():
            _load_error = f"{dll} introuvable"
            log.info("libomt indisponible: %s", _load_error)
            return None
        try:
            if sys.platform == "win32":
                # libomt loads the video codec by bare name on the first
                # frame, after the Python DLL search scope has closed.
                with os.add_dll_directory(str(OMT_BIN_DIR)):
                    vmx = OMT_BIN_DIR / "libvmx.dll"
                    if vmx.is_file():
                        _vmx = ctypes.CDLL(str(vmx))
                    lib = ctypes.CDLL(str(dll))
            else:
                lib = ctypes.CDLL(str(dll))
            _bind(lib)
        except (OSError, AttributeError) as exc:
            _load_error = str(exc)
            log.warning("Chargement de libomt impossible: %s", exc)
            return None
        _lib = lib
        log.info("libomt chargée depuis %s", dll)
        return _lib


def available() -> bool:
    return load() is not None


def load_error() -> str:
    load()
    return _load_error


def discover() -> list[str]:
    """Sources currently announced on the network, as ``MACHINE (Nom)``."""
    lib = load()
    if lib is None:
        return []
    count = c_int(0)
    try:
        addresses = lib.omt_discovery_getaddresses(ctypes.byref(count))
    except OSError:
        log.exception("Découverte OMT impossible")
        return []
    if not addresses:
        return []
    names = []
    for i in range(max(0, count.value)):
        value = addresses[i]
        if value:
            names.append(value.decode("utf-8", errors="replace"))
    return names


@dataclass(frozen=True)
class AudioFrame:
    """One received audio frame, already interleaved for playback."""

    rate: int
    channels: int
    samples: int  # per channel
    data: bytes  # interleaved float32

    @property
    def duration(self) -> float:
        return self.samples / self.rate if self.rate else 0.0


def _planar_to_interleaved(raw: bytes, channels: int, samples: int, gain: float) -> bytes:
    """OMT planes (L…L R…R) to the interleaved float32 PortAudio expects."""
    planes = array("f")
    planes.frombytes(raw)
    if channels == 1:
        if gain != 1.0:
            planes = array("f", [v * gain for v in planes])
        return planes.tobytes()
    out = array("f", bytes(4 * channels * samples))
    for c in range(channels):
        plane = planes[c * samples:(c + 1) * samples]
        if gain != 1.0:
            plane = array("f", [v * gain for v in plane])
        out[c::channels] = plane
    return out.tobytes()


def interleaved_to_planar(raw: bytes, channels: int) -> bytes:
    """The reverse: what omt_send expects."""
    interleaved = array("f")
    interleaved.frombytes(raw)
    if channels == 1:
        return raw
    out = array("f", bytes(len(raw)))
    samples = len(interleaved) // channels
    for c in range(channels):
        out[c * samples:(c + 1) * samples] = interleaved[c::channels]
    return out.tobytes()


class Receiver:
    """An OMT receiver. Use as a context manager."""

    def __init__(self, address: str, frame_types: int = FRAME_AUDIO) -> None:
        self._lib = load()
        if self._lib is None:
            raise RuntimeError(load_error() or "libomt indisponible")
        self._frame_types = frame_types
        self._handle = self._lib.omt_receive_create(
            address.encode("utf-8"), frame_types, VIDEO_FORMAT_UYVY, 0
        )
        if not self._handle:
            raise RuntimeError(f"Connexion à « {address} » impossible")

    def receive_audio(self, timeout_ms: int = 1000, gain: float = 1.0) -> AudioFrame | None:
        """Next audio frame, or None on timeout."""
        pointer = self._lib.omt_receive(self._handle, FRAME_AUDIO, timeout_ms)
        if not pointer:
            return None
        frame = pointer.contents
        if frame.Type != FRAME_AUDIO or frame.Codec != CODEC_FPA1 or not frame.Data:
            return None
        channels = max(1, frame.Channels)
        samples = max(0, frame.SamplesPerChannel)
        if not samples:
            return None
        # Valid only until the next omt_receive call: copy it out now.
        raw = ctypes.string_at(frame.Data, min(frame.DataLength, channels * samples * 4))
        # Trust the bytes actually there, not the announced sample count: a
        # short frame would otherwise desynchronise the de-interleaving.
        samples = min(samples, len(raw) // (4 * channels))
        if not samples:
            return None
        raw = raw[: 4 * channels * samples]
        return AudioFrame(
            rate=frame.SampleRate or 48000,
            channels=channels,
            samples=samples,
            data=_planar_to_interleaved(raw, channels, samples, gain),
        )

    def close(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            self._lib.omt_receive_destroy(handle)

    def __enter__(self) -> Receiver:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class Sender:
    """An OMT sender publishing video and audio under ``name``.

    libomt has separate audio and video send paths. Keep each path serial
    and wait for both before destroying their shared handle.
    """

    def __init__(self, name: str, quality: int = QUALITY_DEFAULT) -> None:
        self._lib = load()
        if self._lib is None:
            raise RuntimeError(load_error() or "libomt indisponible")
        self._video_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._handle = self._lib.omt_send_create(name.encode("utf-8"), quality)
        if not self._handle:
            raise RuntimeError(f"Publication de « {name} » impossible")

    @property
    def address(self) -> str:
        buffer = ctypes.create_string_buffer(1024)
        with self._video_lock:
            if not self._handle:
                return ""
            length = self._lib.omt_send_getaddress(self._handle, buffer, len(buffer))
        return buffer.value.decode("utf-8", errors="replace") if length > 0 else ""

    @property
    def connections(self) -> int:
        with self._video_lock:
            return int(self._lib.omt_send_connections(self._handle)) if self._handle else 0

    def send_video(self, data, width: int, height: int, stride: int, codec: int,
                   fps: int, timestamp: int) -> bool:
        frame = OMTMediaFrame()
        frame.Type = FRAME_VIDEO
        frame.Timestamp = timestamp
        frame.Codec = codec
        frame.Width = width
        frame.Height = height
        frame.Stride = stride
        frame.FrameRateN = fps
        frame.FrameRateD = 1
        frame.AspectRatio = width / height if height else 0.0
        frame.Data = ctypes.cast(data, c_void_p)
        frame.DataLength = stride * height
        with self._video_lock:
            if not self._handle:
                return False
            return bool(self._lib.omt_send(self._handle, ctypes.byref(frame)))

    def send_audio(self, planar: bytes, rate: int, channels: int, timestamp: int) -> bool:
        samples = len(planar) // (4 * channels) if channels else 0
        if not samples:
            return False
        frame = OMTMediaFrame()
        frame.Type = FRAME_AUDIO
        frame.Timestamp = timestamp
        frame.Codec = CODEC_FPA1
        frame.SampleRate = rate
        frame.Channels = channels
        frame.SamplesPerChannel = samples
        buffer = ctypes.create_string_buffer(planar, len(planar))
        frame.Data = ctypes.cast(buffer, c_void_p)
        frame.DataLength = len(planar)
        with self._audio_lock:
            if not self._handle:
                return False
            return bool(self._lib.omt_send(self._handle, ctypes.byref(frame)))

    def close(self) -> None:
        with self._video_lock, self._audio_lock:
            handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            self._lib.omt_send_destroy(handle)

    def __enter__(self) -> Sender:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
