# -*- coding: utf-8 -*-
"""主题档位闸（语料档位）+ _latest as-of 过滤 + leg_gate ④ 破位/急杀 单测。

对应 2026-09-19 用户拍板的三件事：
  ① wolf_context._latest() 加 as-of 过滤（不读晚于钉住当日的文件）
  ② 可买门「主题结构未确认」从二元硬拦改语料档位（confirmed 满档 / not_confirmed·suspect 半档）
  ③ 打开底仓锚「浮亏限定版」WOLF_BASE_FLOOR_LOSS_EXEMPT（驱动里置 1；2026-09-25 用户拍板）
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wolf_context as WC      # noqa: E402
import leg_gate as LG          # noqa: E402


class _NoVolFund:
    """wolf_theme_vol_fund 的替身：全关。"""
    @staticmethod
    def enabled():
        return False

    @staticmethod
    def shadow_enabled():
        return False

    @staticmethod
    def theme_volfund_ok(theme):
        return True, "stub"

    @staticmethod
    def shadow_record(*a, **kw):
        return None

    @staticmethod
    def failclosed_enabled():
        return False

    @staticmethod
    def notify_once(*a, **kw):
        return None


def _stub_env(monkeypatch):
    monkeypatch.setitem(sys.modules, "wolf_theme_vol_fund", _NoVolFund)
    monkeypatch.setattr(WC, "theme_fund_danger", lambda t: (False, ""))
    _m = types.ModuleType("theme_member_llm")
    _m.is_member = lambda symbol, concepts, theme: (True, "stub")
    monkeypatch.setitem(sys.modules, "theme_member_llm", _m)


def test_存在性_开关与函数():
    assert callable(WC.theme_tier_factor) and callable(WC.theme_buyable)
    assert WC.THEME_STAGE_TIER["confirmed"] == 1.0
    assert WC.THEME_STAGE_TIER["not_confirmed"] == 0.5
    assert WC.THEME_STAGE_TIER["suspect"] == 0.5
    assert callable(LG.symbol_broken) and callable(LG._recent_daily)
    _drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_THEME_TIER_GATE", "1")' in _drv
    assert '("WOLF_ASOF_FILE_FILTER", "1")' in _drv
    assert '("WOLF_BASE_FLOOR_LOSS_EXEMPT", "1")' in _drv      # 浮亏限定版（2026-09-25 开启；盈利票仍不豁免）
    assert '("WOLF_MA5_EXIT", "1")' in _drv                    # 破5日线丢（语料内）保留
    _tb = open(os.path.join(_ROOT, "backend", "app", "services", "t_build.py"), encoding="utf-8").read()
    assert "tier_factor" in _tb


def test_档位系数映射(monkeypatch):
    monkeypatch.setattr(WC, "TIER_GATE_ON", True)
    for _stage, _exp in (("confirmed", 1.0), ("suspect", 0.5),
                         ("not_confirmed", 0.5), ("window_limited", 0.5)):
        monkeypatch.setattr(WC, "theme_structure", lambda t, s=_stage: {"stage": s})
        f, why = WC.theme_tier_factor("半导体/芯片")
        assert abs(f - _exp) < 1e-9, (f, _stage, why)
    monkeypatch.setattr(WC, "theme_structure", lambda t: {})
    assert WC.theme_tier_factor("半导体/芯片")[0] == 1.0          # 数据缺失 fail-open
    monkeypatch.setattr(WC, "theme_structure", lambda t: {"stage": "unknown_x"})
    assert WC.theme_tier_factor("半导体/芯片")[0] == 0.0          # 未知档 ⇒ 不买


def test_关闸时恒为满档(monkeypatch):
    monkeypatch.setattr(WC, "TIER_GATE_ON", False)
    monkeypatch.setattr(WC, "theme_structure", lambda t: {"stage": "not_confirmed"})
    assert WC.theme_tier_factor("半导体/芯片")[0] == 1.0
    ok, why = WC.theme_buyable("半导体/芯片")                     # 关闸 ⇒ 原硬拦口径
    assert ok is False and "结构未确认" in why


def test_可买门_档位放行但资金门仍拦(monkeypatch):
    _stub_env(monkeypatch)
    monkeypatch.setattr(WC, "TIER_GATE_ON", True)
    monkeypatch.setattr(WC, "theme_structure", lambda t: {"stage": "not_confirmed"})
    ok, why = WC.theme_buyable("半导体/芯片")
    assert ok is True and "档位放行" in why, why
    monkeypatch.setattr(WC, "theme_fund_danger", lambda t: (True, "资金连续流出"))
    ok2, why2 = WC.theme_buyable("半导体/芯片")                   # 结构没确认只降档，资金危险仍拦
    assert ok2 is False and "资金" in why2, why2


def test_latest_asof过滤(monkeypatch, tmp_path):
    (tmp_path / "zz_20000103.json").write_text("{}", encoding="utf-8")
    (tmp_path / "zz_20990101.json").write_text("{}", encoding="utf-8")   # 相对任何真实时钟都是"未来"
    (tmp_path / "zz_params.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(WC, "DATA", str(tmp_path))
    monkeypatch.setattr(WC, "ASOF_FILE_FILTER", True)
    f, d = WC._latest("zz_")
    assert d == "20000103" and f.endswith("zz_20000103.json"), (f, d)
    monkeypatch.setattr(WC, "ASOF_FILE_FILTER", False)
    f2, d2 = WC._latest("zz_")
    assert d2 == "20990101", (f2, d2)                                    # 关过滤 ⇒ 旧行为（读最大日期）


def _mk(closes, lows=None, vols=None):
    """构造 _recent_daily 的替身（dict: close/low/vol，升序）。"""
    lows = lows or [c * 0.99 for c in closes]
    vols = vols or [100.0] * len(closes)
    return [{"close": c, "low": l, "vol": v} for c, l, v in zip(closes, lows, vols)]


def test_不接急杀_语料口径两条(monkeypatch):
    """④ 只留语料里真实存在的两条：跌停 / 放量破前低。

    （"急杀"已去掉——语料里 253 急杀是**买点**，实测被拦的急杀腿 D+10 +5.9%/+6.2%。）
    """
    # ① 跌停：最近一日 −10%
    monkeypatch.setattr(LG, "_recent_daily", lambda s, n=21: _mk([10.0] * 6 + [9.0]))
    b, w = LG.symbol_broken("SH603203"); assert b is True and "跌停" in w, w
    # ② 急杀 −6% 但未破前低 ⇒ **放行**（新口径）
    monkeypatch.setattr(LG, "_recent_daily",
                        lambda s, n=21: _mk([10.0] * 6 + [9.4], lows=[9.0] * 7, vols=[100.0] * 6 + [300.0]))
    assert LG.symbol_broken("SH603203")[0] is False
    # ③ 放量破前低（−4%，未触发跌停；收盘 9.6 < 前一日最低 9.9；量比 3.0）
    monkeypatch.setattr(LG, "_recent_daily",
                        lambda s, n=21: _mk([10.0] * 6 + [9.6], lows=[9.9] * 6 + [9.5],
                                            vols=[100.0] * 6 + [300.0]))
    b, w = LG.symbol_broken("SH603203"); assert b is True and "破前低" in w, w
    # ④ 缩量破前低 ⇒ 254 买点，放行（量比 0.5 ≤ 0.9）
    monkeypatch.setattr(LG, "_recent_daily",
                        lambda s, n=21: _mk([10.0] * 6 + [9.6], lows=[9.9] * 6 + [9.5],
                                            vols=[100.0] * 6 + [50.0]))
    assert LG.symbol_broken("SH603203")[0] is False
    # ⑤ 未破前低 ⇒ 放行
    monkeypatch.setattr(LG, "_recent_daily",
                        lambda s, n=21: _mk([10.0] * 6 + [10.2], lows=[9.5] * 7))
    assert LG.symbol_broken("SH603203")[0] is False
    # ⑥ 数据不足 ⇒ fail-open
    monkeypatch.setattr(LG, "_recent_daily", lambda s, n=21: [])
    assert LG.symbol_broken("SH603203")[0] is False


def test_指数破位_不新开(monkeypatch):
    """弱市不进新票：指数大级别破位 ⇒ 新票腿 0（做T照旧）。"""
    monkeypatch.setattr(LG, "INDEX_NEW_BUY_ON", True)
    monkeypatch.setattr(LG, "_index_broken", lambda day="": (True, "指数破位: 收盘 3957.05 跌破 [60, 144]（2 根，连续 1 日）"))
    bk, why = LG.index_new_buy_blocked()
    assert bk is True and "指数破位" in why and "不新开" in why, why
    monkeypatch.setattr(LG, "_index_broken", lambda day="": (False, ""))
    assert LG.index_new_buy_blocked()[0] is False
    monkeypatch.setattr(LG, "INDEX_NEW_BUY_ON", False)
    monkeypatch.setattr(LG, "_index_broken", lambda day="": (True, "指数破位"))
    assert LG.index_new_buy_blocked()[0] is False          # 开关关 ⇒ 不拦
    _drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_INDEX_NEW_BUY", "1")' in _drv


def test_主营校验口径(monkeypatch):
    """主营校验＝拦"概念错配"（语料要求的正宗度校验，数据缺口用 dsh 代理）：

    库内默认关（生产零影响）／回测默认开；显式 0/1 可覆盖；dsh=否 落复核清单。
    """
    import importlib
    import theme_member_llm as TM

    monkeypatch.setenv("BT_ASOF_FETCH", "1")
    monkeypatch.delenv("WOLF_THEME_MEMBER_CHECK", raising=False)
    importlib.reload(TM)
    assert TM.ON is True                                   # 回测默认开
    monkeypatch.setenv("WOLF_THEME_MEMBER_CHECK", "0")
    importlib.reload(TM)
    assert TM.ON is False and TM.is_member("SH600658", ["半导体概念"], "半导体/芯片") == (True, "off")
    monkeypatch.delenv("BT_ASOF_FETCH", raising=False)
    monkeypatch.delenv("WOLF_THEME_MEMBER_CHECK", raising=False)
    importlib.reload(TM)
    assert TM.ON is False                                  # 生产默认关（零影响）
    monkeypatch.setenv("BT_ASOF_FETCH", "1")
    importlib.reload(TM)
    _drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_THEME_MEMBER_CHECK", "1")' in _drv
    assert callable(LG._record_member_reject)               # 代理判定必须留复核痕迹


def test_approve_第4步破位即拒(monkeypatch):
    _stub_env(monkeypatch)
    monkeypatch.setattr(LG, "ON", True)
    monkeypatch.setattr(LG, "TIER_GATE_ON", True)
    monkeypatch.setattr(WC, "theme_buyable", lambda t: (True, ""))
    monkeypatch.setattr(LG, "symbol_broken", lambda s: (True, "不接急杀: 最近一日 -6.00%（≤ -5.0%）"))
    ok, why = LG.approve("SH603203", "半导体/芯片")
    assert ok is False and "急杀" in why, why
    monkeypatch.setattr(LG, "TIER_GATE_ON", False)
    assert LG.approve("SH603203", "半导体/芯片")[0] is True               # 关档位闸 ⇒ 第④步不生效

def test_人工白名单放行(monkeypatch):
    """概念表不可信 ⇒ 放行只靠人工点名 WOLF_MEMBER_WHITELIST（不靠概念自动放行）。"""
    import importlib
    import theme_member_llm as TM

    monkeypatch.setenv("BT_ASOF_FETCH", "1")
    monkeypatch.delenv("WOLF_THEME_MEMBER_CHECK", raising=False)
    monkeypatch.setenv("WOLF_MEMBER_WHITELIST", "SH603203,300316")
    importlib.reload(TM)
    assert TM.ON is True
    assert TM.is_member("SH603203", ["CPO概念"], "半导体/芯片") == (True, "whitelist")
    assert TM.is_member("300316.SZ", ["第三代半导体"], "AI/算力/科技") == (True, "whitelist")
    monkeypatch.setenv("WOLF_MEMBER_WHITELIST", "")
    importlib.reload(TM)
    assert TM._in_whitelist("SH603203") is False

def test_执行口腿闸(monkeypatch):
    """执行口二次把关：布腿之外（建议买价/条件单/agent 直下）也要过同一把闸。

    实测背景：SH603203 在布腿口被主营校验拒 3 次，却仍从"建议买价"直接买入 ⇒ 闸必须落在
    `gateway_execute` 这个唯一下单入口（只拦买入）。
    """
    import types
    if os.path.join(_ROOT, "backend") not in sys.path:
        sys.path.insert(0, os.path.join(_ROOT, "backend"))
    from app.services import t_gateway as G

    monkeypatch.setattr(G, "EXEC_GATE_ON", True)
    _m = types.ModuleType("leg_gate")
    _m.ON = True
    _m.approve = lambda sym, th, **kw: (False, "主营校验 dsh=无关（不属于 AI/算力/科技）")
    monkeypatch.setitem(sys.modules, "leg_gate", _m)
    _why = G._leg_gate_exec("SH603203")
    assert _why and "主营校验" in _why, _why
    _m.approve = lambda sym, th, **kw: (True, "")
    assert G._leg_gate_exec("SH603203") is None            # 过闸 ⇒ 放行
    _m.approve = lambda sym, th, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    assert G._leg_gate_exec("SH603203") is None            # 异常 ⇒ fail-open
    monkeypatch.setattr(G, "EXEC_GATE_ON", False)
    _m.approve = lambda sym, th, **kw: (False, "主营校验 dsh=无关")
    assert G._leg_gate_exec("SH603203") is None            # 开关关 ⇒ 不拦
    _drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_EXEC_GATE", "1")' in _drv
