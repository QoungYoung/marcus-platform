# -*- coding: utf-8 -*-
"""G2 每日存档（daily_archive）单测 —— 2026-09-12。

重点：**当日覆盖型**文件必须在清单里（concept_long / theme_inst_flow / etf_share_flow /
main_line_state / stock_confirm_result —— 它们不带日期、每天被覆盖，是"历史缺口"的根源）；
快照可复制到临时目录并产出 manifest；缺失文件被如实记录；开关默认关。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import daily_archive as DA  # noqa: E402

D8 = "20260911"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("WOLF_DAILY_ARCHIVE", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield tmp_path


def test_switch_defaults_off():
    assert DA.enabled() is False


def test_expected_files_cover_overwrite_type_files(_env):
    """当日覆盖型文件必须在清单里——这是本模块存在的理由。"""
    names = [Path(p).name for p in DA.expected_files(D8)]
    for must in ("concept_long.json", "theme_inst_flow.json", "etf_share_flow.json",
                 "main_line_state.json", "stock_confirm_result.json", "strategy_state.json"):
        assert must in names, must
    # 带日期型也要在
    for must in ("mainline_gate_%s.json" % D8, "heat_v2_%s.json" % D8,
                 "trend_confirm_%s_long.json" % D8, "wave_state_%s.json" % D8,
                 "wave_state_2026-09-11.json", "%s.json" % D8):   # decision/{d}.json 的 basename
        assert must in names, must


def test_snapshot_copies_and_reports_missing(_env, tmp_path):
    data = tmp_path
    (data / ("mainline_gate_%s.json" % D8)).write_text('{"confirmed":["半导体"]}', encoding="utf-8")
    (data / "concept_long.json").write_text('{"k":1}', encoding="utf-8")
    (data / "decision").mkdir()
    (data / "decision" / ("%s.json" % D8)).write_text('{"entry_allowed":true}', encoding="utf-8")
    res = DA.snapshot_files(D8)
    assert res["copied"] >= 3
    assert (Path(res["dir"]) / "concept_long.json").is_file()
    assert (Path(res["dir"]) / ("%s.json" % D8)).is_file()
    # 未创建的文件要如实报缺失，而不是假装成功
    assert "etf_share_flow.json" in res["missing"]
    names = [e["name"] for e in res["entries"]]
    assert "concept_long.json" in names and all(e["sha256_16"] for e in res["entries"])


def test_run_disabled_returns_not_ok():
    assert DA.run(D8, save=True, skip_db=True)["ok"] is False


def test_run_writes_manifest(_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WOLF_DAILY_ARCHIVE", "1")
    (tmp_path / ("mainline_gate_%s.json" % D8)).write_text('{"a":1}', encoding="utf-8")
    res = DA.run(D8, save=True, skip_db=True)
    assert res["ok"] is True and res["copied"] >= 1
    mf = Path(res["dir"]) / "manifest.json"
    assert mf.is_file()
    m = json.loads(mf.read_text(encoding="utf-8"))
    assert m["date"] == D8 and "missing" in m
    assert "artifacts" in m["db"] and "files" in m["db"]   # 双写：库 + 文件都在 manifest 里
    assert DA.load(D8)["date"] == D8
    assert D8 in DA.available_dates()


# ── 落库（双写的"库"那一半）────────────────────────────────────────
@pytest.mark.parametrize("name,expect", [
    ("mainline_gate_20260911.json", "mainline_gate"),
    ("heat_v2_20260911.json", "heat_v2"),
    ("trend_confirm_20260911_long.json", "trend_confirm_long"),
    ("wave_state_20260911.json", "wave_state"),
    ("wave_state_2026-09-11.json", "wave_state"),
    ("concept_long.json", "concept_long"),
    ("theme_inst_flow.json", "theme_inst_flow"),
    ("etf_share_flow.json", "etf_share_flow"),
    ("wolf_volume_gate.json", "wolf_volume_gate"),
    ("20260911.json", "decision"),          # decision/<d>.json 的 basename
])
def test_artifact_key_derivation(name, expect):
    assert DA.artifact_key(name, D8) == expect


def test_artifact_key_skips_non_json():
    assert DA.artifact_key("db_t_conditions.csv", D8) is None


@pytest.mark.parametrize("name,size,expect", [
    ("mainline_gate_%s.json" % D8, 1000, None),                      # 正常落库
    ("chain_map_%s.json" % D8, DA.max_payload_bytes() + 1, "too_large"),  # 大对象只登记元信息
    ("db_t_conditions.csv", 100, "not_json"),                        # 非 json 不进库
])
def test_should_skip_payload(name, size, expect):
    assert DA.should_skip_payload({"name": name, "size": size}, D8) == expect


# ── payload 消毒（生产踩到：strategy_state.json 里的 NaN 让 jsonb 拒绝）──────
def test_sanitize_payload_turns_nan_into_null():
    raw = '{"a50_futures": {"current": 5562.52, "change": NaN, "change_pct": NaN}}'
    out = DA.sanitize_payload(raw)
    assert out is not None
    assert "NaN" not in out
    import json as _j
    assert _j.loads(out)["a50_futures"]["change"] is None


def test_sanitize_payload_passes_normal_json():
    raw = '{"confirmed": ["半导体"], "n": 3}'
    assert DA.sanitize_payload(raw) == raw or '"n": 3' in DA.sanitize_payload(raw)


def test_sanitize_payload_rejects_broken_json():
    assert DA.sanitize_payload("{not json") is None


def test_concept_long_scale_payload_is_accepted(monkeypatch):
    """2.17MB 的 concept_long 必须能落库（默认上限 8MB，可用 env 覆盖）。"""
    entry = {"name": "concept_long.json", "size": 2_172_030}
    assert DA.should_skip_payload(entry, D8) is None
    monkeypatch.setenv("WOLF_ARCHIVE_MAX_BYTES", "1000000")
    assert DA.should_skip_payload(entry, D8) == "too_large"
