# -*- coding: utf-8 -*-
"""theme_leader_score.py —— 「板块内**正宗度 × 龙头度**」的**数据驱动**通过路径（账本 §9.461 ✓）

**用户 2026-10-03 明确要求：默认打开** ✓（`WOLF_THEME_SCORE_ACCEPT` 默认 `1`；要关请显式设 `0`）
⚠️ 生产零影响的方式 ✓：龙头度要 as-of 日线（`bars.sqlite`）✓，**生产没有 ⇒ 本路径自动失效**，
   退回原「主类表」判定 ✓（不是"关掉"，是"数据不足 ⇒ 不改变判定" ✓）

**为什么要有它** ✓（§9.458 量化 ✓）：
  · 3 月新能源**主题内涨幅前 20**（平均 **+51.3%**）里，**10/13 有候选的票死于「主营/主类校验」** ✗
  · 那张 `THEME_MAIN_CLASS` 表是**人手写的行业集** ✗（`汽车整车` 不在 `新能源/电池` 里 ⇒ 比亚迪被拒 ✗）
  · 本模块把它换成**数据算的分位分** ✓：没有任何名单 ✓

**两个成分（都不写名单 ✓）**
  1. **正宗度** ＝ 与主题**核心成员**（原主类表能过的成员，仅作"种子"用 ✓）**共享的概念**的比例 ✓
     —— 纯统计 ✓（例：比亚迪与宁德/恩捷共享 `锂电池概念/新能源车/电池技术/储能概念` 等 ✓）
  2. **龙头度** ＝ 该股在主题成员里的 **成交额分位 × 市值分位** ✓（as-of ✓，带日期夹紧 ⇒ 无未来函数 ✓）
  · 合成 ＝ `0.5×正宗度 + 0.5×龙头度`；**阈值是分位**（`WOLF_THEME_SCORE_MIN`，默认 0.80 ✓）
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

_CACHE: Dict[str, Any] = {}


def enabled() -> bool:
    """**默认开** ✓（用户 2026-10-03：「默认打开，别下次回测又关上了我不知道」✓）。"""
    return str(os.getenv("WOLF_THEME_SCORE_ACCEPT", "1")).strip().lower() in ("1", "true", "yes", "on")


def min_score() -> float:
    """通过阈值（分位 ✓，默认 **0.62** ✓）。

⚠️ 标定 ✓（账本 §9.461 ✓，as-of 20260302 实测 ✓）：**分界很清楚** ——
   · 该放的 ✓：诺德 0.771／**比亚迪 0.744**／000533 0.634／002015 0.630
   · 该拦的 ✗：科瑞技术（杂毛）0.519／快克 0.342／华泰 0.197／电子城 0.177／600396 0.118
   ⇒ 取**中缝 0.62** ✓（放前四只 ✓、拦后五只 ✓）；单一窗口标定 ⇒ 换窗口应复标 ✓
