# -*- coding: utf-8 -*-
"""单测：浪型假设链（apps/main_line/wave_ladder.py）——纯函数，不碰数据库/行情文件。

覆盖：四档判定、单向锁、两条重定浪、**实测踩出来的两道误报防线**（跳空幅度 / 摸前高比例）、fail-open、开关。
"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W  # noqa: E402


def mk(closes, highs=None, start="2025-06-02"):
    """→ [(date8, close, high, amount)]，日期连续（跳周末，够用）。"""
    rows, d = [], dt.date.fromisoformat(start)
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        rows.append((d.strftime("%Y%m%d"), float(c), float(c if highs is None else highs[i]), 1e12))
        d += dt.timedelta(days=1)
    return rows


FLAT = [100.0] * 80
# 前高 130（在 60 日窗内、20 日窗外的老高）→ 下行 → 反弹 → 回落
CHOP = FLAT + [105.0] * 15 + [103.0, 102.0, 102.5]
FADED = FLAT + [105.0] * 12 + [103.0, 101.0, 128.0, 126.0, 121.0, 99.0]    # 摸前高后回落**并跌穿前 20 日低**（新口径）


def last(rows):
    return rows[-1][0]


def test_rung0_choppy():
    ev = W.evaluate(mk(CHOP), last(mk(CHOP)))
    assert ev and ev["rung_index"] == 0 and ev["operation"] == "t_only", ev and ev["reasons"]


def test_new_high_goes_rung1():
    rows = mk(FLAT + [130.0])
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 1 and ev["operation"] == "side" and ev["day_stage"] == 1


def test_equal_to_prior_high_is_not_new_high():
    rows = mk(FLAT + [100.0])          # 平盘：等于前高不算创新高
    assert W.evaluate(rows, last(rows))["rung_index"] == 0


def test_failed_rally_goes_rung2():
    rows = mk(FADED)
    rows[-1] = (rows[-1][0], rows[-1][1], 122.0, rows[-1][3])   # 末根不跳空 ⇒ 只测「反抽夭折」这条路
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 2 and ev["operation"] == "defense", ev["reasons"]


def test_shallow_rebound_is_not_rung2():
    # 只摸到前高 95%（不是“挑战前高”）⇒ 仍按 4浪调整（实测：2026-07-31 被误判的正是这一档）
    # 前高 130 落在 20 日窗**之外**（真实盘面：7 月那波反弹距 6 月高点还有 5%）
    rows = mk(FLAT + [130.0] + [112.0] * 19 + [120.0, 124.0, 121.0, 118.0])
    # 最后一根“高 123 收 118”：盘中摸高、不构成跳空 ⇒ 只测「摸前高后回落」这一条
    rows[-1] = (rows[-1][0], rows[-1][1], 123.0, rows[-1][3])
    ev = W.evaluate(rows, last(rows))
    assert ev["features"]["hi20_ratio"] < W.NEAR_MIN, ev["reasons"]
    assert ev["rung_index"] != W.RUNG_INDEX["w45_fail"], ev["reasons"]   # 摸不到前高 ⇒ 不算“失败4-5”


def test_real_gap_goes_rung3():
    rows = mk(CHOP + [99.0], highs=[100.0] * (len(CHOP)) + [100.9])   # 高 100.9 < 前收 102.5
    ev = W.evaluate(rows, last(rows))
    assert ev["rung_index"] == 3 and ev["operation"] == "defense", ev["reasons"]   # ★ 2026-10-15：改 defense


def test_trivial_gap_not_rung3():
    # 0.1% 的“跳空”（2026-06-04 实测误报 0.08%）⇒ 不算破位，档位不动
    rows = mk(CHOP + [102.4], highs=[100.0] * (len(CHOP)) + [102.4])
    ev = W.evaluate(rows, last(rows))
    assert ev["features"]["gap_pct"] < W.GAP_MIN and ev["rung_index"] == 0, ev["reasons"]


def test_latch_is_one_way():
    rows = mk(FADED)
    rows[-1] = (rows[-1][0], rows[-1][1], 121.0, rows[-1][3])   # 末根不跳空（否则走 rung3 的跳空条）
    d = last(rows)
    assert W.evaluate(rows, d)["rung_index"] == 2
    ev2 = W.evaluate(rows, d, prev=2)
    assert ev2["rung_index"] == 2 and ev2["degraded"] is False


def test_reset_on_new_high():
    rows = mk(FLAT + [130.0])
    ev = W.evaluate(rows, last(rows), prev=3)
    assert ev["rung_index"] == 1 and ev["reset"] is True and ev["reset_reason"]


def test_reset_on_big_rebound():
    rows = mk(FLAT + [80.0, 84.0, 88.0])
    ev = W.evaluate(rows, last(rows), prev=3, latch_start=rows[-3][0])
    assert ev["reset"] is True and ev["rung_index"] == ev["day_stage"]


def test_fail_open_no_data():
    assert W.evaluate([], "20260401") is None
    assert W.evaluate(mk([100.0] * 10), "20260401") is None      # 不足 MIN_BARS
    assert W.walk([], start="20260101") == []


def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("WOLF_WAVE_LADDER", raising=False)
    assert W.enabled() is False and W.operation_for_gate({"operation": "exit"}) is None
    monkeypatch.setenv("WOLF_WAVE_LADDER", "1")
    assert W.enabled() is True and W.operation_for_gate({"operation": "exit"}) == "exit"


# ─────────────── 铁律②（教材）与跳空降一档：2026-10-15 接入的硬规矩 ───────────────
def test_tie2_wave1_top_break_goes_rung3():
    """铁律②「第四个浪的底部不可以低于第一个浪的浪顶」⇒ 破 1 浪顶 = 主跌1浪。"""
    closes = [100.0] * 80 + [92.0, 84.0, 80.0] + [82.0, 90.0, 100.0, 110.0] + [95.0]
    rows = mk(closes)
    ev = W.evaluate(rows, last(rows))
    assert ev["levels"]["wave1_top"] == 110.0, ev["levels"]
    assert ev["rung_index"] == 3 and "铁律②" in ev["reasons"], ev["reasons"]


def test_single_gap_only_degrades_one_notch():
    """单日跳空**不再**直接判主跌1浪（实测 2026-08-19 一跳空就误判两个月 ✗）⇒ 只到防守档。"""
    closes = [100.0 + i * 0.2 for i in range(100)] + [119.0]   # 末根跳空 1.7% 下行，但未破 20 日低位
    rows = mk(closes, highs=closes[:-1] + [119.0])
    ev = W.evaluate(rows, last(rows))
    assert ev["features"]["gap_unrecovered"] is True
    assert ev["features"]["break_low20"] is False
    assert ev["rung_index"] == 2 and "跳空" in ev["reasons"], (ev["rung_index"], ev["reasons"])


def test_load_rows_merges_csv_and_json(tmp_path, monkeypatch):
    """长历史来自 CSV、近端来自 JSON，同日以 JSON 为准（铁律② 需要 2024-10 那种老拐点）。"""
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    d = tmp_path / "指数数据" / "index_daily"
    d.mkdir(parents=True)
    (d / "999999.XX.csv").write_text("trade_date,open,high,low,close,amount\n2024-01-02,1,9,1,8.0,100\n"
                                     "2024-01-03,1,9,1,9.0,100\n", encoding="utf-8")
    (tmp_path / "index_daily_999999_XX.json").write_text(
        json.dumps({"rows": [["20240103", 9.5, 9.8, 200], ["20240104", 10.0, 10.2, 300]]}), encoding="utf-8")
    rows = W.load_rows("999999.XX")
    assert [r[0] for r in rows] == ["20240102", "20240103", "20240104"]
    assert rows[1][1] == 9.5 and rows[1][3] == 200     # 同日 JSON 覆盖 CSV ✓


# ─────────────── rung1「止跌确认」（2026-10-15 第二轮：治 2026-08 偏保守） ───────────────
def test_bottom_confirmed_goes_rung1():
    """他的“4-5 腿走完、等 5 浪上升”：低点冻结 + 距低 ≥3% + 站上 MA20（8 月窗口的形状）。"""
    closes = [100.0 - i * 0.4 for i in range(80)] + [70.0, 70.5, 71.0, 72.0, 74.0, 76.0, 78.0]
    rows = mk(closes)
    ev = W.evaluate(rows, last(rows))
    f = ev["features"]
    assert f["low_age"] >= W.LOW_AGE and f["off_low20"] >= W.OFF_LOW and f["above_ma20"]
    assert ev["rung_index"] == W.RUNG_INDEX["w45_ok"] and "止跌确认" in ev["reasons"], ev["reasons"]


def test_continuous_new_lows_not_rung1():
    """10 月窗口的形状：20 日低点一路被刷新、反弹只有 2% ⇒ 不许判“止跌确认”（而是继续等）。"""
    closes = [100.0 - i * 0.25 for i in range(90)] + [78.5, 78.0, 78.3, 77.0, 77.6, 78.0]
    rows = mk(closes)
    ev = W.evaluate(rows, last(rows))
    assert ev["features"]["off_low20"] < W.OFF_LOW
    assert ev["rung_index"] != W.RUNG_INDEX["w45_ok"], ev["reasons"]



# ─────────────── 等级与 4 浪内部腿标注（B：对齐他的「4-4 / 4-5」口径） ───────────────
def test_sub_legs_labeling():
    """主级顶之后按他的编号：4-1 跌 / 4-2 弹 / 4-3…，末段用 up 腿 + 回落判「下一段形成中」。"""
    closes = [100.0] * 80 + [130.0, 120.0, 110.0, 100.0] + [105.0, 108.0, 110.0] + [104.0, 103.0]
    rows = mk(closes)
    st = W.structural_levels(rows, last(rows))
    sub = W.sub_legs(rows, last(rows), st)
    assert [l["label"] for l in sub["legs"]] == ["4-1", "4-2"], sub
    assert (sub["legs"][0]["kind"], sub["legs"][1]["kind"]) == ("down", "up")
    assert sub["stage"] == "4-2" and sub["forming"] == "4-3", sub


def test_degree_ladder_is_monotone():
    """D1(20%) ⊇ D2(10%) ⊇ D3(5%)：阈值越细，拐点越多（＝他的「级别/浪中有浪」）。"""
    closes = [100.0 + (i % 17) * 2.0 + i * 0.6 for i in range(200)]
    dp = W.degree_pivots(mk(closes), "20260401")
    assert set(dp) == {"D1", "D2", "D3"}
    assert len(dp["D1"]) <= len(dp["D2"]) <= len(dp["D3"])


def test_sub_legs_fail_open():
    assert W.sub_legs([], "20260401")["legs"] == []
    flat = mk([100.0] * 90)
    assert W.sub_legs(flat, last(flat))["stage"] == ""



# ─────────────── 写生产 wave_state（2026-10-15 切换：浪型链＝唯一写入方） ───────────────
def test_to_wave_state_level_mapping():
    """level 映射必须对齐消费方：down ⇒ wave_gate/t_monitor 的**指数大级别止损**生效 ✓。"""
    rows = mk(CHOP)
    ev0 = W.evaluate(rows, last(rows))
    ws0 = W.to_wave_state(ev0)
    assert ws0["level"] == "d4" and ws0["source"] == "wave_ladder"
    # ★ 2026-10-15：浅回调(rung0 + 距高≤5% + 近5日跌≥2%) 现在是「4-1 回调初 ⇒ defense」✓（原表 4-1=defense）
    assert ws0["operation"] in ("t_only", "defense") and ws0["date"].count("-") == 2
    assert set(("level", "sub_level", "operation", "confidence", "reasons", "date", "features")) <= set(ws0)
    flat = mk([100.0] * 80 + [92.0, 84.0, 80.0] + [82.0, 90.0, 100.0, 110.0] + [95.0])
    ws3 = W.to_wave_state(W.evaluate(flat, last(flat)))
    # ★ 2026-10-15 语义修正：down1 默认 sub_level=「下跌1浪」，只有**放量急杀当日**才标 C杀 ✓
    assert ws3["level"] == "down" and ws3["operation"] == "defense" and ws3["sub_level"] in ("下跌1浪", "C杀")
    assert ws3["mode"] == "自救" and ws3["mode_note"], ws3
    high = mk([100.0] * 80 + [130.0])
    assert W.to_wave_state(W.evaluate(high, last(high)))["level"] == "d5"


def test_write_wave_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    rows = mk(CHOP)
    p = W.write_wave_state(rows=rows, dated=True)
    assert p and os.path.exists(p)
    d = json.load(open(p, encoding="utf-8"))
    assert d["source"] == "wave_ladder" and d["operation"] in ("t_only", "defense")   # 4-1 回调初 ⇒ defense ✓
    dated = tmp_path / ("wave_state_%s.json" % d["date"])
    assert dated.exists(), "带日期副本没写出来"


def test_old_wave_agent_is_retired():
    """旧 LLM agent 默认**不写盘直接退出**（WOLF_WAVE_LEGACY_AGENT=1 才恢复）。"""
    import subprocess
    env = dict(os.environ)
    env.pop("WOLF_WAVE_LEGACY_AGENT", None)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "apps", "main_line", "wave_agent.py")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "退役" in (r.stderr + r.stdout)



# ─────────────── 主升叠加档 3-3（2026-10-15 补 B；3-2 不做接管） ───────────────


def test_walk_prefix_invariance_no_lookahead():
    # ★ 未来函数检验（前缀截断不变性）：只喂到第 i 天算出的那一天判定，必须等于全量跑出来的同一天 ✓。
    # 未来函数的黄金检验 —— 若某天状态用到了它之后的数据，两者必然不一致 ✗。
    # 实测（2026-10-15，真实 5319 根序列抽样 14 天含最后两天）0 不一致 ✓。
    rows = mk(FLAT + [102.0, 103.0, 104.0] + [104.0] * 30 + [110.0] + [108.0] * 12
              + [96.0] * 23 + [99.0, 101.0] + [120.0] + [112.0] * 9)
    evs = W.walk(rows)
    assert len(evs) > 40
    by = {e['date']: e for e in evs}
    key = lambda e: (e['rung_index'], e['operation'], e.get('main_up') or '',
                     (e.get('features') or {}).get('wave1_top'), (e.get('features') or {}).get('prev_peak'))
    checked = 0
    for i in range(80, len(rows), 7):
        if rows[i][0] not in by:
            continue
        assert key(by[rows[i][0]]) == key(W.walk(rows[: i + 1])[-1]), ('future leak', rows[i][0])
        checked += 1
    assert checked >= 10, checked


def _patch_amount(rows, i, amt):
    rows[i] = (rows[i][0], rows[i][1], rows[i][2], amt)
    return rows


def test_w_bottom_confirm_gives_d1_build():
    # W底：两低点相近(≤3%) + 中间颈线 + **今日放量收上颈线** ⇒ d1/W底/build + 重定浪 ✓
    # （他 10-15 原话『需放量突破颈线才确认重仓 build』；原表 W底=确认建仓 / d1=底部确认后建仓）
    rows = mk(FLAT + [88.0] + [92.0, 96.0, 100.0, 104.0] + [100.0, 95.0] + [90.5] + [95.0, 100.0] + [107.0])
    for i in range(len(rows) - 20, len(rows)):
        _patch_amount(rows, i, 1e12)
    _patch_amount(rows, len(rows) - 1, 3e12)          # 今日放量 ⇒ vol_ratio20 = 3.0 ✓
    ev = W.evaluate(rows, last(rows), prev=2)          # 就算上锁在 defense，W底确认也重定浪 ✓
    assert ev["features"]["w_bottom"] is True, ev["features"].get("w_bottom_neck")
    ws = W.to_wave_state(ev)
    assert ws["level"] == "d1" and ws["sub_level"] == "W底" and ws["operation"] == "build", ws
    assert "W底确认" in (ev.get("reasons") or ""), ev.get("reasons")


def test_basis_43_gives_side():
    # 4-3 筑底：4 浪调整档内 + 20 日低点冻结 ≥5 日 + 仍在低位(≤5%) + **缩量**(20日均额/60日均额 ≤0.8) ⇒ d4/4-3/side ✓
    rows = mk(FLAT + [130.0] + [112.0] * 40 + [112.0 + 0.1 * i for i in range(20)])   # 末 20 日缓升 ⇒ close > lo20 ✓
    _patch_amount(rows, len(rows) - 1, 60.0)
    for i in range(len(rows) - 20, len(rows)):
        _patch_amount(rows, i, 20e9)                   # 近 20 日缩量
    for i in range(len(rows) - 60, len(rows) - 20):
        _patch_amount(rows, i, 100e9)                  # 之前放量 ⇒ 比值 ≈0.2 ✓
    ev = W.evaluate(rows, last(rows))
    f = ev["features"]
    assert f["basis_43"] is True and ev["rung_index"] == 0, (f["basis_43"], ev["rung_index"], f["vol_ratio60"])
    ws = W.to_wave_state(ev)
    assert ws["level"] == "d4" and ws["sub_level"] == "4-3" and ws["operation"] == "side", ws


def test_top_sell_on_shrink_high_and_shadow():
    # 顶部：高位区 + ≥2 个信号（缩量新高 / 长上影 / 滞涨 / 双头）⇒ d5/末段/exit ✓（原表 d5=兑现降仓）
    rows = mk(FLAT + [120.0] + [118.0] * 6 + [119.2] + [119.4] + [119.9] + [122.0] + [124.0])
    for i in range(len(rows) - 20, len(rows)):
        _patch_amount(rows, i, 100e9)
    _patch_amount(rows, len(rows) - 1, 5e9)            # 缩量新高 ⇒ shrink_high ✓
    rows[-1] = (rows[-1][0], 124.4, 126.5, 5e9)        # 长上影 ⇒ upper_shadow ✓
    ev = W.evaluate(rows, last(rows))
    f = ev["features"]
    assert f["top_zone"] and f["shrink_high"] and f["upper_shadow"], (f["top_zone"], f["shrink_high"], f["upper_shadow"])
    ws = W.to_wave_state(ev)
    assert ws["level"] == "d5" and ws["operation"] == "exit", ws


def test_basis_41_gives_defense():
    # 4-1 回调初：4 浪调整档 + 距 60 日前高 ≤5% + 近 5 日跌 ≥2% ⇒ d4/4-1/defense ✓（原表 4-1=defense）
    # 自建序列（不用 FLAT：其 100 基准会让 20 日低点落在 19 天前 ⇒ 被 rung1 抢先 ✓✗）
    # 末根即 20 日最低 ⇒ low_age=0 ⇒ bottom_ok=False ⇒ rung0 ✓；距 60 日前高 −4.6% ✓、近 5 日 −3.8% ✓
    rows = mk([131 + 0.02 * i for i in range(80)] + [133.0, 132.0, 131.0, 130.0, 129.0, 128.0, 127.5, 127.0, 126.5])
    ev = W.evaluate(rows, last(rows))
    f = ev["features"]
    assert f["basis_41"] is True, (f["basis_41"], f["close"] / f["hi60_prev"] - 1)
    # 行为等价断言：4-1 与「失败4-5」的 operation **都是 defense** ✓（4-1 的增量价值是**标签**，不是档位：
    # 实测 rung0 里满足 4-1 的只有 12 天，其余浅回调会被「失败4-5」先接住 —— 两条都指向观望降仓 ✓）
    ws = W.to_wave_state(ev)
    assert ws["operation"] == "defense", ws
    assert ws["sub_level"] in ("4-1", "失败5"), ws


def test_b_rebound_is_label_only():
    # 下跌结构中的反弹 ⇒ 只加 down_tag=B反 标签，**不改 operation**（保持 down1 的 defense+自救 ✓）
    rows = mk(FLAT + [120.0] + [100.0] + [94.0, 93.0] + [95.0] + [99.0, 102.0])
    ev = W.evaluate(rows, last(rows))
    ws = W.to_wave_state(ev)
    if ev["rung_index"] >= 2 and ev["features"]["b_rebound"]:
        assert ev.get("down_tag") == "B反", ev.get("down_tag")
        assert ws["operation"] in ("defense", "exit"), ws


def test_main_up_33_requires_sync3(monkeypatch):
    """他的原话是「**三大指数同时**化解顶背离」⇒ sync3=False（三指数不同步）时**不许**给 3-3 ✓。"""
    rows = mk(FLAT + [103.0, 104.0, 105.0])
    monkeypatch.setattr(W, "sync3", lambda asof, look=3: False)
    ev = W.evaluate(rows, last(rows))
    assert ev["main_up"] == "" and ev["features"]["sync3"] is False, ev["main_up"]


def test_main_up_33_overlay(monkeypatch):
    """连续 ≥3 日收盘 > 前 20 日最高（「站稳三天」的量化）∧ 量能不缩 ⇒ 叠加档 3-3（level=d3 / op=build）。"""
    rows = mk(FLAT + [103.0, 104.0, 105.0])
    monkeypatch.setattr(W, "sync3", lambda asof, look=3: None)      # 无三指数数据 ⇒ 退回单指数口径 ✓
    ev = W.evaluate(rows, last(rows))
    assert ev["main_up"] == "3-3" and ev["main_up_spec"]["operation"] == "build", ev["main_up"]
    ws = W.to_wave_state(ev)
    assert ws["level"] == "d3" and ws["sub_level"] == "3-3" and ws["operation"] == "build", ws


def test_main_up_33_respects_latch():
    """★ 必须看**上锁后的档**：链已经在 defense（rung2）时，不许被主升叠加档放宽成 build ✗
    （首版用当日档 raw ⇒ 2026-10-09 那种上锁 defense 的日子被误放宽，实测打回）。"""
    rows = mk(FADED)                                   # 该 fixture 自身判到 rung2（失败4-5）
    rows[-1] = (rows[-1][0], rows[-1][1], 121.0, rows[-1][3])
    ev = W.evaluate(rows, last(rows), prev=2)           # 上锁在 rung2（失败4-5/defense）
    assert ev["rung_index"] == 2 and ev["main_up"] == "", ev["main_up"]
    assert W.to_wave_state(ev)["operation"] == "defense"


def test_main_up_32_is_info_only():
    """3-2 与「4浪调整」在价格上**不可区分**（2026-07-31 实测）⇒ 只留信息字段，绝不改写 level/op ✓。"""
    rows = mk(FLAT + [130.0] + [112.0] * 19 + [120.0, 124.0, 121.0, 118.0])
    rows[-1] = (rows[-1][0], rows[-1][1], 121.0, rows[-1][3])   # 末根不跳空
    ev = W.evaluate(rows, last(rows))
    assert ev["main_up"] == "" and ev["main_up_pb_info"] is False, ev["main_up"]
    ws = W.to_wave_state(ev)
    # 关键断言：叠加档**没有**改写 level/op（不出现 d3/build），仍按链自己的档 ✓
    assert ws["level"] != "d3" and ws["operation"] != "build", ws


def test_artifact_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    rows = mk(CHOP)
    ev = W.evaluate(rows, last(rows))
    assert W.write_artifact(ev, "wl_test.json") is True
    got = W.read_ladder("wl_test.json")
    assert got and got["rung_index"] == 0 and got["switch"] == "shadow"


def test_window_defaults_unchanged():
    """硬约束守护：窗口参数的**默认值**必须一字不改（生产零影响 ✓）。

    等级模型（E1/E1b）要按「度」重标跑周线 ⇒ 这些参数改成**可环境变量覆盖**；
    默认值一旦被顺手改动，生产 `wave_state.json` 的档位就会跟着挪 ⇒ 本测钉死默认值 ✓。
    """
    import subprocess
    code = ("import sys;sys.path.insert(0, %r);import wave_ladder as W;"
            "print(W.MIN_BARS, W.HI_WIN, W.RALLY_WIN, W.ORIGIN_BARS)"
            % os.path.join(ROOT, "apps", "main_line"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOLF_WAVE_LADDER")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "80 60 20 900", out.stdout


def test_write_wave_state_without_ev_uses_latched_walk(monkeypatch, tmp_path):
    """★ 2026-10-21：write_wave_state(ev=None) 必须走**带锁回放**（原来走 evaluate 无锁 ✗
    ⇒ 实测直接把生产档从 defense 写成 t_only ✗）。假数据：破位后小幅反弹（<5%）⇒ 锁必须保持 ✓"""
    import datetime as dt
    import json

    import wave_ladder as W

    d = dt.date(2026, 1, 5)

    def nxt():
        nonlocal d
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out = d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)
        return out

    rows = [(nxt(), 100.0, 101.0, 1e9) for _ in range(100)]   # 要 > MIN_BARS(80) 才有结论 ✓
    rows.append((nxt(), 95.0, 96.0, 1e9))          # 破位 -5% ⇒ rung3 上锁
    rows += [(nxt(), 95.5, 96.0, 1e9) for _ in range(9)]   # 反弹 +0.5% < 5% ⇒ 锁保持

    monkeypatch.setattr(W, "DATA", str(tmp_path))
    monkeypatch.setattr(W, "load_rows", lambda code=None: rows)
    p = W.write_wave_state(dated=True)
    assert p, "应写出文件"
    ws = json.load(open(p, encoding="utf-8"))
    assert int((ws.get("rung") or {}).get("index", -1)) == 3, ws.get("rung")


# ───────────── 2026-10-23：报告 §1B/§1D/§1E + §3-1/§3-6 的回归锁 ─────────────
def test_legs_stale_rule():
    """腿陈旧判据：①距快照 > STALE_DAYS ②确认顶之后比确认顶高 ≥ LEGS_GAIN_STALE（任一命中 ✓）。"""
    import wave_ladder as W

    assert W._legs_stale(False, 999, 0.5) is False        # 没腿 ⇒ 不谈陈旧
    assert W._legs_stale(True, W.STALE_DAYS, 0.0) is False
    assert W._legs_stale(True, W.STALE_DAYS + 1, 0.0) is True
    assert W._legs_stale(True, 5, W.LEGS_GAIN_STALE) is True
    assert W._legs_stale(True, 5, W.LEGS_GAIN_STALE - 0.01) is False


def test_sub_level_is_closed_enum():
    """`sub_level` 枚举封闭：过渡串（4-2→4-3）**不进** sub_level，改走 leg_transition（报告 §1D）。
    原值仍可从 `sub_level_raw` 取回，便于排查 ✓。"""
    import wave_ladder as W

    fi = {"sub_stage": "4-2", "sub_forming": "4-3", "sub_legs": [{"label": "4-1", "date": "20260101", "value": 100.0}],
          "legs_stale": False}
    ev = {"date": "20260720", "key": "w4_adj", "rung_index": 0, "features": fi, "operation": "t_only"}
    ws = W.to_wave_state(ev)
    assert ws["sub_level"] == "4-2", ws["sub_level"]
    assert ws["leg_transition"] == "4-2→4-3", ws["leg_transition"]
    assert ws["sub_level_raw"] == "4-2→4-3", ws["sub_level_raw"]
    assert W._sub_ok(ws["sub_level"]) and not W._sub_ok(ws["sub_level_raw"])
    # 单词形态/状态词仍在枚举内（消费方按等值/子串匹配，语义唯一 ✓）
    assert all(W._sub_ok(x) for x in ("4-5", "失败5", "下跌1浪", "末段", "C杀", "W底"))


def test_stale_legs_do_not_drive_sub_level():
    """★ 报告 §1B 核心：腿陈旧时**不再**用腿的阶段当档位标签（腿数值原样保留 ✓ §3-5）。"""
    import wave_ladder as W

    legs = [{"label": "4-1", "date": "20241017", "value": 3169.38},
            {"label": "4-5", "date": "20250113", "value": 3160.76}]
    fi = {"sub_stage": "4-5", "sub_forming": "", "sub_legs": legs, "legs_stale": True}
    ev = {"date": "20260615", "key": "w4_adj", "rung_index": 0, "features": fi, "operation": "t_only"}
    ws = W.to_wave_state(ev)
    assert ws["sub_level"] == "", ws["sub_level"]            # 陈旧腿 ⇒ 不外推标签
    assert ws["leg_stage"] == "4-5" and ws["legs_stale"] is True
    assert len(ws["sub_legs"]) == 2 and ws["sub_legs"][0]["value"] == 3169.38   # 腿数值不丢 ✓
    # rung 自己的档（失败5/下跌1浪）不受腿陈旧影响 ✓
    ev2 = dict(ev, key="w45_fail", rung_index=2)
    assert W.to_wave_state(ev2)["sub_level"] == "失败5"


def test_rung_and_overlay_are_separate_fields():
    """★ 报告 §1E 根因：一个字段两个写者 ⇒ 拆成 rung_sub_level / overlay_level（日间翻转可归因 ✓）。"""
    import wave_ladder as W

    fi = {"sub_stage": "4-3", "sub_forming": "", "sub_legs": [], "legs_stale": False}
    ev = {"date": "20250805", "key": "w4_adj", "rung_index": 0, "features": fi,
          "operation": "t_only", "ov_spec": {"level": "d4", "sub_level": "4-3", "operation": "side"}}
    ws = W.to_wave_state(ev)
    assert ws["overlay_level"] == "4-3" and ws["rung_sub_level"] == ""
    assert ws["sub_level"] == "4-3" and ws["operation"] == "side"


def test_anchor_metadata_and_pin(monkeypatch):
    """锚点自描述 + 人工钉住（报告 §3-1）：窗口/边界/确认顶之后最高点都要能看到，钉住后 anchor_source=pin。"""
    import wave_ladder as W

    rows = mk([100.0] * 80 + [130.0] * 3 + [112.0] * 12 + [125.0] * 5)   # 涨 → 回落确认顶 → 再拉起
    st = W.structural_levels(rows, last(rows))
    assert st["window_start"] == "" or st["window_start"] <= last(rows)
    assert st["anchor_source"] == "auto"
    assert st["peak_since_anchor"] is not None
    assert st["anchor_peak_gain"] is None or isinstance(st["anchor_peak_gain"], float)
    monkeypatch.setenv("WOLF_WAVE_LADDER_ORIGIN", "88.5@20250602")
    st2 = W.structural_levels(rows, last(rows))
    assert st2["anchor_source"] == "pin" and st2["origin"] == 88.5 and st2["origin_date"] == "20250602"


def test_anchor_peak_gain_marks_legs_stale():
    """★ 报告 §1B 的实测场景：确认顶被「单边一路上行」甩开（无 10% 回撤 ⇒ 确认滞后）⇒ 腿必须判 stale。"""
    import wave_ladder as W

    # 100×80 → 130（顶）→ 回落 10% 确认该顶 → 再单边上行到 145（比确认顶高 11.5%，中间不回撤 10%）
    rows = mk([100.0] * 80 + [130.0] * 4 + [117.0] * 6 + [119.0 + 1.5 * i for i in range(18)])
    f = W.features(rows, last(rows))
    assert f["prior_peak"] is not None
    assert (f["anchor_peak_gain"] or 0) >= W.LEGS_GAIN_STALE, f["anchor_peak_gain"]
    assert f["legs_stale"] is True, (f["legs_stale"], f["legs_age"], f["anchor_peak_gain"])


def test_generator_version_and_data_window_stamped():
    """报告 §3-6：产物必须带版本戳 + 数据窗口（这次「混 vintage」就是没有它们才要靠 mtime 考古 ✗）。"""
    import wave_ladder as W

    rows = mk(FLAT + [103.0, 104.0, 105.0])
    ev = W.evaluate(rows, last(rows))
    ws = W.to_wave_state(ev)
    assert ws["generator_version"] == W.WAVE_LADDER_VERSION
    fi = ws["features"]
    assert fi["data_window_start"] and fi["data_bars"] > 0
    assert "anchor_window_start" in fi and "origin_near_window_edge" in fi


def test_anchor_revision_logged(monkeypatch, tmp_path):
    """报告 §3-1：锚点改写要留痕（旧值/生效日/原因），平时是空操作 ✓。"""
    import wave_ladder as W

    monkeypatch.setattr(W, "DATA", str(tmp_path))
    ws1 = {"as_of": "20260101", "features": {"origin": 2702.185, "origin_date": "20240205",
                                            "wave1_top": 3174.2658, "wave1_top_date": "20240520",
                                            "anchor_source": "auto", "data_window_start": "20230131"}}
    W._maybe_log_anchor_revision(ws1)
    W._maybe_log_anchor_revision(ws1)                        # 同锚点 ⇒ 不追加
    p = os.path.join(str(tmp_path), W.ANCHOR_FILE)
    obj = json.load(open(p, encoding="utf-8"))
    assert obj["current"]["origin"] == 2702.185 and obj["revisions"] == []
    ws2 = {"as_of": "20270101", "features": {"origin": 2704.09, "origin_date": "20240913",
                                            "wave1_top": 3674.4048, "wave1_top_date": "20241008",
                                            "anchor_source": "auto", "data_window_start": "20240301"}}
    W._maybe_log_anchor_revision(ws2)                        # 锚点变了 ⇒ 追加一条（含旧值/生效日/原因）
    obj = json.load(open(p, encoding="utf-8"))
    assert len(obj["revisions"]) == 1
    rev = obj["revisions"][0]
    assert rev["from"]["origin"] == 2702.185 and rev["to"]["origin"] == 2704.09
    assert rev["detected_on"] == "20270101" and rev["reason"]


def test_leg_cycle_groups_legs_across_cycles():
    """★ 报告 §4-1：4-1…4-5 必须能区分「哪一轮」——每条腿带 `cycle`（= 锚点顶日期），
    消费方按 `(cycle, label)` 分组统计即跨轮不混算 ✓。"""
    import wave_ladder as W

    # 涨到 130 → 回落 10% 确认顶 → 反弹/回落若干细级拐点
    rows = mk([100.0] * 80 + [130.0] * 3 + [117.0] * 8 + [124.0, 119.0, 126.0, 121.0])
    f = W.features(rows, last(rows))
    assert f["leg_cycle"] and f["leg_cycle"] == f["prior_peak_date"], (f["leg_cycle"], f["prior_peak_date"])
    legs = f["sub_legs"]
    assert legs, "应有腿"
    assert all(l["cycle"] == f["leg_cycle"] for l in legs)          # 同一轮 ⇒ 同一个键 ✓
    assert [l["index"] for l in legs] == list(range(1, len(legs) + 1))
    assert f["leg_cycle_legs"] == len(legs)
    assert f["leg_cycle_complete"] == (len(legs) >= 5)
    assert f["leg_cycle_prev"] == (f["prev_peak_date"] or "")     # 上一轮的键 = 再前一个确认顶 ✓
    assert f["leg_cycle_prev"] != f["leg_cycle"]


def test_leg_cycle_state_matches_stale_flag():
    """`leg_cycle_state` 与 `legs_stale` 同源（fresh/stale 二值，供统计侧过滤 ✓）。"""
    import wave_ladder as W

    rows = mk(FLAT + [103.0, 104.0, 105.0])
    f = W.features(rows, last(rows))
    assert f["leg_cycle_state"] == ("stale" if f["legs_stale"] else "fresh")
    assert f["leg_cycle_state"] in ("fresh", "stale")
