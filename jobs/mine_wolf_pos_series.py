# -*- coding: utf-8 -*-
"""E1d 步骤 A：从他自己的话里抽出**他报出的仓位数字**，建成 date → 仓位% 时间序列。

动机（承 E1b/E1c 的失败根因）：以「他的一句话 = 一个动作标签」为 ground truth 时，
他讲操作的日子 68~71% 本就在降风险 ⇒ 基线高、线索 lift 全被压死 ✗。
**仓位数字是连续量**：同一条发言携带的信息量更大，而且「减仓事件」可以从序列里**客观**算出
（Δ ≤ −5pp 就是一次减仓），不必依赖我对句子的方向判断 ✓。

三道门（沿用 E1b，**不放宽** ✗）：
  1. **账户/指数语境**：仓位词必须是**裸**的（总仓位/仓位/仓），且不是被行业·板块·资产词修饰的
     子仓（科技仓位 / 游戏仓位 ✗）；窗口里出现 期货|杠杆|多单|空单|现货 ⇒ 非 A 股指数账户 ✗ 剔除；
  2. **第一人称**：±60 字窗口内必须有 我/我们（讲你们/散户/机构 ✗ 不算）；
  3. **非条件句**：「如果/一旦/只要/假如/要是/万一」⇒ 入 COND 桶（不进序列，只审计 ✓）。

单位归一（**先声明**）：`X%` 直取；`X层` / `X成` × 10（他的口语，如 2026-04-02「4层仓位加到6层」）；
「半仓 / 满仓 / 空仓」**不给数字** ⇒ 只进漏斗计数，不进序列（避免把我编的数字掺进 ground truth ✗）。

同日多条取哪条（**先声明**）：优先含「收盘」的那条（他的收盘口径）；否则取当日**最后**一条
（他的最终状态）。**当日全部候选都留在产物里**（可逐条复核 ✓）。

用法：.venv/bin/python jobs/mine_wolf_pos_series.py    → data/_wolf_pos_series.json
"""
import collections
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
os.chdir(ROOT)
from eval_wave_ladder_vs_corpus import read_corpus  # noqa: E402

MIN_VALUE, MAX_VALUE = 5, 100          # 5% 以下基本是子仓/打野仓（他口径的总仓不会 <5%）⇒ 原则性下界
CHANGE_PP = 5.0                        # 「减仓/加仓事件」= 相邻观测变动 ≥5pp（一个整数档的量级 ✓）
GATE_DAYS, GATE_CHANGES = 60, 25       # 可行性闸（**先声明**）：够不够做事件研究

# 仓位数字：① 数字在前（60%仓位 / 30%的底仓）② 数字在后（仓位70% / 剩余仓位57%）
RX_PCT = re.compile(r"(\d{1,3})\s*%\s*(?:的)?(?:总仓位|仓位|仓|底仓)"
                    r"|(?:总仓位|仓位|剩余仓位|收盘仓位|我的仓位)\s*(\d{1,3})\s*%")
RX_UNIT = re.compile(r"(\d{1,2})\s*[层成](?:\s*(?:仓位|仓))?")
RX_WORD = re.compile(r"半仓|满仓|空仓|全仓")
SCENARIO = re.compile(r"如果|一旦|只要|假如|要是|万一")
# 未来/计划句（「预计就100%仓位了」「到时候调10%」「控制30%仓位内」「会减到25%」）⇒ 计划不是观测 ✗
FUTURE = re.compile(r"预计|到时候|计划|准备|目标|以内|会减到|会加到|会回到|会打满|要减到|要控制|就会")
PERSON = re.compile(r"我|我们")
NOT_MINE = re.compile(r"期货|杠杆|多单|空单|现货|夜盘|外盘")
# 子仓修饰词（紧贴仓位词前的行业/资产/工具词 ⇒ 不是总仓位 ✗）
SECTOR = re.compile(r"(?:科技|半导体|芯片|机器人|稀土|游戏|光伏|医药|药|电池|锂|AI|算力|军工|航天|券商|银行|保险|"
                    r"地产|基建|电力|黄金|白银|原油|石油|化工|钢铁|有色|消费|红利|北证|科创|创业|港股|美股|ETF|个股|长线|短线|活动)$")


def _has_person(txt: str, a: int, b: int) -> bool:
    return bool(PERSON.search(txt[max(0, a - 60):b + 60]))


FILLER = re.compile(r"[，,\s]|就|占|是|的|我|也|还|大概|大约|约|一共|总共|总|共|目前|现在|已经|还有|剩下|剩|保持|到了|到|了|全|都")


def _is_bare(txt: str, pos_tok_start: int) -> bool:
    """仓位词前 14 字内（去掉「就占我」这类填词后）仍有行业/资产词 ⇒ 子仓，不算总仓位 ✗。"""
    prefix = FILLER.sub("", txt[max(0, pos_tok_start - 14):pos_tok_start])
    return not SECTOR.search(prefix)


