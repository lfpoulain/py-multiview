from pathlib import Path

from py_multiview.audio import parse_dshow_audio
from py_multiview.config import normalize_config
from py_multiview.ffmpeg import build_sender_command, dshow_audio_input_args, loopback_input_args

DSHOW_OUTPUT = """
[dshow @ 01] "HD Pro Webcam C920" (video)
[dshow @ 01]   Alternative name "@device_pnp_..."
[dshow @ 01] "Microphone (HD Pro Webcam C920)" (audio)
[dshow @ 01] "CABLE Output (VB-Audio Virtual Cable)" (audio)
[dshow @ 01] "Microphone (HD Pro Webcam C920)" (audio)
"""
DISPLAY = {"x": 0, "y": 0, "width": 1920, "height": 1080}


def test_dshow_audio_devices_are_parsed():
    assert parse_dshow_audio(DSHOW_OUTPUT) == ["Microphone (HD Pro Webcam C920)", "CABLE Output (VB-Audio Virtual Cable)"]


def test_audio_source_is_validated():
    assert normalize_config({"sender": {"audioSource": "loopback"}})["sender"]["audioSource"] == "loopback"
    assert normalize_config({"sender": {"audioSource": "dshow:Micro"}})["sender"]["audioSource"] == "dshow:Micro"
    assert normalize_config({"sender": {"audioSource": "nope"}})["sender"]["audioSource"] == ""


def test_sender_without_audio():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"])
    assert "-an" in args and "1:a:0" not in args


def test_sender_with_loopback_reads_stdin():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], loopback_input_args(44100, 2))
    assert args[args.index("pipe:0") - 1] == "-i"
    assert args[args.index("-ar") + 1] == "44100"  # input rate, resampled to 48 kHz on output
    assert "-an" not in args and "1:a:0" in args
    assert args[-3:-1] == ["-f", "libomt"]


def test_sender_with_dshow_device():
    args = build_sender_command(Path("ffmpeg"), DISPLAY, normalize_config({})["sender"], dshow_audio_input_args("Micro (USB)"))
    assert "audio=Micro (USB)" in args
