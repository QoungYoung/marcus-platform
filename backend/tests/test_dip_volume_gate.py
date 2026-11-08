# -*- coding: utf-8 -*-
"""放量破位门单测（2026-09-19 用户拍板 A）。

语料：254 低吸 =「破前低 + **缩量**」；2025-06-25「只要看见没量就别追」；2026-01-12「等收盘确认破位」。
动机：0107 13:45 SH601138 的回踩买点，jan11 的 AI 判 await_retry（「创日内新低并放量 6.85M ⇒ 下跌未止」），
jan12 的 AI 判 executed（「未超前根 7.75M ⇒ 缩量回踩」）——同一份量能数据两种解读，因该侧无确定性判据。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_dip_volume_gate as G  # noqa: E402


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(G, "ON", True)
    monkeypatch.setattr(G, "EXPAND", 1.5)
    monkeypatch.setattr(G, "PREVLOW_TOL", 0.005)


def test_放量破前低拦():
    ok, why = G.verdict(63.0, 62.62, 1.8)
    assert ok is False and "放量破前低" in why and "缩量" in why


def test_缩量破前低放行():
    ok, why = G.verdict(63.0, 62.62, 0.8)
    assert ok is True and "缩量" in why


def test_未破前低放行():
    assert G.verdict(63.0, 63.6, 2.0)[0] is True


def test_贴着前低但未破放行():
    assert G.verdict(63.0, 63.4, 1.9)[0] is True


def test_缺数据failopen():
    assert G.verdict(None, 62.0, 2.0)[0] is True
    assert G.verdict(63.0, None, 2.0)[0] is True
    assert G.verdict(63.0, 62.0, None)[0] is True


def test_卖腿不适用():
    assert G.verdict(63.0, 62.0, 2.0, side="sell")[0] is True


def test_开关关闭放行(monkeypatch):
    monkeypatch.setattr(G, "ON", False)
    assert G.verdict(63.0, 62.0, 2.0)[0] is True


def test_阈值可调(monkeypatch):
    monkeypatch.setattr(G, "EXPAND", 2.5)
    assert G.verdict(63.0, 62.62, 1.8)[0] is True, "阈值调高后同一情形不算放量"


def test_接线_网关已接且monitor写快照():
    gw = (REPO_ROOT / "backend" / "app" / "services" / "t_gateway.py").read_text(encoding="utf-8")
    mon = (REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py").read_text(encoding="utf-8")
    assert "wolf_dip_volume_gate" in gw and "DIP_VOL" in gw, "网关没接放量破位门"
    assert "prev_low" in mon and "vol_ratio" in mon, "monitor 没把前低/量比写进触发快照"

# ── 回归：触发快照富化不得打断 wolf_t_rules（2026-09-19 事故）─────────────────────────
#
# 事故：`_insert_wolf_trigger` 里按 **dict** 读 `_prev_daily()`，而它实际返回 **list[dict]** ⇒
# `.get` 打到 list ⇒ `wolf_t_rules检查异常: 'list' object has no attribute 'get'` 每标的都抛
# ⇒ 全天零触发、零成交（用户："完全没有买入了"）。

def test_快照富化_prev_daily为list时不抛异常并写入字段(monkeypatch):
    import app.services.t_monitor as M
    caught = {}

    class _FakeDB:
        @staticmethod
        def insert_trigger(trig, status="pending"):
            caught["trig"] = trig
            return 4242

    monkeypatch.setattr(M, "t_db", _FakeDB, raising=False)
    import types as _t
    mute = _t.ModuleType("app.services.t_trigger_mute")
    mute.is_muted = lambda *a, **k: False
    monkeypatch.setitem(sys.modules, "app.services.t_trigger_mute", mute)

    mon = M.TMonitor()
    # _prev_daily 的真实形状 = list[dict]（按日升序，最后一个是"前一日"）
    monkeypatch.setattr(mon, "_prev_daily",
                        lambda sym, n=5: [{"close": 63.0, "high": 64.0, "low": 62.5, "vol": 1},
                                          {"close": 63.6, "high": 64.9, "low": 62.9, "vol": 1}])
    quote = {"current": 64.64, "low": 62.9, "high": 64.97, "vol_ratio": 1.2}
    tid = mon._insert_wolf_trigger("SH601138", "wolf_zheng_t_buy", quote, "test")
    assert tid == 4242, "快照富化把 wolf 触发打断了（事故回归）"
    sn = caught["trig"]["snapshot"]
    assert sn.get("prev_low") == 62.9, sn
    assert sn.get("today_low") == 62.9, sn
    assert sn.get("vol_ratio") == 1.2, sn
    assert sn.get("day_rise_pct") is not None


def test_快照富化_prev_daily为空也不抛(monkeypatch):
    import app.services.t_monitor as M
    caught = {}

    class _FakeDB:
        @staticmethod
        def insert_trigger(trig, status="pending"):
            caught["trig"] = trig
            return 1

    monkeypatch.setattr(M, "t_db", _FakeDB, raising=False)
    import types as _t
    mute = _t.ModuleType("app.services.t_trigger_mute")
    mute.is_muted = lambda *a, **k: False
    monkeypatch.setitem(sys.modules, "app.services.t_trigger_mute", mute)
    mon = M.TMonitor()
    monkeypatch.setattr(mon, "_prev_daily", lambda sym, n=5: [])
    assert mon._insert_wolf_trigger("SH601138", "wolf_zheng_t_buy",
                                    {"current": 10.0}, "t") == 1
    assert caught["trig"]["snapshot"].get("prev_low") is None

