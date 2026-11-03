# -*- coding: utf-8 -*-
from app.services.exit_rule_engine import evaluate


def _b(t, o, h, l, c, v=100):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "vol": v}


def _snap(bars, current, pre=8.09, cost=7.693, symbol="SH600299", levels=None):
    return {"symbol": symbol, "asof": "20261103", "bars_5m": bars,
            "quote": {"current": current, "open": bars[0]["open"], "high": max(b["high"] for b in bars),
                      "low": min(b["low"] for b in bars), "pre_close": pre,
                      "average": 8.448, "vol": 100000, "amount": 10000},
            "position": {"original_volume": 1000, "current_volume": 500, "sellable": 500,
                         "avg_cost": cost, "sold_today": 0, "sold_since_entry": 500,
                         "remaining_cycle_budget": 500, "board_half_done": False},
            "levels": levels or []}


def test_accel_end_after_break():
    bars = [
        _b("202611030935", 8.2, 8.51, 8.13, 8.45, 200000),
        _b("202611030940", 8.45, 8.46, 8.28, 8.33, 100000),
        _b("202611031015", 8.56, 8.60, 8.53, 8.56, 20000),
        _b("202611031040", 8.61, 8.63, 8.60, 8.60, 19000),
    ]
    # push2 之后 6 根：不新高、量缩、价格回落到 push2*0.99 下
    for i in range(6):
        bars.append(_b("2026110310%02d" % (45 + i * 5), 8.55, 8.58, 8.45, 8.47, 5000))
    r = evaluate(_snap(bars, 8.46))
    assert r["decision"] == "exit_all"
    assert r["signal"] == "accel_end"
    assert r["features"]["push1"]["price"] == 8.51
    assert r["features"]["push2"]["price"] == 8.63


def test_board_half_when_sealed():
    bars = [_b("202611030935", 3.42, 3.42, 3.42, 3.42, 100000)]
    for i in range(8):
        bars.append(_b("2026110309%02d" % (40 + i), 3.42, 3.42, 3.42, 3.42, 10000))
    r = evaluate(_snap(bars, 3.42, pre=3.11, cost=2.64, symbol="SZ002086"))
    assert r["decision"] == "reduce_half"
    assert r["signal"] == "board_half"


def test_support_break():
    bars = [_b("202611030935", 8.2, 8.3, 8.1, 8.2), _b("202611030940", 8.2, 8.2, 8.0, 8.0)]
    r = evaluate(_snap(bars, 7.90, levels=[{"price": 8.0, "type": "support"}]))
    assert r["decision"] == "exit_all"
    assert r["signal"] == "support_break"


def test_no_accel_end_on_new_high():
    bars = [
        _b("202611030935", 8.2, 8.51, 8.13, 8.45),
        _b("202611030940", 8.45, 8.46, 8.28, 8.33),
        _b("202611031015", 8.56, 8.60, 8.53, 8.56),
        _b("202611031040", 8.61, 8.63, 8.60, 8.60),
    ]
    for i in range(6):
        bars.append(_b("2026110310%02d" % (45 + i * 5), 8.6, 8.70, 8.55, 8.65, 5000))
    r = evaluate(_snap(bars, 8.65))
    assert not r["features"].get("accel_end")


def test_missing_price_never_sells():
    bars = [_b("202611030935", 8.2, 8.3, 8.1, 8.2), _b("202611030940", 8.2, 8.2, 8.0, 8.0)]
    s = _snap(bars, 0.0, levels=[{"price": 8.0, "type": "support"}])
    r = evaluate(s)
    assert r["decision"] == "hold"
    assert r["blocked_reason"] == "no_price"
    assert r["volume"] == 0


def test_cutoff_drops_future_bar():
    bars = [
        _b("202611030935", 8.2, 8.51, 8.13, 8.45, 200000),
        _b("202611030940", 8.45, 8.46, 8.28, 8.33, 100000),
        _b("202611031015", 8.56, 8.60, 8.53, 8.56, 20000),
        _b("202611031040", 8.61, 8.63, 8.60, 8.60, 19000),
    ]
    for i in range(6):
        bars.append(_b("2026110310%02d" % (45 + i * 5), 8.55, 8.58, 8.45, 8.47, 5000))
    s = _snap(bars, 8.46); s["decision_time"] = "11:20"
    s["bars_5m"].append(_b("202611031345", 8.7, 8.75, 8.7, 8.75, 99999))
    r = evaluate(s)
    assert r["signal"] == "accel_end"
    assert r["volume"] == 500


def test_board_half_blocked_when_volume_below_lot():
    bars = [_b("202611030935", 3.42, 3.42, 3.42, 3.42, 100000)]
    for i in range(8):
        bars.append(_b("2026110309%02d" % (40 + i), 3.42, 3.42, 3.42, 3.42, 10000))
    s = _snap(bars, 3.42, pre=3.11, cost=2.64, symbol="SZ002086")
    s["position"].update({"current_volume": 150, "sellable": 150, "remaining_cycle_budget": 100})
    r = evaluate(s)
    assert r["decision"] == "hold"
    assert r["blocked_reason"]
    assert r["volume"] == 0


def test_data_cutoff_is_honored():
    bars = [
        _b("202611030935", 8.2, 8.51, 8.13, 8.45, 200000),
        _b("202611030940", 8.45, 8.46, 8.28, 8.33, 100000),
        _b("202611031015", 8.56, 8.60, 8.53, 8.56, 20000),
        _b("202611031040", 8.61, 8.63, 8.60, 8.60, 19000),
    ]
    for i in range(6):
        bars.append(_b("2026110310%02d" % (45 + i * 5), 8.55, 8.58, 8.45, 8.47, 5000))
    s = _snap(bars, 8.46); s["decision_time"] = "13:45"; s["data_cutoff"] = "11:20"
    s["bars_5m"].append(_b("202611031345", 8.7, 8.75, 8.7, 8.75, 99999))
    assert evaluate(s)["signal"] == "accel_end"


def test_asof_filters_other_day_bars():
    bars = [
        _b("202611020935", 9.4, 9.50, 9.3, 9.45, 200000),
        _b("202611030935", 8.2, 8.51, 8.13, 8.45, 200000),
        _b("202611030940", 8.45, 8.46, 8.28, 8.33, 100000),
        _b("202611031015", 8.56, 8.60, 8.53, 8.56, 20000),
        _b("202611031040", 8.61, 8.63, 8.60, 8.60, 19000),
    ]
    for i in range(6):
        bars.append(_b("2026110310%02d" % (45 + i * 5), 8.55, 8.58, 8.45, 8.47, 5000))
    s = _snap(bars, 8.46); s["decision_time"] = "11:20"
    assert s["asof"] == "20261103"
    assert evaluate(s)["features"]["push1"]["price"] == 8.51
