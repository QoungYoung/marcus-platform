# -*- coding: utf-8 -*-
"""单测：建仓两道真闸门（(e) 交叉格 / 抗跌）——2026-09-15 用户指示真对接生产。

round 40 增补：分位口径改为「当日候选域」（用户指令"分位改在更宽的候选域上算"）
—— 原来算在当日终选腿（3~4 条）上，域内样本 <6 → 两道门实际从未生效。
"""
import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "apps" / "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

F = importlib.import_module("wolf_entry_filters")


def test_switches_default_on_and_env(monkeypatch):
    monkeypatch.delenv("WOLF_CLOSE_POS_GATE", raising=False)
    monkeypatch.delenv("WOLF_DEFENSIVE_GATE", raising=False)
    assert F.close_pos_enabled() is True and F.defensive_enabled() is True
    monkeypatch.setenv("WOLF_CLOSE_POS_GATE", "0")
    monkeypatch.setenv("WOLF_DEFENSIVE_GATE", "0")
    assert F.close_pos_enabled() is False and F.defensive_enabled() is False


def test_close_pos_cross_cell_blocks_only_that_cell():
    pool = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    cut = F.tercile_high(pool)
    hit, why = F.close_pos_blocked(cut + 0.5, 0.6, pool)      # 高档 ∧ 半强收盘 → 拦
    assert hit and "交叉格" in why
    assert F.close_pos_blocked(cut + 0.5, 0.9, pool)[0] is False   # 强收盘不拦
    assert F.close_pos_blocked(1.0, 0.6, pool)[0] is False         # 低档不拦
    assert F.close_pos_blocked(None, 0.6, pool)[0] is False        # 数据缺失放行
    assert F.close_pos_blocked(cut + 0.5, 0.6, [1, 2, 3])[0] is False  # 样本<6 不判


def test_defensive_blocks_weak_low_position():
    pool = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    cut = F.tercile_low(pool)
    hit, why = F.defensive_blocked(cut - 0.1, -3.0, -1.0, pool)   # 低位 ∧ 弱于大盘 → 拦
    assert hit and "抗跌" in why
    assert F.defensive_blocked(cut - 0.1, +2.0, -1.0, pool)[0] is False   # 低位但更强 → 放行
    assert F.defensive_blocked(8.0, -3.0, -1.0, pool)[0] is False         # 非低位 → 放行
    assert F.defensive_blocked(cut - 0.1, None, -1.0, pool)[0] is False   # 缺数放行


def test_pos_of_bounds():
    assert F.pos_of(5.0, 6.0, 4.0) == 0.5
    assert F.pos_of(5.0, 5.0, 5.0) is None


# ── round 40（用户指令"分位改在更宽的候选域上算"）────────────────────
def _dom(n=30, **kw):
    """宽候选域：dist_prevlow = 0..n-1（三分位门槛 = 20 / 10）。"""
    out = []
    for i in range(n):
        d = {"symbol": "SH%06d" % (600000 + i), "dist_prevlow": float(i), "pos": 0.6, "ret20": 5.0}
        d.update(kw)
        out.append(d)
    return out


def test_qdomain_default_is_cand_and_env_switch(monkeypatch):
    monkeypatch.delenv("WOLF_ENTRY_QDOMAIN", raising=False)
    assert F.qdomain_mode() == "cand"
    monkeypatch.setenv("WOLF_ENTRY_QDOMAIN", "legs")
    assert F.qdomain_mode() == "legs"


def test_domain_map_field_mapping():
    """pick_v2 字段名 → 闸门口径（它的 `dist_prevlow` 是距当日低，距前一日低的是 `dist_prevlow_prev`）。"""
    m = F.domain_map([{"xq": "SH600000", "ts": "600000.SH", "dist_prevlow_prev": 1.5,
                       "pos_range": 0.61, "r20": 3.2}])
    assert m["SH600000"]["dist_prevlow"] == 1.5
    assert m["SH600000"]["pos"] == 0.61
    assert m["SH600000"]["ret20"] == 3.2


