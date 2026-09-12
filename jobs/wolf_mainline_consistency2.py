# -*- coding: utf-8 -*-
"""wolf_mainline_consistency2.py — 主线一致性检验 v2（2026-09-12，用户要求）。

**与 v1 的三点区别**
1. "他的方向"改为 **dsh 直读判定的 `doing[]`**（他**真正在做**的），经 LLM 映射到 13 主题；
   **不再用关键词**（关键词会把"提到/评论/明确不做"算成关注，v1 就是这么做的）
2. **加同日基线**：gate 基线 = 当天全部主题的确认率；heat 基线 = 随机期望排名 (1+13)/2
   → 报告**超额命中率**与**配对 t 值**，而不是裸命中率
3. **两种口径并列**：`doing`（实际在做）vs `mentions`（仅提到，v1 口径）→ 直接看出差别

用法：python jobs/wolf_mainline_consistency2.py
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

THEMES = ["AI/算力/科技", "半导体/芯片", "医药", "新能源/电池", "机器人/智能制造", "汽车/智驾",
          "消费/内需", "农业", "电力/公用", "稳增长/基建", "资源/周期", "金融", "军工/航天"]
# v1 口径（仅用于对照）：他的说法 → 主题的关键词映射（我们构造的）
HIS_KW: Dict[str, List[str]] = {
    "AI/算力/科技": ["算力", "AI", "人工智能", "光模块", "CPO", "光通信", "液冷", "国算", "服务器",
                     "数据中心", "大光", "AI软", "光存", "光芯"],
    "半导体/芯片": ["半导体", "芯片", "存储", "晶圆", "光刻", "封测"],
    "医药": ["医药", "药", "创新药", "CXO", "医疗"],
    "新能源/电池": ["新能源", "锂电", "电池", "光伏", "储能", "风电"],
    "机器人/智能制造": ["机器人", "人形", "减速器", "母机", "机床", "自动化"],
    "汽车/智驾": ["汽车", "智驾", "整车", "零部件", "车路云"],
    "消费/内需": ["消费", "白酒", "食品", "饮料", "零售", "家电", "免税"],
    "农业": ["农业", "养殖", "生猪", "种业", "饲料"],
    "电力/公用": ["电力", "电网", "核电", "水电", "公用事业", "燃气"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械"],
    "资源/周期": ["有色", "煤炭", "黄金", "贵金属", "稀土", "石油", "化工", "小金属", "白银"],
    "金融": ["券商", "银行", "保险", "金融", "红利"],
    "军工/航天": ["军工", "航天", "航空", "卫星", "国防", "低空", "无人机"],
}


def _t(v: List[float]) -> Dict[str, Any]:
    n = len(v)
    if n == 0:
        return {"n": 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {"n": n, "mean": round(m, 4), "t": round(m / (sd / n ** 0.5), 2) if sd else None,
            "pos_ratio": round(sum(1 for x in v if x > 0) / n, 3)}


def load_doing(db) -> Dict[str, Set[str]]:
    """{date: {13 主题}} —— 来自 dsh 直读的 doing[] + LLM 主题映射；排除『非主题』。"""
    from sqlalchemy import text
    mp: Dict[str, str] = {}
    for r in db.execute(text("SELECT dir_text, theme FROM wolf_dir_theme_map")).mappings().all():
        if r["theme"]:
            mp[r["dir_text"]] = r["theme"]
    out: Dict[str, Set[str]] = {}
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")).mappings().all():
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
        s: Set[str] = set()
        unmapped = 0
        for x in (p.get("doing") or []):
            d = (x.get("dir") or "").strip()
            th = mp.get(d)
            if th in THEMES:
                s.add(th)
            else:
                unmapped += 1
        if s:
            out[r["trade_date"]] = s
    return out


def load_mentions(corpus_path: str) -> Dict[str, Set[str]]:
    """v1 口径：他当天文本里**提到**的主题（关键词）——仅作对照。"""
    d = json.load(open(corpus_path, encoding="utf-8"))
    out: Dict[str, Set[str]] = {}
    for day in d.get("days", []):
        dt = (day.get("date") or "").replace("-", "")
        if not dt:
            continue
        txt = [day.get("market_view") or ""]
        for a in day.get("actions", []) or []:
            txt.append((a.get("what") or "") + " " + (a.get("quote") or ""))
        for s in day.get("strategies", []) or []:
            txt.append((s.get("topic") or "") + " " + (s.get("quote") or ""))
        blob = " ".join(txt)
        hit = {th for th, kws in HIS_KW.items() if any(k in blob for k in kws)}
        if hit:
            out[dt] = hit
    return out


def load_gate_heat(db) -> Tuple[Dict[str, Dict[str, bool]], Dict[str, Dict[str, int]]]:
    from sqlalchemy import text
    gate: Dict[str, Dict[str, bool]] = {}
    heat: Dict[str, Dict[str, int]] = {}
    for r in db.execute(text("SELECT trade_date, artifact_key, payload FROM daily_artifacts "
                             "WHERE artifact_key IN ('mainline_gate','heat_v2')")).mappings().all():
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
    return gate, heat


def load_title_scores(db, window: int = 30) -> Dict[str, Dict[str, int]]:
    from sqlalchemy import text
    by_day: Dict[str, List[str]] = {}
    for d, t in db.execute(text("SELECT trade_date, title FROM research_reports WHERE title IS NOT NULL")).all():
        by_day.setdefault(d, []).append(t)
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


def evaluate(name: str, his: Dict[str, Set[str]], gate: Dict[str, Dict[str, bool]],
             heat: Dict[str, Dict[str, int]], titles: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    days = sorted(set(his) & set(gate))
    gate_exc, heat_exc, title_exc, hit_gate, base_gate, ranks = [], [], [], [], [], []
    per_day = []
    for d in days:
        fset = his[d]
        g = gate.get(d) or {}
        h = heat.get(d) or {}
        if not g:
            continue
        base = sum(1 for th in THEMES if g.get(th)) / len(THEMES)
        foc = [1.0 if g.get(th) else 0.0 for th in fset]
        if not foc:
            continue
        hit = sum(foc) / len(foc)
        hit_gate.append(hit)
        base_gate.append(base)
        gate_exc.append(hit - base)
        rk = [h.get(th, 99) for th in fset]
        ranks.append(sum(rk) / len(rk))
        heat_exc.append(7.0 - (sum(rk) / len(rk)))     # 随机期望排名 (1+13)/2=7，越大越好
        prev = [x for x in sorted(titles.keys()) if x < d]
        tsc = titles.get(prev[-1], {}) if prev else {}
        if tsc:
            tf = sum(tsc.get(th, 0) for th in fset) / len(fset)
            tn = sum(v for th, v in tsc.items() if th not in fset) / max(1, len(THEMES) - len(fset))
            title_exc.append(tf - tn)
        per_day.append({"date": d, "his": sorted(fset), "hit": round(hit, 3), "base": round(base, 3),
                        "avg_heat_rank": round(sum(rk) / len(rk), 2),
                        "gate_confirmed": sorted([th for th, v in g.items() if v])})
    return {
        "source": name, "n_days": len(per_day),
        "gate_hit_mean": _t(hit_gate), "gate_baseline_mean": _t(base_gate),
        "gate_excess": _t(gate_exc),                       # 配对：他doing通过率 − 同日全体通过率
        "heat_rank_mean": _t(ranks), "heat_excess_vs_random": _t(heat_exc),
        "title_excess_focus_minus_other": _t(title_exc),
        "per_day": per_day[:60],
    }


def main() -> int:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        doing = load_doing(db)
        mentions = load_mentions("/app/data/_bt_batch2/seg2026_merged.json")
        gate, heat = load_gate_heat(db)
        titles = load_title_scores(db)
        print("[c2] dsh 直读 doing 天数 %d（与 gate 交集 %d）| 关键词 mentions 天数 %d" %
              (len(doing), len(set(doing) & set(gate)), len(mentions)), flush=True)
        r1 = evaluate("doing_llm", doing, gate, heat, titles)
        r2 = evaluate("mentions_kw", mentions, gate, heat, titles)
        out = {"generated_at": None, "themes": THEMES, "doing": r1, "mentions": r2,
               "note": ("口径：gate 基线=当日全体主题确认率；heat 随机基线=7.0（13 主题期望排名）；"
                        "配对差=他doing均值−同日基线；标题分为 T-1（PIT）")}
        import datetime as _dt
        out["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        for r in (r1, r2):
            print("[c2] === %s（%d 天）===" % (r["source"], r["n_days"]), flush=True)
            print("     gate 命中 %s | 同日基线 %s" % (json.dumps(r["gate_hit_mean"], ensure_ascii=False),
                                                     json.dumps(r["gate_baseline_mean"], ensure_ascii=False)), flush=True)
            print("     gate 超额(配对) %s" % json.dumps(r["gate_excess"], ensure_ascii=False), flush=True)
            print("     heat 平均排名 %s | 相对随机(7.0) %s" % (json.dumps(r["heat_rank_mean"], ensure_ascii=False),
                                                            json.dumps(r["heat_excess_vs_random"], ensure_ascii=False)), flush=True)
            print("     标题分超额(关注−其他) %s" % json.dumps(r["title_excess_focus_minus_other"], ensure_ascii=False), flush=True)
        with open("/app/data/wolf_mainline_consistency2.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[c2] 已写 /app/data/wolf_mainline_consistency2.json", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
