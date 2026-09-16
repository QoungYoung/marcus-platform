# -*- coding: utf-8 -*-
"""底仓 floor 新口径（2026-09-16 用户拍板）单测：一次性认账 + 只增不减 + 分档 ratio。

覆盖：
  · 未认账时 = 累计买入 × ratio（下限 100 股，取整到一手）—— 旧口径的既有行为；
  · floor 把 T 仓锁死（floor > 可卖 − 100）→ 一次性认账，锚重标为 可卖 × ratio；
  · 认账后**卖出不再缩小 floor**（防 2026-09-07 那种"每卖一次 T 仓复活"的几何侵蚀）；
  · 认账后**新增买入 floor 只增**（+ 新增买入 × ratio）；
  · 持仓归零 → reset 记录，重建后不被历史累计买入毒住；
  · 可卖不足两条腿（<200 股）→ 不解锁（别把唯一一手当 T 仓卖光）；
  · ratio 分档（浪型 operation → 档）+ 环境变量/状态文件覆盖优先级；
  · T_BASE_FLOOR_REBASE=0 退回旧口径。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_base_floor as B  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """状态文件写临时目录、关掉 t_triggers 审计（单测不连库）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("T_BASE_FLOOR_AUDIT", "0")
    monkeypatch.delenv("T_BASE_FLOOR_REBASE", raising=False)
    monkeypatch.delenv("T_BASE_KEEP_RATIO", raising=False)
    monkeypatch.delenv("T_BASE_KEEP_RATIO_MAIN_UP", raising=False)
    monkeypatch.delenv("T_BASE_KEEP_RATIO_HIGH", raising=False)
    yield


# ────────────────────────── 纯核心 ──────────────────────────

def test_floor_without_rebase_is_cum_buy_times_ratio():
    floor, ev = B.compute_floor(cum_buy=600, sellable=600, ratio=0.5)
    assert (floor, ev) == (300, None)


def test_floor_lower_bound_and_lot_align():
    # 累计买入很少 → 下限 100 股
    assert B.compute_floor(cum_buy=100, sellable=1000, ratio=0.5)[0] == 100
    # 取整到一手（floor 与可卖额度都是 100 的整数倍）
    assert B.compute_floor(cum_buy=650, sellable=10000, ratio=0.5)[0] == 300


def test_locked_floor_triggers_one_time_rebase():
    """SZ002409 实况：持仓/可卖 200，累计买入 400 → 旧口径 floor 200 → T 仓 0。"""
    floor, ev = B.compute_floor(cum_buy=400, sellable=200, ratio=0.5)
    assert floor == 100                      # 重标为 可卖 × 0.5
    assert ev and ev["kind"] == "rebase" and ev["floor"] == 100
    assert ev["sellable"] == 200 and ev["cum_buy"] == 400 and ev["old_floor"] == 200


def test_rebase_on_big_oversold_symbol():
    """SH588170 实况：持仓 23200、累计买入 109000 → 旧口径 floor 54500（锁死）。"""
    floor, ev = B.compute_floor(cum_buy=109000, sellable=23200, ratio=0.5)
    assert floor == 11600 and ev["kind"] == "rebase"
    assert 23200 - floor == 11600            # 恢复出来的 T 弹药


def test_after_rebase_selling_does_not_erode_floor():
    """关键性质：认账后每卖一笔，floor 不动（不是 09-07 之前那种几何衰减）。"""
    rec = {"base": 100, "cum_at_base": 400}   # 200 股认账为 base100
    for sellable in (200, 100):
        floor, ev = B.compute_floor(cum_buy=400, sellable=sellable, ratio=0.5, rec=rec)
        assert floor == 100
    # 卖到只剩 100 股：floor 仍是 100（不会被啃到 50/25）
    assert 100 - B.compute_floor(cum_buy=400, sellable=100, ratio=0.5, rec=rec)[0] == 0


def test_after_rebase_new_buys_only_raise_floor():
    rec = {"base": 11600, "cum_at_base": 109000}
    floor, ev = B.compute_floor(cum_buy=109000 + 2000, sellable=25200, ratio=0.5, rec=rec)
    assert floor == 12600 and ev is None      # +2000 买入 → +1000 底仓


def test_zero_position_records_reset_then_rebuild_is_clean():
    """清仓 → reset；重建后 floor 只按重建期买入算，不被历史累计买入毒住。"""
    floor, ev = B.compute_floor(cum_buy=109000, sellable=0, ratio=0.5)
    assert floor == 0 and ev["kind"] == "reset"
    rec = {"base": 0, "cum_at_base": 109000}
    floor, ev = B.compute_floor(cum_buy=109000 + 2000, sellable=2000, ratio=0.5, rec=rec)
    assert floor == 1000 and ev is None       # 2000 × 0.5
    # 认账前的可卖只在 100~199 股时不解锁（不足两条腿）
    assert B._rebase_floor(100, 0.5) == 100


