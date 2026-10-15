# -*- coding: utf-8 -*-
"""单测：跨链陪葬/传导（apps/main_line/chain_contagion.py）——纯函数，不碰 DB/网络。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import chain_contagion as CC  # noqa: E402


def series(vals, start=1):
    return [("2026%02d%02d" % (1 + (start - 1 + i) // 28, 1 + (start - 1 + i) % 28), float(v), float(v))
            for i, v in enumerate(vals)]


def test_broke_is_an_event_not_a_state():
    """破位只在**首次下穿那一天**为真（用状态当信号会把全市场一次点亮 ✗ 实测 256 只）。"""
    vals = [100.0] * 25 + [95.0] + [94.0] * 8        # 下穿后横住 8 日（> WINDOW=5）
    s = series(vals)
    d0 = s[25][0]
    assert CC.broke(s, d0)["cross_low"] == d0 and CC.broke(s, d0)["broke"] is True
    later = s[-1][0]
    assert CC.broke(s, later)["broke"] is False, CC.broke(s, later)     # 窗口外 ⇒ 不再是事件
    inside = s[28][0]                                                  # 窗口内 ⇒ 仍算事件（连坐观察期）
    assert CC.broke(s, inside)["broke"] is True and CC.broke(s, inside)["cross_low"] == d0


def test_broke_sample_insufficient():
    assert CC.broke(series([100.0] * 5), "20260105")["broke"] is False


def test_kw_hit():
    assert CC._kw_hit(["光通信模块", "CPO概念"], ["光通信模块"]) is True
    assert CC._kw_hit(["半导体材料"], ["光通信模块"]) is False


def _fake_map(tmp_path, date="20260901"):
    d = tmp_path
    (d / ("chain_map_%s.json" % date)).write_text(json.dumps({
        "themes": [{"theme": "AI/算力/科技", "segments": [
            {"role": "upstream", "label": "上游·光器件", "concepts": ["光通信模块", "CPO概念"],
             "candidates": ["300308.SZ", "300502.SZ"],
             "leading_verified": [{"ts": "300308.SZ", "name": "中际旭创", "ok": True}]},
            {"role": "mid", "label": "中游·国产半导体", "concepts": ["半导体材料", "半导体设备"],
             "candidates": ["688012.SH", "002371.SZ"],
             "leading_verified": [{"ts": "688012.SH", "name": "中微公司", "ok": True}]},
        ]}]}, ensure_ascii=False), encoding="utf-8")
    return d


def test_chains_and_causal_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(CC, "DATA", str(_fake_map(tmp_path)))
    monkeypatch.setattr(CC, "chain_maps", lambda: [("20260901", str(tmp_path / "chain_map_20260901.json"))])
    assert CC.load_chain_map("20260831") is None          # 快照晚于 as-of ⇒ 不许用（无未来函数 ✓）
    cs = CC.chains("20260901")
    assert len(cs) == 2 and cs[0]["anchors"][0]["ts"] == "300308.SZ"


def test_assess_ok_and_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(CC, "DATA", str(_fake_map(tmp_path)))
    monkeypatch.setattr(CC, "chain_maps", lambda: [("20260901", str(tmp_path / "chain_map_20260901.json"))])
    monkeypatch.setattr(CC, "load_bars", lambda codes=None: {})       # 无日线 ⇒ 全部未破
    a = CC.assess("20260901")
    assert a["n_high"] == 0 and a["n_watch"] == 0 and len(a["chains"]) == 2
    monkeypatch.setattr(CC, "chain_maps", lambda: [])
    assert CC.assess("20260901")["risk_by_code"] == {}               # 无快照 ⇒ fail-open ✓


def test_assess_flags_anchor_break_and_cross_chain(tmp_path, monkeypatch):
    """链主当日新破 ⇒ 该链 high；跨链（光器件 → 国产半导体）按 CHAIN_ADJ 传导。"""
    monkeypatch.setattr(CC, "DATA", str(_fake_map(tmp_path)))
    monkeypatch.setattr(CC, "chain_maps", lambda: [("20260901", str(tmp_path / "chain_map_20260901.json"))])
    flat = series([100.0] * 25)
    drop = series([100.0] * 25 + [95.0])
    bars = {"300308.SZ": drop, "688012.SH": flat, "300502.SZ": flat, "002371.SZ": flat}
    monkeypatch.setattr(CC, "load_bars", lambda codes=None: bars)
    a = CC.assess("20260901")
    assert a["risk_by_code"]["300308.SZ"]["level"] == "high"
    assert a["risk_by_code"]["688012.SH"]["source"] == "cross", a["risk_by_code"]
    assert a["n_high"] >= 3


def test_risk_of_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("WOLF_CHAIN_CONTAGION", raising=False)
    assert CC.risk_of("300308.SZ", {"risk_by_code": {"300308.SZ": {"level": "high"}}}) is None
    monkeypatch.setenv("WOLF_CHAIN_CONTAGION", "1")
    assert CC.risk_of("300308.SZ", {"risk_by_code": {"300308.SZ": {"level": "high"}}}) == "high"
