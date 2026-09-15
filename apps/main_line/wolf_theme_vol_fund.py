# -*- coding: utf-8 -*-
"""wolf_theme_vol_fund.py — 他称的「**选板块的第一要素**」（2026-09-15 参数对齐新增）。

狼大原话（xls2025 逐字，2025-06-16）:
> 「**量能活跃**(也就是最近一周内至少2/3天数以上在10日量能以上)，**资金没有5日连续流出**的。
>    这是**选板块的第一要素**」

口径（**全部按他的话，不留自设数值**）:
  · 量能活跃 = 近 **5** 个交易日中，板块成交额 > 该板块 **10** 日成交额均值的**天数 ≥ ceil(5×2/3) = 4**；
  · 资金      = 板块主力净流入**没有**连续 **5** 个交易日全部为负（任一为负即放行）。
四个数字（5 / 10 / 2÷3 / 5）**全部来自这一句原话**；唯一的实现选择是"10 日量能"取
**含当日的 MA10**（标准读法）、"板块资金"取该主题各子概念 net_amount 的**均值**（沿用既有
`wolf_context._concept_hist_by_name` 的口径，与 P2-2 同源）。

**与我们的池判据的关系**（2026-09-15 离线对照 `jobs/eval_theme_vol_fund.py`，2,132 主题×日）：
  · 我们的池 `O`（share5 topK=3 ∩ r5>0，**自造口径**）：后 5 日超额 +0.076%（块状 t 2.00，n=909）
  · 他的门 `H`：+0.139%（t 0.75，n=562）
  · **`O ∩ H`：+0.718%（胜率 65.5%，块状 t 1.73，n=177）**；而 `O ∩ ¬H`：**−0.080%（n=732）**
    → 池内通过他这条门的那一半明显更好，且与他门的松紧无关（|H|≥4 的子集 t=2.23）
  · 代价：他的门在 104/164 天非空 → **开启后 60 天没有任何池内主题**（不新开低吸腿）

开关 `WOLF_THEME_VOLFUND_GATE`：**默认 0（关）**——开启是行为变更（会让部分日子不布腿），
按纪律需用户拍板后再置 1。**语料侧的值是"应当开启"**（他称"第一要素"）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

VOL_WIN = 10        # 他："10日量能"
ACT_DAYS = 5        # 他："最近一周"
ACT_NEED = 4        # 他："至少2/3天数以上" → ceil(5 × 2/3) = 4
FUND_DAYS = 5       # 他："资金没有5日连续流出的"


def enabled() -> bool:
    """`WOLF_THEME_VOLFUND_GATE` 默认 0（关）：开启会让"过不了门"的日子不布新腿，需拍板。"""
    return os.getenv("WOLF_THEME_VOLFUND_GATE", "0").strip().lower() not in ("0", "false", "no", "")


def check(amounts: Sequence[float], nets: Sequence[float],
          vol_win: int = VOL_WIN, act_days: int = ACT_DAYS, act_need: int = ACT_NEED,
          fund_days: int = FUND_DAYS) -> Tuple[bool, str, Dict[str, Any]]:
    """纯函数：按他的话判"板块可不可做"。

    `amounts`：该板块**逐日**成交额（升序，末尾=当日）；`nets`：逐日主力净流入（升序，末尾=当日）。
    数据不足 → **放行**（fail-open，与 `theme_buyable` 既有约定一致：数据问题不封死买路）。
    返回 (ok, reason, detail)。
    """
    detail: Dict[str, Any] = {"vol_win": vol_win, "act_days": act_days, "act_need": act_need,
                              "fund_days": fund_days}
    a = [float(x) for x in (amounts or []) if x is not None and float(x) == float(x)]
    # 每日都要有完整的 10 日量能基准 → 至少需要 vol_win + act_days 个点（否则头部几天会拿空窗口当 0）
    need = vol_win + act_days
    if len(a) < need:
        return True, "量能序列不足(%d<%d, 需 10日基准×5日) → 放行" % (len(a), need), detail
    act = 0
    for k in range(act_days):
        idx = len(a) - act_days + k                       # 对应"当日"在整体序列中的位置
        assert idx - vol_win + 1 >= 0                     # 由上面的 need 保证
        base = sum(a[idx - vol_win + 1:idx + 1]) / float(vol_win)   # 含当日的 MA10（标准读法）
        if a[idx] > base:
            act += 1
    detail["active_days"] = act
    if act < act_need:
        return False, "量能不活跃：近%d日仅%d天在%d日量能之上（需≥%d）" % (act_days, act, vol_win, act_need), detail

    n = [float(x) for x in (nets or []) if x is not None and float(x) == float(x)]
    if len(n) < fund_days:
        detail["fund_data"] = "insufficient"
        return True, "资金序列不足(%d<%d) → 放行" % (len(n), fund_days), detail
    tail = n[-fund_days:]
    detail["fund_tail"] = [round(x, 1) for x in tail]
    if len(set(tail)) == 1:
        # 序列全同 = 前值填充/陈旧（生产 concept_hist 实测如此）→ 资金半边放行，不据此拦
        detail["fund_data"] = "stale(all-equal)"
        return True, "量能活跃 %d/%d ∧ 资金序列疑似前值填充(全同) → 资金半边放行" % (act, act_days), detail
    if all(x < 0 for x in tail):
        return False, "资金连续%d日净流出 → 不新开腿" % fund_days, detail
    return True, "量能活跃 %d/%d ∧ 资金非连续%d日流出" % (act, act_days, fund_days), detail


def theme_amounts(theme: str, as_of: Optional[str] = None, days: int = VOL_WIN + ACT_DAYS + 2) -> List[float]:
    """主题逐日成交额（亿元，升序）。数据源：PG `mkt_bars_daily`（成分股加总）。失败 → []。"""
    try:
        import psycopg2
        import sys as _s
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in _s.path:
            _s.path.insert(0, _p)
        from fusion_mainline import THEME_CONCEPTS
        concepts = THEME_CONCEPTS.get(theme) or []
        if not concepts:
            return []
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        ph = ",".join(["%s"] * len(concepts))
        cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (" + ph + ")",
                    concepts)
        codes = [str(r[0]) for r in cur.fetchall()]
        if not codes:
            cur.close(); conn.close(); return []
        ph2 = ",".join(["%s"] * len(codes))
        d8 = as_of or None
        if d8:
            cur.execute("SELECT trade_date FROM mkt_bars_daily WHERE trade_date<=%s "
                        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s", (d8, days))
        else:
            cur.execute("SELECT trade_date FROM mkt_bars_daily GROUP BY trade_date "
                        "ORDER BY trade_date DESC LIMIT %s", (days,))
        ds = sorted(str(r[0]) for r in cur.fetchall())
        out = []
        for d in ds:
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM mkt_bars_daily "
                        "WHERE trade_date=%s AND ts_code IN (" + ph2 + ")", [d] + codes)
            v = float((cur.fetchone() or [0])[0] or 0) / 1e5      # 千元 → 亿元
            out.append(v)
        cur.close(); conn.close()
        return out
    except Exception as e:
        print("[theme_volfund] 主题成交额取数失败 %s: %s" % (theme, str(e)[:80]))
        return []


def _trade_days(as_of: Optional[str], n: int) -> List[str]:
    """最近 n 个交易日（升序，≤as_of）。数据源：PG mkt_bars_daily。"""
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        if as_of:
            cur.execute("SELECT DISTINCT trade_date FROM mkt_bars_daily WHERE trade_date<=%s "
                        "ORDER BY trade_date DESC LIMIT %s", (as_of, n))
        else:
            cur.execute("SELECT DISTINCT trade_date FROM mkt_bars_daily "
                        "ORDER BY trade_date DESC LIMIT %s", (n,))
        ds = sorted(str(r[0]) for r in cur.fetchall())
        cur.close(); conn.close()
        return ds
    except Exception as e:
        print("[theme_volfund] 交易日取数失败: %s" % str(e)[:80])
        return []


def _mf_cache_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"), "theme_mf_daily.json")


def _refresh_mf_day(d8: str) -> Dict[str, float]:
    """取某日**全市场** moneyflow（中继，1 次调用）→ 13 个主题的主力净流入合计（万元）。

    ⚠️ 不用 `concept_hist.net_amount`：生产实测该字段是**前值填充**（末 5 日完全相同），
    拿它判"连续 5 日净流出"会恒真/恒假 → 这里改用日频个股资金流（Tushare moneyflow）按成分加总。
    """
    import sys as _s
    _p = os.path.dirname(os.path.abspath(__file__))
    if _p not in _s.path:
        _s.path.insert(0, _p)
    from fusion_mainline import THEME_CONCEPTS
    import importlib
    relay = None
    for name in ("tushare_relay",):
        try:
            relay = importlib.import_module(name)
            break
        except ImportError:
            continue
    if relay is None:
        cur = _p
        for _ in range(6):
            cand = os.path.join(cur, "core")
            if os.path.exists(os.path.join(cand, "tushare_relay.py")):
                if cand not in _s.path:
                    _s.path.insert(0, cand)
                relay = importlib.import_module("tushare_relay")
                break
            cur = os.path.dirname(cur)
    if relay is None:
        return {}
    fields, items = relay.relay_items("moneyflow", fields="ts_code,trade_date,net_mf_amount", trade_date=d8)
    if not items or not fields:
        return {}
    i_ts, i_v = fields.index("ts_code"), fields.index("net_mf_amount")
    by_ts = {}
    for it in items:
        try:
            by_ts[str(it[i_ts])] = float(it[i_v] or 0)
        except (TypeError, ValueError):
            continue
    out = {}
    for th, cons in THEME_CONCEPTS.items():
        try:
            import sqlite3
            db = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "stock_pool.db")
            c = sqlite3.connect(db)
            ph = ",".join(["?"] * len(cons))
            codes = {str(r[0]) for r in c.execute(
                "SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (%s)" % ph, cons)}
            c.close()
        except Exception:
            codes = set()
        out[th] = sum(by_ts.get(c, 0.0) for c in codes)
    return out


def theme_nets(theme: str, as_of: Optional[str] = None, days: int = FUND_DAYS + 2) -> List[float]:
    """主题逐日主力净流入（万元，升序）——来自**日频个股资金流**按成分加总（非 concept_hist）。

    取数：每个交易日 1 次中继调用（全市场），结果按日缓存 `data/theme_mf_daily.json`（13 个主题各一个数）。
    失败/数据不足 → 返回已取到的部分（调用方 fail-open）。
    """
    dts = _trade_days(as_of, days)
    if not dts:
        return []
    path = _mf_cache_path()
    cache: Dict[str, Any] = {}
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f) or {}
    except Exception:
        cache = {}
    changed = False
    for d in dts:
        if not isinstance(cache.get(d), dict):
            got = _refresh_mf_day(d)
            if got:
                cache[d] = got
                changed = True
    if changed:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            print("[theme_volfund] 资金缓存写入失败: %s" % str(e)[:80])
    return [float(cache[d][theme]) for d in dts if isinstance(cache.get(d), dict) and theme in cache[d]]


def shadow_enabled() -> bool:
    """`WOLF_THEME_VOLFUND_SHADOW` 默认 1：**只记录不拦**（灰度期，拿到"会拦谁"的逐日记录）。"""
    return os.getenv("WOLF_THEME_VOLFUND_SHADOW", "1").strip().lower() not in ("0", "false", "no", "")


def shadow_record(theme: str, ok: bool, why: str) -> None:
    """把门的结果写进 `data/theme_volfund_shadow_<date>.json`（按主题覆盖，附时间戳）。"""
    try:
        import datetime as _dt
        d8 = _dt.date.today().strftime("%Y%m%d")
        path = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "theme_volfund_shadow_%s.json" % d8)
        cur = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cur = json.load(f) or {}
            except Exception:
                cur = {}
        cur.setdefault("date", d8)
        cur.setdefault("themes", {})
        cur["themes"][theme] = {"pass": bool(ok), "why": str(why)[:200],
                                "ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                "mode": "enforce" if enabled() else "shadow"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme_volfund] 影子写入失败: %s" % str(e)[:80])


def theme_volfund_ok(theme: str, as_of: Optional[str] = None) -> Tuple[bool, str]:
    """生产入口：按他的话判该主题当前可不可做（数据缺失 → 放行）。"""
    if not theme:
        return True, "主题未知 → 放行"
    ok, why, _ = check(theme_amounts(theme, as_of), theme_nets(theme))
    return ok, why