def test_t1_frozen_position_keeps_anchor_not_reset():
    """持仓还在、今日无券可卖（当日买入冻结）→ 既不认账也不重置（别把锚清成 0）。"""
    floor, ev = B.compute_floor(cum_buy=700, sellable=0, ratio=0.5, position=700)
    assert ev is None and floor == 300        # 锚保持 = 累计买入×ratio（取整到一手）
    # 对照：真清仓（position=0）才记 reset
    _f, ev2 = B.compute_floor(cum_buy=700, sellable=0, ratio=0.5, position=0)
    assert ev2["kind"] == "reset"


def test_tiny_position_not_unlocked():
    floor, ev = B.compute_floor(cum_buy=400, sellable=100, ratio=0.5)
    assert floor == 100 and 100 - floor == 0  # 唯一一手仍是底仓


def test_override_only_used_without_trade_history():
    floor, _ = B.compute_floor(cum_buy=0, sellable=0, ratio=0.5, override=66900)
    assert floor == 66900
    # 有成交流水时旧覆盖不再生效（与 2026-09-07 起的口径一致）
    assert B.compute_floor(cum_buy=1000, sellable=1000, ratio=0.5, override=66900)[0] == 500


# ────────────────────────── 分档 / 开关 ──────────────────────────

def test_ratio_buckets_and_env_override(monkeypatch):
    assert B.ratio_of("range") == 0.5
    assert round(B.ratio_of("main_up"), 4) == 0.6667
    assert round(B.ratio_of("high"), 4) == 0.3333
    assert B.ratio_of("exit") == 0.0
    assert B.ratio_of("不存在") == 0.5
    monkeypatch.setenv("T_BASE_KEEP_RATIO_MAIN_UP", "0.75")
    assert B.ratio_of("main_up") == 0.75


def test_wave_bucket_mapping(monkeypatch, tmp_path):
    p = tmp_path / "wave_state.json"
    p.write_text(json.dumps({"operation": "build"}), encoding="utf-8")
    assert B.wave_bucket() == "main_up"
    p.write_text(json.dumps({"operation": "defense"}), encoding="utf-8")
    assert B.wave_bucket() == "high"
    p.write_text(json.dumps({"operation": "t_only"}), encoding="utf-8")
    assert B.wave_bucket() == "range"


def test_state_file_overrides(monkeypatch, tmp_path):
    (tmp_path / "wave_state.json").write_text(json.dumps({"operation": "defense"}), encoding="utf-8")
    st = {"buckets": {"SZ002409": "main_up"}}
    assert B.resolve_ratio(symbol="SZ002409", state=st) == (B.ratio_of("main_up"), "main_up")
    assert B.resolve_ratio(symbol="SH600584", state=st)[1] == "high"      # 落到浪型档
    assert B.resolve_ratio(state={"bucket_override": "exit"})[0] == 0.0   # 人工全局覆盖优先于浪型


# ────────────────────────── 端到端（含状态落盘）──────────────────────────

def test_evaluate_position_zero_distinction(monkeypatch):
    """evaluate 层：无券可卖时必须能区分 T+1 冻结与真清仓（靠 paper_positions 真实持仓）。"""
    monkeypatch.setattr(B, "_hold_shares", lambda a, s: 700)
    floor, ev = B.evaluate("stock", "SH600584", volume=0, cum_buy=700)
    assert (floor, ev) == (300, None)          # T+1 冻结：不重置
    monkeypatch.setattr(B, "_hold_shares", lambda a, s: 0)
    floor, ev = B.evaluate("stock", "SH600584", volume=0, cum_buy=700)
    assert floor == 0 and ev["kind"] == "reset"


def test_evaluate_persists_and_is_idempotent(tmp_path):
    floor, ev = B.evaluate("stock", "SZ002409", volume=200, cum_buy=400, persist=True)
    assert (floor, ev["kind"]) == (100, "rebase")
    st = json.loads((tmp_path / B._STATE_NAME).read_text(encoding="utf-8"))
    assert st["symbols"]["stock:SZ002409"]["base"] == 100
    # 幂等：再评估一次不再产生事件，floor 不变
    floor2, ev2 = B.evaluate("stock", "SZ002409", volume=200, cum_buy=400, persist=True)
    assert (floor2, ev2) == (100, None)


def test_evaluate_three_live_symbols_regain_ammo(tmp_path):
    """2026-09-16 生产实况三只死腿：认账后都拿回 T 弹药。"""
    cases = {"SZ002409": (400, 200), "SH588170": (109000, 23200), "SH512480": (8000, 1300)}
    got = {}
    for sym, (cum, sel) in cases.items():
        floor, ev = B.evaluate("stock", sym, volume=sel, cum_buy=cum, persist=True)
        got[sym] = sel - floor
    assert got == {"SZ002409": 100, "SH588170": 11600, "SH512480": 700}


def test_legacy_switch(monkeypatch):
    monkeypatch.setenv("T_BASE_FLOOR_REBASE", "0")
    floor, ev = B.evaluate("stock", "SZ002409", volume=200, cum_buy=400, persist=True)
    assert (floor, ev) == (200, None)          # 旧口径：锁死
