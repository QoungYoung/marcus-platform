# -*- coding: utf-8 -*-
"""wolf_neg_event.py — 层①「无利空」前提的**公告判据**（2026-09-11 落地）。

────────────────────────────────────────────────────────────────
狼大原话（2026-03-05，与 -3% 那条同一段）:
  「是自己逻辑的有效跌破 **除非是意外事件，黑天鹅那种**。如果是**无利空**13日内下跌
    那新低后-3%就是逻辑问题 要控制损失就必须止损。后面涨是别的逻辑」
────────────────────────────────────────────────────────────────

**语义（务必照原话，反直觉）**：
  · **无利空** → 跌破新低后 -3% = 自己买入逻辑被证伪 → **必须止损**（① 结构线适用）；
  · **有利空/意外事件/黑天鹅** → 那是"别的逻辑" → **不套结构线**（退回 stop_loss_price）。
所以本模块产出的标记是「**不要套用 ① 结构线**」的信号，写进 `data/wolf_negative_events.json`，
读侧 `wolf_early_stop.negative_event()` 已实现（只认持有期内、限期内有效、读失败 fail-open），**无需改动**。

**判据为什么用公告而不是新闻情绪**（2026-09-11 生产实测）:
  · `ak.stock_notice_report(symbol='全部', date=YYYYMMDD)` 当日全市场公告清单可用
    （实测 1,144 行 / 2.1s），且带**结构化 `公告类型`** 字段 —— 「立案/退市风险/处罚/诉讼/停牌/
    风险提示」这一档可由类型直接识别，不必让模型猜情绪。项目里此前**未使用**任何公告接口。
  · 个股接口 `ak.stock_individual_notice_report` 在生产 akshare 版本里抛 `KeyError: '代码'`（坏的）
    → 因此走"拉全市场当日清单 → 按持仓过滤"。
  · 绝不使用新闻 `sentiment='negative'` / `impact_level` 作判据：negative 占全库约 19%（常态高发，
    而黑天鹅的定义是**罕见**），且会把「中报净利润同比下降 1.95%」这类**常规财报**算成利空
    → 错误豁免 → **该止不止**，风险方向危险。

**风险不对称（决定了判据必须"宁可漏不可滥"）**:
  · 漏报的代价 = 少豁免一次 → 仍按 ① 止损 → **安全**；
  · 误报的代价 = 该止损没止损 → **危险**。
故：类型白名单 + 高精度标题短语，**不追求召回**；拿不准就不标。

**已知适用范围（勿越界使用）**:
  1. **只对个股有意义**：ETF 没有个股公告（实测当日持仓 588170/512480/600519 各 0 条）。
  2. 公告只有 `公告日期`、**无发布时间字段** → 日粒度，不是 tick 级；本机制配合 ① 的
     "收盘确认"口径使用是匹配的（狼大 2026-01-29「收盘跌破我才出」）。
  3. 与狼大"盘中即时知情"仍有差距 → 属**合理外推**，不等于等价。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

NEG_EVENT_FILE = "wolf_negative_events.json"
KEEP_DAYS_DEFAULT = 30        # 标记保留自然日数（读侧另有 WOLF_NEG_EVENT_DAYS 窗口）
MAX_PER_SYMBOL = 3            # 同一标的保留最近几条（审计用，不宜堆积）

# ── 判据 1：`公告类型` 子串白名单（结构化、精度高）──
# **注意：这里不放「冻结」** —— 生产实测类型 `股份质押、冻结` 会把当天 1144 条公告里的
# 25 条**常规质押/解质押**全部拉进来（2026-09-11 实测），而质押不是黑天鹅。
# 冻结改由**标题**判据识别（见 TITLE_PATTERNS 的 被冻结/司法冻结/轮候冻结）。
TYPE_RISK = (
    "风险提示", "退市", "停牌", "处罚", "立案", "诉讼", "仲裁",
    "违规", "调查", "会计差错", "非标",
)

# ── 判据 2：标题高精度短语（只放"几乎不可能误判"的词）──
TITLE_PATTERNS = (
    r"立案(调查)?", r"被立案", r"退市风险", r"终止上市", r"强制退市", r"暂停上市",
    r"行政处罚", r"处罚决定", r"涉嫌", r"违法违规", r"违法",
    r"司法冻结", r"被冻结", r"轮候冻结", r"强制冻结", r"账户.{0,6}冻结", r"查封", r"扣押",
    r"重大诉讼", r"仲裁", r"留置", r"逮捕", r"被拘留", r"刑事",
    r"停产", r"爆炸", r"事故", r"火灾", r"泄漏",
    r"预亏", r"预计亏损", r"由盈转亏", r"首次亏损", r"首亏",
    r"业绩大幅下滑", r"业绩.{0,4}下修", r"盈利预测.{0,4}下修", r"商誉减值", r"计提大额",
    r"平仓风险", r"强制平仓", r"债务违约", r"逾期未",
    r"无法表示意见", r"保留意见", r"非标意见",
    r"实际控制人.{0,8}(被|失联|留置|被采取强制措施)", r"控股股东.{0,8}(被|失联|平仓)",
    r"终止.{0,6}(重大合同|合作|重组|收购)",
)
_TITLE_RE = re.compile("|".join(TITLE_PATTERNS))

# ── 明确排除：这些即使命中上面的词也不标（避免"该止不止"）──
EXCLUDE_TYPES = ("调研活动", "股东大会", "董事会决议", "分配方案", "分红", "担保", "关联交易",
                 "法律意见书", "保荐", "核查意见", "高管人员任职变动", "管理办法", "制度")
EXCLUDE_TITLE = (
    r"减持(计划|结果|进展)", r"评级", r"中标", r"签订.{0,10}合同",
    r"调研", r"业绩说明会", r"投资者关系", r"股东大会", r"分红|派息|权益分派",
    # 2026-09-11 实测补：质押/解质押/展期是**常态**，不是黑天鹅（当日误报的主因）
    r"质押", r"转股价格.{0,6}修正", r"换股吸收合并", r"吸收合并",
)
_EXCLUDE_RE = re.compile("|".join(EXCLUDE_TITLE))


def classify_notice(notice_type: Any, title: Any) -> Tuple[bool, str]:
    """单条公告 → (是否属"意外事件/黑天鹅级利空", 命中理由)。

    先排除（利好/常规事项），再按类型白名单，最后按标题短语。**拿不准返回 False**。
    """
    t = str(notice_type or "")
    s = str(title or "")
    if not t and not s:
        return False, ""
    if any(x in t for x in EXCLUDE_TYPES):
        return False, ""
    if _EXCLUDE_RE.search(s):        # 常态化事项（含质押全家族）优先排除
        return False, ""
    for x in TYPE_RISK:
        if x in t:
            return True, "公告类型=%s" % t
    m = _TITLE_RE.search(s)
    if m:
        return True, "标题命中[%s]" % m.group(0)
    return False, ""


def _norm_code(sym: Any) -> str:
    return "".join(ch for ch in str(sym or "") if ch.isdigit())[:6]


def _xq(code6: str) -> str:
    """6 位代码 → 本仓 xq 写法（与 t_conditions.symbol / paper_positions 一致）。"""
    c = str(code6).zfill(6)
    if c[0] == "6":
        return "SH" + c
    if c[:2] in ("43", "83", "87", "92"):
        return "BJ" + c
    return "SZ" + c


def fetch_today_notices(date8: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """当日全市场公告清单 → [{code, name, title, ntype, date}]；失败返回 None（**不写文件**）。

    数据源：`ak.stock_notice_report(symbol='全部', date=YYYYMMDD)`（生产实测可用）。
    """
    import datetime as _dt
    d = date8 or _dt.date.today().strftime("%Y%m%d")
    try:
        import akshare as ak
        df = ak.stock_notice_report(symbol="全部", date=d)
        if df is None:
            return None
        out = []
        for _, r in df.iterrows():
            out.append({
                "code": _norm_code(r.get("代码")),
                "name": str(r.get("名称") or ""),
                "title": str(r.get("公告标题") or ""),
                "ntype": str(r.get("公告类型") or ""),
                "date": str(r.get("公告日期") or d).replace("-", "")[:8],
            })
        return out
    except Exception as e:
        print(f"[wolf_neg_event] 拉取公告失败({d}): {type(e).__name__}: {str(e)[:120]}")
        return None


def scan(notices: Iterable[Dict[str, Any]], symbols: Iterable[Any]) -> List[Dict[str, Any]]:
    """从公告清单里挑出**持仓标的**的意外事件级利空 → [{symbol, code, ntype, title, date, why}]。"""
    want = {}
    for s in symbols or []:
        c = _norm_code(s)
        if c:
            want[c] = str(s)
    hits = []
    for n in notices or []:
        c = n.get("code") or ""
        if c not in want:
            continue
        ok, why = classify_notice(n.get("ntype"), n.get("title"))
        if ok:
            hits.append({"symbol": want[c], "code": c, "name": n.get("name", ""),
                         "ntype": n.get("ntype", ""), "title": n.get("title", ""),
                         "date": n.get("date") or "", "why": why})
    return hits


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"), NEG_EVENT_FILE)


def load_events() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def merge_events(hits: Iterable[Dict[str, Any]], today8: str,
                 keep_days: Optional[int] = None,
                 cur: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """把命中项并入既有标记 → (新字典, 新增/更新的 symbol 列表)。

    · 同标的同日多条 → 合并进 note（保留各条类型/标题，便于审计），不覆盖成一条；
    · 保留期 KEEP_DAYS_DEFAULT 自然日，过期项自动清理（避免文件无限增长）；
    · **只增不删语义**：读侧 negative_event() 另按持有期与 WOLF_NEG_EVENT_DAYS 收窄，
      所以这里多留一点是安全的。
    """
    import datetime as _dt
    kd = int(keep_days if keep_days is not None else KEEP_DAYS_DEFAULT)
    d = dict(cur if cur is not None else load_events())
    # 先清理过期
    cutoff = (_dt.date(int(today8[:4]), int(today8[4:6]), int(today8[6:8]))
              - _dt.timedelta(days=kd)).strftime("%Y%m%d")
    for k in list(d.keys()):
        v = d.get(k)
        if not isinstance(v, dict):
            d.pop(k, None)
            continue
        if str(v.get("date") or "") < cutoff:
            d.pop(k, None)
    touched: List[str] = []
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for h in hits or []:
        by_sym.setdefault(h["symbol"], []).append(h)
    for sym, items in by_sym.items():
        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        lines = ["[%s]%s %s" % (i.get("ntype") or "-", i.get("name") or "", i.get("title") or "")
                 for i in items[:MAX_PER_SYMBOL]]
        rec = {
            "date": max((i.get("date") or today8) for i in items),
            "note": "；".join(lines)[:500],
            "source": "notice",
            "codes": sorted({i.get("code") for i in items if i.get("code")}),
        }
        old = d.get(sym) or {}
        if old.get("note") == rec["note"] and old.get("date") == rec["date"]:
            continue                      # 无变化 → 不算 touched（避免每轮重复刷）
        d[sym] = rec
        touched.append(sym)
    return d, touched


def save_events(d: Dict[str, Any]) -> bool:
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print(f"[wolf_neg_event] 写入失败: {str(e)[:120]}")
        return False


def scan_and_mark(symbols: Iterable[Any], date8: Optional[str] = None,
                  dry_run: bool = False, notices: Optional[List[Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    """主入口：取当日公告 → 过滤持仓 → 分类 → 并入标记文件。返回摘要 dict。

    `notices` 可注入（测试/复用）；为 None 时自行拉取。**拉取失败 → 不动原文件**（fail-safe）。
    """
    import datetime as _dt
    d8 = date8 or _dt.date.today().strftime("%Y%m%d")
    ns = notices if notices is not None else fetch_today_notices(d8)
    if ns is None:
        return {"ok": False, "reason": "fetch_failed", "hits": 0, "touched": [],
                "notices": 0, "symbols": len(list(symbols or []))}
    syms = list(symbols or [])
    hits = scan(ns, syms)
    new, touched = merge_events(hits, d8)
    written = False
    if touched and not dry_run:
        written = save_events(new)
    if not touched:
        _tail = " [无变动]"
    elif dry_run:
        _tail = " [dry-run 未写]"
    else:
        _tail = "" if written else " [写入失败!]"
    print(f"[wolf_neg_event] {d8} 公告 {len(ns)} 条 / 持仓 {len(syms)} 只 → 命中 {len(hits)} 条, "
          f"标记 {len(touched)} 只 {touched}{_tail}")
    for h in hits:
        print(f"    {h['symbol']} {h.get('ntype')} | {h.get('title')[:70]} | {h['why']}")
    return {"ok": True, "hits": len(hits), "touched": touched, "notices": len(ns),
            "symbols": len(syms), "written": written, "markers": new}