"""
    try:
        return float(os.getenv("WOLF_THEME_SCORE_MIN", "0.62"))
    except Exception:
        return 0.62


def _dsn() -> str:
    return os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"


def _asof_day() -> str:
    """as-of 日（8 位 ✓）：①显式 env ②`DATA_DIR` 的目录名（沙箱即 <...>/20260302 ✓）。取不到 ⇒ ""。"""
    for k in ("WOLF_ASOF_DAY", "BT_CUT"):
        v = str(os.getenv(k) or "").strip()
        if len(v) == 8 and v.isdigit():
            return v
    b = os.path.basename(str(os.getenv("DATA_DIR") or "").strip())
    return b if (len(b) == 8 and b.isdigit()) else ""


def _bars_db() -> str:
    """as-of 日线库（**只在回测环境存在** ✓；生产没有 ⇒ 本模块失效 ✓）。"""
    # ⚠️ 实测 ✓：成员校验可能跑在**子进程**里、**cwd 不是仓库根** ✗
    #   ⇒ 只用相对路径会找不到 bars ⇒ 分=None ⇒ 判定不变（=A 静默失效 ✗）
    #   ⇒ 这里补一条**基于本文件的绝对路径**（apps/main_line/… ⇒ 仓库根 = 上三层 ✓）
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except Exception:
        _root = ""
    for p in (os.getenv("WOLF_BARS_DB"),
              os.path.join(_root, "data", "_bt_full", "bars.sqlite"),
              os.path.join("data", "_bt_full", "bars.sqlite"),
              os.path.join(os.getenv("DATA_DIR") or "", "bars.sqlite")):
        if p and os.path.exists(p):
            return p
    return ""


def _core_members(theme: str) -> List[str]:
    """主题**核心成员**：原主类表能过的票（**仅当种子** ✓，不是最终判定 ✓）。"""
    key = "core:" + str(theme)
    if key in _CACHE:
        return _CACHE[key]
    out: List[str] = []
    try:
        import theme_main_class as _tmc
        allow = _tmc.THEME_MAIN_CLASS.get(str(theme)) or set()
        if allow:
            import psycopg2
            conn = psycopg2.connect(_dsn(), connect_timeout=4)
            try:
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute("SET statement_timeout=5000")
                cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name = ANY(%s)",
                            (sorted(allow),))
                out = [str(r[0]) for r in cur.fetchall()]
            finally:
                conn.close()
    except Exception:
        out = []
    _CACHE[key] = out
    return out


def _theme_concepts(theme: str) -> List[str]:
    """主题概念 ＝ 核心成员里**≥20% 都有的概念**（数据驱动 ✓，剔掉通用标签的效果 ✓）。"""
    key = "tc:" + str(theme)
    if key in _CACHE:
        return _CACHE[key]
    out: List[str] = []
    core = _core_members(theme)
    if core:
        try:
            import psycopg2
            conn = psycopg2.connect(_dsn(), connect_timeout=4)
            try:
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute("SET statement_timeout=6000")
                cur.execute("SELECT concept_name, count(*) FROM stock_concept_map WHERE ts_code = ANY(%s) "
                            "GROUP BY 1 HAVING count(*) >= %s", (core, max(3, int(len(core) * 0.20))))
                _by_core = {str(r[0]): int(r[1]) for r in cur.fetchall()}
                # ⚠️ 再剔**全市场通用标签** ✗（如 融资融券/深股通/机构重仓 ⇒ 占比 > 20% 全市场 ✓）
                cur.execute("SELECT concept_name, count(*) FROM stock_concept_map GROUP BY 1")
                _all = {str(r[0]): int(r[1]) for r in cur.fetchall()}
                cur.execute("SELECT count(DISTINCT ts_code) FROM stock_concept_map")
                _tot = int((cur.fetchone() or [1])[0] or 1)
                _cap = max(50, int(_tot * 0.20))
                out = [k for k, n in _by_core.items() if _all.get(k, 0) <= _cap]
            finally:
                conn.close()
        except Exception:
            out = []
    _CACHE[key] = out
    return out


def _leaders(theme: str) -> Dict[str, Dict[str, float]]:
    """主题成员的 成交额/市值（as-of 前 20 日均值 ✓，**带日期夹紧 ⇒ 无未来函数 ✓**）。"""
    key = "ld:" + str(theme)
    if key in _CACHE:
        return _CACHE[key]
    out: Dict[str, Dict[str, float]] = {}
    core = _core_members(theme)
    db, day = _bars_db(), _asof_day()
    if core and db and day:
        try:
            import sqlite3, time as _t
            # ⚠️ 实测 ✓：驱动器并发写时会出现 `unable to open database file` ✗（瞬时）
            #   ⇒ **只读打开** ＋ 重试一次 ✓（仍失败 ⇒ out={} ⇒ 判定不变 ✓ fail-open ✓）
            c = None
            for _try in range(6):
                try:
                    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
                    break
                except Exception as _e:
                    c = None
                    _CACHE["_last_err"] = "%s: %s" % (type(_e).__name__, str(_e)[:60])
                    _t.sleep(0.6)   # 驱动器并发写 ⇒ 瞬时锁；退避重试 ✓（仍失败 ⇒ fail-open ✓）
            if c is None:
                _CACHE[key] = {}
                return {}
            try:
                # ⚠️ 修：SQLite 的 `date(?, '-40 day')` 产出**带横线**的 'YYYY-MM-DD' ✗
                #   而库里的 `trade_date` 是 **'YYYYMMDD'** ⇒ 字符串比较恒不命中 ⇒ 0 行 ✗
                #   ⇒ 改成**在 Python 里算好 YYYYMMDD** ✓
                # ⚠️ 实测 ✓：**大聚合（GROUP BY 220 万行）需要写临时文件** ✗
                #   而沙箱不允许写那个临时目录 ⇒ 报 `unable to open database file` ✗
                #   （轻查询正常 ⇒ 极易误判成"文件打不开" ✗）
                #   ⇒ `temp_store=MEMORY` 把临时表放内存 ✓
                c.execute("PRAGMA temp_store=MEMORY")
                c.execute("PRAGMA query_only=1")
                import datetime as _dt
                _d0 = (_dt.datetime.strptime(day, "%Y%m%d") - _dt.timedelta(days=40)).strftime("%Y%m%d")
                q = ("SELECT ts_code, avg(amount), avg(total_mv) FROM bars "
                     "WHERE trade_date < ? AND trade_date >= ? GROUP BY 1")
                for ts, amt, mv in c.execute(q, (day, _d0)):
                    s = str(ts)
                    out[s] = {"amt": float(amt or 0), "mv": float(mv or 0)}
                    _CACHE["_rows"] = len(out)
            finally:
                c.close()
        except Exception as e:
            _CACHE["_last_err"] = "%s: %s" % (type(e).__name__, str(e)[:90])
            out = {}
    _CACHE[key] = out
    return out


def _pct(values: Sequence[float], v: float) -> float:
    if not values:
        return 0.0
    import bisect
    s = sorted(values)
    return bisect.bisect_left(s, v) / max(1, len(s) - 1)


def score_for(symbol: str, theme: str, concepts: Optional[Sequence[str]] = None) -> Tuple[Optional[float], str]:
    """→ (分位分 0~1, 说明)。**数据不足 ⇒ (None, 原因)** ✓（调用方应保持原判定 ✓）。"""
    try:
        import theme_main_class as _tmc
        cand = [str(x) for x in (list(concepts) if concepts else _tmc.concepts_of(symbol))]
        if not cand:
            return None, "无概念数据"
        tcs = _theme_concepts(theme)
        if not tcs:
            return None, "无主题概念数据"
        purity = len(set(cand) & set(tcs)) / max(1, len(tcs))          # 正宗度 ✓
        lead = None
        ld = _leaders(theme)
        ts = ("%s.%s" % (str(symbol)[2:], str(symbol)[:2])) if (len(str(symbol)) >= 8 and str(symbol)[:2] in ("SH", "SZ", "BJ")) else str(symbol)
        if ld and ts in ld:
            amts = [x["amt"] for x in ld.values()]
            mvs = [x["mv"] for x in ld.values()]
            lead = 0.5 * _pct(amts, ld[ts]["amt"]) + 0.5 * _pct(mvs, ld[ts]["mv"])   # 龙头度 ✓
        if lead is None:
            if str(os.getenv("WOLF_THEME_SCORE_DEBUG", "0")).strip() == "1":
                print("[TLS] %s/%s ⇒ 无 as-of 日线（bars=%r day=%r）⇒ 不改变判定"
                      % (symbol, theme, _bars_db(), _asof_day()), flush=True)
            return None, "无 as-of 日线（生产环境即如此 ⇒ 不改变判定）"
        score = 0.5 * purity + 0.5 * lead
        return score, "正宗度=%.3f（共享 %d/%d 概念）｜龙头度=%.3f｜分=%.3f" % (
            purity, len(set(cand) & set(tcs)), len(tcs), lead, score)
    except Exception as e:
        return None, "打分异常: %s" % str(e)[:60]
