# -*- coding: utf-8 -*-
"""break_policy 单测（BreakStop v1 / P3）。

覆盖 2026-10-23 用户拍板 A：
  · 指数级 = 唯一自动止损；个股 B2 默认只提示
  · B3-1 cap 50% / B3-2 cap 30% / B3-3 down-wave cap 0-30%，默认 0
  · 30% 是 cap 不是 floor；2025-11-21 的 20% 允许更低
  · 地量/跌停不割；深亏无指数破位不自动砍
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services import break_policy as BP  # noqa: E402


def _sig(status="confirmed", typ="ma60", branch="direct", confirmed=None, **kw):
    s = {
        "status": status, "level_type": typ, "level_price": 100.0,
        "vol_ratio": 1.5, "volume_branch": branch,
        "confirmed": (status in ("confirmed", "rebound_fail")) if confirmed is None else confirmed,
    }
    s.update(kw)
    return s


def test_b3_1_cap_50():
    r = BP.decide(index_signal=_sig(), wave_op="build", broken_major=1)
    assert r["severity"] == "B3-1"
    assert r["action"] == "cap_50"
    assert r["target_pct"] == 0.50
    assert r["block_new_buy"] is True


def test_b3_2_two_major_caps_30():
    r = BP.decide(index_signal=_sig(), wave_op="build", broken_major=2)
    assert r["severity"] == "B3-2"
    assert r["action"] == "cap_30"
    assert r["target_pct"] == 0.30


def test_b3_2_platform_volume_caps_30():
    r = BP.decide(index_signal=_sig(typ="platform_low_20", branch="direct"),
                  wave_op="side", broken_major=1)
    assert r["severity"] == "B3-2"
    assert r["target_pct"] == 0.30


def test_b3_3_exit_no_short_default_zero():
    r = BP.decide(wave_op="exit", position={"has_short_term_book": False})
    assert r["severity"] == "B3-3"
    assert r["action"] == "cap_0_30"
    assert r["target_pct"] == 0.0
    assert r["allow_zero"] is True
    assert r["block_new_buy"] is True


def test_b3_3_exit_with_short_caps_30():
    r = BP.decide(wave_op="exit", position={"has_short_term_book": True})
    assert r["severity"] == "B3-3"
    assert r["target_pct"] == 0.30
    assert r["allow_zero"] is True


def test_down_level_without_exit_is_b3_3():
    r = BP.decide(wave_op="side", wave_level="down", position={"has_short_term_book": False})
    assert r["severity"] == "B3-3"
    assert r["target_pct"] == 0.0


def test_exit_wave_overrides_index_signal():
    r = BP.decide(index_signal=_sig(), wave_op="exit", broken_major=2)
    assert r["severity"] == "B3-3"
    assert r["target_pct"] == 0.0


def test_defense_with_index_caps_30():
    r = BP.decide(index_signal=_sig(), wave_op="defense", broken_major=1)
    assert r["severity"] == "B3-1"
    assert r["target_pct"] == 0.30
    assert r["action"] == "cap_30"


def test_stock_b2_default_alert():
    r = BP.decide(stock_signal=_sig(typ="platform_low_20"), wave_op="side",
                  config={"stock_exec": False})
    assert r["severity"] == "B2"
    assert r["action"] == "alert"
    assert r["sell"] is False


def test_stock_b2_exec_reduce_half():
    r = BP.decide(stock_signal=_sig(typ="platform_low_20"), wave_op="side",
                  position={"pnl_pct": 5.0, "pos_class": "high"},
                  config={"stock_exec": True})
    assert r["severity"] == "B2"
    assert r["action"] == "reduce_half"
    assert r["sell"] is True


def test_stock_b2_deep_loss_does_not_auto_cut():
    r = BP.decide(stock_signal=_sig(typ="platform_low_20"), wave_op="side",
                  position={"pnl_pct": -15.0},
                  config={"stock_exec": True})
    assert r["severity"] == "B2"
    assert r["action"] == "alert"
    assert r["sell"] is False


def test_b0_intraday_observe():
    r = BP.decide(index_signal=_sig(status="intraday_break", confirmed=False),
                  wave_op="side")
    assert r["severity"] == "B0"
    assert r["action"] == "observe"
    assert r["sell"] is False


def test_b1_close_break_alert():
    r = BP.decide(index_signal=_sig(status="close_break", confirmed=False),
                  wave_op="side")
    assert r["severity"] == "B1"
    assert r["action"] == "alert"
    assert r["sell"] is False


def test_b4_default_alert_and_exec():
    r = BP.decide(time_stop_confirmed=True, wave_op="side")
    assert r["severity"] == "B4"
    assert r["action"] == "alert"
    r2 = BP.decide(time_stop_confirmed=True, wave_op="side",
                   position={"pnl_pct": 5.0},
                   config={"time_stop_exec": True})
    assert r2["severity"] == "B4"
    assert r2["action"] == "reduce_half"


def test_volume_dry_alerts_no_sell():
    r = BP.decide(index_signal=_sig(), wave_op="side", volume_dry=True)
    assert r["action"] == "alert"
    assert r["sell"] is False


def test_limit_down_alerts_no_sell():
    r = BP.decide(stock_signal=_sig(typ="platform_low_20"), wave_op="side",
                  limit_down=True, config={"stock_exec": True})
    assert r["action"] == "alert"
    assert r["sell"] is False

def test_target_override_lowers_b3_1_to_40():
    r = BP.decide(index_signal=_sig(), wave_op="build", broken_major=1,
                  target_override=0.40)
    assert r["target_pct"] == 0.40
    assert r["action"] == "cap_custom"


def test_target_override_lowers_b3_2_to_20():
    r = BP.decide(index_signal=_sig(), wave_op="build", broken_major=2,
                  target_override=0.20)
    assert r["target_pct"] == 0.20
    assert r["action"] == "cap_custom"


def test_target_override_is_authoritative():
    """点位表是权威口径：C1 3922→40 高于 B3-2 默认 30，也要按 40 执行。"""
    r = BP.decide(index_signal=_sig(), wave_op="build", broken_major=1,
                  target_override=0.40)
    assert r["target_pct"] == 0.40
    assert r["action"] == "cap_custom"


def test_target_override_down_wave_can_go_to_10():
    r = BP.decide(wave_op="exit", position={"has_short_term_book": True},
                  target_override=0.10)
    assert r["target_pct"] == 0.10
    assert r["action"] == "cap_custom"


def test_restore_to_base_when_no_signal():
    r = BP.decide(wave_op="side", base_target=1.0)
    assert r["severity"] == "B0"
    assert r["action"] == "restore"
    assert r["target_pct"] == 1.0
    assert r["sell"] is False


def test_reclaim_restores_base_target():
    r = BP.decide(index_signal=_sig(status="close_break", confirmed=False),
                  wave_op="side", base_target=1.0, reclaim=True)
    assert r["action"] == "restore"
    assert r["target_pct"] == 1.0


def test_caps_are_caps_not_floors():
    """B3-3 有短线仓 30%、无短线仓 0%；都 ≤30%，不是“必须留 30%”。"""
    a = BP.decide(wave_op="exit", position={"has_short_term_book": True})
    b = BP.decide(wave_op="exit", position={"has_short_term_book": False})
    assert a["target_pct"] == 0.30
    assert b["target_pct"] == 0.0
    assert a["target_is_cap"] and b["target_is_cap"]
