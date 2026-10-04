# -*- coding: utf-8 -*-
"""wolf_weekend_hedge.py — G9 周末/长假前避险（2026-09-12，语料逐条精读所得）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（NGA **2026-08-21**，含精确时刻，逐字）:

  14:20 「**2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半 防止周末出利空
         这样周一再拿回来。 出于仓位安全考虑 65%仓位过周末。**」
  14:28 「反正今天都煎熬了80%交易时间了 我怕大家刚才割肉到今天最低点。所以给了个时间 再忍忍吧
         **2点半 至少减不会减到最低**。」        ← 时点选择理由（不是任意时刻减）
  14:35 「**2点半过了 我按刚才说的操作了。**」        ← 条件成立 → 当场执行
  14:43 「…这次缩量太快 如果是非周末我就不怕 ，但是**周末毕竟2天不可预判**，所以还是先在不怎么亏的位置
         出来一部分 等周一确认安全再说 **万一低开 那就等于做了个反T 万一高开 那没吃到就没吃到了 不纠结**」

旁证（同一族"收盘固定仓位"）:
  · 2025-05-20「**打死收盘就固定这么多仓位（20%~30%），盘中可以加到50%-60%，收盘前T出**就行」
  · 2026-09-03 14:14「**仓位不会低于65%收盘，日内做T仓位20%**」        ← 底仓下限 vs 周末 65% 目标
  · 2026-09-03 13:51「黄金股要舍得卖 **明天我开始轮动减液冷**」          ← 节前/周末前的减仓是"轮动减"

**与既有 `wolf_discipline.weekend_de_risk` 的区别（这是 G9 存在的理由）**
  既有实现只判「周五 + 指定时间窗 + 仓位≥阈值」→ **不看量能、不看是否拉升**；
  他的原话是**条件式**的：只有「**还是缩量 ∧ 还是不拉升**」才减。本模块补上这两个前提，
  并补上他明确给出的目标（**65% 过周末**）与后续动作（**周一确认安全再拿回**）。
═══════════════════════════════════════════════════════════════════════════

**口径**
  · 「缩量」= 今日截至 14:30 的指数累计成交量（腾讯 m5 `vol` 之和）**低于昨日同期** × `WOLF_WH_SHRINK`（默认 1.0，即"不及昨日同期"）
    —— 他原文用的是成交额，我们用指数分钟成交量做代理（分钟成交额字段不一定可得），此代理已在文末声明
  · 「未拉升」= 指数当日涨幅 < `WOLF_WH_RALLY`（默认 +0.30%）
  · 「节前」= 下一个交易日的自然日间隔 ≥ 4 天；「周末前」= 间隔 == 3 天（周五→周一）
  · 检查时点 `WOLF_WH_TIME`（默认 `14:30`，他原话"2点半"）

**落点**：提示层（进 `wolf_discipline.discipline_context`）。开关 `WOLF_WEEKEND_HEDGE`，**默认关**。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


STATE_FILE = "wolf_weekend_hedge.json"
IDX = "sh000001"          # 腾讯分钟接口用带前缀代码
CUTOFF_DEFAULT = "14:30"  # 他原话"2点半"


# ── 执行层开关（2026-09-18 用户"补一个执行层开关"）────────────────────────────
# 现状：本模块只到**提示层**（`directive()` 注入纪律上下文）；语料那条
#   「2点半若仍缩量不拉升 → 把这两天T进去的仓位出来一半、65% 过周末、周一拿回」（2026-08-21 14:20）
#   **没有任何执行动作**。执行层由 `WOLF_WEEKEND_HEDGE_EXEC` 控制（**默认关**）；
#   回测侧默认开（`jobs/bt_prod_run.py` setdefault），生产零影响。
def exec_enabled() -> bool:
    return os.getenv("WOLF_WEEKEND_HEDGE_EXEC", "0").strip().lower() not in ("0", "false", "no", "")


def exec_plan(res: Dict[str, Any], bought_today: Optional[Dict[str, int]] = None,
              lot: int = 100, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """避险执行计划（纯函数，便于单测）。

    语料口径：**减"这两天 T 进去的仓位"的一半**（不是减底仓）＋**收盘仓位目标 ≤65%**＋
    **当日不再新开仓/加仓**（周一确认安全后再拿回）。
    返回 {active, sells:[(symbol, vol)], block_new_buys, target_pct, reason}。
    开关关 / 条件未成立 → active=False、不下任何指令（与旧行为一致）。
    """
    on = exec_enabled() if enabled is None else bool(enabled)
    out = {"active": False, "sells": [], "block_new_buys": False,
           "target_pct": float((res or {}).get("target_pct") or 0.65), "reason": ""}
    if not on:
        out["reason"] = "执行层开关关（仅提示）"
        return out
    if not (res or {}).get("active"):
        out["reason"] = (res or {}).get("reason") or "条件未成立"
        return out
    step = max(int(lot or 100), 1)
    for sym, vol in (bought_today or {}).items():
        try:
            half = int(int(vol) // 2 // step) * step
        except Exception:
            half = 0
        if half > 0:
            out["sells"].append((sym, half))
    out["active"] = True
    out["block_new_buys"] = True
    out["reason"] = ("周末/长假前避险条件成立 → 卖出近两日 T 进仓位的一半 %s，"
                     "收盘目标 ≤%.0f%%，当日不再新开仓/加仓" % (out["sells"], out["target_pct"] * 100))
    return out


def enabled() -> bool:
    """`WOLF_WEEKEND_HEDGE` 默认 **0（关）**。"""
    return os.getenv("WOLF_WEEKEND_HEDGE", "0").strip().lower() not in ("0", "false", "no", "")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_WH_FILE", STATE_FILE))


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def cutoff() -> str:
    return (os.getenv("WOLF_WH_TIME", CUTOFF_DEFAULT) or CUTOFF_DEFAULT).strip()


def cutoff_for(kind: str = "") -> str:
    """避险**检查时点**按"周末前 / 长假前"分开（狼大两句原话的时点不同）：

      · **长假前 = 早盘卖**：2025-04-29「以后是节假日出今日 甭管当时指数什么行情，尽量做到
        **早盘卖 尾盘买的反T**。从概率上来说都是对的」→ `WOLF_WH_HOLIDAY_HM`（默认 10:00）；
      · **周末前 = 14:30**：2026-08-21 14:20「**2点半** 如果还是缩量 还是不拉升…」→ `WOLF_WH_TIME`（默认 14:30）。
    """
    if str(kind) == "holiday":
        # ⚠️ 必须是 HH:MM 格式：模块内的 _hhmm() 用 ":" 分割，给 "1000" 会被解析成 0 分钟（实测踩到）
        return (os.getenv("WOLF_WH_HOLIDAY_HM", "10:00") or "10:00").strip()
    return cutoff()


# ───────────────────────── 判据层（纯函数，可测） ─────────────────────────

def is_pre_break_day(date8: str, trade_days: Sequence[str]) -> Dict[str, Any]:
    """今天是否为「周末/长假前最后一个交易日」。

    gap = 下一个交易日与今天的自然日间隔：3 → 周末前；≥4 → 长假前；否则不是。
    若日历里找不到今天之后的日子 → `unknown`（不猜）。
    """
    ds = sorted({str(d)[:8] for d in (trade_days or []) if str(d)[:8].isdigit()})
    today = str(date8)[:8]
    if today not in ds:
        return {"is_pre_break": False, "kind": "", "gap_days": None, "reason": "today_not_in_calendar"}
    i = ds.index(today)
    if i + 1 >= len(ds):
        return {"is_pre_break": False, "kind": "", "gap_days": None, "reason": "no_next_trade_day"}
    import datetime as _dt
    try:
        t = _dt.datetime.strptime(today, "%Y%m%d").date()
        n = _dt.datetime.strptime(ds[i + 1], "%Y%m%d").date()
    except ValueError:
        return {"is_pre_break": False, "kind": "", "gap_days": None, "reason": "bad_date"}
    gap = (n - t).days
    if gap >= 4:
        return {"is_pre_break": True, "kind": "holiday", "gap_days": gap, "next_day": ds[i + 1]}
    if gap == 3:
        return {"is_pre_break": True, "kind": "weekend", "gap_days": gap, "next_day": ds[i + 1]}
    return {"is_pre_break": False, "kind": "", "gap_days": gap, "next_day": ds[i + 1]}


def shrink_ratio(today_vol: float, yday_vol: float) -> Optional[float]:
    """今日累计量 / 昨日同期累计量（<1 即缩量）。分母缺失 → None。"""
    try:
        t, y = float(today_vol), float(yday_vol)
    except (TypeError, ValueError):
        return None
    if y <= 0:
        return None
    return round(t / y, 3)


def still_shrinking(ratio: Optional[float], th: Optional[float] = None) -> bool:
    """「还是缩量」：比值存在且 < 阈值（默认 1.0 = 不及昨日同期）。"""
    if ratio is None:
        return False
    return float(ratio) < float(th if th is not None else _env_f("WOLF_WH_SHRINK", 1.0))


def not_rallied(idx_pct: Optional[float], th: Optional[float] = None) -> bool:
    """「还是不拉升」：指数涨幅 < 阈值（默认 +0.30%）。"""
    if idx_pct is None:
        return False
    return float(idx_pct) < float(th if th is not None else _env_f("WOLF_WH_RALLY", 0.30))


def evaluate(hhmm: str, pre: Dict[str, Any], ratio: Optional[float],
             idx_pct: Optional[float], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """三段判定：①是否节前最后一个交易日 ②是否已到检查时点 ③缩量∧未拉升 → 减半。"""
    c = cfg or {}
    target = float(c.get("target_pct", _env_f("WOLF_WH_TARGET", 0.65)))
    reduce_to = c.get("reduce_ratio", 0.5)
    res: Dict[str, Any] = {"pre_break": bool(pre.get("is_pre_break")), "kind": pre.get("kind") or "",
                           "gap_days": pre.get("gap_days"), "cutoff": cutoff(),
                           "shrink_ratio": ratio, "idx_pct": idx_pct,
                           "still_shrinking": still_shrinking(ratio, c.get("shrink_th")),
                           "not_rallied": not_rallied(idx_pct, c.get("rally_th")),
                           "target_pct": target, "active": False, "reason": "", "directive": ""}
    if not res["pre_break"]:
        res["reason"] = pre.get("reason") or "not_pre_break"
        return res
    _cut = cutoff_for(pre.get("kind") or "")
    res["cutoff"] = _cut
    if _hhmm(hhmm) < _hhmm(_cut):
        res["reason"] = "before_cutoff(%s/%s)" % (pre.get("kind") or "-", _cut)
        return res
    if not res["still_shrinking"]:
        res["reason"] = "放量(量比 %s) → 按他 08-21 口径不减" % (ratio if ratio is not None else "未知")
        res["directive"] = _text(res)
        return res
    if not res["not_rallied"]:
        res["reason"] = "已拉升(指数 %+.2f%%) → 按他 08-21 口径不减" % float(idx_pct or 0)
        res["directive"] = _text(res)
        return res
    res["active"] = True
    res["reason"] = ("%s前最后一个交易日（间隔 %s 天）∧ 缩量（量比 %s）∧ 未拉升（指数 %+.2f%%）"
                     % ("长假" if res["kind"] == "holiday" else "周末", res["gap_days"], ratio,
                        float(idx_pct or 0)))
    res["directive"] = _text(res, reduce_to=reduce_to)
    return res


def _hhmm(s: str) -> int:
    """'HH:MM' → 分钟数；也兼容 'HHMM'（4 位数字）。

    ⚠️ 2026-09-14：此前只认 'HH:MM'，传 '1005' 会静默返回 **0**（被当成 0 点）→ 时点门失效。
    """
    t = str(s).strip()
    try:
        if ":" in t:
            h, m = t.split(":")[:2]
            return int(h) * 60 + int(m)
        d = "".join(ch for ch in t if ch.isdigit())
        if len(d) >= 4:
            return int(d[:2]) * 60 + int(d[2:4])
        if len(d) == 3:
            return int(d[:1]) * 60 + int(d[1:3])
    except Exception as _e_sil1:
        _silent_alert("wolf_weekend_hedge.py:228", _e_sil1)
    return 0


def _text(r: Dict[str, Any], reduce_to: float = 0.5) -> str:
    """注入用的提示文本（含他的话与原话日期）。"""
    when = "长假前" if r.get("kind") == "holiday" else "周末前"
    head = ("⏳ " + when + "最后一个交易日避险（狼大 2026-08-21 14:20：「2点半 如果还是缩量 还是不拉升 "
            "我会先把这两天T进去的仓位出来一半 防止周末出利空 这样周一再拿回来。"
            "出于仓位安全考虑 65%仓位过周末」）")
    body = "｜量比 %s（昨日同期=1.0）、指数 %s" % (
        r.get("shrink_ratio") if r.get("shrink_ratio") is not None else "未知",
        ("%+.2f%%" % float(r["idx_pct"])) if r.get("idx_pct") is not None else "未知")
    if r.get("active"):
        return (head + body + "｜⚠️ **条件成立 → 把近两日 T 进去的仓位减约 %.0f%%，收盘仓位目标 ≤%.0f%%"
                "（底仓不卖），下周一确认安全后再拿回；不得新开仓/加仓**"
                % (reduce_to * 100, float(r.get("target_pct") or 0.65) * 100))
    return head + body + "｜条件未成立（%s）→ 按他 08-21 口径**不减**，继续持仓" % (r.get("reason") or "—")


# ───────────────────────── 数据层 ─────────────────────────

def _parse_bar_time(t: Any) -> Tuple[str, str]:
    """腾讯 m5 时间 '202608211430' → ('20260821','14:30')；兼容 'YYYY-MM-DD HH:MM'。"""
    s = str(t).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 12:
        return digits[:8], "%s:%s" % (digits[8:10], digits[10:12])
    return "", ""


def _cum_vol(bars: Sequence[Dict[str, Any]], day8: str, upto: str) -> Tuple[float, Optional[float]]:
    """返回 (该日累计成交量, 该日最后收盘价)。"""
    cut = _hhmm(upto)
    vol, close = 0.0, None
    for b in bars or []:
        d, hm = _parse_bar_time(b.get("time"))
        if d != day8 or not hm:
            continue
        if _hhmm(hm) <= cut:
            try:
                vol += float(b.get("vol") or 0)
            except (TypeError, ValueError) as _e_sil2:
                _silent_alert("wolf_weekend_hedge.py:271", _e_sil2)
        try:
            close = float(b.get("close"))
        except (TypeError, ValueError) as _e_sil3:
            _silent_alert("wolf_weekend_hedge.py:275", _e_sil3)
    return round(vol, 2), close


def index_m5() -> List[Dict[str, Any]]:
    """复用 A6 已有的指数分钟源（腾讯 m5，跨多日）。失败 → []。"""
    try:
        from app.services.t_data_sources import fetch_tencent_mkline
        return fetch_tencent_mkline(IDX, freq="m5", count=500) or []
    except Exception as e:
        print(f"[weekend_hedge] 指数 m5 失败: {type(e).__name__}: {str(e)[:70]}")
        return []


# ── 量能源替代（2026-09-18，用户"按这个改"）────────────────────────────────
# 实测：本地指数分钟文件（`data/_bt_full/mins/000001_SH_5min_<day>.json`）**只有收盘价有值**，
#   成交量/成交额两列**全为 null**，且没有 1 分钟指数文件 ⇒ `still_shrinking(ratio=None)` 恒不成立
#   ⇒ 避险规则会**静默沉默**。改法：缩量判定改用**全市场成交额**（也更贴他原话
#   「量能放到 1500E / 2000E」——那是市场总额，不是指数成交量）。
# 数据源：PG `mkt_bars_daily.amount` 当日/昨日求和（一次查询，快、PIT 有界：只取 ≤ 当日的日期）。
def market_amount(day8: str) -> Optional[float]:
    """全市场成交额，**单位＝千元**（tushare `daily.amount` 原生口径；与 `bars.sqlite` 完全一致）。

    ⚠️ 2026-09-18 教训：我最初把它当"元"再除以 1e8，读成"约 30 亿"，其实**是我的换算标签写错了**——
    20260109 求和 = **3,152,368,298 千元 = 3.15 万亿元/日**（与 tushare 口径、`bars.sqlite` 逐值一致）。
    换算：千元 → 元 ×1e3；→ 亿元 ÷1e5；→ 万亿元 ÷1e9。
    取数失败 → None。
    """
    try:
        import psycopg2
        d = str(day8).replace("-", "")
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""))
        try:
            cur = conn.cursor()
            # ⚠️ `mkt_bars_daily.trade_date` 存的是 **'YYYYMMDD'**（实测 '20260914'），
            #    不是 'YYYY-MM-DD'；用带横杠的形式查会**静默返回 0 行**（2026-09-18 踩到）
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM mkt_bars_daily WHERE trade_date=%s", (d,))
            v = float((cur.fetchone() or [0])[0] or 0)
        finally:
            conn.close()
        return v if v > 0 else None
    except Exception:
        return None


def market_amount_yi(day8: str) -> Optional[float]:
    """全市场成交额（**亿元**，便于与语料的 1500E/2000E 同量纲对话）。"""
    v = market_amount(day8)
    return round(v / 1e5, 1) if v else None


def market_shrink_ratio(day8: str, prev8: str) -> Optional[float]:
    """市场级缩量比 = 当日成交额 / 上一交易日成交额（日级；他原话的 1500E/2000E 就是这一量纲）。"""
    a, b = market_amount(day8), market_amount(prev8)
    if not a or not b:
        return None
    return round(a / b, 4)


def effective_shrink_ratio(day8: str, prev8: str, bars: Optional[List[Dict[str, Any]]] = None,
                           upto: str = "14:30") -> Tuple[Optional[float], str]:
    """取"缩量比"：优先**指数分钟量比**（同期），不可得 → 回落**市场成交额日级比**。

    返回 (ratio, source)；两者都不可得 → (None, "unavailable")。
    """
    try:
        b = bars if bars is not None else index_m5()
        vt, _ = _cum_vol(b, day8, upto)
        vp, _ = _cum_vol(b, prev8, upto)
        if vt and vp:
            return round(vt / vp, 4), "index_m5"
    except Exception as _e_sil4:
        _silent_alert("wolf_weekend_hedge.py:347", _e_sil4)
    r = market_shrink_ratio(day8, prev8)
    return (r, "market_amount") if r else (None, "unavailable")


def recent_trade_days() -> List[str]:
    """含**未来**交易日（判断"下一个交易日间隔"必需）。"""
    # ── 本地日历优先（2026-09-18 修"回放里日历只有 6 天"）──
    # 根因：`resolve_trade_days` 主源走 tushare 中继/as-of 网关，**在回放里被夹到 ≤ 回放日**
    #   ⇒ 拿不到"未来交易日"，而本函数**必须含未来交易日**（判断"下一个交易日间隔几天"）。
    #   交易日历是**公开日历信息**（知道 20260112 是交易日不构成未来函数，行情/价格才算），
    #   故这里优先用**本地 bars.sqlite 的 distinct trade_date**（覆盖 20250102→20260914，PIT 无关价格）。
    try:
        import datetime as _dt
        import os as _os
        import sqlite3 as _sq
        _s = (_dt.date.today() - _dt.timedelta(days=10)).strftime("%Y%m%d")
        _e = (_dt.date.today() + _dt.timedelta(days=20)).strftime("%Y%m%d")
        _db = _os.getenv("BT_BARS_DB", "data/_bt_full/bars.sqlite")
        _c = _sq.connect(_db)
        _rows = _c.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
                           (_s, _e)).fetchall()
        _c.close()
        _loc = sorted({str(r[0]).replace("-", "")[:8] for r in _rows if str(r[0]).replace("-", "")[:8].isdigit()})
        if len(_loc) >= 5:          # 至少够判断"今→下一个交易日"的间隔
            return _loc
        print("[weekend_hedge] 本地日历不足(%d)，回落 tushare 中继" % len(_loc))
    except Exception as _le:
        print("[weekend_hedge] 本地日历失败(%s)，回落中继" % str(_le)[:60])
    try:
        import datetime as _dt
        from app.services.t_backtest_data import resolve_trade_days
        start = (_dt.date.today() - _dt.timedelta(days=10)).strftime("%Y%m%d")
        end = (_dt.date.today() + _dt.timedelta(days=20)).strftime("%Y%m%d")
        # ⚠️ 2026-09-18 修：`resolve_trade_days` 可能返回 **'YYYY-MM-DD'**，旧写法 `str(d)[:8]`
        #  会得到 '2026-01-' → `.isdigit()` 为假 → **日历被过滤成空** ⇒ `is_pre_break_day` 恒返回
        #  today_not_in_calendar ⇒ **周末避险永远不触发**（实测 0109 日志："避险未成立: today_not_in_calendar"）。
        return sorted({str(d).replace("-", "")[:8] for d in (resolve_trade_days(start, end) or [])
                       if str(d).replace("-", "")[:8].isdigit()})
    except Exception as e:
        print(f"[weekend_hedge] 交易日历失败: {type(e).__name__}: {str(e)[:70]}")
        return []


def run(save: bool = True, bars: Optional[List[Dict[str, Any]]] = None,
        trade_days: Optional[List[str]] = None, today8: Optional[str] = None,
        hhmm: Optional[str] = None) -> Dict[str, Any]:
    """采集 → 判定 → 落 `wolf_weekend_hedge.json`（参数可注入，便于测试/回放）。"""
    import datetime as _dt
    today8 = today8 or _dt.date.today().strftime("%Y%m%d")
    hhmm = hhmm or _dt.datetime.now().strftime("%H:%M")
    bars = bars if bars is not None else index_m5()
    cal = trade_days if trade_days is not None else recent_trade_days()
    pre = is_pre_break_day(today8, cal)
    if not bars:
        res = {"ok": False, "reason": "no_bars", "pre_break": pre, "as_of": today8}
        if save and enabled():
            _save(res)
        return res
    days = sorted({_parse_bar_time(b.get("time"))[0] for b in bars if _parse_bar_time(b.get("time"))[0]})
    prev8 = None
    for d in reversed(days):
        if d < today8:
            prev8 = d
            break
    t_vol, t_close = _cum_vol(bars, today8, hhmm)
    y_vol, y_close = _cum_vol(bars, prev8, hhmm) if prev8 else (0.0, None)
    ratio = shrink_ratio(t_vol, y_vol)
    # ── 量能源回落（2026-09-18 用户"按这个改"）──
    # 本地指数分钟文件**只有收盘价、量能两列全 null** ⇒ `ratio` 恒 None ⇒ 规则**静默沉默**。
    # 故指数口径取不到量比时，回落**全市场成交额日级比**（`market_amount`，tushare 千元口径）。
    ratio_source = "index_m5" if ratio is not None else ""
    if ratio is None and prev8:
        try:
            ratio, ratio_source = effective_shrink_ratio(today8, prev8, bars, hhmm)
        except Exception:
            ratio, ratio_source = None, "unavailable"
    idx_pct = None
    if t_close and y_close:
        idx_pct = round((t_close - y_close) / y_close * 100.0, 3)
    res = evaluate(hhmm, pre, ratio, idx_pct)
    res["ratio_source"] = ratio_source or "unavailable"
    res.update({"ok": True, "as_of": today8, "hhmm": hhmm,
                "today_vol": t_vol, "prev_day": prev8, "prev_vol": y_vol,
                "index_close": t_close})
    if save and enabled():
        _save(res)
    print(f"[weekend_hedge] {today8} {hhmm} pre_break={res['pre_break']}({res.get('kind')}) "
          f"量比={ratio} 指数={idx_pct} → active={res['active']}")
    return res


def _save(res: Dict[str, Any]) -> None:
    try:
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[weekend_hedge] 落盘失败: {str(e)[:80]}")


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """周末/长假前避险提示；关闭时返回空（防"假开关"）。"""
    if not enabled():
        return ""
    st = load()
    if not st:
        return ""
    if st.get("ok") is False and not st.get("directive"):
        return ""
    return st.get("directive") or ""
