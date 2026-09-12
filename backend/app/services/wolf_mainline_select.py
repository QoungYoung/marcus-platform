# -*- coding: utf-8 -*-
"""wolf_mainline_select.py — L1 方向层「选主线」（2026-09-12，D1 重建）。

**为什么要重建**（本轮实测结论，见 docs/wolf-alignment-checklist.md）：
  · 现有 `gate` = `B_only >= 0.35` 的**结构资格闸**：每天 14 候选确认 10 个（60.5%），
    **完全不读消息/研报**，且各主题 ratio 的**基线差异**造成"月月同一批"（与他实际主力重合仅 1–8 个方向）。
  · 他的方向选择是**变化量**：他 2026-01-13「看看**主线题材动没动**就知道了」、
    2026-02-07「**不要在没有行情的时候**重仓在这段时间没有行情的方向」、
    2026-02-09「券商互金不表现…大资金认可的点位」、2026-02-12「十字星后不突破+卖单加大+**板块没有带动效应**」。
  · 验收线：同口径（方向后 5 日超额、PIT、剔 beta）**> +0.41%（t>2.6）**（他 1–7 月实测水平），我们 gate 是 −0.05%。

**本模块 = 资格与选择分离后的"选择层"**：输入主题日收益面板，输出**每日主线排名**与分解依据。
评分（v1，全部为**边际/相对量**，不用绝对水平）：
  · `accel`  = 近 5 日超额 − 前 5 日超额        —— "谁在**动**"（边际加速）
  · `r5`     = 近 5 日超额                     —— "有没有行情"（趋势存在性）
  · `breadth_chg` = 成分股站上 MA20 比例的 5 日变化 —— 参与面是否在扩散
  · `lead`   = 带动板块（券商/银行篮子）近 5 日超额 —— "大资金认不认"（风险偏好）
  · 横截面 z 标准化后加权求和（权重可用 env 覆盖，便于验收式迭代，不做无依据调参）

**诚实边界**：本层只做"方向排序"，不做选股/择时；所有输入都是当日及以前（PIT）。
开关 `WOLF_MAINLINE_SELECT`（默认 0）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

THEMES = ["AI/算力/科技", "半导体/芯片", "医药", "新能源/电池", "机器人/智能制造", "汽车/智驾",
          "消费/内需", "农业", "电力/公用", "稳增长/基建", "资源/周期", "金融", "军工/航天"]

# 主题 → 概念关键词（取成分股；与 stock_pool.db::stock_concept_map 对齐）
THEME_CONCEPT_KW: Dict[str, List[str]] = {
    "半导体/芯片": ["半导体", "芯片", "光刻", "封测", "存储"],
    "AI/算力/科技": ["算力", "人工智能", "光通信", "光器件", "CPO", "数据中心", "服务器"],
    "医药": ["医药", "创新药", "CXO", "疫苗", "医疗器械", "中药"],
    "新能源/电池": ["锂电", "电池", "光伏", "储能", "风电", "新能源"],
    "机器人/智能制造": ["机器人", "机床", "工业母机", "自动化", "智能制造"],
    "汽车/智驾": ["汽车", "智能驾驶", "车路云", "智能网联", "汽车零部件", "激光雷达"],
    "消费/内需": ["白酒", "食品", "饮料", "零售", "家电", "免税", "餐饮"],
    "农业": ["农业", "生猪", "养殖", "种业", "饲料", "种植"],
    "电力/公用": ["电力", "电网", "核电", "水电", "燃气", "绿电"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械", "水利"],
    "资源/周期": ["有色", "煤炭", "黄金", "稀土", "石油", "化工", "小金属"],
    "金融": ["券商", "银行", "保险", "多元金融"],
    "军工/航天": ["军工", "航天", "航空", "卫星", "国防", "低空"],
}
# 带动板块（他口径：券商互金 / 银行 → "大资金认不认"）
LEAD_KW = ["券商", "银行", "保险", "多元金融"]


def enabled() -> bool:
    return os.getenv("WOLF_MAINLINE_SELECT", "0").strip().lower() not in ("0", "false", "no", "")


def _w(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def zscore(d: Dict[str, float]) -> Dict[str, float]:
    v = list(d.values())
    if not v:
        return {}
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / max(1, len(v))) ** 0.5
    return {k: ((x - m) / sd if sd else 0.0) for k, x in d.items()}


def basket_return(px_day: Dict[str, float], codes: Sequence[str]) -> Optional[float]:
    vals = [px_day[c] for c in codes if c in px_day]
    if not vals:
        return None
    return sum(vals) / len(vals)


def compound(series: Sequence[Optional[float]]) -> Optional[float]:
    c = 1.0
    n = 0
    for x in series:
        if x is None:
            continue
        c *= (1 + x)
        n += 1
    return (c - 1) if n else None


def score_day(days: Sequence[str], i: int, px: Dict[str, Dict[str, float]],
              uni: Dict[str, List[str]], lead_codes: Sequence[str],
              market_codes: Sequence[str],
              gate_set: Optional[Sequence[str]] = None,
              breadth: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    """给 13 主题打分并选主线（PIT：只用 ≤ 第 i 天）。

    **设计依据（2026-09-12 实测，见 /app/data/eval_ms_variants.json / eval_ms_robust.json）**
      · 主信号 = **近 5 日相对强度**（主题等权复利 − 全市场等权复利）——即他的「**有没有行情**」
      · 资格 = 现有 `gate`（结构资格闸）：**选择在资格集合内做**，
        `gate ∩ r5 top1` 的后 5 日超额 **+0.622%（t=2.86, n=159）**；纯 r5 top1 为 +0.562%（t=2.33）
      · **被实测否决、因此不进球**：①「边际加速」(r5−前5日r5) 单独 top1 +0.08%（t=0.34）、加进去反而变差；
        ② 参与面扩散(breadth_chg)、位置分层、带动板块闸 都不如纯 r5。
        → 只保留为**诊断字段**，不进分数（不把无效因子塞进分数）。
    """
    scores, diag = {}, {}
    if i < 5:
        return {}
    def win(th: str, lo: int, hi: int) -> Optional[float]:
        codes = uni.get(th) or []
        ser = []
        for k in range(lo, hi + 1):
            if 0 <= k < len(days):
                b = basket_return(px.get(days[k], {}), codes)
                m = basket_return(px.get(days[k], {}), market_codes) if market_codes else None
                ser.append(None if (b is None or m is None) else (b - m))
        return compound(ser)
    r5 = {th: win(th, i - 4, i) for th in uni}
    r5p = {th: win(th, i - 9, i - 5) for th in uni}
    r20 = {th: win(th, i - 19, i) for th in uni}
    for th in uni:
        if r5.get(th) is None:
            continue
        scores[th] = round(r5[th], 6)                      # ← 主信号：有没有行情
        diag[th] = {
            "r5": round(r5[th], 4),
            "accel": None if (r5p.get(th) is None) else round(r5[th] - r5p[th], 4),   # 诊断（未进球）
            "r20": None if r20.get(th) is None else round(r20[th], 4),
            "breadth_chg": None if not breadth else (
                None if ((breadth.get(days[i]) or {}).get(th) is None or i - 5 < 0
                         or (breadth.get(days[i - 5]) or {}).get(th) is None)
                else round((breadth[days[i]][th] - breadth[days[i - 5]][th]), 4)),
        }
    gs = [t for t in (gate_set or []) if t in scores]
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    pool = gs or [t for t, _ in ranked]
    order = [t for t in pool if t in scores]
    order.sort(key=lambda t: -scores[t])
    out = {"date": days[i], "r5": {t: scores[t] for t in order[:6]},
           "rank_all": [t for t, _ in ranked],
           "rank_in_gate": order,
           "mainline": order[0] if order else None,
           "second": order[1] if len(order) > 1 else None,
           "gate_n": len(gs), "diag": {t: diag[t] for t in order[:6]}}
    return out


def run(save: bool = True, date8: Optional[str] = None) -> Dict[str, Any]:
    """盘后运行：算当日主线选择（状态文件 + daily_artifacts 落库）。**只写不交易**。"""
    import datetime as _dt
    import json as _json
    import pandas as pd
    from app.database import SessionLocal
    from sqlalchemy import text
    d8 = str(date8) if date8 else _dt.date.today().strftime("%Y%m%d")   # 防传 int（生产实测踩到）
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily "
                               "WHERE pct_chg IS NOT NULL AND trade_date <= :d"), {"d": d8}).all()
        if not rows:
            return {"ok": False, "reason": "no_bars"}
        df = pd.DataFrame(rows, columns=["d", "ts", "pc"])
        df["pc"] = df["pc"].astype(float) / 100.0
        wide = df.pivot_table(index="d", columns="ts", values="pc", aggfunc="first").sort_index()
        idx = wide.index.tolist()
        px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
        uni, lead, allc = load_universe()
        ma20 = wide.rolling(20).mean(); above = (wide > ma20)
        breadth = {d: {th: (sum(1 for c in uni[th] if bool(above.loc[d].get(c, False))) / max(1, len(uni[th])))
                       for th in uni} for d in idx}
        gset = []
        r = db.execute(text("SELECT payload FROM daily_artifacts WHERE artifact_key='mainline_gate' "
                            "AND trade_date=:d"), {"d": d8}).mappings().first()
        if r:
            p = r["payload"] if isinstance(r["payload"], dict) else _json.loads(r["payload"])
            gset = [x["theme"] for x in (p.get("rows") or []) if x.get("gate") and x.get("theme") in uni]
        res = score_day(idx, len(idx) - 1, px, uni, lead, allc, gate_set=gset, breadth=breadth)
        if not res:
            return {"ok": False, "reason": "insufficient_history"}
        res["validation"] = {"design": "gate ∩ r5 top1", "h5_excess": 0.622, "t": 2.86,
                             "n": 159, "source": "/app/data/eval_ms_robust.json",
                             "note": "accel/breadth 已实测无效，仅作诊断"}
        if save:
            try:
                p = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "wolf_mainline_select.json")
                with open(p, "w", encoding="utf-8") as f:
                    _json.dump(res, f, ensure_ascii=False, indent=1)
            except Exception:
                pass
            try:
                db.execute(text("""
                    INSERT INTO daily_artifacts (trade_date, artifact_key, payload, src_path)
                    VALUES (:d, 'mainline_select', CAST(:p AS jsonb), 'wolf_mainline_select')
                    ON CONFLICT (trade_date, artifact_key) DO UPDATE
                      SET payload=EXCLUDED.payload, created_at=now()
                """), {"d": d8, "p": _json.dumps(res, ensure_ascii=False)})
                db.commit()
            except Exception:
                db.rollback()
        print("[mainline] %s 主线=%s（gate 资格 %d 个）｜r5 top3=%s" %
              (d8, res.get("mainline"), res.get("gate_n"),
               [(t, res["r5"][t]) for t in res["rank_in_gate"][:3]]), flush=True)
        return {"ok": True, **res}
    finally:
        db.close()


def load() -> Dict[str, Any]:
    import json as _json
    try:
        with open(os.path.join(os.environ.get("DATA_DIR", "/app/data"), "wolf_mainline_select.json"),
                  encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """给上下文注入的当日主线一行（关闭或没有状态时返回空）。"""
    if not enabled():
        return ""
    st = load()
    if not st or not st.get("mainline"):
        return ""
    order = st.get("rank_in_gate") or []
    top3 = "、".join("%s(%+.2f%%)" % (t, 100 * (st.get("r5", {}).get(t) or 0)) for t in order[:3])
    return ("🧭 方向层主线（资格闸∩近5日相对强度；实测 h5 超额 +0.62% t=2.86）｜**%s**"
            "（次选 %s）｜候选前三：%s" % (st["mainline"], st.get("second") or "—", top3))
