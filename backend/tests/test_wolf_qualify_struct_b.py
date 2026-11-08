# -*- coding: utf-8 -*-
"""B 判据单测（2026-09-19 用户拍板「按 B 起一版」）。

B 的语料依据（狼大原话）：
  · 2025-12-30「下周开始做半导体」→ 主题候选
  · 2026-01-26「指数没破位就没有悲观的理由」
  · 2026-01-27「趋势不破就不看空」
⇒ 资格口径 = **结构未破（未创新低）∧ 指数未破位 ⇒ 不撤资格**；
   而我们的旧口径是 trend_confirm「近 5 日创 60 日新高」⇒ 1 月半导体高位回踩不创新高 → 天天 not_confirmed → 只能半档做。

B1 = 资格不撤（WOLF_THEME_QUALIFY_STRUCT，库内默认关 / 回测开）
B2 = 合格主题里放宽个股 stage 到 {结构到位, 缩量止跌}（WOLF_QUALIFIED_THEME_DIP，同上）——**买点不变**（仍 253/254）。
"""
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wolf_context as WC      # noqa: E402
import switch_builder as SB    # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    WC._QUAL_CACHE.clear()
    yield
    WC._QUAL_CACHE.clear()


def _series(n=120, base=1000.0, step=0.5, dip_last=0.0):
    """主题指数序列：默认单调缓涨（近 20 日最低 ≥ 近 60 日最低 ⇒ 结构未破）。"""
    out = [base + i * step for i in range(n)]
    if dip_last:
        out[-1] = out[-20] - dip_last
    return out


# ── B1：资格不撤 ──────────────────────────────────────────────────────────────
def test_B1_结构未破且指数未破位_资格不撤(monkeypatch):
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", True)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series())
    monkeypatch.setattr(WC, "_index_not_broken", lambda day="": (True, "指数未破位"))
    ok, why = WC.theme_qualify_struct("半导体/芯片")
    assert ok is True and "资格不撤" in why


def test_B1_创新低则结构已破(monkeypatch):
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", True)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series(dip_last=80.0))
    monkeypatch.setattr(WC, "_index_not_broken", lambda day="": (True, "指数未破位"))
    ok, why = WC.theme_qualify_struct("半导体/芯片")
    assert ok is False and "结构已破" in why


def test_B1_指数破位则撤资格(monkeypatch):
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", True)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series())
    monkeypatch.setattr(WC, "_index_not_broken", lambda day="": (False, "指数破位(跌破 [60, 144])"))
    ok, why = WC.theme_qualify_struct("半导体/芯片")
    assert ok is False and "指数破位" in why


def test_B1_序列不足_fail_closed(monkeypatch):
    """数据不足 → 不放行（回落到旧口径，不会因此凭空放行）。"""
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", True)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series(n=30))
    monkeypatch.setattr(WC, "_index_not_broken", lambda day="": (True, "指数未破位"))
    ok, why = WC.theme_qualify_struct("半导体/芯片")
    assert ok is False and "序列不足" in why


def test_B1_开关关闭恒False(monkeypatch):
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", False)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series())
    ok, why = WC.theme_qualify_struct("半导体/芯片")
    assert ok is False and "B 关" in why


def test_B1_day_透传到指数判定(monkeypatch):
    """day 参与判定与缓存键：不同 day 必须各判一次（回放里按 as-of 日判，不吃未来）。"""
    seen = []
    monkeypatch.setattr(WC, "QUALIFY_STRUCT_ON", True)
    monkeypatch.setattr(WC, "_theme_index_series", lambda th: _series())
    monkeypatch.setattr(WC, "_index_not_broken", lambda day="": (seen.append(day) or (True, "指数未破位")))
    WC.theme_qualify_struct("半导体/芯片", day="20260105")
    WC.theme_qualify_struct("半导体/芯片", day="20260323")
    assert seen == ["20260105", "20260323"]


# ── B1 → 仓位档位 ─────────────────────────────────────────────────────────────
def test_B1_未确认但B合格_满档(monkeypatch):
    monkeypatch.setattr(WC, "TIER_GATE_ON", True)
    monkeypatch.setattr(WC, "theme_structure", lambda th: {"stage": "not_confirmed"})
    monkeypatch.setattr(WC, "theme_qualify_struct", lambda th, **kw: (True, "资格不撤：未创新低 ∧ 指数未破位"))
    f, r = WC.theme_tier_factor("半导体/芯片")
    assert f == 1.0 and "B 资格不撤" in r


