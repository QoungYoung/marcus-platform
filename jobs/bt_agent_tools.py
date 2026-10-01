# -*- coding: utf-8 -*-
"""bt_agent_tools.py — 回测专用：把「AI 工具通道」钉到 as-of（PIT 修复，**默认只对 `--agent on` 生效**）。

## 背景（2026-09-17 实测，证据在 `data/_bt_prod/*.json` 与 dsh 会话事件流）
回测提示词里的快照是 **as-of（沙箱口径）**，但 AI 可以自行调用**线上工具**：
`get_stock_quote` / `get_t_realtime_indicators` / `get_intraday_minute` / `get_portfolio_positions` /
`get_stock_moneyflow`。这些工具由 **dsh 侧执行**（`MARCUS_API_URL=http://backend:8000/api/v1`，
即**生产后端**），拿到的是**真实现价** → 4/4 条决策都以"快照价与实时价脱节 21%~58%"为由 abandon。

## 为什么是"提示词注入"而不是"拦截工具请求"（A 方案不可行的证据）
`/chat` 的协议只有 `{message, session_id, mode, model, thinking_level}` 五个字段
（`/opt/dsh-plugins/dsh-dsh-marcus-bridge/lib/index.js` 的 `/chat` handler），**没有** `tools` /
`tool_choice` / `tool_results` / as-of 参数；工具在 dsh Agent 内部执行，工具请求**不经过我们可拦的
任何通道**（`bt_llm_replay` 只能拦 `/chat` 这一跳的请求体与响应体，工具那一跳在容器里）。
⇒ 唯一能在**只改 jobs/** 的前提下把工具口径钉住的办法：**在请求体（message）里**做两件事
  ① 明确声明"本会话处于回测口径，工具通道返回的是另一口径（生产实时）数据，禁止调用"，
     并把生产提示词里"可调用查询工具补数"的邀请句改写成 as-of 版；
  ② 把 AI 真正会去查的那几项数据，**按 as-of 从沙箱分钟库 / 本地日线库 / 本地 PG** 算好一并写进提示词
     （= 工具结果的 as-of 替身，AI 不需要再调工具）。

## 数据来源（全部复用回测驱动已有的取数，不另写一套）
· 行情/分钟线：`jobs/bt_prod_run.LocalMarket`（`data/_bt_full/mins` 分钟库，≤ 当前 bar）
· 日线：`data/_bt_full/bars.sqlite`（≤ 交易日**之前**）
· 实时技术指标：生产 `core.realtime_indicators.calculate_realtime_indicators`，
  入参 = LocalMarket 的腾讯口径 quote + 本地日线（**同一套生产算法**）
· 持仓：本地 PG 副本 `paper_positions` + 生产 `t_gateway.get_sellable_ledger`
· 大盘状态：本地 PG 副本 `market_diagnosis`（生产 `/market/market-state` 同表同语义）
· 资金流 / 完整技术面序列：**本地无 as-of 数据源** → 显式声明"回测未提供"，不给替代值

## 与 A 方案的关系
这是"退而可验证"的 B 方案：工具调用**不经过**本模块（我们看不到 dsh 内部那一跳），
但**决策所需的 as-of 数据**全部由本模块供给，且每次调用都被记录进 `prod_<day>.json` 的
`agent.tool_calls`（工具名/入参/来源/被 as-of 化的字段）。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# 生产提示词里"邀请调用工具"的原句（backend/app/services/t_bridge.py::wake_agent）
_INVITE_RES: List[Any] = [
    (re.compile(r"如需更多数据可调用查询工具（[^）]*），不必只依赖本快照。"),
     "【回测模式】本会话的工具通道已被系统禁用（原因见文末 as-of 数据块）——不要再调用任何查询工具。"),
    (re.compile(r"若快照缺现价/量能，请先调用查询工具补数再判。"),
     "若快照缺现价/量能，请直接用文末的 as-of 数据块补数再判（那里已是同一时点的取数）。"),
]
_TRIGGER_RE = re.compile(r"【做T触发】\s*([A-Za-z]{2}\d{6})")

# 本模块能给 as-of 版本的工具 / 不能给的（必须诚实写进提示词）
ASOF_TOOLS = ("get_stock_quote", "get_portfolio_positions", "get_intraday_minute",
              "get_t_realtime_indicators", "get_market_state")


def _asof_trim_on() -> bool:
    """as-of 块精简开关（库内默认 0 = 关 ✓ ⇒ 逐字不变 ✓）"""
    return str(os.getenv("WOLF_ASOF_TRIM", "0")).strip().lower() in ("1", "true", "yes", "on")


def _bar_limit() -> int:
    """K 线保留根数：默认 12（原状 ✓）；精简时默认 6 ✓"""
    try:
        if _asof_trim_on():
            return max(2, int(os.getenv("WOLF_ASOF_BAR_LIMIT", "6") or 6))
        return 12
    except Exception:
        return 12
UNAVAILABLE_TOOLS = ("get_stock_moneyflow", "get_stock_technical", "get_t_candidates_summary")


def moneyflow_on() -> bool:
    """`WOLF_AGENT_MONEYFLOW`（库内默认 0）：把**个股逐日资金流**发给 AI。

    2026-09-22 用户："停掉 T6 立刻改完再重起"。此前本工具对 AI 恒返回 n/a
    （「本地无 as-of 数据源」——诚实但不是无害：AI 审买腿时看不到资金流，只能用放量/缩量/分时替代，
    判断天然偏形态）。数据其实可拉（tushare `moneyflow` 按票），已预拉到 `data/_bt_fund/_ref/moneyflow/`。
    """
    return str(os.getenv("WOLF_AGENT_MONEYFLOW", "0")).strip().lower() in ("1", "true", "yes", "on")

GUARD_VERSION = "bt-asof-tools-v1"


def _f(x: Any, nd: int = 3) -> str:
    try:
        if x is None:
            return "-"
        return ("%%.%df" % nd) % float(x)
    except Exception:
        return str(x)


def _vr_line(symbol: str, q: dict) -> str:
    """as-of 数据块里的**量比**行（2026-09-24 补）。

    为什么必须有：prompt 的【决策 checklist】第 ④ 条要求判「恐慌放量追跌（**量比骤升**+创新低）」，
    但数据块原先只有 换手率/成交量/成交额 —— **要求判量比却不给量比** ⇒ AI 只能从散文里猜
    ⇒ 那类判断抛硬币（账本 §9.13：13 个抛硬币节点里 9 个是它；补上量比后稳定度 0.56→0.80）。
    口径与条件腿一致（`t_monitor.calc_volume_ratio_at`），基准用**该股自己的**近5日同刻均值。
    开关 `WOLF_VOL_RATIO_FILL` 关 ⇒ 本行不出现（逐位旧行为）。
    """
    try:
        from app.services import t_monitor as _tm
        if not getattr(_tm, "VOL_RATIO_FILL", False):
            return ""
        import datetime as _dt
        return "量比: %s（= 当前累计换手×时段伸缩 ÷ 该股近5日同刻均值；**≥1.5 放量 / ≤0.9 缩量**）" % (
            _f(_tm.wolf_leg_vol_ratio(q, symbol=symbol) or 0, 2))
    except Exception:
        return ""


class AsOfToolService:
    """as-of 工具数据服务：每个方法返回一条「工具结果替身」（含来源与被 as-of 化的字段）。"""

    def __init__(self, market, hhmm_ref: Dict[str, str], day: str, account: str = "stock",
                 db_url: Optional[str] = None, verbose: bool = True):
        self.market = market
        self.hhmm_ref = hhmm_ref
        self.day = str(day)
        self.account = str(account)
        self.db_url = db_url or os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL") or ""
        self.verbose = bool(verbose)
        self.names: Dict[str, str] = {}
        self.errors: List[str] = []

    # ── 基础 ──
    @property
    def hhmm(self) -> str:
        return str(self.hhmm_ref.get("hhmm") or "09:15")

    def _q(self, sql: str, args=()) -> List[Dict[str, Any]]:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(self.db_url)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def name_of(self, symbol: str) -> str:
        if symbol in self.names:
            return self.names[symbol]
        nm = ""
        try:
            code = symbol[2:] if symbol[:2] in ("SH", "SZ", "BJ") else symbol
            rows = self._q("SELECT name FROM stock_pool WHERE symbol=%s OR ts_code LIKE %s LIMIT 1",
                           (code, code + ".%"))
            nm = str((rows[0] or {}).get("name") or "") if rows else ""
        except Exception as e:
            self.errors.append("name_of(%s): %s" % (symbol, str(e)[:80]))
        self.names[symbol] = nm or symbol
        return self.names[symbol]

    def _entry(self, tool: str, args: Dict[str, Any], source: str, fields: List[str],
               text: str, ok: bool = True) -> Dict[str, Any]:
        return {"tool": tool, "args": args, "source": source, "asof_fields": list(fields),
                "text": text, "ok": bool(ok), "hhmm": self.hhmm}

    # ── ① get_stock_quote ──
    def get_stock_quote(self, symbol: str) -> Dict[str, Any]:
        src = "本地分钟库 data/_bt_full/mins（%s_*_5min_%s.json，≤ %s）" % (
            symbol[2:] if symbol[:2] in ("SH", "SZ", "BJ") else symbol, self.day, self.hhmm)
        q = None
        try:
            q = self.market.quote(symbol, self.hhmm)
        except Exception as e:
            self.errors.append("quote(%s): %s" % (symbol, str(e)[:80]))
        if not q:
            return self._entry("get_stock_quote", {"symbol": symbol}, src,
                               ["current", "pre_close", "open", "high", "low", "vol", "amount",
                                "turnover_rate", "amplitude", "change_pct"],
                               "📭 %s 无 as-of 行情（本地分钟库无当日 ≤%s 的 bar）" % (symbol, self.hhmm),
                               ok=False)
        name = self.name_of(symbol)
        chg = round(float(q["current"]) - float(q["pre_close"]), 3)
        text = "\n".join([
            "📈 %s(%s) as-of 行情（%s %s）" % (name, symbol, self.day, self.hhmm),
            "现价: %s | 涨跌: %s（%s%%）" % (_f(q["current"]), _f(chg), _f(q["change_pct"], 2)),
            "昨收: %s | 今开: %s" % (_f(q["pre_close"]), _f(q["open"])),
            "最高: %s | 最低: %s" % (_f(q["high"]), _f(q["low"])),
            "成交量: %s | 成交额: %s" % (_f(q["vol"], 2), _f(q["amount"], 2)),
            "换手率: %s | 振幅: %s | 均价: %s" % (_f(q["turnover_rate"], 4), _f(q["amplitude"], 2),
                                                   _f(q["average"])),
            "日内分位: %s" % _f((float(q["current"]) - float(q["low"])) /
                                (float(q["high"]) - float(q["low"])) * 100, 1)
            if float(q["high"]) > float(q["low"]) else "日内分位: -",
            _vr_line(symbol, q),
        ])
        return self._entry("get_stock_quote", {"symbol": symbol}, src,
                           ["current", "change", "change_pct", "pre_close", "open", "high", "low",
                            "vol", "amount", "turnover_rate", "amplitude", "intraday_percentile"], text)

    # ── ② get_portfolio_positions ──
    def get_portfolio_positions(self) -> Dict[str, Any]:
        src = "本地 PG %s：paper_positions + t_gateway.get_sellable_ledger（account=%s）" % (
            (self.db_url.rsplit("/", 1)[-1] or "?"), self.account)
        fields = ["symbol", "name", "volume", "sellable", "avg_price", "current_price",
                  "change_pct", "floating_pnl_pct"]
        try:
            ledger = {}
            try:
                from app.services.t_gateway import get_sellable_ledger
                ledger = get_sellable_ledger(self.account) or {}
            except Exception as e:
                self.errors.append("sellable_ledger: %s" % str(e)[:80])
            rows = self._q("SELECT symbol, volume, avg_price FROM paper_positions "
                           "WHERE account_id=%s AND volume>0 ORDER BY symbol", (self.account,))
        except Exception as e:
            self.errors.append("positions: %s" % str(e)[:120])
            return self._entry("get_portfolio_positions", {}, src, fields,
                               "📭 持仓读取失败：%s" % str(e)[:120], ok=False)
        if not rows:
            return self._entry("get_portfolio_positions", {}, src, fields, "📭 当前无持仓", ok=True)
        _tgt = str(getattr(self, "_sym", "") or "")
        _trim = _asof_trim_on() and bool(_tgt)
        lines = ["💼 当前持仓（%d 只，as-of %s %s）%s：" % (
            len(rows), self.day, self.hhmm,
            ("——**只详列本次标的 %s**，其余仅列「是否持有/可卖」 ✓" % _tgt) if _trim else ""), ""]
        for r in rows:
            sym = str(r["symbol"])
            if _trim and sym != _tgt:
                _sl = (ledger.get(sym) or {}).get("sellable", "-")
                lines.append("• %s %s 持仓%s股 可卖%s（⭐非本次标的，仅列仓位 ✓）"
                             % (sym, self.name_of(sym), int(r["volume"] or 0), _sl))
                continue
            q = None
            try:
                q = self.market.quote(sym, self.hhmm)
            except Exception:
                q = None
            cur = float(q["current"]) if q else 0.0
            avg = float(r["avg_price"] or 0)
            pnl = (cur / avg - 1) * 100 if avg > 0 and cur > 0 else 0.0
            sellable = (ledger.get(sym) or {}).get("sellable", "-")
            lines.append("• %s %s 持仓%s股 可卖%s 成本%s 现价%s 浮盈%s%%（可卖额来自 T+1 账本）"
                         % (sym, self.name_of(sym), int(r["volume"] or 0), sellable,
                            _f(avg), _f(cur) if cur else "-", _f(pnl, 2) if cur else "-"))
        return self._entry("get_portfolio_positions", {"account": self.account}, src, fields,
                           "\n".join(lines))

    # ── ③ get_intraday_minute ──
    def get_intraday_minute(self, symbol: str, freq: str = "5") -> Dict[str, Any]:
        src = "本地分钟库 data/_bt_full/mins（freq=%s，≤ %s）" % (freq, self.hhmm)
        fields = ["time", "open", "high", "low", "close", "vol"]
        try:
            bars = self.market.bars_upto(symbol, self.hhmm)
        except Exception as e:
            self.errors.append("mins(%s): %s" % (symbol, str(e)[:80]))
            bars = []
        if not bars:
            return self._entry("get_intraday_minute", {"symbol": symbol, "freq": freq}, src, fields,
                               "📭 无分钟K线数据（本地库无当日 ≤%s 的 bar）" % self.hhmm, ok=False)
        _rev = list(reversed(bars))
        _lim = _bar_limit()
        if _asof_trim_on() and len(_rev) > _lim:
            _sel = _rev[:_lim] + [_rev[-1]]          # 最近 N 根 ＋ 当日首根 ✓
            _head = ("⏱️ %s %s分钟K线（当日 ≤%s 共 %d 根，**精简**：最近 %d 根 ＋ 当日首根，倒序）："
                     % (symbol, freq, self.hhmm, len(bars), _lim))
        else:
            _sel = _rev[:_lim]
            _head = "⏱️ %s %s分钟K线（当日 ≤%s 共 %d 根，倒序）：" % (symbol, freq, self.hhmm, len(bars))
        lines = [_head, ""]
        for b in _sel:
            lines.append("• %s O%s H%s L%s C%s V%s"
                         % (str(b.get("time"))[11:16], _f(b.get("open")), _f(b.get("high")),
                            _f(b.get("low")), _f(b.get("close")), _f(b.get("vol"), 0)))
        return self._entry("get_intraday_minute", {"symbol": symbol, "freq": freq}, src, fields,
                           "\n".join(lines))

    # ── ④ get_t_realtime_indicators（复用生产算法 + 本地 as-of 输入）──
    def get_t_realtime_indicators(self, symbol: str) -> Dict[str, Any]:
        src = ("本地日线 bars.sqlite（< %s）+ 本地分钟库 quote；算法=生产 core.realtime_indicators"
               % self.day)
        fields = ["current_price", "macd_dif", "macd_dea", "macd_bar", "kdj_k", "kdj_d", "kdj_j",
                  "rsi_6", "rsi_12", "rsi_24", "ma5", "ma10", "ma20"]
        try:
            try:            # 生产布局（backend/app 优先）走点号路径；
                from core.realtime_indicators import DailyBar, calculate_realtime_indicators
            except Exception:   # 本地仓库布局：`backend/app/core` 会遮蔽 repo 的 `core/` → 退回顶层名
                from realtime_indicators import DailyBar, calculate_realtime_indicators
            q = self.market.quote(symbol, self.hhmm) or {}
            daily = self.market.daily(symbol) or []
            bars = [DailyBar(trade_date=str(r["date"]).replace("-", ""), open=float(r["open"]),
                             high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                             vol=float(r["vol"] or 0)) for r in daily if r.get("close")]
            quote = {"current": q.get("current"), "high": q.get("high"), "low": q.get("low"),
                     "open": q.get("open"), "last_close": q.get("pre_close"),
                     "volume": q.get("vol"), "amount": q.get("amount")}
            res = calculate_realtime_indicators(symbol=symbol, realtime_quote=quote,
                                                historical_bars=bars[-60:], prev_indicators=None)
            if not res.current_price:
                raise RuntimeError(str(res.warning or "无现价"))
            text = "\n".join([
                "📊 %s as-of 实时技术指标（%s %s，日线 as-of 截至 %s，算法=生产）"
                % (symbol, self.day, self.hhmm, (bars[-1].trade_date if bars else "-")),
                "现价: %s" % _f(res.current_price),
                "MACD: DIF %s DEA %s BAR %s" % (_f(res.macd_dif, 4), _f(res.macd_dea, 4),
                                                _f(res.macd_bar, 4)),
                "KDJ: K %s D %s J %s" % (_f(res.kdj_k, 2), _f(res.kdj_d, 2), _f(res.kdj_j, 2)),
                "RSI6: %s | RSI12: %s | RSI24: %s" % (_f(res.rsi_6, 2), _f(res.rsi_12, 2),
                                                      _f(res.rsi_24, 2)),
                "MA5: %s | MA10: %s | MA20: %s（未用盘后确认锚点，为盘中估算口径）"
                % (_f(res.ma5, 2), _f(res.ma10, 2), _f(res.ma20, 2)),
            ])
            return self._entry("get_t_realtime_indicators", {"symbol": symbol}, src, fields, text)
        except Exception as e:
            self.errors.append("indicators(%s): %s" % (symbol, str(e)[:120]))
            return self._entry("get_t_realtime_indicators", {"symbol": symbol}, src, fields,
                               "📭 回测无法计算 as-of 技术指标：%s" % str(e)[:120], ok=False)

    # ── ⑤ get_market_state（本地 PG 副本，生产同表同语义）──
    def get_market_state(self) -> Dict[str, Any]:
        src = "本地 PG %s：market_diagnosis（trade_date=%s）" % (
            (self.db_url.rsplit("/", 1)[-1] or "?"), self.day)
        fields = ["trade_date", "state", "label", "suggestion", "score", "indicators"]
        try:
            rows = self._q("SELECT trade_date, state, label, suggestion, score_trend, score_oscillation,"
                           " score_extreme, indicators_json FROM market_diagnosis WHERE trade_date=%s",
                           (self.day,))
        except Exception as e:
            self.errors.append("market_state: %s" % str(e)[:80])
            rows = []
        if not rows:
            return self._entry("get_market_state", {}, src, fields,
                               "🌐 市场状态（%s）\n状态: ⚪ 未知\n建议: 回测库中该交易日无盘前诊断记录"
                               "（生产同样返回「尚未执行盘前诊断」）" % self.day)
        r = rows[0]
        ind = {}
        try:
            ind = json.loads(r.get("indicators_json") or "{}") or {}
        except Exception:
            ind = {}
        text = "\n".join([
            "🌐 市场状态（%s）" % r.get("trade_date"),
            "状态: %s" % (r.get("label") or r.get("state") or "未知"),
            "建议: %s" % (r.get("suggestion") or "-"),
            "评分: 趋势 %s / 震荡 %s / 极端 %s" % (r.get("score_trend"), r.get("score_oscillation"),
                                                  r.get("score_extreme")),
            "指标: %s" % (json.dumps(ind, ensure_ascii=False)[:300] if ind else "-"),
        ])
        return self._entry("get_market_state", {}, src, fields, text)

    # ── ①b get_stock_moneyflow（个股逐日资金流，as-of ≤ 当日）──
    def get_stock_moneyflow(self, symbol: str) -> Dict[str, Any]:
        """近 20 日净流入（亿元）+ 5/10/20 日累计 + 连续同号天数。取不到 → ok=False（fail-open）。"""
        src = "本地缓存 data/_bt_fund/_ref/moneyflow/<code>.json（tushare moneyflow，≤ %s）" % self.day
        fields = ["net_mf_amount(d1..d5)", "sum5", "sum10", "sum20", "streak"]
        s_ = None
        try:
            import bt_fund_asof as _F
        except ImportError:
            # 与 `leader_v2._fund_mod()` 同款兜底：本文件就在 jobs/ 下，显式补 sys.path，
            # 免得调用方的 sys.path 不同导致**静默拿不到数据**（2026-09-22 实测踩到：工具返回 ok=False）
            import sys as _sys
            from pathlib import Path as _P
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            try:
                import bt_fund_asof as _F
            except Exception as e:
                _F = None
                self.errors.append("moneyflow import(%s): %s" % (symbol, str(e)[:80]))
        if _F is not None:
            try:
                s_ = _F.net_series(symbol, upto=self.day)
            except Exception as e:
                self.errors.append("moneyflow(%s): %s" % (symbol, str(e)[:80]))
        if s_ is None or len(s_) == 0:
            return self._entry("get_stock_moneyflow", {"symbol": symbol}, src, fields,
                               "📭 %s 无 as-of 资金流（缓存缺失）" % symbol, ok=False)
        v = [float(x) for x in s_.values]

        def yi(x):
            # ⚠️ 单位：tushare `moneyflow.net_mf_amount` 是**万元**（不是元）⇒ 换亿元要 /1e4。
            # 2026-09-23 修：此前写的是 /1e8（当成元），**整整差 10,000 倍** —— 实测 600183 的
            # 5 日累计 −67,579 万元（= −6.76 亿）被显示成 **−0.0007 亿**；喂给 AI 的取值里
            # **88% 恰好显示为 0.0**、其余 ≤0.003 亿。也就是说 `WOLF_AGENT_MONEYFLOW=1`
            # 名义上"把资金流发给 AI"，实际上 AI 看到的一直是一排 0.0（→ 还是只能靠形态判断，
            # 正是这次改动想解决的问题）。数量级自检：修后显示值应落在 0.01~10 亿区间。
            return round(x / 1e4, 3)
        last5 = [yi(x) for x in v[-5:]]
        streak = 0
        for x in reversed(v):
            if x == 0:
                break
            if streak == 0 or (x > 0) == (v[-1] > 0):
                streak += 1
            else:
                break
        txt = "\n".join([
            "💰 %s 个股资金流（as-of %s，仅 ≤ 当日）" % (self.name_of(symbol), str(s_.index[-1])[:10]),
            "近 5 日净流入(亿): %s" % last5,
            "累计: 5日 %s / 10日 %s / 20日 %s" % (yi(sum(v[-5:])), yi(sum(v[-10:])), yi(sum(v[-20:]))),
            "最近方向: %s，连续 %d 日" % ("流入" if v[-1] > 0 else "流出", streak),
        ])
        return self._entry("get_stock_moneyflow", {"symbol": symbol}, src, fields, txt)

    # ── 汇总：一次决策要注入的全部 as-of 数据 ──
    def snapshot(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        self._sym = str(symbol or "")          # 账本 §9.382：供"持仓只详列本标的"用 ✓
        out: List[Dict[str, Any]] = []
        if symbol:
            out.append(self.get_stock_quote(symbol))
        out.append(self.get_portfolio_positions())
        if symbol:
            out.append(self.get_intraday_minute(symbol))
            out.append(self.get_t_realtime_indicators(symbol))
        out.append(self.get_market_state())
        _todo = UNAVAILABLE_TOOLS
        if moneyflow_on():
            out.append(self.get_stock_moneyflow(symbol) if symbol else
                       self._entry("get_stock_moneyflow", {}, "n/a（无标的）", [], "无标的可查", ok=False))
            _todo = tuple(t for t in UNAVAILABLE_TOOLS if t != "get_stock_moneyflow")
        for t in _todo:
            out.append(self._entry(t, {}, "n/a（本地无 as-of 数据源）", [],
                                   "⛔ 回测未提供 %s 的 as-of 版本" % t, ok=False))
        return out


class BacktestToolGuard:
    """请求体（`{message: ...}`）改写器：钉住工具口径 + 附 as-of 数据块。**只对 /chat 生效**。"""

    def __init__(self, service: AsOfToolService, day: str, enabled: bool = True, verbose: bool = True,
                 session_tag: Optional[str] = None):
        self.svc = service
        self.day = str(day)
        self.enabled = bool(enabled)
        self.verbose = bool(verbose)
        # 会话隔离：生产 dsh 的 `trade:t-agent-<symbol>` 会话**跨运行持久化**（历史里躺着上次跑
        # 出来的实时工具结果与旧决策）→ 直接复用会把上次的实时价"记忆"带进本次决策（实测：AI 会
        # 数「同一 as-of 时点已第 N 次触发」而改判 wait/update_condition）。回测里给 session_id 加
        # 后缀（默认 `btasof-<day>-r<pid>`，每次运行一个）：**一次运行内上下文照旧累积**（与生产
        # 同语义），跨运行从零开始。后缀不进提示词指纹 → 不影响录制/回放。
        # 前缀仍是 `t-agent-`，dsh 侧的工具白名单判据（sessionId.includes('t-agent-')）不受影响。
        self.session_tag = str(session_tag) if session_tag else None
        self._trig: Optional[Dict[str, Any]] = None
        self.calls: List[Dict[str, Any]] = []          # 每次被改写的 LLM 请求
        self.tool_calls: List[Dict[str, Any]] = []     # 每次"工具结果替身"（工具名/入参/来源/字段）
        self.n_skip = 0
        self.errors: List[str] = []

    # ── 当前触发上下文（由 bt_agent_loop 设置；取不到就从 message 里解析）──
    def set_trigger(self, trig: Optional[Dict[str, Any]]) -> None:
        self._trig = dict(trig) if trig else None

    def clear(self) -> None:
        self._trig = None

    def _symbol_of(self, message: str) -> str:
        if self._trig and self._trig.get("symbol"):
            return str(self._trig["symbol"])
        m = _TRIGGER_RE.search(message or "")
        return m.group(1) if m else ""

    def render_block(self, symbol: str) -> (str, List[Dict[str, Any]]):
        entries = self.svc.snapshot(symbol)
        ok = [e for e in entries if e.get("ok")]
        bad = [e for e in entries if not e.get("ok")]
        lines = [
            "",
            "【⚠️ 回测模式·数据口径声明（最高优先级，覆盖上文一切「可调用工具补数」提示）】",
            "本会话由**回测管线**驱动：交易日=%s，当前时刻=%s（沙箱 as-of）。" % (self.day, self.svc.hhmm),
            "工具通道（get_stock_quote / get_t_realtime_indicators / get_intraday_minute /",
            "get_portfolio_positions / get_stock_moneyflow / get_market_state …）连的是"
            "**生产实时数据源**，",
            "其行情日期与本次决策口径**不一致**；拿它与上面快照比价会得出**错误的「脱节」结论**。",
            "⇒ **本次决策禁止调用任何工具**；任何基于工具返回值（实时价/实时资金流）的"
            "wait/abandon 理由在",
            "   回测口径下**无效**。下面是你可能需要的全部数据，已按 as-of 从沙箱与本地库算好：",
            "",
        ]
        for e in ok:
            lines.append("▸ %s(%s) —— 来源：%s" % (e["tool"], json.dumps(e["args"], ensure_ascii=False),
                                                   e["source"]))
            lines.append(e["text"])
            lines.append("")
        if bad:
            lines.append("▸ 以下工具在回测中**没有 as-of 版本**（不要用它们取实时值做决策依据）：")
            for e in bad:
                lines.append("  · %s：%s" % (e["tool"], e.get("text") or "未提供"))
            lines.append("")
        lines += [
            "【决策口径】只用：① 上面的 as-of 数据；② 触发快照；③ 历史决策/统计（提示词已给）。",
            "两者若不一致，**以 as-of 数据为准**；数据不足时按生产规则二选一（wait 注明缺什么 / abandon 写明证据），",
            "但**不得**以「与实时行情脱节」为由 abandon。输出格式仍为原 JSON（action/reason[/condition]）。",
        ]
        return "\n".join(lines), entries

    # ── LLMReplay 钩子：改写 /chat 的请求体 ──
    def decorate(self, url: str, body: Any) -> Any:
        if not self.enabled or not isinstance(body, dict) or not isinstance(body.get("message"), str):
            self.n_skip += 1
            return body
        if "/chat" not in str(url):
            return body
        msg = body["message"]
        symbol = self._symbol_of(msg)
        try:
            block, entries = self.render_block(symbol)
        except Exception as e:                      # 任何异常都不能让回测停下（只是退化成原提示词）
            self.errors.append("render_block: %s" % str(e)[:160])
            if self.verbose:
                print("[tool-guard] ⚠️ as-of 数据块渲染失败：%s（本次请求按原提示词发出）" % str(e)[:160])
            return body
        n_rewrite = 0
        for rx, repl in _INVITE_RES:
            msg, n = rx.subn(repl, msg)
            n_rewrite += n
        new_msg = msg.rstrip() + "\n" + block
        sid_in = str(body.get("session_id") or "")
        sid_out = ("%s@%s" % (sid_in, self.session_tag)
                   if (self.session_tag and sid_in.startswith("t-agent-")) else sid_in)
        rec = {"seq": len(self.calls) + 1, "hhmm": self.svc.hhmm, "symbol": symbol,
               "event_type": (self._trig or {}).get("event_type") or "-",
               "trigger_id": (self._trig or {}).get("id"),
               "session_id": sid_out, "rewritten_invites": n_rewrite,
               "asof_tools": [e["tool"] for e in entries if e.get("ok")],
               "unavailable_tools": [e["tool"] for e in entries if not e.get("ok")],
               "msg_chars": len(new_msg)}
        self.calls.append(rec)
        for e in entries:
            self.tool_calls.append({"seq": rec["seq"], "hhmm": e["hhmm"], "symbol": symbol,
                                    "tool": e["tool"], "args": e["args"],
                                    "source": e["source"], "asof_fields": e["asof_fields"],
                                    "ok": e["ok"]})
        if self.verbose:
            print("[tool-guard] #%d %s %s → as-of 工具数据 %d 项（%s）+ 声明 %d 个工具不可用；"
                  "改写工具邀请句 %d 处；prompt %d 字符"
                  % (rec["seq"], rec["hhmm"], symbol or "-", len(rec["asof_tools"]),
                     ",".join(rec["asof_tools"]), len(rec["unavailable_tools"]),
                     n_rewrite, len(new_msg)))
            for e in entries:
                print("[tool-guard]     ▸ %s(%s) ← %s%s"
                      % (e["tool"], json.dumps(e["args"], ensure_ascii=False), e["source"],
                         "" if e["ok"] else "  [无 as-of 版本]"))
                print("[tool-guard]       as-of 字段：%s" % (",".join(e["asof_fields"]) or "-"))
        new_body = dict(body)
        new_body["message"] = new_msg
        if sid_out != sid_in:
            new_body["session_id"] = sid_out
        return new_body

    def summary(self) -> Dict[str, Any]:
        by_tool: Dict[str, int] = {}
        for t in self.tool_calls:
            by_tool[t["tool"]] = by_tool.get(t["tool"], 0) + 1
        return {"guard": GUARD_VERSION, "enabled": self.enabled,
                "session_tag": self.session_tag,
                "n_llm_requests_decorated": len(self.calls),
                "n_tool_datasets_served": len(self.tool_calls),
                "asof_tools": list(ASOF_TOOLS), "unavailable_tools": list(UNAVAILABLE_TOOLS),
                "by_tool": by_tool,
                "sources_used": sorted({t["source"] for t in self.tool_calls}),
                "errors": self.errors[:10], "svc_errors": self.svc.errors[:10]}
