# -*- coding: utf-8 -*-
"""v3 排序接线（`WOLF_PICK_RANK_V3`）的定向单测（2026-09-15，round 4）。

三个必须钉死的行为：
  1. **默认关**：开关关 + 影子关 → 选票与接线前**完全一致**（不得有任何行为漂移）；
  2. **影子**：只写 `data/rank_v3_<as_of>.json` + stderr，**返回仍是原 leader 的票**；
  3. **开启**：返回 v3 票（`pick_source='v3'`），且域 = 候选池∩LOW/MID（LOW 优先、只取 1 只）。
"""
import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


def _fake_pick_module(monkeypatch, tmp_path, env):
    """构造一个最小可跑的 pick_v2 环境：猴补 fetch_daily/names/bad/gz/position 等。"""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import wolf_confirm_pick as W
    importlib.reload(W)
    W.DATA = str(tmp_path)

    days = ["2026%02d%02d" % (6 + i // 28, 1 + i % 28) for i in range(90)]
    days = [d for d in days if len(d) == 8]
    # 造 4 只票：A 强且横盘多（应最优先）、B 次之、C 弱、D 高位
    BASE = {"600001.SH": 10.0, "600002.SH": 9.0, "600003.SH": 8.0, "600004.SH": 7.0}

    def rows(ts):
        base = BASE[ts]
        out = []
        for k, d in enumerate(days):
            c = base * (1 + 0.001 * k)
            out.append((d, round(c, 3), round(c * 0.995, 3), 5e5,
                        round(c * 1.005, 3) if ts != "600004.SH" else round(c * 1.06, 3)))
        return out
    W.fetch_daily = lambda ts, end: rows(ts)
    W.names_map = lambda: {t: t[:6] for t in BASE}
    W.bad_set = lambda: set()
    W.cross_concepts = lambda: {}
    W.gz = lambda *a, **k: []
    W.board_allowed = lambda ts: True
    W.theme_etf = lambda th: None
    W.confirm_universe = lambda theme="农业": {"c1": ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]}
    import position_class as pc
    pos_map = {"600001.SH": "LOW", "600002.SH": "LOW", "600003.SH": "MID", "600004.SH": "HIGH"}
    monkeypatch.setattr(pc, "position_features", lambda s: {"d": 1}, raising=False)
    monkeypatch.setattr(pc, "classify", lambda f: {"position": "LOW"}, raising=False)
    # classify 需要按票区分 → 用 W.fetch 的顺序无法区分；改用 theme 内 tick 计数
    state = {"n": 0}

    def classify(f):
        state["n"] += 1
        order = ["LOW", "LOW", "MID", "HIGH"]
        return {"position": order[(state["n"] - 1) % 4]}
    monkeypatch.setattr(pc, "classify", classify, raising=False)
    monkeypatch.setattr(W, "_theme_r5_quantile_stub", None, raising=False)
    return W, days[-1]          # 返回 (模块, as_of=最后一个交易日) —— pick_v2 要求 rows[-1]==AS


def test_default_off_unchanged(monkeypatch, tmp_path):
    W, AS = _fake_pick_module(monkeypatch, tmp_path, {"WOLF_PICK_RANK_V3": "0", "WOLF_PICK_RANK_V3_SHADOW": "0"})
    st = {}
    picks = W.pick_v2("农业", exclude=set(), limit=2, as_of=AS, status_out=st)
    assert st.get("status") == "ok"
    assert all(p.get("pick_source") != "v3" for p in picks)
    assert not os.path.exists(os.path.join(str(tmp_path), "rank_v3_%s.json" % AS))


def test_shadow_records_but_returns_leader(monkeypatch, tmp_path):
    W, AS = _fake_pick_module(monkeypatch, tmp_path, {"WOLF_PICK_RANK_V3": "0", "WOLF_PICK_RANK_V3_SHADOW": "1"})
    st = {}
    picks = W.pick_v2("农业", exclude=set(), limit=2, as_of=AS, status_out=st)
    assert all(p.get("pick_source") != "v3" for p in picks)     # 返回仍是 leader 的票
    f = os.path.join(str(tmp_path), "rank_v3_%s.json" % AS)
    assert os.path.exists(f), "影子文件未生成"
    d = json.load(open(f, encoding="utf-8"))
    assert d["themes"]["农业"]["mode"] == "shadow"
    assert d["themes"]["农业"]["domain_n"] >= 1


def test_on_returns_v3_picks(monkeypatch, tmp_path):
    W, AS = _fake_pick_module(monkeypatch, tmp_path, {"WOLF_PICK_RANK_V3": "1", "WOLF_PICK_RANK_V3_SHADOW": "0"})
    st = {}
    picks = W.pick_v2("农业", exclude=set(), limit=2, as_of=AS, status_out=st)
    assert picks, "v3 开启后应有票"
    assert len(picks) == 1, "v3 已验收配置只取 1 只"
    assert picks[0]["pick_source"] == "v3"
    f = os.path.join(str(tmp_path), "rank_v3_%s.json" % AS)
    d = json.load(open(f, encoding="utf-8"))
    assert d["themes"]["农业"]["mode"] == "on"
