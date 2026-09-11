# -*- coding: utf-8 -*-
"""wolf_review_score.py — B1「每日复盘打分表」（2026-09-11）。

═══════════════════════════════════════════════════════════════════════════
语料来源（**两层**，都已核到原文）:

① 表本体 = XLS **2025-04-21** 楼层内嵌截图 `./mon_202504/21/jmQ91k5-gq3xZdT3cSos-ab.jpg`
   （已下载留存: `.dsh-tmp/wolfbt/b1_review_table.jpg`，逐字转录见 `b1_table_transcribed.md`）
   表头 = A列 项目 | B列 **多方优势** | C列 **空方优势** | D列 关联性 | E列 观察方向
   15 项：1 7:00AM数据 / 2 A50股指期货数据 / 3 沪深300股指期货数据 / 4 中证1000股指期货数据 /
        5 行业资金流入榜(包括3日) / 6 行业资金流出榜(包括3日) / 7 融资融券资金 /
        8 龙虎榜机构、柚子资金 / 9 龙虎榜热点板块个股 / 10 观察涨停板方向 /
        11 新加坡A50指数 / 12 纳斯达克指数 / 13 金龙指数 / 14 三倍恐慌情绪 / 15 三倍做多中国
② 打分规则（同楼文字，逐字）:
   「根据这个表格，**前8项，多方占有+1分，空方占优+1分**」
   「**空方分数两倍大于多方时，空方占优，多方两倍分数大于空方时，多方占优**」
   「这样可以简略的推断出**明天的指数前2小时**的大致方向」
   「**第10不用打分 只是观察方向** 连扳梯队代表了板块的强弱 然后看首板多的方向」
   「**外围的分数多空都是0.1分**」
   「那两个行业流入流出是咋打分的啊?看总的净流入和流出？」→「**对** 然后再关注一下流入是什么方向
     流出是什么方向就好了」
═══════════════════════════════════════════════════════════════════════════

**口径决议（记下来，避免以后又猜）**
  · 计分项 = 第 1–8 项（各 **1 分**）+ 第 11–15 项（各 **0.1 分**）；第 9/10 项 = **观察，不计分**。
  · 第 5/6 项按他本人确认的"看**总的**净流入和流出"记分：全市场主力**净流入**为正 → 第5项投多方；
    **净流出**为正 → 第6项投空方。**这两项是同一数据的正反两面，故只投一票**（不重复计分）。
  · 第 1 项「7:00AM数据」**表述不明**（截图里只有"看多单和空单的变化"）→ 记 **数据待明确，不计分**，
    不猜。
  · 第 13/14/15 项（金龙指数 / 三倍恐慌 / 三倍做多中国）：tushare `us_daily` 实测
    **YINN/YANG/HXC/KWEB 均返回 0 行** → 目前记 **数据缺**，不计分（等有源再补）。
  · **参评项数 < 4 时不下结论**（只输出分数与缺失清单），避免"3 项就敢判多空"。

**数据源（全部实测可用，2026-09-11）**
  · 期指多空持仓 = tushare `fut_holding`（按 symbol 前缀 IF/IH/IC/IM 聚合 long_hld/short_hld）
  · 两融 = tushare `margin`（SSE+SZSE，`rzmre - rzche` 为融资净买入）
  · 龙虎榜 = tushare `top_inst`（机构/游资席位 net_buy）
  · 行业资金 = `jobs/fund_flow.get_sector_fund_flow_summary()`（东财 push2）
  · 外围 = `core/utils/us_market_linkage`（get_us_indices / get_a50_futures / get_china_etfs）
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

STATE_FILE = "wolf_review_score.json"

# 原表 15 项（逐字，来自截图转录）
ITEMS: Dict[int, Dict[str, str]] = {
    1: {"name": "7:00AM数据", "strength": "强", "watch": "看多单和空单的变化"},
    2: {"name": "A50股指期货数据", "strength": "强", "watch": "看多单和空单的变化"},
    3: {"name": "沪深300股指期货数据", "strength": "强", "watch": "看多单和空单的变化"},
    4: {"name": "中证1000股指期货数据", "strength": "强", "watch": "看多单和空单的变化"},
    5: {"name": "行业资金流入榜（包括3日）", "strength": "强", "watch": "观察前5"},
    6: {"name": "行业资金流出榜（包括3日）", "strength": "强", "watch": "观察前5"},
    7: {"name": "融资融券资金", "strength": "强", "watch": "是否有大幅度流入流出"},
    8: {"name": "龙虎榜机构、柚子资金", "strength": "强", "watch": "是否有大幅度流入流出"},
    9: {"name": "龙虎榜热点板块个股", "strength": "强", "watch": "观察席位"},
    10: {"name": "观察涨停板方向", "strength": "强", "watch": "用开盘啦 打板客网等软件看资金方向"},
    11: {"name": "新加坡A50指数", "strength": "弱", "watch": "只微弱影响开盘"},
    12: {"name": "纳斯达克指数", "strength": "弱", "watch": "只微弱影响开盘"},
    13: {"name": "金龙指数", "strength": "弱", "watch": "只微弱影响开盘"},
    14: {"name": "三倍恐慌情绪", "strength": "弱", "watch": "只微弱影响开盘"},
    15: {"name": "三倍做多中国", "strength": "弱", "watch": "只微弱影响开盘"},
}
# 权重：前8项 1 分；外围 11–15 各 0.1 分；9/10 观察不计分（他原话「第10不用打分」）
WEIGHT = {i: 1.0 for i in range(1, 9)}
WEIGHT.update({i: 0.1 for i in range(11, 16)})
MIN_ITEMS = 4          # 参评项数下限（低于此不下结论）


def enabled() -> bool:
    return os.getenv("WOLF_REVIEW_SCORE", "1").strip() not in ("0", "false", "no")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_REVIEW_SCORE_FILE", STATE_FILE))


# ───────────────────────── 采集器（每个都容错返回 None） ─────────────────────────

def _vote(cond: Optional[bool]) -> Optional[str]:
    return None if cond is None else ("long" if cond else "short")


def _fut_net(symbol_prefix: str, date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """某股指期货品种的**净持仓**：sum(long_hld) − sum(short_hld)（按席位聚合）。"""
    try:
        import datetime as _dt
        from app.core.trading._api_config import get_tushare_pro
        d8 = date8 or _dt.date.today().strftime("%Y%m%d")
        df = get_tushare_pro().fut_holding(trade_date=d8)
        if df is None or len(df) == 0:
            return None
        sub = df[df["symbol"].astype(str).str.upper().str.startswith(symbol_prefix.upper())]
        if len(sub) == 0:
            return None
        lg = float(sub["long_hld"].sum())
        sh = float(sub["short_hld"].sum())
        return {"long_hld": lg, "short_hld": sh, "net": lg - sh,
                "long_chg": float(sub["long_chg"].sum()), "short_chg": float(sub["short_chg"].sum()),
                "n": int(len(sub))}
    except Exception as e:
        print(f"[review_score] fut_holding({symbol_prefix}) 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _fut_bias(date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """期指多空**增减**（他 2026-01-23 的口径）→ 复用 wolf_index_futures.fetch_holdings。"""
    try:
        from app.services.wolf_index_futures import fetch_holdings
        return fetch_holdings(date8)
    except Exception as e:
        print(f"[review_score] 期指多空失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _margin(date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """两融：融资净买入 = Σ(rzmre − rzche)（沪+深）。"""
    try:
        import datetime as _dt
        from app.core.trading._api_config import get_tushare_pro
        d8 = date8 or _dt.date.today().strftime("%Y%m%d")
        df = get_tushare_pro().margin(trade_date=d8)
        if df is None or len(df) == 0:
            return None
        df = df[df["exchange_id"].astype(str).isin(["SSE", "SZSE"])]
        buy = float(df["rzmre"].sum())
        sell = float(df["rzche"].sum())
        return {"rzmre": buy, "rzche": sell, "net_buy": buy - sell,
                "rzye": float(df["rzye"].sum())}
    except Exception as e:
        print(f"[review_score] margin 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _lhb_inst(date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """龙虎榜机构/游资席位净买合计。"""
    try:
        import datetime as _dt
        from app.core.trading._api_config import get_tushare_pro
        d8 = date8 or _dt.date.today().strftime("%Y%m%d")
        df = get_tushare_pro().top_inst(trade_date=d8)
        if df is None or len(df) == 0:
            return None
        net = float(df["net_buy"].sum()) if "net_buy" in df.columns else None
        top = []
        if "exalter" in df.columns and "net_buy" in df.columns:
            g = df.groupby("exalter")["net_buy"].sum().sort_values(ascending=False)
            top = [{"exalter": str(k), "net_buy": float(v)} for k, v in g.head(3).items()]
        return {"net_buy": net, "n": int(len(df)), "top": top}
    except Exception as e:
        print(f"[review_score] top_inst 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _bounded(fn, timeout: float, name: str):
    """在**有界时间**内跑一个采集器；超时返回 None（不让任何数据源挂死整个任务）。

    本项目已有"取数必须有界"的教训（test_api_responsiveness 专门断言过 timeout 参数），
    但第三方 SDK 内部仍可能无界重试 —— 这里用守护线程兜底。
    """
    import threading
    box: Dict[str, Any] = {}

    def _w():
        try:
            box["v"] = fn()
        except Exception as e:
            box["e"] = e
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(float(timeout))
    if t.is_alive():
        print(f"[review_score] {name} 超过 {timeout}s → 放弃（按数据缺处理）")
        return None
    if box.get("e"):
        print(f"[review_score] {name} 异常: {type(box['e']).__name__}: {str(box['e'])[:70]}")
        return None
    return box.get("v")


def _market_flow(date8: Optional[str] = None, days: int = 3) -> Optional[Dict[str, Any]]:
    """**全市场**主力净额（他确认的口径「看总的净流入和流出」），并按表头「**包括3日**」做 3 日合计。

    源：tushare `moneyflow_mkt_dc`（东财大盘资金流，日频、盘后更新；走本项目 tushare 代理，
    有界）。**注意**：`net_amount` 单位是元，`buy_*_amount` 是万元（见 jobs/fund_flow 的注释），
    这里统一转成亿元输出。
    """
    try:
        import datetime as _dt
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        d8 = date8 or _dt.date.today().strftime("%Y%m%d")
        end = _dt.datetime.strptime(d8, "%Y%m%d").date()
        got: List[Dict[str, Any]] = []
        for back in range(0, 12):
            d = (end - _dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = pro.moneyflow_mkt_dc(trade_date=d)
            except Exception:
                continue
            if df is None or len(df) == 0:
                continue
            r = df.iloc[0]
            got.append({"date": str(r.get("trade_date") or d),
                        "net_yi": round(float(r.get("net_amount") or 0) / 1e8, 2)})
            if len(got) >= max(1, int(days)):
                break
        if not got:
            return None
        return {"days": got, "sum_yi": round(sum(x["net_yi"] for x in got), 2),
                "last_yi": got[0]["net_yi"], "n": len(got)}
    except Exception as e:
        print(f"[review_score] moneyflow_mkt_dc 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


# us_market_linkage 在"全部数据源失败"时会返回**硬编码占位值**（不是真实行情）。
# 2026-09-11 发现：A50 占位 {current:11580, change:80, change_pct:0.70}，且**原来没有任何标记**，
# 消费方会把 0.70% 当成真实涨幅去投票（我第一版就中招了）。该模块现已加 fallback=True 标记，
# 这里再叠加值比对做双保险 —— 宁可不投这一票，也不能投假票。
_A50_PLACEHOLDER = (11580, 0.70)


def _a50_pct() -> Optional[float]:
    """新加坡 A50 涨跌幅；识别并**丢弃硬编码占位值**。"""
    try:
        import sys
        for p in ("/home/fengx/marcus-platform", "/app"):
            if os.path.isdir(os.path.join(p, "core")) and p not in sys.path:
                sys.path.insert(0, p)
        from core.utils.us_market_linkage import get_a50_futures
        d = get_a50_futures() or {}
        if d.get("fallback"):
            print("[review_score] A50 命中占位值标记 → 按数据缺处理")
            return None
        cur, pct = d.get("current"), d.get("change_pct")
        if cur is None or pct is None:
            return None
        if (float(cur), float(pct)) == _A50_PLACEHOLDER:
            print("[review_score] A50 值与硬编码占位值相同 → 按数据缺处理")
            return None
        return float(pct)
    except Exception as e:
        print(f"[review_score] A50 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _nasdaq_pct() -> Optional[float]:
    """纳斯达克涨跌幅；`current=0 / update_time=数据不可用` 视为缺（否则 0 会被当成「平」投一票）。"""
    try:
        import sys
        for p in ("/home/fengx/marcus-platform", "/app"):
            if os.path.isdir(os.path.join(p, "core")) and p not in sys.path:
                sys.path.insert(0, p)
        from core.utils.us_market_linkage import get_us_indices
        d = (get_us_indices() or {}).get("纳斯达克") or {}
        if float(d.get("current") or 0) <= 0 or str(d.get("update_time")) == "数据不可用":
            return None
        return float(d.get("change_pct") or 0)
    except Exception as e:
        print(f"[review_score] 纳指 失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def _overseas() -> Dict[str, Any]:
    """外围：11 新加坡A50 / 12 纳斯达克（13/14/15 暂无源）。"""
    return {"a50": _a50_pct(), "nasdaq": _nasdaq_pct()}


# ───────────────────────── 打分 ─────────────────────────

def collect(date8: Optional[str] = None, ov: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """采集各计分项 → {item_no: {'vote': 'long'|'short'|None, 'note': str}}。"""
    v: Dict[int, Dict[str, Any]] = {}
    ov = ov if ov is not None else _bounded(_overseas, 60, "外围联动")

    v[1] = {"vote": None, "note": "「7:00AM数据」表述不明（截图仅注『看多单和空单的变化』）→ 不猜、不计分"}
    # 第2项「A50股指期货数据」他的观察方向是「看**多单和空单的变化**」= 持仓，不是涨跌。
    # 新交所 A50 期货的持仓数据我们没有源 → **记数据缺**；不得拿第11项的 A50 指数涨跌顶替，
    # 否则同一标的会投两票（第2项 1 分 + 第11项 0.1 分）。
    v[2] = {"vote": None, "note": "A50 期货**持仓/多空单变化**无数据源（不得用第11项指数涨跌代替，否则重复计分）"}
    # ⚠️ 口径按他 2026-01-23 的纠正：「**不是当日空单和多单相比 是和多空前一日的增减对比**」
    #    （表里第2/3/4项的观察方向也是"看多单和空单的**变化**"）→ 用**偏多度 = 多单增减% − 空单增减%**。
    #    第一版用的是"净持仓相比"，那是他明确否掉的口径。
    #    实测（wolf_index_futures 文档）：该规则对次日黄白线的命中率约 51%（158 日样本）→ 仅作一项投票。
    h = _bounded(lambda: _fut_bias(date8), 30, "期指多空增减")
    for no, code, nm in ((3, "IF", "沪深300"), (4, "IM", "中证1000")):
        x = (h or {}).get(code) or {}
        b = x.get("bias")
        v[no] = {"vote": _vote(None if b is None else b > 0),
                 "note": ("%s 偏多度 %+.2f（多单%+.2f%%/空单%+.2f%%）" % (nm, b, x.get("long_chg_pct") or 0,
                                                                        x.get("short_chg_pct") or 0))
                 if b is not None else "数据缺"}
    sf = _bounded(lambda: _market_flow(date8), 45, "全市场资金流(3日)")
    if sf is None:
        v[5] = {"vote": None, "note": "全市场资金流数据缺"}
        v[6] = {"vote": None, "note": "全市场资金流数据缺"}
    else:
        net = sf.get("sum_yi") or 0
        detail = "、".join(f"{x['date'][4:]}:{x['net_yi']:+.0f}亿" for x in sf["days"])
        # 他本人确认的口径：看**总的**净流入/流出；5、6 是同一数据正反两面 → 只投一票
        if net > 0:
            v[5] = {"vote": "long", "note": f"全市场主力 3 日净流入 {net:+.0f}亿（{detail}）"}
            v[6] = {"vote": None, "note": "与第5项同一数据反面 → 不重复计分"}
        else:
            v[5] = {"vote": None, "note": "与第6项同一数据反面 → 不重复计分"}
            v[6] = {"vote": "short", "note": f"全市场主力 3 日净流出 {net:+.0f}亿（{detail}）"}
    mg = _bounded(lambda: _margin(date8), 30, "两融")
    v[7] = {"vote": _vote(None if not mg else mg["net_buy"] > 0),
            "note": "融资净买入 %s" % (f"{mg['net_buy'] / 1e8:+.1f}亿" if mg else "数据缺")}
    lhb = _bounded(lambda: _lhb_inst(date8), 30, "龙虎榜")
    v[8] = {"vote": _vote(None if not lhb or lhb.get("net_buy") is None else lhb["net_buy"] > 0),
            "note": "机构/游资席位净买 %s" % (f"{lhb['net_buy'] / 1e8:+.1f}亿" if lhb and lhb.get("net_buy") is not None else "数据缺")}
    v[9] = {"vote": None, "note": "观察项（看席位）不计分" + ("｜Top: " + ", ".join(
        f"{t['exalter']}:{t['net_buy'] / 1e8:+.2f}亿" for t in (lhb or {}).get("top", [])[:3]) if lhb else "")}
    v[10] = {"vote": None, "note": "观察项（涨停板方向）不计分 —— 见 B4 wolf_limit_ladder"}
    for no, key in ((11, "a50"), (12, "nasdaq")):
        x = ov.get(key)
        v[no] = {"vote": _vote(None if x is None else float(x) > 0), "note": f"{ITEMS[no]['name']} {x}"}
    for no in (13, 14, 15):
        v[no] = {"vote": None, "note": "tushare us_daily 实测 YINN/YANG/HXC/KWEB 均 0 行 → 数据缺"}
    return v


def item_status(no: int, vote: Optional[str], note: str) -> str:
    """条目状态：scored(计分) / observed(观察项不计分) / nodata(数据缺) / dedup(与另一项同源不重复计分)。"""
    if no in (9, 10):
        return "observed"
    if vote is not None:
        return "scored"
    if "不重复计分" in (note or ""):
        return "dedup"
    return "nodata"


def score(votes: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """按他原话打分：前8项各 1 分，外围各 0.1 分；**两倍差**判多空。"""
    lng = sht = 0.0
    detail = []
    for no in sorted(votes.keys()):
        if no not in WEIGHT:
            continue
        vo = votes[no].get("vote")
        w = WEIGHT[no]
        if vo == "long":
            lng += w
            detail.append({"no": no, "name": ITEMS[no]["name"], "vote": "long", "w": w,
                           "note": votes[no].get("note")})
        elif vo == "short":
            sht += w
            detail.append({"no": no, "name": ITEMS[no]["name"], "vote": "short", "w": w,
                           "note": votes[no].get("note")})
    lng, sht = round(lng, 2), round(sht, 2)
    n = len(detail)
    if n < MIN_ITEMS:
        verdict = "insufficient"
    elif sht >= 2 * lng and sht > 0:
        verdict = "short"
    elif lng >= 2 * sht and lng > 0:
        verdict = "long"
    else:
        verdict = "neutral"
    return {"long": lng, "short": sht, "n_scored": n, "verdict": verdict, "detail": detail}


def verdict_cn(verdict: str) -> str:
    return {"long": "多方占优", "short": "空方占优", "neutral": "多空均衡（两倍差未达）",
            "insufficient": "样本不足，不下结论"}.get(verdict, verdict)


def run(date8: Optional[str] = None, save: bool = True) -> Dict[str, Any]:
    """采集 → 打分 → 落状态文件（供次日盘前用）。"""
    import datetime as _dt
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    d8 = date8 or _dt.date.today().strftime("%Y%m%d")
    votes = collect(d8)
    sc = score(votes)
    res = {"ok": True, "date": d8, "items": {str(k): v for k, v in votes.items()}, **sc}
    res["verdict_cn"] = verdict_cn(sc["verdict"])
    res["missing"] = [ITEMS[n]["name"] for n in sorted(votes)
                      if item_status(n, votes[n].get("vote"), votes[n].get("note")) == "nodata"
                      and n in WEIGHT]
    res["dedup"] = [ITEMS[n]["name"] for n in sorted(votes)
                    if item_status(n, votes[n].get("vote"), votes[n].get("note")) == "dedup"]
    res["observed"] = [ITEMS[n]["name"] for n in sorted(votes)
                       if item_status(n, votes[n].get("vote"), votes[n].get("note")) == "observed"]
    if save:
        try:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[review_score] 落盘失败: {str(e)[:80]}")
            res["ok"] = False
    print(f"[review_score] {d8} 多方{sc['long']} vs 空方{sc['short']}（参评 {sc['n_scored']} 项）"
          f" → {res['verdict_cn']}")
    for d in sc["detail"]:
        print(f"    #{d['no']} {d['name']}: {d['vote']} (+{d['w']})｜{d['note']}")
    if res["missing"]:
        print(f"    数据缺: {', '.join(res['missing'])}")
    if res.get("dedup"):
        print(f"    同源不重复计分: {', '.join(res['dedup'])}")
    if res.get("observed"):
        print(f"    观察项(不计分): {', '.join(res['observed'])}")
    return res


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """给次日盘前上下文的预判（他的原话：据此推断**明天指数前2小时**方向）。"""
    st = load()
    if not st or not st.get("verdict_cn"):
        return ""
    d = str(st.get("date") or "")
    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
    lines = ["🧮 %s 复盘打分（狼大 2025-04-21：前8项各1分、外围各0.1分，**两倍差判多空**）"
             "→ 多方 %s vs 空方 %s（参评 %s 项）→ **%s**；据此预判**次日前 2 小时**方向"
             % (d, st.get("long"), st.get("short"), st.get("n_scored"), st.get("verdict_cn"))]
    for x in (st.get("detail") or [])[:6]:
        lines.append("  - #%s %s → %s｜%s" % (x.get("no"), x.get("name"),
                                              "多方" if x.get("vote") == "long" else "空方", x.get("note")))
    if st.get("missing"):
        lines.append("  （数据缺: %s）" % "、".join(st["missing"]))
    if st.get("dedup"):
        lines.append("  （与同源项不重复计分: %s）" % "、".join(st["dedup"]))
    return "\n".join(lines)
