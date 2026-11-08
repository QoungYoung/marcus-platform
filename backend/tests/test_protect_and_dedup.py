# -*- coding: utf-8 -*-
"""A′（保护性判定结构化）与 B′-1（同价重复兑现互斥）单测。

起点：T6/drabt6 2026-01-07 09:35 SZ002156 同分钟同价三条止盈腿各卖一次（1,400 股）。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_protect as P          # noqa: E402
from app.services import t_sell_dedup as D       # noqa: E402
from app.services import t_gateway as G          # noqa: E402
from app.services import t_turnover as T         # noqa: E402

# 实测里那条 AI 理由（含否定句「未跌破止损」）
AI_REASON = "高抛卖腿为正向兑现：现价39.98=建议卖价（价差0%），成本38.686浮盈3.35%，未跌破止损，regime震荡"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_PROTECT_STRUCTURED", "WOLF_PROTECT_KINDS", "WOLF_SELL_DEDUP",
              "WOLF_SELL_DEDUP_MIN", "WOLF_SELL_DEDUP_PCT", "WOLF_SELL_DEDUP_KINDS"):
        monkeypatch.delenv(k, raising=False)


# ── A′ ───────────────────────────────────────────────────────
def test_A_默认关等于旧行为():
    assert P.structured_on() is False
    assert P.is_protective(trigger_kind="wolf_profit_take_sell", reason=AI_REASON) is True, "旧行为：文本命中『止损』⇒ 豁免"


def test_A_买腿永不豁免(monkeypatch):
    """回归（自查抓到）：`is_protective` 是卖出语义，买腿传进来必须恒 False，
    否则「未跌破止损」这类理由会把买腿的换手闸豁免又放回来。"""
    assert P.is_protective(trigger_kind="wolf_zheng_t_buy", reason=AI_REASON, side="buy") is False
    monkeypatch.setenv("WOLF_PROTECT_STRUCTURED", "1")
    assert P.is_protective(trigger_kind="stop_loss", reason="", side="buy") is False, "买腿连止损腿型都不认"
    assert G.trade_cap_ok(2, 2, reason=AI_REASON, side="buy", trigger_kind="wolf_zheng_t_buy") is False


def test_A_开启后只认结构化腿型(monkeypatch):
    monkeypatch.setenv("WOLF_PROTECT_STRUCTURED", "1")
    assert P.is_protective(trigger_kind="wolf_profit_take_sell", reason=AI_REASON) is False, "止盈腿 + 含『止损』的文本 ⇒ 不再豁免"
    assert P.is_protective(trigger_kind="stop_loss", reason="") is True
    assert P.is_protective(trigger_kind="custom_support_sell", reason="") is True, "破位"
    assert P.is_protective(trigger_kind="derisk_cut", reason="") is True, "风控减仓"
    assert P.is_protective(trigger_kind="", reason=AI_REASON) is False, "取不到腿型 ⇒ 保守按非保护性"


def test_A_G6上限恢复(monkeypatch):
    # 旧行为：理由含「止损」⇒ 第 3 笔也放行
    assert G.trade_cap_ok(2, 2, reason=AI_REASON) is True
    monkeypatch.setenv("WOLF_PROTECT_STRUCTURED", "1")
    assert G.trade_cap_ok(2, 2, reason=AI_REASON, trigger_kind="wolf_fib_target_sell") is False, "第 3 笔应被上限拦住"
    assert G.trade_cap_ok(2, 2, is_stop_loss=True, trigger_kind="stop_loss") is True, "真止损仍豁免"
    assert G.trade_cap_ok(1, 2, trigger_kind="wolf_fib_target_sell") is True, "未到上限"


def test_A_换手闸不再被理由文本旁路(monkeypatch):
    monkeypatch.setenv("WOLF_PROTECT_STRUCTURED", "1")
    monkeypatch.setattr(T, "GUARD_ON", True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    import datetime as _dt
    now = _dt.datetime(2026, 1, 7, 11, 0)
    # `high_sell` 在 ③ 时段窗白名单里（wolf_fib_target_sell 不在 ⇒ 不受 ③ 约束，别拿它测）
    ok, why = T.check("SZ002156", "sell", 39.98, "drabt6", reason=AI_REASON,
                      trigger_kind="high_sell", now=now)
    assert ok is False and "做T时段窗" in why, "11:00 不在窗口内，且不再靠『未跌破止损』豁免"


# ── B′-1 ─────────────────────────────────────────────────────
def test_B_默认关():
    assert D.enabled() is False
    ok, why = D.check("drabt6", "SZ002156", 39.98, trigger_kind="wolf_fib_target_sell")
    assert ok is True and "关闭" in why


def test_B_同类腿型与参数(monkeypatch):
    monkeypatch.setenv("WOLF_SELL_DEDUP", "1")
    assert D.is_take("wolf_fib_target_sell") and D.is_take("wolf_profit_take_sell")
    assert not D.is_take("wolf_defensive_t_reduce"), "防守减仓不是止盈类（不受本闸约束）"
    assert not D.is_take("stop_loss")
    assert D.window_min() == 15 and D.tol_pct() == 0.5


def test_B_同价同窗判重复(monkeypatch):
    monkeypatch.setenv("WOLF_SELL_DEDUP", "1")
    import datetime as _dt
    t0 = _dt.datetime(2026, 1, 7, 9, 35)
    t1 = _dt.datetime(2026, 1, 7, 9, 36)
    # 桩：窗口内已有一笔同价成交
    monkeypatch.setattr(D, "_recent_sells", lambda *a, **k: [(t0, 39.98, 500)])
    ok, why = D.check("drabt6", "SZ002156", 39.98, trigger_kind="wolf_fib_target_sell", now=t1)
    assert ok is False and "同价重复兑现" in why
    # 价差 1% > 容差 ⇒ 放行
    ok2, _ = D.check("drabt6", "SZ002156", 40.38, trigger_kind="wolf_fib_target_sell", now=t1)
    assert ok2 is True
    # 超过窗口（20 分钟）⇒ 放行
    ok3, _ = D.check("drabt6", "SZ002156", 39.98, trigger_kind="wolf_fib_target_sell",
                     now=t0 + _dt.timedelta(minutes=20))
    assert ok3 is True
    # 非止盈类 ⇒ 放行
    ok4, _ = D.check("drabt6", "SZ002156", 39.98, trigger_kind="wolf_defensive_t_reduce", now=t1)
    assert ok4 is True


def test_B_无历史成交与取数失败都放行(monkeypatch):
    monkeypatch.setenv("WOLF_SELL_DEDUP", "1")
    monkeypatch.setattr(D, "_recent_sells", lambda *a, **k: [])
    assert D.check("drabt6", "SZ002156", 39.98, trigger_kind="high_sell")[0] is True
    monkeypatch.setattr(D, "_recent_sells", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert D.check("drabt6", "SZ002156", 39.98, trigger_kind="high_sell")[0] is True


def test_接线_网关与开关默认关():
    src = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
    assert 'level": "SELL_DEDUP"' in src and "t_sell_dedup" in src
    assert "t_protect" in src, "A′ 必须接到网关（G6 与时点门）"
    _tp = (REPO_ROOT / "backend/app/services/t_protect.py").read_text(encoding="utf-8")
    _sd = (REPO_ROOT / "backend/app/services/t_sell_dedup.py").read_text(encoding="utf-8")
    assert 'ENV = "WOLF_PROTECT_STRUCTURED"' in _tp and 'os.getenv(ENV, "0")' in _tp, "库内默认 0"
    assert 'ENV = "WOLF_SELL_DEDUP"' in _sd and 'os.getenv(ENV, "0")' in _sd, "库内默认 0"


# ── 同轮监控上限（2026-09-22 又一例"池子一大就随机吞腿"）──────────────
def test_监控上限可配且持仓优先():
    import os as _os
    src = (REPO_ROOT / "backend/app/services/t_monitor.py").read_text(encoding="utf-8")
    assert 'os.getenv("WOLF_MAX_CORE_SYMBOLS"' in src, "上限必须可配（库内默认 20 = 旧行为）"
    assert "sorted(_held) + sorted(_all_syms - _held)" in src, "持仓优先 + 确定性排序"
    assert "本轮不评估" in src, "截断必须打日志（原来完全静默）"
    assert "_core_cap_warned" in src, "告警每日一次，防刷屏"


def test_取价与遍历顺序都是确定的():
    """防止"集合乱序取前 N"复发：①取价名单 sorted；②条件遍历顺序由 SQL ORDER BY 决定。"""
    mon = (REPO_ROOT / "backend/app/services/t_monitor.py").read_text(encoding="utf-8")
    db = (REPO_ROOT / "backend/app/services/t_db.py").read_text(encoding="utf-8")
    assert "list({c[\"symbol\"] for c in conditions})[:" not in mon.replace(
        "# ⚠️ 2026-09-22 修：原实现 `list({c[\"symbol\"] for c in conditions})[:MAX_CORE_SYMBOLS]`", ""), \
        "反模式（集合乱序取前 N）不得复活"
    assert "sorted(_held) + sorted(_all_syms - _held)" in mon, "取价名单必须确定性排序"
    assert "syms = sorted({c.get('symbol') for c in conds" in mon, "wolf 腿路径本来就 sorted（保持）"
    assert "ORDER BY trade_date DESC, id DESC" in db, "条件遍历顺序由 SQL 固定（trade_date DESC, id DESC）"


# ── T0/T1/T2-a~d 候选优先度排序（2026-09-22 用户拍板）──────────────────
def test_优先度排序_分层顺序(monkeypatch, tmp_path):
    """T0 持仓 → T1 有卖腿 → T2a 主线>次线 → T2c 在低吸腿池 → T2d 代码升序。"""
    import json as _j
    from app.services import t_monitor as M
    (tmp_path / "main_line_state.json").write_text(_j.dumps({
        "mainline_select": {"mainline": "半导体/芯片", "second": "新能源/电池"}}), encoding="utf-8")
    (tmp_path / "legs_switch.jsonl").write_text("\n".join([
        _j.dumps({"symbol": "SZ000002", "theme": "新能源/电池"}),
        _j.dumps({"symbol": "SZ000003", "theme": "半导体/芯片"}),
    ]), encoding="utf-8")
    (tmp_path / "legs.jsonl").write_text(_j.dumps({"symbol": "SZ000009", "theme": "trend"}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    mon = M.TMonitor.__new__(M.TMonitor)          # 只测排序键，不启动监控
    ctx = mon._prio_ctx("20260105")
    assert ctx["main"] == "半导体/芯片" and ctx["second"] == "新能源/电池"
    assert ctx["pool"] == {"SZ000002", "SZ000003"}
    # 持仓 SZ000009（无卖腿）；SZ000004 有卖腿；SZ000003 主线；SZ000002 次线；SZ000001 其他
    conds = [{"symbol": "SZ000004", "direction": "sell"}, {"symbol": "SZ000003", "direction": "buy"},
             {"symbol": "SZ000002", "direction": "buy"}, {"symbol": "SZ000001", "direction": "buy"}]
    key = mon._core_sort_key(conds, held={"SZ000009"})
    order = sorted(["SZ000001", "SZ000002", "SZ000003", "SZ000004", "SZ000009"], key=key)
    want = ["SZ000009",   # T0 持仓
            "SZ000004",   # T1 有卖腿
            "SZ000003",   # T2a 主线
            "SZ000002",   # T2a 次线
            "SZ000001"]   # 其他 → 代码升序兜底
    assert order == want, order


def test_优先度_取不到上下文时回落代码序(monkeypatch, tmp_path):
    from app.services import t_monitor as M
    monkeypatch.setenv("DATA_DIR", str(tmp_path))      # 空目录：两个文件都没有
    mon = M.TMonitor.__new__(M.TMonitor)
    key = mon._core_sort_key([], held=set())
    assert sorted(["SZ000002", "SZ000001"], key=key) == ["SZ000001", "SZ000002"]
    src = (REPO_ROOT / "backend/app/services/t_monitor.py").read_text(encoding="utf-8")
    assert 'os.getenv("WOLF_CORE_PRIORITY", "0")' in src, "库内默认 0（生产零影响）"
    assert "回落代码序" in src, "异常必须 fail-open 到确定性顺序"
