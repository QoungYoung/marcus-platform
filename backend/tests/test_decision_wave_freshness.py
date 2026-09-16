# -*- coding: utf-8 -*-
"""决策对象的「波浪状态来源 / 新鲜度」校验（2026-09-16 修）。

**修的是什么**：`daily_decision.build()` 原先只找 `wave_state_{YYYYMMDD}.json`，而日更的
`apps/main_line/wave_agent.py` **只写无日期的** `data/wave_state.json`（内部 date 是带横杠的
'2026-09-16'），带日期的文件全是历史回填产物 ⇒
  · `missing` 里每天恒有 `wave_dated`（噪声，且让"数据齐全"判断失真）；
  · L2 档位的 basis 写着 `wave_state_<d>.json`，与真实来源不符；
  · **没有新鲜度校验**：08:10 判浪失败时，盘前/盘后对象会静默沿用旧 `wave_state.json` 的档位。
本文件锁死：dated 优先 / 无日期文件按内部 date 算 as_of+stale_days / 陈旧进 warnings 与 stale /
无 date 字段要显式告警 / directive 标注陈旧；**陈旧只告警不拦开仓**（不新增拦阻机制）。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import daily_decision as DD  # noqa: E402
from app.services import wolf_discipline as WD  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WOLF_DAILY_DECISION", "1")
    DD._ALLOW_CACHE.update({"at": 0.0, "key": "", "value": ""})
    # 单测**不碰数据库**：本机 18789 无监听时连库失败要走 ~140s 熔断，会把用例拖死
    monkeypatch.setattr(DD, "_load_pg", lambda d8: None)
    monkeypatch.setattr(DD, "_save_pg", lambda obj: {"ok": False, "reason": "test_stub"})
    monkeypatch.setattr(DD, "_latest_pg_at_or_before", lambda d8: None)
    monkeypatch.setattr(WD, "tier_target_pct", lambda op: 50.0, raising=False)
    monkeypatch.setattr(WD, "tier_floor_pct", lambda op: 30.0, raising=False)
    yield


MS = {"mainline": "半导体/芯片", "second": "新能源/电池", "pool": ["半导体/芯片"],
      "rank_in_gate": ["半导体/芯片"], "pool_k": 3}


def _write(name: str, payload: dict) -> None:
    d = Path(DD.data_dir())
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _wave(op="build", **kw):
    return {"operation": op, "level": "d4", "sub_level": "4-4", **kw}


# ── 来源选择 ────────────────────────────────────────────────────────
def test_dated_file_wins_and_is_not_stale():
    _write("wave_state_20260916.json", _wave())
    _write("wave_state.json", _wave("defense", date="2026-09-01"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    src = obj["sources"]["wave"]
    assert src["kind"] == "dated" and src["file"] == "wave_state_20260916.json"
    assert obj["layers"]["L2_operation"]["value"]["operation"] == "build"
    assert obj["stale"] == [] and obj["warnings"] == []


def test_dashed_dated_filename_also_accepted():
    """历史回填产物用的是带横杠的命名（wave_state_2026-09-16.json），也要认。"""
    _write("wave_state_2026-09-16.json", _wave())
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    assert obj["sources"]["wave"]["kind"] == "dated"
    assert "wave" not in obj["missing"]


# ── 新鲜度 ─────────────────────────────────────────────────────────
def test_undated_latest_same_day_is_fine():
    """生产常态：只有 wave_state.json，内部 date=当天（带横杠）→ 不算陈旧、不算缺失。"""
    _write("wave_state.json", _wave(date="2026-09-16"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    v = obj["layers"]["L2_operation"]["value"]
    assert v["wave_source"] == "wave_state.json" and v["wave_as_of"] == "20260916"
    assert v["wave_stale_days"] == 0
    assert obj["warnings"] == [] and obj["stale"] == []
    assert "wave" not in obj["missing"] and "wave_dated" not in obj["missing"]


def test_stale_wave_warns_but_does_not_block():
    """08:10 判浪失败 → 沿用昨天的 wave_state.json：必须显式告警，但**不拦开仓**。"""
    _write("wave_state.json", _wave(date="2026-09-15"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    v = obj["layers"]["L2_operation"]["value"]
    assert v["wave_stale_days"] == 1 and v["wave_as_of"] == "20260915"
    assert obj["stale"] == ["wave(as_of=20260915, 陈旧1天)"]
    assert any("陈旧 1 天" in w for w in obj["warnings"])
    assert obj["entry_allowed"] is True          # 陈旧只告警，L5 不新增拦阻
    assert obj["layers"]["L5_entry"]["value"]["blockers"] == []


def test_wave_without_date_field_warns():
    _write("wave_state.json", _wave())           # 没有 date 字段
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    assert obj["layers"]["L2_operation"]["value"]["wave_as_of"] is None
    assert any("没有 date 字段" in w for w in obj["warnings"])


def test_missing_wave_recorded_once():
    """没有波浪状态时：`missing` 里是单个 'wave'（不再是每天都出现的 wave_dated 噪声）。"""
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    assert obj["sources"]["wave"]["kind"] == "none"
    assert "wave" in obj["missing"] and "wave_dated" not in obj["missing"]
    assert obj["layers"]["L2_operation"]["value"] is None


# ── basis / directive ──────────────────────────────────────────────
def test_basis_names_the_real_file():
    _write("wave_state.json", _wave(date="2026-09-16"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    basis = obj["layers"]["L2_operation"]["basis"]
    assert "wave_state.json" in basis and "as_of=20260916" in basis


def test_directive_flags_stale_tier():
    _write("wave_state.json", _wave(date="2026-09-15"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    Path(DD.decision_dir()).mkdir(parents=True, exist_ok=True)
    Path(DD.path_for("20260916")).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    txt = DD.directive("20260916")
    assert "档位陈旧 1 天" in txt and "20260915" in txt


def test_run_reports_stale_in_log(capsys, monkeypatch):
    _write("wave_state.json", _wave(date="2026-09-15"))
    # 不碰数据库/网络：闸门状态直接给空（真读会去连本地 18789 → 挂 ~140s 熔断）
    monkeypatch.setattr(DD, "_gates_state", lambda: {})
    res = DD.run("20260916", save=True)
    assert res["ok"] is True
    out = capsys.readouterr().out
    assert "陈旧" in out and "wave_state.json" in out
