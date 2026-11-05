from pathlib import Path

from py_multiview.config import normalize_config
from py_multiview.ffmpeg import (
    build_player_command,
    build_route_command,
    build_sender_command,
    srt_listener_url,
    udp_input_url,
    video_filter,
)

DISPLAY = {"x": 1920, "y": 0, "width": 1280, "height": 720}


def test_srt_latency_is_in_microseconds():
    assert "latency=120000" in srt_listener_url(9001, 120)
    assert srt_listener_url(9001, 0).startswith("srt://0.0.0.0:9001?mode=listener")


def test_udp_url_binds_interface():
    assert udp_input_url("239.1.1.1", 1234).startswith("udp://@239.1.1.1:1234?")
    assert udp_input_url("239.1.1.1", 1234, "10.0.0.5").endswith("&localaddr=10.0.0.5")


def test_fit_needs_no_filter():
    assert video_filter("fit", 0, 1920, 1080) is None


def test_rotation_comes_first():
    vf = video_filter("fill", 90, 1080, 1920)
    assert vf.startswith("transpose=clock,")
    assert "crop=" in vf


def test_stretch_sets_display_aspect():
    assert video_filter("stretch", 0, 1280, 720) == "setdar=1280/720"


def test_player_command_layout():
    args = build_player_command(
        Path("ffplay"), input_args=["-i", "srt://x"], display=DISPLAY, mute=True, decoder="h264_cuvid", title="T"
    )
    assert args[-2:] == ["-i", "srt://x"]
    assert args[args.index("-left") + 1] == "1920"
    assert args[args.index("-vcodec") + 1] == "h264_cuvid"
    for flag in ("-autoexit", "-fs", "-an", "-stats"):
        assert flag in args
    assert "-vf" not in args
    assert "-infbuf" not in args


def test_player_command_unthrottles_raw_omt_video():
    args = build_player_command(
        Path("ffplay"), input_args=["-f", "libomt", "-i", "SRC"], display=DISPLAY, raw_video=True
    )
    # Uncompressed frames overflow ffplay's packet queue and starve the audio.
    assert "-infbuf" in args and "-framedrop" in args
    assert args[args.index("-sync") + 1] == "audio"


def test_route_command():
    route = normalize_config({"routes": [{"id": "r", "inputPort": 9100, "multicastPort": 5000}]})["routes"][0]
    args = build_route_command(Path("ffmpeg"), route, "10.0.0.5")
    assert any(a.startswith("srt://0.0.0.0:9100?mode=listener") for a in args)
    assert args[-1] == "udp://239.10.10.10:5000?pkt_size=1316&ttl=1&localaddr=10.0.0.5"
    assert "nobuffer" not in args


def test_sender_command():
    sender = normalize_config({"sender": {"name": "Régie", "fps": 50}})["sender"]
    args = build_sender_command(Path("ffmpeg"), DISPLAY, sender)
    assert args[-3:] == ["-f", "libomt", "Régie"]
    assert args[args.index("-video_size") + 1] == "1280x720"
    assert args[args.index("-framerate") + 1] == "50"
    assert args[args.index("-fps_mode") + 1] == "cfr"