def test_B1_未确认且B不合格_半档(monkeypatch):
    monkeypatch.setattr(WC, "TIER_GATE_ON", True)
    monkeypatch.setattr(WC, "theme_structure", lambda th: {"stage": "not_confirmed"})
    monkeypatch.setattr(WC, "theme_qualify_struct", lambda th, **kw: (False, "指数破位"))
    f, r = WC.theme_tier_factor("半导体/芯片")
    assert f == 0.5


def test_B1_已确认_满档且不必判B(monkeypatch):
    monkeypatch.setattr(WC, "TIER_GATE_ON", True)
    monkeypatch.setattr(WC, "theme_structure", lambda th: {"stage": "confirmed"})

    def _boom(*a, **kw):
        raise AssertionError("confirmed 时不应调用 B 判定")

    monkeypatch.setattr(WC, "theme_qualify_struct", _boom)
    f, _r = WC.theme_tier_factor("半导体/芯片")
    assert f == 1.0


# ── B2：合格主题放宽个股 stage ────────────────────────────────────────────────
def _stub_env(monkeypatch, qualify):
    """top1..2 = T1；stock_confirm_result 里 4 只票各占一个 stage。"""
    m = types.ModuleType("mainline_confirm_state")
    m.mainline_top_themes = lambda n=3: (["T1"], "stub")
    m.gate_top_themes = lambda n=2: (["T1"], "stub")
    monkeypatch.setitem(sys.modules, "mainline_confirm_state", m)
    sc = {"c1": {"theme": "T1", "stocks": [
        {"code": "600001.SH", "stage": "确认"},
        {"code": "600002.SH", "stage": "突破候选"},
        {"code": "600003.SH", "stage": "结构到位"},
        {"code": "600004.SH", "stage": "缩量止跌"},
        {"code": "600005.SH", "stage": "下跌中"},
    ]}}
    ml = {"fusion": {"T1": {"score": 9.0}, "T2": {"score": 1.0}}}   # 默认来源=fusion（现状口径）
    monkeypatch.setattr(SB, "_load", lambda name: (sc if name == "stock_confirm_result.json" else ml))
    monkeypatch.setattr(SB, "_dip_on", lambda: True)
    monkeypatch.setattr(WC, "theme_qualify_struct", lambda th, **kw: (True, "资格不撤") if qualify else (False, "指数破位"))


def test_B2_B合格_stage_放宽到结构到位与缩量止跌(monkeypatch):
    _stub_env(monkeypatch, qualify=True)
    hit, _ = SB.active_stocks_by(SB.BASE_STAGES, stages_for=SB.stages_for_theme)
    codes = sorted(hit.keys())               # 键 = 6 位代码
    assert "600003" in codes and "600004" in codes
    assert "600005" not in codes             # 下跌中**不接**（语料：挖坑段不加/结构未确认不接）


def test_B2_B不合格_只留旧口径(monkeypatch):
    _stub_env(monkeypatch, qualify=False)
    hit, _ = SB.active_stocks_by(SB.BASE_STAGES, stages_for=SB.stages_for_theme)
    assert sorted(hit.keys()) == ["600001", "600002"]


def test_B2_开关关闭_即使B合格也不放宽(monkeypatch):
    _stub_env(monkeypatch, qualify=True)
    monkeypatch.setattr(SB, "_dip_on", lambda: False)
    assert SB.stages_for_theme("T1") == SB.BASE_STAGES
    hit, _ = SB.active_stocks_by(SB.BASE_STAGES, stages_for=SB.stages_for_theme)
    assert sorted(hit.keys()) == ["600001", "600002"]


def test_B2_无主题_不改口径(monkeypatch):
    _stub_env(monkeypatch, qualify=True)
    assert SB.stages_for_theme("") == SB.BASE_STAGES


# ── 真实序列（有沙箱就跑；没有则跳过）─────────────────────────────────────────
def test_真实数据_20260105合格_20260323撤资格():
    sb = os.path.join(_ROOT, "data", "_bt_jan9", "20260105")
    if not os.path.isdir(sb):
        pytest.skip("缺沙箱 data/_bt_jan9/20260105")
    old = os.environ.get("DATA_DIR")
    os.environ["DATA_DIR"] = sb
    try:
        import importlib
        wc = importlib.reload(WC)
        wc.QUALIFY_STRUCT_ON = True
        wc._QUAL_CACHE.clear()
        ok5, why5 = wc.theme_qualify_struct("半导体/芯片", day="20260105")
        ok3, why3 = wc.theme_qualify_struct("半导体/芯片", day="20260323")
        assert ok5 is True, why5
        assert ok3 is False, why3
    finally:
        if old is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = old
        import importlib
        importlib.reload(WC)