def mine() -> int:
    rows = read_corpus()
    funnel = collections.Counter()
    cands: Dict[str, List[dict]] = collections.defaultdict(list)
    for d8, dt, txt in rows:
        for sn in re.split(r"[。！？；\n]", txt):
            if not sn.strip():
                continue
            hits: List[Tuple[int, int, float, str]] = []
            for m in RX_PCT.finditer(sn):
                v = m.group(1) or m.group(2)
                if v:
                    hits.append((m.start(), m.end(), float(v), "pct"))
            for m in RX_UNIT.finditer(sn):          # 「7层仓位」= 70%（必须先声明归一 ✓）
                if float(m.group(1)) <= 10 and re.search(r"仓位|仓", sn[max(0, m.start() - 6):m.end() + 6]):
                    hits.append((m.start(), m.end(), float(m.group(1)) * 10.0, "unit"))
            if not hits:
                continue
            for start, end, val, kind in hits:
                if not (MIN_VALUE <= val <= MAX_VALUE):
                    funnel["数值越界 skip"] += 1
                    continue
                if not _has_person(sn, start, end):
                    funnel["无第一人称 skip"] += 1
                    continue
                if NOT_MINE.search(sn[max(0, start - 40):end + 40]):
                    funnel["非 A 股账户（期货/杠杆…）skip"] += 1
                    continue
                tok_start = start if kind == "unit" else (start if "%" in sn[start:end] else start)
                if not _is_bare(sn, tok_start):
                    funnel["子仓（行业/资产修饰）skip"] += 1
                    continue
                cond = bool(SCENARIO.search(sn)) or bool(FUTURE.search(sn))
                funnel["条件/计划句 → COND 桶" if cond else "进序列"] += 1
                near = sn[max(0, start - 10):end + 10]
                cands[d8].append({"date": d8, "dt": dt[:16], "pos": val, "kind": kind,
                                  "cond": cond, "close_basis": "收盘" in near,
                                  "quote": re.sub(r"\s+", " ", sn.strip())[:150]})
    # 同日择优（先声明）：含「收盘」优先，否则当日最后一条；COND 桶不参与择优 ✓
    series, conds = {}, []
    for d8, items in cands.items():
        ok = [c for c in items if not c["cond"]]
        conds.extend(c for c in items if c["cond"])
        if not ok:
            continue
        close = [c for c in ok if c.get("close_basis")]
        pick = close[-1] if close else ok[-1]
        series[d8] = {"date": d8, "pos": pick["pos"], "dt": pick["dt"], "quote": pick["quote"],
                      "basis": "收盘" if close else "当日最后一条", "n_cands": len(items)}
    dates = sorted(series)
    changes = []
    for a, b in zip(dates, dates[1:]):
        d = series[b]["pos"] - series[a]["pos"]
        changes.append({"from": a, "to": b, "delta": d,
                        "gap_days": (int(b[:4]) * 372 + int(b[4:6]) * 31 + int(b[6:])) -
                                     (int(a[:4]) * 372 + int(a[4:6]) * 31 + int(a[6:]))})
    ups = [c for c in changes if c["delta"] >= CHANGE_PP]
    downs = [c for c in changes if c["delta"] <= -CHANGE_PP]
    print("语料 %d 行（%s ~ %s）｜漏斗：%s" % (
        len(rows), min(r[0] for r in rows), max(r[0] for r in rows), dict(funnel)))
    print("\n有效观测日 %d 天（%s ~ %s）｜覆盖语料跨度 %d 天" % (
        len(dates), dates[0] if dates else "-", dates[-1] if dates else "-", len(dates)))
    _lo = min(dates, key=lambda d: series[d]["pos"]) if dates else None
    _hi = max(dates, key=lambda d: series[d]["pos"]) if dates else None
    print("仓位分布：中位 %.0f%% ｜ 最低 %.0f%%（%s）｜ 最高 %.0f%%（%s）" % (
        sorted(series[d]["pos"] for d in dates)[len(dates) // 2] if dates else 0,
        series[_lo]["pos"] if _lo else 0, _lo or "-",
        series[_hi]["pos"] if _hi else 0, _hi or "-"))
    print("相邻观测变动：%d 次 ｜ ≥+%dpp 加仓 %d 次 ｜ ≤-%dpp 减仓 %d 次 ｜ 中间跨期中位 %d 天" % (
        len(changes), CHANGE_PP, len(ups), CHANGE_PP, len(downs),
        sorted(c["gap_days"] for c in changes)[len(changes) // 2] if changes else 0))
    print("\n2025 起（与 E1b/E1c 标注期重叠段）逐年观测日：%s" % dict(collections.Counter(
        d[:4] for d in dates)))
    print("条件句桶（不进序列，仅审计）：%d 条，例：%s" % (
        len(conds), conds[-1]["quote"][:60] if conds else "-"))

    ok = len(dates) >= GATE_DAYS and (len(ups) + len(downs)) >= GATE_CHANGES
    print("\n可行性闸（≥%d 观测日 ∧ ≥%d 次变动）：%s（实测 %d 日 / %d 次）" % (
        GATE_DAYS, GATE_CHANGES, "过闸 ✓ 可做事件研究" if ok else "不过闸 ✗ 如实写样本不足", len(dates),
        len(ups) + len(downs)))
    print("\n== 2025 起全部观测（日期 仓位 口径）==")
    for d in [x for x in dates if x >= "20250101"]:
        s = series[d]
        print("  %s %3.0f%% [%s] %s" % (d, s["pos"], s["basis"], s["quote"][:72]))
    out = {"series": [series[d] for d in dates], "changes": changes, "conds": conds,
           "funnel": dict(funnel), "gate": {"ok": ok, "days": len(dates),
                                            "changes": len(ups) + len(downs),
                                            "need_days": GATE_DAYS, "need_changes": GATE_CHANGES},
           "params": {"MIN_VALUE": MIN_VALUE, "MAX_VALUE": MAX_VALUE, "CHANGE_PP": CHANGE_PP}}
    p = os.path.join("data", "_wolf_pos_series.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(mine())
