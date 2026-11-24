import ctypes
import threading
from array import array

from py_multiview import omtlib
from py_multiview.config import normalize_config


def floats(values):
    return array("f", values).tobytes()


def test_media_frame_matches_the_c_layout():
    # libomt.h: int + padded int64 + 13 fields + 3 padded pointer/length pairs.
    assert ctypes.sizeof(omtlib.OMTMediaFrame) == 112
    offsets = {name: getattr(omtlib.OMTMediaFrame, name).offset for name, _ in omtlib.OMTMediaFrame._fields_}
    assert offsets["Timestamp"] == 8  # padded, not packed right after Type
    assert offsets["Data"] == 64


def test_planar_audio_is_interleaved_for_playback():
    # Two planes (left, right) become L R L R.
    planar = floats([1.0, 2.0, 3.0, -1.0, -2.0, -3.0])
    out = array("f")
    out.frombytes(omtlib._planar_to_interleaved(planar, channels=2, samples=3, gain=1.0))
    assert list(out) == [1.0, -1.0, 2.0, -2.0, 3.0, -3.0]


def test_volume_is_applied_while_de_interleaving():
    planar = floats([1.0, 1.0, -1.0, -1.0])
    out = array("f")
    out.frombytes(omtlib._planar_to_interleaved(planar, channels=2, samples=2, gain=0.5))
    assert list(out) == [0.5, -0.5, 0.5, -0.5]


def test_capture_audio_round_trips_to_planes():
    interleaved = floats([1.0, -1.0, 2.0, -2.0, 3.0, -3.0])
    planar = omtlib.interleaved_to_planar(interleaved, channels=2)
    assert list(array("f", planar)) == [1.0, 2.0, 3.0, -1.0, -2.0, -3.0]
    back = omtlib._planar_to_interleaved(planar, channels=2, samples=3, gain=1.0)
    assert back == interleaved


def test_mono_audio_is_left_untouched():
    mono = floats([0.25, 0.5])
    assert omtlib.interleaved_to_planar(mono, channels=1) == mono
    assert omtlib._planar_to_interleaved(mono, 1, 2, 1.0) == mono


def test_short_frames_do_not_desynchronise_channels():
    frame = omtlib.OMTMediaFrame()
    frame.Type = omtlib.FRAME_AUDIO
    frame.Codec = omtlib.CODEC_FPA1
    frame.SampleRate, frame.Channels = 48000, 2
    frame.SamplesPerChannel = 4  # announced…
    payload = ctypes.create_string_buffer(floats([1.0, 2.0, -1.0, -2.0]), 16)
    frame.Data = ctypes.cast(payload, ctypes.c_void_p)
    frame.DataLength = 16  # …but only 2 per channel delivered

    class FakeLib:
        @staticmethod
        def omt_receive(_handle, _types, _timeout):
            return ctypes.pointer(frame)

    receiver = omtlib.Receiver.__new__(omtlib.Receiver)
    receiver._lib, receiver._handle = FakeLib, 1
    received = receiver.receive_audio(0, 1.0)
    assert received.samples == 2
    assert list(array("f", received.data)) == [1.0, -1.0, 2.0, -2.0]


def test_input_sources_accept_both_prefixes():
    for source in ("", "loopback", "in:CABLE Output", "dshow:Micro"):
        assert normalize_config({"sender": {"audioSource": source}})["sender"]["audioSource"] == source
    assert normalize_config({"sender": {"audioSource": "nope"}})["sender"]["audioSource"] == ""


def test_slow_video_encode_allows_audio_and_close_waits_for_video(monkeypatch):
    encoding, release_video, audio_sent, destroyed = (threading.Event() for _ in range(4))

    class Lib:
        def omt_send_create(self, name, quality):
            return 1

        def omt_send(self, handle, frame):
            if frame._obj.Type == omtlib.FRAME_VIDEO:
                encoding.set()
                release_video.wait(3)
            else:
                audio_sent.set()
            assert not destroyed.is_set()
            return 1

        def omt_send_destroy(self, handle):
            destroyed.set()

    monkeypatch.setattr(omtlib, "load", Lib)
    sender = omtlib.Sender("Diagnostic")
    video = threading.Thread(target=sender.send_video, args=(
        ctypes.create_string_buffer(4), 1, 1, 4, omtlib.CODEC_BGRA, 30, 0,
    ))
    sound = threading.Thread(target=sender.send_audio, args=(bytes(7680), 48000, 2, 0))
    close = threading.Thread(target=sender.close)
    video.start()
    try:
        assert encoding.wait(1)
        sound.start()
        assert audio_sent.wait(1)
        close.start()
        assert not destroyed.wait(0.05)
    finally:
        release_video.set()
        video.join()
        for thread in (sound, close):
            if thread.ident is not None:
                thread.join()
    assert destroyed.is_set()
    assert not sender.send_audio(bytes(7680), 48000, 2, 200000)