def test_filter_legs_blocks_on_candidate_domain_quantile(monkeypatch, capsys, tmp_path):
    """宽候选域（30 只）→ 三分位算得出来 → 高档 ∧ 半强收盘的腿被拦（旧口径下会被"样本<6"放过）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(F, "DATA", str(tmp_path))
    monkeypatch.setattr(F, "leg_features",
                        lambda s, a: (_ for _ in ()).throw(AssertionError("域内有特征时不应再取数")))
    dom = _dom(30)
    legs = [{"symbol": "SH600028"}, {"symbol": "SH600002"}]      # dist 28（高档）/ 2（低档）
    kept, blocked = F.filter_legs(legs, "20260915", domain=dom)
    assert [b["symbol"] for b in blocked] == ["SH600028"]
    assert [b["symbol"] for b in kept] == ["SH600002"]
    err = capsys.readouterr().err
    assert "ENTRY_FILTER_DOMAIN n=30 src=cand" in err
    art = json.loads((tmp_path / "entry_filter_20260915.json").read_text(encoding="utf-8"))
    assert art["domain_n"] == 30 and art["src"] == "cand"
    assert art["legs"][0]["in_domain"] is True and art["legs"][0]["blocked"] is True


def test_filter_legs_falls_back_to_legs_when_domain_thin(monkeypatch, capsys):
    """域太窄（<6）→ 退回旧口径（终选腿），且行为与旧版一致（不判、放行）+ 行里写明 src=legs。"""
    monkeypatch.setattr(F, "leg_features", lambda s, a: {"dist_prevlow": 28.0, "pos": 0.6, "ret20": 5.0})
    kept, blocked = F.filter_legs([{"symbol": "SH600028"}], "20260915",
                                  domain=[{"symbol": "X1", "dist_prevlow": 1.0}])
    assert blocked == [] and len(kept) == 1
    assert "src=legs" in capsys.readouterr().err


def test_filter_legs_qdomain_legs_switch_restores_old_behavior(monkeypatch, capsys):
    """回退开关：WOLF_ENTRY_QDOMAIN=legs → 忽略候选域（= 旧口径，两道门实际不生效）。"""
    monkeypatch.setenv("WOLF_ENTRY_QDOMAIN", "legs")
    monkeypatch.setattr(F, "leg_features", lambda s, a: {"dist_prevlow": 28.0, "pos": 0.6, "ret20": 5.0})
    kept, blocked = F.filter_legs([{"symbol": "SH600028"}], "20260915", domain=_dom(30))
    assert blocked == [] and len(kept) == 1
    assert "src=legs" in capsys.readouterr().err


def test_filter_legs_out_of_domain_leg_uses_own_features(monkeypatch):
    """域外腿（ETF 兜底/legacy 回退）：只对该腿单独取数，不影响别人的分位。"""
    calls = []
    monkeypatch.setattr(F, "leg_features",
                        lambda s, a: calls.append(s) or {"dist_prevlow": 28.0, "pos": 0.6, "ret20": 5.0})
    dom = _dom(30)
    legs = [{"symbol": "SH600002"}, {"symbol": "SZ159915"}]      # 后者不在域里
    kept, blocked = F.filter_legs(legs, "20260915", domain=dom)
    assert calls == ["SZ159915"]
    assert [b["symbol"] for b in blocked] == ["SZ159915"]        # 域外腿按自身特征判（高档∧半强 → 拦）
    assert [b["symbol"] for b in kept] == ["SH600002"]


def test_filter_legs_defensive_gate_on_domain(monkeypatch):
    """闸门 2 也用同一候选域分位：低位（下三分位）∧ 弱于大盘 → 拦；等价条件下强于大盘 → 放行。"""
    monkeypatch.setattr(F, "leg_features", lambda s, a: {})
    monkeypatch.setattr(F, "index_ret", lambda w, a: 1.0)
    dom = _dom(30)
    dom[1].update({"ret20": -6.0})      # SH600001：dist=1（低档）∧ 20 日 -6% < 指数 +1%
    dom[3].update({"ret20": +8.0})      # SH600003：dist=3（低档，≤10）∧ 强于大盘 → 放行
    kept, blocked = F.filter_legs([{"symbol": "SH600001"}, {"symbol": "SH600003"}],
                                  "20260915", domain=dom)
    assert [b["symbol"] for b in blocked] == ["SH600001"]
    assert [b["symbol"] for b in kept] == ["SH600003"]



# ── 接线：布腿器（rotation_switch_arm）把"当日候选域"喂给闸门 ──────────────
def _load_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    arm_path = os.path.join(str(ROOT), "jobs", "rotation_switch_arm.py")
    spec = importlib.util.spec_from_file_location("rsa_entry_test", arm_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DATA = str(tmp_path)
    return mod


def _stub_pos(monkeypatch, label="LOW"):
    """免 pandas 的位置分类桩（生产用 position_class，测试只关心 LOW/MID 闸）。"""
    monkeypatch.setitem(sys.modules, "pandas", types.SimpleNamespace(Series=lambda x: x))
    monkeypatch.setitem(sys.modules, "position_class",
                        types.SimpleNamespace(position_features=lambda s: {"ok": True},
                                              classify=lambda f: {"position": label}))


def test_arm_dom_feat_same_definition_as_gate(tmp_path, monkeypatch):
    """路径A 的域特征口径必须与闸门（§50 证据）一致：距**前一日**低 / 当日区间位置 / 20 日涨幅。"""
    arm = _load_arm(tmp_path, monkeypatch)
    n = 25
    closes = [10.0] * (n - 1) + [12.0]
    lows = [9.0] * (n - 1) + [11.0]
    highs = [10.5] * (n - 1) + [13.0]
    f = arm._dom_feat(closes, lows, highs)
    assert abs(f["dist_prevlow"] - (12.0 / 9.0 - 1) * 100) < 1e-9      # 前一日低 = 9.0
    assert abs(f["pos"] - (12.0 - 11.0) / (13.0 - 11.0)) < 1e-9
    assert abs(f["ret20"] - 20.0) < 1e-9


def test_arm_lowmid_domain_keeps_only_buyable_candidates(tmp_path, monkeypatch):
    """候选域 = 短名单里位置闸 LOW/MID 的候选（不设 limit）——"能买但没被选中"的也要进分位分母。"""
    arm = _load_arm(tmp_path, monkeypatch)
    _stub_pos(monkeypatch, "LOW")
    closes, lows, highs = [10.0] * 25, [9.0] * 25, [10.5] * 25
    stats = [{"ts": "600000.SH", "xq": "SH600000", "chain": "农业", "closes": closes,
              "lows": lows, "highs": highs, "dom": {"dist_prevlow": 1.0, "pos": 0.5, "ret20": 3.0}},
             {"ts": "600001.SH", "xq": "SH600001", "chain": "农业", "closes": closes,
              "lows": lows, "highs": highs, "dom": {"dist_prevlow": 2.0, "pos": 0.6, "ret20": 4.0}}]
    dom = arm._lowmid_domain(stats)
    assert [d["symbol"] for d in dom] == ["SH600000", "SH600001"]
    assert dom[0]["dist_prevlow"] == 1.0 and dom[1]["pos"] == 0.6
    _stub_pos(monkeypatch, "HIGH")                     # 位置闸不过 → 不进候选域
    assert arm._lowmid_domain(stats) == []


def test_arm_confirm_pick_forwards_v2_domain(tmp_path, monkeypatch):
    """`pick_v2` 的候选域必须被透出到 domain_out（否则闸门只能退回"终选腿"旧口径）。"""
    arm = _load_arm(tmp_path, monkeypatch)
    dom = [{"symbol": "SH600000", "dist_prevlow": 1.0, "pos": 0.5, "ret20": 3.0}]

    def pick_v2(theme, exclude=None, limit=2, concepts=None, status_out=None, **kw):
        if isinstance(status_out, dict):
            status_out.update({"status": "ok", "domain": dom})
        return [{"symbol": "SH600000"}]

    monkeypatch.setitem(sys.modules, "wolf_confirm_pick", types.SimpleNamespace(pick_v2=pick_v2))
    out = []
    picks = arm.confirm_pick("农业", exclude=set(), limit=2, domain_out=out)
    assert out == dom and [p["symbol"] for p in picks] == ["SH600000"]
    out2 = []                                          # 不传 domain_out：不得炸（向后兼容）
    assert arm.confirm_pick("农业", exclude=set(), limit=2)
    assert out2 == []


def test_shadow_daily_reports_entry_filter(tmp_path):
    """影子日报要能看到这两道**真闸门**（候选域大小 / 门槛 / 拦了谁）——不是影子也要可见。"""
    spec = importlib.util.spec_from_file_location(
        "shadow_daily_under_test", os.path.join(str(ROOT), "jobs", "shadow_daily.py"))
    sd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sd)
    d = "20260915"
    assert sd.entry_filter_item(str(tmp_path), d) is None          # 无文件 → 不产出条目
    (tmp_path / ("entry_filter_%s.json" % d)).write_text(json.dumps({
        "date": d, "qdomain": "cand", "src": "cand", "domain_n": 37, "cut_hi": 4.2, "cut_lo": -1.1,
        "index_ret20": 2.5, "legs": [{"symbol": "SH600584"}],
        "blocked": [{"symbol": "SH600584", "why": "距前低高档(6.10>=4.20) ∧ 半强收盘 → 不买"}]},
        ensure_ascii=False), encoding="utf-8")
    it = sd.entry_filter_item(str(tmp_path), d)
    assert it["domain_n"] == 37 and it["src"] == "cand" and it["n_legs"] == 1
    assert it["blocked"][0]["symbol"] == "SH600584"
