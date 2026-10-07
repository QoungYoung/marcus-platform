# -*- coding: utf-8 -*-
"""wolf_mainline_consistency.py — 「我们的主线判定」能不能对上「狼大的方向表述」（2026-09-12）。

**要回答的问题**（用户）：这样能对比狼大判断主线的一致性吗？
**能，但有三条口径限制，必须写明**：
  1. 语料给的是**他"说了什么"**，不是每日主线结论；只取**有明确方向表述的日子**做对比，分母如实报告
  2. **主题颗粒度不同**：他说"大光/液冷/国算/AI软/药/红利"，我们的主题是 13 类 → 需要映射表（**我们定的**，会打印以便审计）
  3. 他的主线**会切换**（H1 科技→金融/红利）→ 除"逐日一致率"，还要看**切换时点**是否同步

**对比三方**：① 他当日关注主题（语料映射）② 我们的主线判定（`daily_artifacts.mainline_gate.rows[].gate`）
③ 我们的热度排序（`heat_v2.ranked`）+ 标题密度分（研报，PIT ≤ D-1）。

指标：他 top-1 方向是否被 gate 确认（命中率）、他关注主题的 gate 通过率 vs 非关注、
他 top-1 在 heat 排名中的分位、他关注主题的标题分 vs 非关注（区分度 + t 值）。
用法：python jobs/wolf_mainline_consistency.py [--corpus /app/data/_bt_batch2/seg2026_merged.json]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

# 他的说法 → 我们的 13 主题（**我们的映射**，打印出来供审计；含他的缩写：大光/光存/液冷/国算/药/红利）
HIS_KW: Dict[str, List[str]] = {
    "AI/算力/科技": ["算力", "AI", "人工智能", "光模块", "CPO", "光通信", "液冷", "国算", "服务器",
                     "数据中心", "大光", "光大哥", "AI软", "光存", "光芯"],
    "半导体/芯片": ["半导体", "芯片", "存储", "晶圆", "光刻", "封测", "科创半导", "存储芯片"],
    "医药": ["医药", "药", "创新药", "CXO", "医疗", "临床"],
    "新能源/电池": ["新能源", "锂电", "电池", "光伏", "储能", "风电", "钠电", "固态"],
    "机器人/智能制造": ["机器人", "人形", "减速器", "母机", "机床", "自动化"],
    "汽车/智驾": ["汽车", "智驾", "整车", "零部件", "车路云", "激光雷达"],
    "消费/内需": ["消费", "白酒", "食品", "饮料", "零售", "家电", "免税", "餐饮", "啤酒"],
    "农业": ["农业", "养殖", "生猪", "种业", "饲料", "种植"],
    "电力/公用": ["电力", "电网", "核电", "水电", "公用事业", "燃气", "火电"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械", "水利"],
    "资源/周期": ["有色", "煤炭", "黄金", "贵金属", "稀土", "石油", "化工", "小金属", "白银", "铜", "铝"],
    "金融": ["券商", "银行", "保险", "金融", "红利", "多元金融"],
    "军工/航天": ["军工", "航天", "航空", "卫星", "国防", "低空", "无人机", "导弹"],
}
THEMES = list(HIS_KW.keys())


def _t_stats(v: List[float]) -> Dict[str, Any]:
    n = len(v)
    if n == 0:
        return {"n": 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / n ** 0.5), 2) if sd else None}


def load_corpus(path: str) -> Dict[str, List[str]]:
    """他逐日的文本（actions + strategies + market_view 的 quote/what 合并）。"""
    d = json.load(open(path, encoding="utf-8"))
    out: Dict[str, List[str]] = {}
    for day in d.get("days", []):
        dt = day.get("date")
        if not dt:
            continue
        txt = [day.get("market_view") or ""]
        for a in day.get("actions", []) or []:
            txt.append((a.get("what") or "") + " " + (a.get("quote") or ""))
        for s in day.get("strategies", []) or []:
            txt.append((s.get("topic") or "") + " " + (s.get("quote") or ""))
        out[dt.replace("-", "")] = txt
    return out


def his_focus(days_text: List[str]) -> Dict[str, int]:
    sc = {th: 0 for th in THEMES}
    for t in days_text:
        for th, kws in HIS_KW.items():
            for k in kws:
                if k in t:
                    sc[th] += 1
                    break
    return sc


def load_gate_and_heat() -> Tuple[Dict[str, Dict[str, bool]], Dict[str, Dict[str, int]]]:
    """{date: {theme: gate_confirmed}}, {date: {theme: heat_rank}}"""
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    gate: Dict[str, Dict[str, bool]] = {}
    heat: Dict[str, Dict[str, int]] = {}
    try:
        rows = db.execute(text("SELECT trade_date, artifact_key, payload FROM daily_artifacts "
                               "WHERE artifact_key IN ('mainline_gate','heat_v2')")).mappings().all()
        for r in rows:
            p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
            d8 = r["trade_date"]
            if r["artifact_key"] == "mainline_gate":
                for x in (p.get("rows") or []):
                    if x.get("theme") in THEMES:
                        gate.setdefault(d8, {})[x["theme"]] = bool(x.get("gate"))
            else:
                for x in (p.get("ranked") or []):
                    if x.get("theme") in THEMES:
                        heat.setdefault(d8, {})[x["theme"]] = int(x.get("rank") or 99)
    finally:
        db.close()
    return gate, heat


def load_title_scores(window: int = 30) -> Dict[str, Dict[str, int]]:
    """{date: {theme: 标题命中数}}，PIT：只用 trade_date <= 该日（调用方自行错位到 T-1）。"""
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    by_day: Dict[str, List[str]] = {}
    try:
        for d, t in db.execute(text("SELECT trade_date, title FROM research_reports "
                                    "WHERE title IS NOT NULL")).all():
            by_day.setdefault(d, []).append(t)
    finally:
        db.close()
    days = sorted(by_day)
    out: Dict[str, Dict[str, int]] = {}
    for i, d in enumerate(days):
        sc = {th: 0 for th in THEMES}
        for dd in days[max(0, i - window): i + 1]:
            for t in by_day[dd]:
                for th, kws in HIS_KW.items():
                    if any(k in t for k in kws):
                        sc[th] += 1
                        break
        out[d] = sc
    return out


def main() -> int:
    argv = sys.argv
    corpus = "/app/data/_bt_batch2/seg2026_merged.json"
    if "--corpus" in argv:
        corpus = argv[argv.index("--corpus") + 1]
    his = load_corpus(corpus)
    gate, heat = load_gate_and_heat()
    titles = load_title_scores()
    his_days = sorted(his.keys())
    common = [d for d in his_days if d in gate]
    print("[cons] 他有记录的日子 %d；与 gate 有交集 %d（%s→%s）" %
          (len(his_days), len(common), common[0] if common else "-", common[-1] if common else "-"), flush=True)
    print("[cons] 主题映射（我们的构造）:", json.dumps({k: v[:4] for k, v in HIS_KW.items()}, ensure_ascii=False)[:400], flush=True)

    focus_days, hit_gate, hit_top3, ranks, foc_scores, non_scores, gate_pos, gate_neg, title_pos, title_neg = ([] for _ in range(10))
    per_day = []
    for d in common:
        sc = his_focus(his[d])
        if sum(sc.values()) == 0:
            continue                     # 当天没有明确方向表述 → 不进分母（诚实）
        focus_days.append(d)
        order = sorted(sc.items(), key=lambda kv: -kv[1])
        top1 = [th for th, v in order if v == order[0][1]]
        g = gate.get(d, {})
        h = heat.get(d, {})
        # T-1 的标题分（PIT）
        prev = [x for x in sorted(titles.keys()) if x < d]
        tsc = titles.get(prev[-1], {}) if prev else {}
        if not tsc:
            tsc = {th: 0 for th in THEMES}
        # ① 他 top-1 是否被 gate 确认
        hit_gate.append(1.0 if any(g.get(th) for th in top1) else 0.0)
        # ② 他 top-1 是否在 heat 前 3
        hit_top3.append(1.0 if any(h.get(th, 99) <= 3 for th in top1) else 0.0)
        # ③ 他 top-1 的 heat 排名
        ranks.append(min(h.get(th, 99) for th in top1))
        # ④ 标题分区分度
        foc_scores.append(sum(tsc.get(th, 0) for th in top1) / len(top1))
        non_scores.append(sum(v for th, v in tsc.items() if th not in top1) / max(1, len(THEMES) - len(top1)))
        # ⑤ gate 通过率：他关注的 vs 没提的
        fset = {th for th, v in sc.items() if v > 0}
        gp = [1.0 if g.get(th) else 0.0 for th in fset if th in g]
        gn = [1.0 if g.get(th) else 0.0 for th in THEMES if th not in fset and th in g]
        if gp:
            gate_pos.append(sum(gp) / len(gp))
        if gn:
            gate_neg.append(sum(gn) / len(gn))
        title_pos.append(sum(tsc.get(th, 0) for th in fset) / len(fset))
        title_neg.append(sum(v for th, v in tsc.items() if th not in fset) / max(1, len(THEMES) - len(fset)))
        per_day.append({"date": d, "his_top": top1, "his_focus": sorted(fset),
                        "gate_confirmed": sorted([th for th, v in g.items() if v]),
                        "heat_top3": sorted([th for th, r in h.items() if r <= 3], key=lambda x: h[x]),
                        "hit_gate": bool(hit_gate[-1]), "hit_heat3": bool(hit_top3[-1]),
                        "title_score_top": tsc.get(top1[0], 0)})

    res: Dict[str, Any] = {
        "n_his_days": len(his_days), "n_common_with_gate": len(common), "n_direction_days": len(focus_days),
        "hit_gate_rate": round(sum(hit_gate) / len(hit_gate), 3) if hit_gate else None,
        "hit_heat_top3_rate": round(sum(hit_top3) / len(hit_top3), 3) if hit_top3 else None,
        "his_top1_heat_rank": _t_stats([float(x) for x in ranks]),
        "gate_pass_focus": _t_stats(gate_pos), "gate_pass_nonfocus": _t_stats(gate_neg),
        "title_focus": _t_stats(title_pos), "title_nonfocus": _t_stats(title_neg),
        "note": ("分母=他有明确方向表述且与 gate 有交集的日子；主题映射是我们构造的；"
                 "标题分用 T-1（PIT）"),
        "per_day": per_day[:80],
    }
    print("[cons] 方向表述日 %d | 他 top1 被 gate 确认 %s | 在 heat 前3 %s | top1 平均 heat 排名 %s" %
          (len(focus_days), res["hit_gate_rate"], res["hit_heat_top3_rate"],
           (res["his_top1_heat_rank"] or {}).get("mean")), flush=True)
    print("[cons] 他关注主题 gate 通过率 %s vs 未提主题 %s" %
          (json.dumps(res["gate_pass_focus"], ensure_ascii=False), json.dumps(res["gate_pass_nonfocus"], ensure_ascii=False)), flush=True)
    print("[cons] 标题分：关注 %s vs 未提 %s" %
          (json.dumps(res["title_focus"], ensure_ascii=False), json.dumps(res["title_nonfocus"], ensure_ascii=False)), flush=True)
    try:
        with open("/app/data/wolf_mainline_consistency.json", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("[cons] 已写 /app/data/wolf_mainline_consistency.json", flush=True)
    except Exception as e:
        print("[cons] 落盘失败: %s" % str(e)[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
