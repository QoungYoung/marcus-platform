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


def pool_k() -> int:
    """池宽 K（量能占比 topK）。默认 3：实测 K=3 选择质量最好（top3 74%、超额 +1.43%），
    放大 K 只提 recall 不提选择质量。"""
    try:
        return max(1, int(os.getenv("WOLF_MS_POOL_K", "3") or 3))
    except (TypeError, ValueError):
        return 3


def use_gate() -> bool:
    """是否用 gate（结构资格闸）约束选择。**默认 0 = 不用**。

    三方对照（2026，72 天，"他股票主线层"口径，见 jobs/eval_gate_vs_pool.py）：
      · 池 T6（不用 gate）：top1 54% / top3 85% / recall 75% / 完全不在 15% / +1.789%(t=4.54)
      · 池 T6 ∩ gate：      top1 51% / top3 78% / recall 63% / 完全不在 22% / +1.746%(t=4.90)
      · gate 单独（旧判定）：top1 28% / top3 51% / precision 12% / +1.513%(t=4.43)
    → gate 作**主线判定**无信息（≈随机）；作**约束**净负（砍 12pp 对齐度、收益差在噪声内）。
    只在需要回退对比时用 WOLF_MS_USE_GATE=1 打开。
    """
    return os.getenv("WOLF_MS_USE_GATE", "0").strip().lower() in ("1", "true", "yes", "on")


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
              breadth: Optional[Dict[str, Dict[str, float]]] = None,
              share5: Optional[Dict[str, float]] = None,
              pool_k: int = 3) -> Dict[str, Any]:
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
    # ── 池（当期主线）= 结构判据：「资金在哪儿」×「有没有在动」 ──
    # 依据（2026-09-13 跨 2025/2026 两年实测）：池把"他方向落 top1"从 23% 提到 48%（top3 74%）；
    # 在"他股票主线层"口径下 recall 75% / 全部在池内 68% / 完全不在 15% / top3 85%；
    # 放大 K 只提 recall、不提选择质量（K=8 时 top3 降到 61%），故默认 K=3。
    # 语料依据：「光+半导体加起来是市场 50% 成交量…要降到 25%-30% 才可能重新走起来」（2026-08-24，量能集中度）
    #          「看看主线题材动没动就知道了」（2026-01-13）→ r5>0。
    src5: Dict[str, float] = {}
    for t, v in (share5 or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == fv:
            src5[t] = fv
    pool_top = [t for t, _ in sorted(src5.items(), key=lambda kv: -kv[1])[:max(1, int(pool_k))]]
    pool = [t for t in pool_top if (scores.get(t) or 0) > 0]
    if not pool:                       # 池全在跌 → 退回全主题（不硬拦）
        pool = [t for t, _ in ranked]
    # 选择域 = 池（默认）；仅当 WOLF_MS_USE_GATE=1 时才再 ∩ gate（实测净负，见 use_gate 注释）
    cand = ([t for t in gs if t in pool] or pool) if use_gate() else pool
    order = [t for t in cand if t in scores]
    order.sort(key=lambda t: -scores[t])
    out = {"date": days[i], "r5": {t: scores[t] for t in order[:6]},
           "rank_all": [t for t, _ in ranked],
           "rank_in_gate": order,
           "mainline": order[0] if order else None,
           "second": order[1] if len(order) > 1 else None,
           "gate_n": len(gs), "diag": {t: diag[t] for t in order[:6]},
           "pool": pool, "pool_top": pool_top, "pool_n": len(pool),
           "pool_k": int(pool_k),
           "pool_share5": {t: round(src5.get(t, 0.0), 4) for t in pool_top},
           "use_gate": bool(use_gate()),
           "gate_in_pool": [t for t in (gate_set or []) if t in pool]}
    return out


def load_universe(min_codes: int = 20):
    """主题成分（stock_pool.db::stock_concept_map，剔 ST）→ (uni, lead, allc)。

    **为什么用概念库而不是 confirm_universe**：后者只覆盖 3 个主题（C2 实测），横截面不足；
    概念库是同一生产数据源（stock_concept_map），13 主题全覆盖、逐日稳定。
      · uni  = {主题: [代码]}（成分数 >= min_codes 才保留）
      · lead = 带动板块代码（券商/银行/保险/多元金融 —— 他口径"大资金认不认"）
      · allc = 全市场代码（等权基准）
    env WOLF_MS_UNIVERSE_DB 覆盖 db 路径（回测沙箱）；env WOLF_MS_UNIVERSE_JSON 用现成 json。
    """
    import sqlite3
    uni = {}
    lead = []
    allc = []
    jp = os.getenv("WOLF_MS_UNIVERSE_JSON", "").strip()
    if jp and os.path.exists(jp):
        try:
            with open(jp, encoding="utf-8") as fh:
                d = json.load(fh)
            return ({k: list(v) for k, v in (d.get("uni") or {}).items()},
                    list(d.get("lead") or []), list(d.get("allc") or []))
        except Exception as e:
            print("[mainline] 成分 json 读取失败 %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
    db_path = os.getenv("WOLF_MS_UNIVERSE_DB") or os.path.join(
        os.environ.get("DATA_DIR", "/app/data"), "stock_pool.db")
    try:
        c = sqlite3.connect(db_path)
        try:
            st = {r[0] for r in c.execute("SELECT ts_code FROM stock_pool WHERE is_st=1")}
        except sqlite3.Error:
            st = set()
        for th, kws in THEME_CONCEPT_KW.items():
            codes = set()
            for kw in kws:
                for row in c.execute(
                        "SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE ?",
                        ("%" + kw + "%",)):
                    ts = row[0]
                    if ts and ts not in st:
                        codes.add(ts)
            if len(codes) >= min_codes:
                uni[th] = sorted(codes)
        lead_codes = set()
        for kw in LEAD_KW:
            for row in c.execute(
                    "SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE ?",
                    ("%" + kw + "%",)):
                if row[0]:
                    lead_codes.add(row[0])
        lead = sorted(lead_codes)
        try:
            allc = sorted(r[0] for r in c.execute("SELECT DISTINCT ts_code FROM stock_pool") if r[0])
        except sqlite3.Error:
            allc = []
        c.close()
    except Exception as e:
        print("[mainline] 主题成分加载失败 %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
    if not allc:
        allc = sorted({ts for codes in uni.values() for ts in codes})
    return uni, lead, allc

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
        # **回看窗口**：只取最近 LOOKBACK 个交易日（r5/r20/breadth 只需 ~25 天）。
        # 生产教训：全历史拉取在 512MB 容器里会把 pandas 撑爆（2025 回填后表有 455 天/250 万行）。
        lookback = int(os.getenv("WOLF_MS_LOOKBACK_DAYS", "60") or 60)
        dmin = db.execute(text("SELECT min(trade_date) FROM (SELECT DISTINCT trade_date FROM mkt_bars_daily "
                               "WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT :n) t"),
                          {"d": d8, "n": lookback}).scalar() or d8
        rows = db.execute(text("SELECT trade_date, ts_code, pct_chg, amount FROM mkt_bars_daily "
                               "WHERE pct_chg IS NOT NULL AND trade_date <= :d AND trade_date >= :d0"),
                          {"d": d8, "d0": dmin}).all()
        if not rows:
            return {"ok": False, "reason": "no_bars"}
        df = pd.DataFrame(rows, columns=["d", "ts", "pc", "amt"])
        df["pc"] = df["pc"].astype(float) / 100.0
        df["amt"] = pd.to_numeric(df["amt"], errors="coerce").fillna(0.0)
        wide = df.pivot_table(index="d", columns="ts", values="pc", aggfunc="first").sort_index()
        amt = df.pivot_table(index="d", columns="ts", values="amt", aggfunc="sum").reindex(wide.index).fillna(0.0)
        del df
        idx = wide.index.tolist()
        px = {d: {c: float(v) for c, v in wide.loc[d].dropna().items()} for d in idx}
        uni, lead, allc = load_universe()
        if not uni:
            return {"ok": False, "reason": "no_universe"}
        if not allc:   # 基准兜底：面板全部代码（与 eval 脚本的全市场等权口径一致）
            allc = sorted({x for d in px.values() for x in d})
        ma20 = wide.rolling(20).mean(); above = (wide > ma20)
        breadth = {d: {th: (sum(1 for c in uni[th] if bool(above.loc[d].get(c, False))) / max(1, len(uni[th])))
                       for th in uni} for d in idx}
        gset = []
        r = db.execute(text("SELECT payload FROM daily_artifacts WHERE artifact_key='mainline_gate' "
                            "AND trade_date=:d"), {"d": d8}).mappings().first()
        if r:
            p = r["payload"] if isinstance(r["payload"], dict) else _json.loads(r["payload"])
            gset = [x["theme"] for x in (p.get("rows") or []) if x.get("gate") and x.get("theme") in uni]
        # 主题近 5 日成交额占全市场比（池判据之一：资金在哪儿）
        mamt5 = amt.sum(axis=1).rolling(5).sum()
        share5: Dict[str, float] = {}
        for th, codes in uni.items():
            cols = [c for c in codes if c in amt.columns]
            if not cols:
                continue
            s = amt[cols].sum(axis=1).rolling(5).sum() / mamt5.replace(0, pd.NA)
            v = s.iloc[-1] if len(s) else None
            if v is not None and v == v:
                share5[th] = float(v)
        res = score_day(idx, len(idx) - 1, px, uni, lead, allc, gate_set=gset, breadth=breadth,
                        share5=share5, pool_k=pool_k())
        if not res:
            return {"ok": False, "reason": "insufficient_history"}
        res["validation"] = {"design": "池(量能占比topK ∩ r5>0) ∩ gate → 池内 r5 top1",
                             "his_top1_2026": 0.54, "his_top3_2026": 0.85, "recall_2026": 0.75,
                             "h5_excess_2026": 1.03, "t_2026": 3.37, "h5_2025": 0.338, "t_2025": 3.01,
                             "source": "docs/wolf-structural-pool.md §七/§十",
                             "note": "consistency 口径=他股票主线层；accel/breadth/联动/龙头 均已实测无效，仅作诊断"}
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
        print("[mainline] %s 主线=%s｜池=%s（K=%d, 资格 %d 个）｜候选前3=%s" %
              (d8, res.get("mainline"), res.get("pool"), res.get("pool_k", 0), res.get("gate_n"),
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
    pool = st.get("pool") or []
    sh = st.get("pool_share5") or {}
    pdesc = "、".join("%s(占比%.1f%%)" % (t, 100 * (sh.get(t) or 0)) for t in pool) or "—"
    return ("🧭 方向层主线（**池=量能占比top%d ∩ 近5日相对强度>0** ∩ 资格闸 → 池内 r5 top1；"
            "他方向落 top1 54%%/top3 85%%）｜**%s**（次选 %s）｜池：%s｜候选前3：%s"
            % (st.get("pool_k", 3), st["mainline"], st.get("second") or "—", pdesc, top3))
