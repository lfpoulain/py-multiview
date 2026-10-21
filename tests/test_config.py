import json

from py_multiview.config import (
    DISPLAY_ID_SCHEMA_VERSION,
    default_config,
    load_config,
    normalize_config,
    save_config,
)


def test_garbage_is_normalized():
    config = normalize_config({"streams": "nope", "mapping": [], "routes": [1, None], "sender": 3})
    assert config["streams"] == []
    assert config["mapping"] == {}
    assert config["routes"] == []
    assert config["sender"]["name"] == "py-multiview"
    assert config["receiver"]["decode"] == "cpu"


def test_non_dict_root():
    assert normalize_config(["a"])["streams"] == []


def test_stream_values_are_clamped():
    config = normalize_config(
        {
            "displayIdSchemaVersion": DISPLAY_ID_SCHEMA_VERSION,
            "streams": [{"id": "a", "port": 99999, "latency": -4, "rotate": 45, "displayMode": "zoom", "source": "x"}],
        }
    )
    stream = config["streams"][0]
    assert stream["port"] == 65535
    assert stream["latency"] == 120
    assert stream["rotate"] == 0
    assert stream["displayMode"] == "fit"
    assert stream["source"] == "srt"


def test_duplicate_ids_are_fixed():
    config = normalize_config({"streams": [{"id": "a"}, {"id": "a"}], "routes": [{"id": "r"}, {"id": "r"}]})
    assert len({s["id"] for s in config["streams"]}) == 2
    assert len({r["id"] for r in config["routes"]}) == 2


def test_legacy_decoders_are_migrated():
    assert normalize_config({"receiver": {"decode": "h264_cuvid"}})["receiver"]["decode"] == "nvidia"
    assert normalize_config({"receiver": {"decode": "dxva2"}})["receiver"]["decode"] == "cpu"
    assert normalize_config({"receiver": {"decode": "gpu"}})["receiver"]["decode"] == "cpu"


def test_mapping_survives_when_schema_is_current():
    config = normalize_config(
        {
            "displayIdSchemaVersion": DISPLAY_ID_SCHEMA_VERSION,
            "streams": [{"id": "a"}],
            "mapping": {"a": "disp-1", "ghost": "disp-2"},
        }
    )
    assert config["mapping"] == {"a": "disp-1"}


def test_mapping_reset_on_old_schema():
    config = normalize_config({"streams": [{"id": "a"}], "mapping": {"a": "DISPLAY1"}})
    assert config["mapping"] == {}


def test_dangling_route_reference_is_cleared():
    config = normalize_config({"streams": [{"id": "a", "source": "route", "sourceRouteId": "missing"}]})
    assert config["streams"][0]["sourceRouteId"] == ""


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    config = default_config()
    assert save_config(config, path)
    assert load_config(path)["streams"] == config["streams"]


def test_corrupt_file_is_quarantined(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    config = load_config(path)
    assert len(config["streams"]) == 3
    assert list(tmp_path.glob("config.corrupt-*.json"))


def test_non_object_json_is_quarantined(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert load_config(path)["streams"]
    assert list(tmp_path.glob("config.corrupt-*.json"))
