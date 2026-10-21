from py_multiview.omt import parse_find_sources
from py_multiview.process import _FFPLAY_STATUS_RE

FIND_OUTPUT = """
[libomt @ 0000020] OMT Sources
[libomt @ 0000020] -------------------------------------------
[libomt @ 0000020] REGIE (vMix - Output 1)
[libomt @ 0000020] PC-2 (Caméra)
[libomt @ 0000020] -------------------------------------------
[in#0 @ 0000021] Error opening input: Immediate exit requested
"""


def test_find_sources_skips_separators():
    sources, header = parse_find_sources(FIND_OUTPUT)
    assert header
    assert sources == ["REGIE (vMix - Output 1)", "PC-2 (Caméra)"]


def test_find_sources_none():
    assert parse_find_sources("[libomt @ 01] No OMT Sources found\n") == ([], True)


def test_ffplay_status_line():
    line = "   5.23 M-V:  0.001 fd=   0 aq=    0KB vq=   12KB sq=    0B "
    assert _FFPLAY_STATUS_RE.match(line).group(1) == "5.23"
    assert _FFPLAY_STATUS_RE.match("    nan    :  0.000 fd=   0 aq=    0KB vq=    0KB sq=    0B ").group(1) == "nan"
    assert _FFPLAY_STATUS_RE.match("[h264 @ 0x1] error while decoding MB") is None
