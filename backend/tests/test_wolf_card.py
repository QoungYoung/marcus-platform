import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "services"))

from wolf_card.negative_filter import evaluate  # noqa: E402
from wolf_card.instrument_watch import watch  # noqa: E402


def test_negative_bank_blocked_core():
    r = evaluate([{"code": "601398.SH", "name": "工商银行", "theme": "金融"}], "core")
    assert any(x["code"] == "601398.SH" for x in r["blocked"])


def test_negative_broker_tactical():
    r = evaluate([{"code": "600030.SH", "name": "中信证券", "theme": "金融"}], "core")
    assert any(x["code"] == "600030.SH" for x in r["tactical_only"])


def test_negative_optical_watch_not_blocked():
    r = evaluate([{"code": "300308.SZ", "name": "中际旭创", "theme": "AI/算力/科技"}], "core")
    assert any(x["code"] == "300308.SZ" for x in r["watch"])
    assert not any(x["code"] == "300308.SZ" for x in r["blocked"])


def test_negative_kechuang_blocked():
    r = evaluate([{"code": "002230.SZ", "name": "科大讯飞", "theme": "AI/算力/科技"}], "core")
    assert any(x["code"] == "002230.SZ" for x in r["blocked"])


def test_instrument_watch_semi():
    r = watch("半导体/芯片", "20260812", "core", top_etf=5, top_stock=10)
    codes = {x["code"] for x in r["etf"]}
    assert "159516.SZ" in codes and "588170.SH" in codes
    assert r["stocks"], "chain_map 20260812 should have semi stocks"
    assert r["quality"]["chain_map"]["date"] == "20260812"


def test_daily_card_side_uses_t_sleeve():
    from wolf_card.daily_card import build_card
    c = build_card("20260812")
    assert c["regime"]["operation"] == "side"
    assert c["sleeve_used"] == "T"
    assert c["no_trade"] is False
    themes = [d["theme"] for d in c["directions"]["directions"]]
    assert "半导体/芯片" in themes
    codes = {x["code"] for it in c["instruments"] for x in it["etf"]}
    assert "159516.SZ" in codes


def test_daily_card_self_rescue_no_trade():
    from wolf_card.daily_card import build_card
    c = build_card("20261016")
    assert c["regime"]["regime"] == "主跌1浪"
    assert c["no_trade"] is True
    assert c["instruments"] == []


def test_direction_watch_prev_close_for_1020():
    from wolf_card.direction_watch import get_directions
    d = get_directions("20261020", 3)
    assert d["quality"] == "pit_prev_close"
    assert d["asof"] == "20261019"
    assert d["directions"]


def test_etf_rank_semi_liquid_tools_first():
    from wolf_card.etf_watch import rank_etfs
    r = rank_etfs("半导体/芯片", "20260812", 5)
    codes = [x["code"] for x in r]
    assert "588170.SH" in codes and "159516.SZ" in codes
    assert r[0]["score"] >= r[-1]["score"]


def test_etf_rank_new_listing_kept():
    from wolf_card.etf_watch import rank_etfs
    r = rank_etfs("医药", "20250328", 5)
    assert "159316.SZ" in [x["code"] for x in r]
    assert next(x for x in r if x["code"] == "159316.SZ")["new_listing"] is True


def test_trend_breakout_bridge_confirm_and_theme():
    from wolf_card.trend_breakout_bridge import build_context, check_stock
    ctx = build_context("20260812")
    # side 需要确认：默认不通过
    r = check_stock("002371.SZ", "北方华创", "半导体/芯片", ctx)
    assert r["allowed"] is False and r["reason"] == "confirm_needed"
    # 测试/人工确认后：允许，主线 + 公募 penalty
    r = check_stock("002371.SZ", "北方华创", "半导体/芯片", ctx, auto_confirm=True)
    assert r["allowed"] is True and r["negative_status"] == "penalized"
    # 非主线：拒绝
    r = check_stock("601398.SH", "工商银行", "金融", ctx, auto_confirm=True)
    assert r["allowed"] is False and r["reason"] == "theme_not_in_mainline"


def test_trend_breakout_bridge_watch():
    from wolf_card.trend_breakout_bridge import build_context, check_stock
    ctx = build_context("20260812")
    r = check_stock("300308.SZ", "中际旭创", "AI/算力/科技", ctx, auto_confirm=True)
    assert r["allowed"] is True and r["negative_status"] == "watch"
    assert r["etf_context"]


def test_trend_breakout_bridge_etf_detail():
    from wolf_card.trend_breakout_bridge import build_context, check_stock
    ctx = build_context("20260812")
    r = check_stock("002371.SZ", "北方华创", "半导体/芯片", ctx, auto_confirm=True)
    assert r["allowed"] is True
    assert r["etf_context"], "ETF codes should be present"
    assert r["etf_candidates"], "ETF detail should be present"
    first = r["etf_candidates"][0]
    assert first.get("code") in r["etf_context"]
    assert "score" in first and "role" in first
    assert "588170.SH" in r["etf_context"] or "159516.SZ" in r["etf_context"]
    assert r["etf_summary"]
