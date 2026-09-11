# -*- coding: utf-8 -*-
"""wolf_index_futures.py — A3 股指期货多空 → 次日黄白线预判（2026-09-11）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（XLS，逐字）:
  ① 2025-04-15 条件1「…看一下 **A50期货 沪深300期货 科创50期货** 的**多单和空单的变化**
     (**以此分辨次日开盘是黄线还是白线在上**)」
  ② 2025-04-21 复盘表第 2/3/4 项「A50股指期货数据 / 沪深300股指期货数据 /
     **中证1000**股指期货数据」，观察方向都写「看**多单和空单的变化**」
  ③ **2026-01-23（这条最关键，纠正口径）**「…我刚说了这个应该怎么用，**不是当日空单和多单相比
     是和多空前一日的增减对比**」
  ④ 2026-01-23「前面有一天 **中证1000 多单加了4% 空单加了不到1%** 而且是**当日大跌**的情况
     那第二天就是**大概率黄线在上** 那第二天开盘只用判断量能，缩量上冲是不是可以比平时快15分钟
     判断可以做正T」
  ⑤ 2026-01-21「昨天盘后期指**除了上证50都是多单占优** 今天 低开 缩量 然后**黄穿白**」
═══════════════════════════════════════════════════════════════════════════

**口径（按③④定，不再自创）**
  · 每品种算 **多单增减% = long_chg / 前一日多单**、**空单增减% = short_chg / 前一日空单**
    （tushare `fut_holding` 直接给 long_hld/short_hld/long_chg/short_chg，按席位聚合）
  · **偏多度 = 多单增减% − 空单增减%**（他说的"多空前一日的增减对比"）
  · **黄白线映射**：**中证1000(IM) = 小盘 → 决定黄线**；**上证50(IH)/沪深300(IF) = 权重 → 决定白线**。
    ④ 的实例是"IM 多单加 4%、空单加不到 1% → 次日黄线在上"；⑤ 则是"除上证50外都多单占优 → 黄穿白"。
  · **A50 与"科创50期货"无源**（CFFEX 只有 IF/IH/IC/IM；新交所 A50 持仓我们拿不到）→ 如实标注，
    不拿别的数据顶替（与 B1 第 2 项同一处置）。
  · 「当日大跌」是他举的例子里的**语境**（跌的时候多单还在加更说明看多）→ 作为 `context` 输出，
    **不作为硬性前提**（他没说"必须大跌才算"）。

**落点**：盘后算出 → 次日盘前提示（进纪律上下文）。这是他"预判次日开盘黄白线"的手段；
我们 A4 的黄白线是**当日实时**值，两者互补（一个是预判、一个是实况）。

**⚠️ 本规则已实测（2026-09-11，脚本 `.dsh-tmp/wolfbt/bt_a3_validate.py`）——结论要如实带着走**
  样本：2026-01-05 ~ 2026-09-11 共 **158 个可比交易日**（期指持仓 × 次日 中证1000−上证 实测涨跌幅差）
  · 只用 IM 偏多度（他 2026-01-23 的字面口径）：**命中 51.3%**
  · 小盘组 vs 权重组（本模块 composite）：**命中 51.3%**
  · 旧的"净持仓相比"口径（他 2026-01-23 明确否掉的）：55.1%（≈ 噪声，不要用）
  · **分组**：当日指数**下跌**日 → 54.4%（n=68）；当日上涨日 → 48.9%（n=90）
    → 他举例时带的"当日大跌"语境**确实让规则略好**，但强度有限。
  （更短的 26 天窗口曾出现 65.4%，**是样本噪声** —— 样本量一变就回到 51%。）
  ⇒ 故本模块**只做提示层**（不参与硬门），并在指令里**公开命中率**，避免被当成可靠信号；
    真正决定日内做T力度的是 A4 当日**实测**黄白线。
开关 `WOLF_INDEX_FUTURES=0`。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

STATE_FILE = "wolf_index_futures.json"
# 品种 → (名称, 归属)：IM/IC 小盘(黄线侧)，IF/IH 权重(白线侧)
SPECS = [
    ("IM", "中证1000", "small"),
    ("IC", "中证500", "small"),
    ("IF", "沪深300", "big"),
    ("IH", "上证50", "big"),
]


def enabled() -> bool:
    return os.getenv("WOLF_INDEX_FUTURES", "1").strip() not in ("0", "false", "no")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_INDEX_FUTURES_FILE", STATE_FILE))


def fetch_holdings(date8: Optional[str] = None) -> Optional[Dict[str, Dict[str, Any]]]:
    """按品种聚合席位持仓与增减 → {symbol: {...}}；失败返回 None。"""
    try:
        import datetime as _dt
        from app.core.trading._api_config import get_tushare_pro
        d8 = date8 or _dt.date.today().strftime("%Y%m%d")
        df = get_tushare_pro().fut_holding(trade_date=d8)
        if df is None or len(df) == 0:
            return None
        out: Dict[str, Dict[str, Any]] = {}
        sym = df["symbol"].astype(str).str.upper()
        for code, name, side in SPECS:
            sub = df[sym.str.startswith(code)]
            if len(sub) == 0:
                continue
            lh = float(sub["long_hld"].sum())
            sh = float(sub["short_hld"].sum())
            lc = float(sub["long_chg"].sum())
            sc = float(sub["short_chg"].sum())
            out[code] = {
                "name": name, "side": side, "n_brokers": int(len(sub)),
                "long_hld": lh, "short_hld": sh, "net": lh - sh,
                "long_chg": lc, "short_chg": sc,
                # ⚠️ 他 2026-01-23 明确：看的是**增减对比**，不是当日多空持仓相比
                "long_chg_pct": round(lc / lh * 100, 3) if lh else None,
                "short_chg_pct": round(sc / sh * 100, 3) if sh else None,
                "bias": round((lc / lh - sc / sh) * 100, 3) if (lh and sh) else None,
                "date": d8,
            }
        return out or None
    except Exception as e:
        print(f"[index_futures] fut_holding 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def predict(hold: Dict[str, Dict[str, Any]], bench_pct: Optional[float] = None) -> Dict[str, Any]:
    """→ 次日开盘黄白线预判（纯函数，可测）。

    口径 = 他 2026-01-23：**偏多度 = 多单增减% − 空单增减%**；IM/IC 代表小盘(黄线)、IF/IH 代表权重(白线)。
    """
    if not hold:
        return {"ok": False, "reason": "no_data"}
    small = [v["bias"] for v in hold.values() if v.get("side") == "small" and v.get("bias") is not None]
    big = [v["bias"] for v in hold.values() if v.get("side") == "big" and v.get("bias") is not None]
    s_avg = round(sum(small) / len(small), 3) if small else None
    b_avg = round(sum(big) / len(big), 3) if big else None
    side = None
    if s_avg is not None and b_avg is not None:
        side = "huang" if s_avg > b_avg else ("bai" if b_avg > s_avg else None)
    elif s_avg is not None:
        side = "huang" if s_avg > 0 else None
    elif b_avg is not None:
        side = "bai" if b_avg > 0 else None
    im = (hold.get("IM") or {}).get("bias")
    return {"ok": True, "small_bias": s_avg, "big_bias": b_avg,
            "im_bias": im, "side": side,
            "detail": hold, "bench_pct": bench_pct,
            "context": ("当日指数大跌而多单仍在加 → 更能说明看多（他 2026-01-23 举的例子里带了这个语境）"
                        if (bench_pct is not None and bench_pct < 0) else None)}


def run(date8: Optional[str] = None, save: bool = True,
        hold: Optional[Dict[str, Dict[str, Any]]] = None,
        bench_pct: Optional[float] = None) -> Dict[str, Any]:
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    import datetime as _dt
    d8 = date8 or _dt.date.today().strftime("%Y%m%d")
    hold = hold if hold is not None else fetch_holdings(d8)
    if not hold:
        return {"ok": False, "reason": "no_data", "date": d8}
    res = predict(hold, bench_pct)
    res["date"] = d8
    if save:
        try:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[index_futures] 落盘失败: {str(e)[:80]}")
            res["ok"] = False
    side_cn = {"huang": "黄线在上（小盘强）", "bai": "白线在上（权重强）", None: "分歧/不可判"}[res["side"]]
    print(f"[index_futures] {d8} 小盘组偏多度 {res['small_bias']} vs 权重组 {res['big_bias']} → 次日预判 {side_cn}")
    for k, v in sorted((res.get("detail") or {}).items()):
        print(f"    {k}({v['name']}): 多单{v['long_chg_pct']:+.2f}% 空单{v['short_chg_pct']:+.2f}% "
              f"→ 偏多度 {v['bias']:+.2f}")
    return res


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """次日盘前的黄白线预判（他 2025-04-15 条件1 的用途）。"""
    st = load()
    if not st or not st.get("ok"):
        return ""
    side_cn = {"huang": "**黄线在上**（小盘/科技消费强）", "bai": "**白线在上**（权重强，减少做T）"}.get(
        st.get("side"), "分歧（不预判）")
    d = str(st.get("date") or "")
    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
    det = st.get("detail") or {}
    im = det.get("IM") or {}
    lines = ["🔮 %s 期指多空 → 次日开盘预判 %s（狼大 2025-04-15 条件1「以此分辨次日开盘是黄线还是白线在上」；"
             "口径按他 2026-01-23「**不是当日空单和多单相比 是和多空前一日的增减对比**」）" % (d, side_cn)]
    if im:
        lines.append("  - 中证1000(IM)：多单 %+.2f%% 空单 %+.2f%% → 偏多度 %+.2f（他举例：IM 多单加4%%/空单加不到1%% "
                     "→ 次日大概率黄线在上）" % (im.get("long_chg_pct") or 0, im.get("short_chg_pct") or 0,
                                          im.get("bias") or 0))
    if st.get("context"):
        lines.append("  （语境：%s）" % st["context"])
    lines.append("  ⚠️ 实测命中率（2026-01-05~09-11，158 个交易日）：整体 **51%**，当日下跌日 54%、上涨日 49% "
                 "→ **仅作参考，已确认不参与任何硬门**")
    return "\n".join(lines)
