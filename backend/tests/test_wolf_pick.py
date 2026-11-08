# -*- coding: utf-8 -*-
"""P0 skeleton 单测：新鲜度、波浪标签、方向选择、关键位、决策卡。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend", "app", "services"),
          os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

from wolf_pick.state_guard import check_files, check_state  # noqa: E402
from wolf_pick.direction import select_directions  # noqa: E402
from wolf_pick.key_levels import evaluate  # noqa: E402
from wolf_pick.card import build_card  # noqa: E402
from wolf_pick.stock_select import index_regime, limit_threshold  # noqa: E402
from wave_ladder import enrich_wave_state  # noqa: E402


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def test_check_state_fresh_stale_future_missing():
    fresh = check_state({"date": "2026-10-14"}, "20261016")
    assert fresh["stale"] is False and fresh["age_days"] == 2
    stale = check_state({"date": "2026-09-13"}, "20261016")
    assert stale["stale"] is True and stale["reason"].startswith("stale_")
    future = check_state({"date": "2026-10-20"}, "20261016")
    assert future["stale"] is True and future["reason"] == "future_state"
    missing = check_state({}, "20261016")
    assert missing["stale"] is True and missing["missing"] is True


def test_market_data_newer_marks_stale(tmp_path):
    data = str(tmp_path)
    _write(os.path.join(data, "wave_state.json"), {"date": "2026-10-14", "level": "d5"})
    _write(os.path.join(data, "main_line_state.json"), {"date": "2026-10-14"})
    _write(os.path.join(data, "index_daily_000001_SH.json"),
           {"rows": [["20261014", 3767.2, 3811.8, 6.6e11], ["20261015", 3707.4, 3724.5, 7.7e11]]})
    st = check_files(data, "20261016")
    assert st["wave"]["stale"] is True
    assert "market_data_newer" in st["wave"]["reason"]


def test_enrich_wave_state_keeps_level_and_adds_labels():
    ws = {"date": "2026-10-14", "level": "d5", "sub_level": "失败5", "operation": "defense"}
    e = enrich_wave_state(ws)
    assert e["level"] == "d5" and e["sub_level"] == "失败5"   # 旧字段不改
    assert e["family"] == "4" and e["stage"] == "4-5" and e["status"] == "failed"
    top = enrich_wave_state({"level": "d5", "sub_level": "末段", "operation": "exit"})
    assert top["family"] == "5" and top["status"] == "failed"


def test_select_directions_reads_mainline_select():
    ms = {"mainline_select": {"mainline": "半导体/芯片", "second": "新能源/电池",
                              "pool": ["半导体/芯片", "新能源/电池"],
                              "r5": {"半导体/芯片": 0.02, "新能源/电池": 0.01}}}
    rows = select_directions(ms, topk=2)
    assert [r["theme"] for r in rows] == ["半导体/芯片", "新能源/电池"]
    assert rows[0]["score"] == 0.02 and rows[0]["in_pool"] is True


def test_key_levels_conditional_and_disabled_by_default():
    wave = {"family": "4", "stage": "4-5", "status": "failed"}
    levels = [{"level": 3764, "kind": "support"}]
    obs = evaluate(levels, close=3700, wave=wave, amount_ratio=1.3, days_below=2, enabled=False)
    assert obs[0]["conditional"] is True and obs[0]["confirmed"] is True
    assert obs[0]["triggered"] is False and obs[0]["action"] == "watch"
    obs2 = evaluate(levels, close=3700, wave=wave, amount_ratio=1.3, days_below=2, enabled=True)
    assert obs2[0]["triggered"] is True and obs2[0]["action"] == "down1"


def test_limit_threshold():
    assert limit_threshold("600000.SH") == 9.7
    assert limit_threshold("300001.SZ") == 19.7
    assert limit_threshold("688001.SH") == 19.7
    assert limit_threshold("830001.BJ") == 29.7


def test_index_regime_ma120(tmp_path):
    rows = []
    for i in range(130):
        d = "2025%02d%02d" % (1 + i // 28, 1 + i % 28)
        rows.append([d, 3000 + i, 3000 + i, 1e8])
    _write(os.path.join(str(tmp_path), "index_daily_000001_SH.json"), {"rows": rows})
    r = index_regime("20250601", os.path.join(str(tmp_path), "index_daily_000001_SH.json"))
    assert r["above_ma120"] is True
    assert r["r5"] is not None


def test_build_card_with_stocks_shadow(tmp_path, monkeypatch):
    data = str(tmp_path)
    _write(os.path.join(data, "main_line_state.json"), {
        "date": "20261014",
        "mainline_select": {"mainline": "半导体/芯片", "second": "新能源/电池",
                            "pool": ["半导体/芯片", "新能源/电池"],
                            "r5": {"半导体/芯片": 0.02, "新能源/电池": 0.01}},
    })
    _write(os.path.join(data, "wave_state.json"), {"date": "20261014", "level": "d4", "operation": "side"})
    import wolf_pick.stock_select as ss
    monkeypatch.setattr(ss, "select_for_directions", lambda *a, **k: {
        "candidates": [{"code": "X", "name": "Y"}], "regime": {"above_ma120": True}, "blocked": None})
    card = build_card(data, "20261016", with_stocks=True, stock_config={"shadow": True, "hold_days": 10})
    assert card["stock_candidates"][0]["shadow"] is True
    assert card["stock_candidates"][0]["hold_days"] == 10


def test_build_card_blind_and_oracle(tmp_path):
    data = str(tmp_path)
    _write(os.path.join(data, "main_line_state.json"), {
        "date": "20261014",
        "mainline_select": {"mainline": "半导体/芯片", "second": "新能源/电池",
                            "pool": ["半导体/芯片", "新能源/电池"],
                            "r5": {"半导体/芯片": 0.02, "新能源/电池": 0.01}},
    })
    _write(os.path.join(data, "wave_state.json"), {
        "date": "20261014", "level": "d5", "sub_level": "失败5",
        "operation": "defense", "features": {"close": 3767.23, "amount": 6.6e11},
    })
    _write(os.path.join(data, "etf_theme_map_pi.json"), {
        "themes": [{"theme": "半导体/芯片", "primary": [
            {"ts_code": "588170.SH", "name": "科创半导体ETF", "reason": "test"}]}],
    })
    labels = os.path.join(data, "labels.jsonl")
    with open(labels, "w", encoding="utf-8") as f:
        f.write(json.dumps({"date": "20261014", "type": "etf", "action": "doing",
                            "theme": "半导体/芯片", "instrument": {"code": "588170"},
                            "quote": "量能开始缩转放了"}, ensure_ascii=False) + "\n")
    card = build_card(data, "20261016", mode="blind", labels_path=labels)
    assert card["meta"]["stale"]["wave"]["stale"] is False
    assert card["regime"]["wave"]["family"] == "4"
    assert card["directions"][0]["theme"] == "半导体/芯片"
    assert card["candidates"][0]["code"] == "588170"
    assert card["plan"]["new_entry_allowed"] is False
    assert card["his_side"] is None
    oracle = build_card(data, "20261016", mode="oracle", labels_path=labels)
    assert oracle["his_side"]["doing"] == ["半导体/芯片"]
    assert "588170" in oracle["his_side"]["disclosed"]
