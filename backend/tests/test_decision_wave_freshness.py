# -*- coding: utf-8 -*-
"""决策对象的「波浪状态来源 / 新鲜度」校验（2026-09-16 修）。

**修的是什么**：`daily_decision.build()` 原先只找 `wave_state_{YYYYMMDD}.json`，而日更的
`apps/main_line/wave_agent.py` **只写无日期的** `data/wave_state.json`（内部 date 带横杠），
带日期的文件全是历史回填产物 ⇒
  · `missing` 里每天恒有 `wave_dated`（噪声，且让"数据齐全"判断失真）；
  · L2 档位的 basis 写着 `wave_state_<d>.json`，与真实来源不符；
  · **没有新鲜度校验**：08:10 判浪失败时，盘前/盘后对象会静默沿用旧 `wave_state.json` 的档位。

**新鲜度的正确口径（第一版踩坑，2026-09-16 当天改正）**：wave_agent 08:10 跑，
注释写明「确保指数日线到最近收盘，**wave 判定 date=昨日**」→ 盘前/盘后对象的
`as_of = d8 之前最近一个已收盘交易日` 是**正常**的，不是陈旧！
第一版按"as_of != d8 即陈旧"上线，生产立刻每天误报「陈旧 1 天」→ 改为与
`_prev_trade_day(d8)` 比较，只有 as_of **早于**上一交易日才算陈旧（判浪没跑成）。

本文件锁死：dated 优先 / as_of 与 expected_as_of 的差距进 warnings+stale / 无 date 字段显式告警 /
directive 标注陈旧；**陈旧只告警不拦开仓**（不新增拦阻机制）。
"""
import datetime as _dt
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
    # 交易日历打桩（离线确定性）：上一交易日 = 往回第一个工作日
    def _fake_prev(d8):
        d = _dt.datetime.strptime(d8, "%Y%m%d").date() - _dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= _dt.timedelta(days=1)
        return d.strftime("%Y%m%d")
    monkeypatch.setattr(DD, "_prev_trade_day", _fake_prev)
    DD._PREV_TD_CACHE.clear()
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
def test_undated_latest_at_prev_trade_day_is_fine():
    """**生产常态**（2026-09-16 实测）：wave_state.json 写于当天 08:11、内部 date='2026-09-15'
    （= 最近已收盘交易日）→ 这是判浪的正常口径，**不得**报陈旧。"""
    _write("wave_state.json", _wave(date="2026-09-15"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    v = obj["layers"]["L2_operation"]["value"]
    assert v["wave_source"] == "wave_state.json" and v["wave_as_of"] == "20260915"
    assert v["wave_stale_days"] == 0
    assert obj["warnings"] == [] and obj["stale"] == []
    assert "wave" not in obj["missing"] and "wave_dated" not in obj["missing"]


def test_wave_newer_than_expected_is_not_stale():
    """盘中重算/当日数据（as_of=当天）比"上一交易日"还新 → 不算陈旧。"""
    _write("wave_state.json", _wave(date="2026-09-16"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    assert obj["layers"]["L2_operation"]["value"]["wave_stale_days"] == 0
    assert obj["stale"] == []


def test_stale_wave_warns_but_does_not_block():
    """08:10 判浪没跑成 → wave_state.json 还停在更早的数据日（as_of=09-14 < 应为 09-15）：
    必须显式告警，但**不拦开仓**（不新增拦阻机制）。"""
    _write("wave_state.json", _wave(date="2026-09-14"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    v = obj["layers"]["L2_operation"]["value"]
    assert v["wave_stale_days"] == 1 and v["wave_as_of"] == "20260914"
    assert obj["stale"] == ["wave(as_of=20260914, 应为20260915, 陈旧1天)"]
    assert any("陈旧 1 天" in w and "08:10 判浪" in w for w in obj["warnings"])
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
    _write("wave_state.json", _wave(date="2026-09-15"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    basis = obj["layers"]["L2_operation"]["basis"]
    assert "wave_state.json" in basis and "as_of=20260915" in basis


def test_directive_flags_stale_tier():
    _write("wave_state.json", _wave(date="2026-09-10"))
    obj = DD.build("20260916", ms=MS, tiers={}, picks=None, gates={})
    Path(DD.decision_dir()).mkdir(parents=True, exist_ok=True)
    Path(DD.path_for("20260916")).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    txt = DD.directive("20260916")
    assert "档位陈旧" in txt and "20260910" in txt


def test_run_reports_stale_in_log(capsys, monkeypatch):
    _write("wave_state.json", _wave(date="2026-09-11"))
    # 不碰数据库/网络：闸门状态直接给空（真读会去连本地 18789 → 挂 ~140s 熔断）
    monkeypatch.setattr(DD, "_gates_state", lambda: {})
    res = DD.run("20260916", save=True)
    assert res["ok"] is True
    out = capsys.readouterr().out
    assert "陈旧" in out and "wave_state.json" in out
