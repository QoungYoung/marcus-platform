# -*- coding: utf-8 -*-
"""腿批准闸（共享）：主营校验(dsh 无思考) + 可买门(theme_buyable) + 板块权限(board_ok)
+ ⓪ 卫生过滤(退市/ST/北交所, universe_clean) + ④ 破位/急杀。

背景（2026-09-18 排查结论）：
  · 08:18 路径（jobs/bt_day_legs_switch.py）用**上一交易日**的确认域、09:20 路径（jobs/bt_day_legs.py）
    用**当日**确认域 —— 两条路径是"时间错位"而非重复；但**闸门原先只挂在 09:20 一条上**
    （theme_buyable 仅 bt_day_legs.py 调用），08:18 不检查 ⇒ 门关了照样布腿、照样买入。
  · 主营校验原先只在 stock_confirm_judge.py（confirm 步骤）里做 ⇒ 08:18 要隔一天才受约束。

做法：把"批准与否"收敛到一个函数，两条路径在**写腿前**都调用它；返回 (ok, why)，不过就不写腿，
  调用方打 "LEG_REJECT <symbol> <stage>: <why>" 统一留痕。
开关：WOLF_LEG_GATE（回测 BT_ASOF_FETCH=1 时默认开；生产默认关）。
原则：任何一步取数/判定失败都 **fail-open**（放行），避免"闸门故障=停摆"。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

ON = str(os.getenv("WOLF_LEG_GATE", "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
_STATS: Dict[str, int] = {"calls": 0, "rej_member": 0, "rej_gate": 0, "rej_board": 0, "rej_break": 0, "rej_index": 0, "err": 0}
_TIMING: Dict[str, float] = {}
_SLOW_LOG = str(os.getenv("WOLF_SLOW_LOG", "1")).strip().lower() in ("1", "true", "yes", "on")          # 分相耗时累计（2026-09-19 定位"布腿器为什么慢"）


def _tick(name: str, t0: float) -> None:
    try:
        _TIMING[name] = _TIMING.get(name, 0.0) + (time.time() - t0)
    except Exception:
        pass

# ── ④ 个股「不接急杀」（语料口径；仅 WOLF_THEME_TIER_GATE=1 时生效）──
# 2026-09-19 用户拍板两步走：①去掉 MA20 硬判（语料里没有均线口径）；②**再去掉"急杀"**
#   —— 语料里"急杀"是**买点**（253 指数急杀；实测被拦的急杀腿 D+10 反而 +5.9%/+6.2%），
#   弱市不进新票改由**指数破位**承担（见下方 index_new_buy_blocked，语料：下跌趋势就不做 / 收盘破位才动）。
# 本函数只保留语料里真实存在的两条：
#   · 跌停   ：最近一日 pct_chg ≤ −9.8%（WOLF_LIMIT_DOWN_PCT）
#   · 破前低而**未缩量**：收盘 < 前一日最低 ∧ 量比 > 0.9（WOLF_SHRINK_MAX）
#     —— 语料 254 买点＝「破前低 + 缩量」（wolf-buy-gap-audit.md:69 C1，量比 ≤0.9），
#        所以缩量破前低是**买点**要放行；只有放量破前低才是"急杀式破位"，不接。
# 数据全部走 as-of 日线（严格 trade_date < 钉住当日，无未来函数）；不足/异常 ⇒ fail-open。
TIER_GATE_ON = str(os.getenv("WOLF_THEME_TIER_GATE",
                             "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
DUMP_PCT = float(os.getenv("WOLF_DUMP_PCT", "-5") or -5)
LIMIT_DOWN_PCT = float(os.getenv("WOLF_LIMIT_DOWN_PCT", "-9.8") or -9.8)
SHRINK_MAX = float(os.getenv("WOLF_SHRINK_MAX", "0.9") or 0.9)
_DAILY_CACHE: Dict[str, list] = {}


def _recent_daily(symbol: str, n: int = 21) -> list:
    """最近 n 根日线（升序，dict: close/low/vol）。取数失败 → []（fail-open）。"""
    try:
        import psycopg2
        from datetime import datetime as _dt
        s = str(symbol).upper()
        code = ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
        key = "%s|%s" % (code, _dt.now().strftime("%Y%m%d"))
        if key in _DAILY_CACHE:
            return _DAILY_CACHE[key]
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""), connect_timeout=4)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout=4000")
            cur.execute("SELECT close, low, vol FROM mkt_bars_daily WHERE ts_code=%s AND trade_date<%s "
                        "ORDER BY trade_date DESC LIMIT %s", (code, _dt.now().strftime("%Y%m%d"), int(n)))
            out = [{"close": float(r[0]), "low": float(r[1] or 0), "vol": float(r[2] or 0)}
                   for r in cur.fetchall() if r and r[0]]
            out.reverse()
        finally:
            conn.close()
        _DAILY_CACHE[key] = out
        return out
    except Exception:
        return []


def symbol_broken(symbol: str) -> Tuple[bool, str]:
    """个股「不接急杀」→ (True, 原因)；数据不足或异常 → (False, "")（fail-open）。

    语料口径三条（见上方注释）：跌停 / 急杀 / 放量破前低；缩量破前低是 254 买点，放行。
    """
    rows = _recent_daily(symbol, 21)
    if len(rows) < 7:
        return False, ""
    last, prev = rows[-1], rows[-2]
    pc = float(prev.get("close") or 0)
    chg = (float(last.get("close") or 0) / pc - 1) * 100 if pc > 0 else 0.0
    if chg <= LIMIT_DOWN_PCT:
        return True, "跌停: 最近一日 %.2f%%（≤ %.1f%%）" % (chg, LIMIT_DOWN_PCT)
    prev_low = float(prev.get("low") or 0)
    vols = [float(r.get("vol") or 0) for r in rows[-6:-1]]
    vmean = (sum(vols) / len(vols)) if vols else 0.0
    vratio = (float(last.get("vol") or 0) / vmean) if vmean > 0 else 0.0
    if prev_low > 0 and float(last.get("close") or 0) < prev_low and vratio > SHRINK_MAX:
        return True, ("急杀式破前低: 收盘 %.3f < 前一日最低 %.3f 且量比 %.2f > %.2f（未缩量）"
                      % (float(last.get("close") or 0), prev_low, vratio, SHRINK_MAX))
    return False, ""


def stats() -> Dict[str, Any]:
    return {"on": ON, "index_new_buy": INDEX_NEW_BUY_ON, "timing_s": {k: round(v, 2) for k, v in _TIMING.items()}, **_STATS}


# ── 弱市不进新票（语料：下跌趋势就不做 / 收盘确认破位才走；2026-09-19 用户拍板）──
# 用指数大级别破位（backend/app/services/t_index_break.index_breakdown：收盘跌破 MA60/144/200 ≥2 根）
# 作"不新开"的闸；**只拦新票**，持仓做 T 照旧（语料里破位是止损/不新开，不是停止做T）。
# 开关 WOLF_INDEX_NEW_BUY：库内默认关（生产逐位不变），回测默认开。
INDEX_NEW_BUY_ON = str(os.getenv("WOLF_INDEX_NEW_BUY",
                                 "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")


def _index_broken(day: str = "") -> Tuple[bool, str]:
    """指数大级别破位 → (True, 原因)。取数失败/开关关 → (False, "")（fail-open）。"""
    try:
        import sys as _s
        _root = os.path.dirname(os.path.dirname(_HERE))          # 仓库根（或版本树根）
        _be = os.path.join(_root, "backend")
        if _be not in _s.path:
            _s.path.insert(0, _be)
        from app.services.t_index_break import index_breakdown, SWITCH as _IDX_SWITCH
        if not _IDX_SWITCH:
            return False, "指数破位判定关(WOLF_INDEX_BREAKDOWN=0)"
        from datetime import datetime as _dt
        _d = str(day or "").replace("-", "") or _dt.now().strftime("%Y%m%d")
        j = index_breakdown(_d)
        if j.get("broken"):
            return True, ("指数破位: 收盘 %s 跌破 %s（%d 根，连续 %s 日）"
                          % (j.get("close"), j.get("below"), len(j.get("below") or []), j.get("streak")))
        return False, ""
    except Exception as _e:
        return False, "指数破位取数失败(fail-open): %s" % str(_e)[:60]


def index_new_buy_blocked(day: str = "") -> Tuple[bool, str]:
    """指数破位 ⇒ 不新开新票（做T照旧）。返回 (blocked, why)；失败 fail-open。

    ⚠️ 账本 §9.494 ✓ 修正（用户「直接改」✓）：**买侧不再按"指数破位"拦** ✓
    依据（他的原话 ✓）：「好票跌到事先画好的线（13/34/60/144）→ **提前挂单买、与指数无关**」（behavior-blueprint:218）✓
    而"指数破位"他**只用止损/卖** ✓（exit-playbook:25「止损只在指数大级别破位」）✓
    量化 ✓（§9.493）：**破位期的低吸明显更好**（T+5 均值 +4.07% vs +0.81%、中位 +3.09% vs +0.31%、
    **左尾 1.1% vs 4.0%**）✓ ⇒ ⇒ 原先"破位不买"**与数据相反** ✓
    开关 ✓：`WOLF_BUY_IGNORE_INDEX_BREAK`（**默认 1 ＝ 买侧忽略** ✓，按用户要求默认开 ✓；
              置 0 ＝ 恢复旧行为 ✓）。⚠️ **卖侧的指数破位止损不受影响** ✓
    """
    if str(os.getenv("WOLF_BUY_IGNORE_INDEX_BREAK", "1")).strip().lower() in ("1", "true", "yes", "on"):
        return False, "买侧忽略指数破位(WOLF_BUY_IGNORE_INDEX_BREAK=1；§9.494 ✓ 买与指数无关 ✓)"
    if not INDEX_NEW_BUY_ON:
        return False, ""
    try:
        _b, _w = _index_broken(day)
        if _b:
            _STATS["rej_index"] += 1
            return True, _w + " ⇒ 不新开新票（持仓做T照旧）"
        return False, ""
    except Exception:
        _STATS["err"] += 1
        return False, ""


def _record_member_reject(symbol: str, theme: str, concepts: List[str], why: str) -> None:
    """把 dsh=否 的标的落成复核清单（`member_reject_<当日>.jsonl`），供人工抽查/维护白名单。

    dsh 主营判定是"数据缺口下的代理"（语料要的是主营构成），代理就会误伤 ⇒ 必须留痕。
    任何异常都不抛（fail-open，不影响布腿）。
    """
    try:
        import json as _json
        from datetime import datetime as _dt
        _data = os.getenv("DATA_DIR") or "/app/data"
        _d8 = _dt.now().strftime("%Y%m%d")
        _p = os.path.join(_data, "member_reject_%s.jsonl" % _d8)
        if not os.path.isdir(_data):
            return
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({"date": _d8, "symbol": symbol, "theme": theme,
                                  "concepts": concepts[:12], "dsh": why},
                                 ensure_ascii=False) + "\n")
    except Exception:
        pass


def _concepts_of(symbol: str) -> List[str]:
    """该票自身概念（供 dsh 主营判定用）。取数失败 → []（fail-open）。"""
    try:
        import psycopg2
        s = str(symbol).upper()
        code = ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
        conn = psycopg2.connect(os.getenv("DATABASE_URL", ""), connect_timeout=4)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout=4000")
            cur.execute("SELECT concept_name FROM stock_concept_map WHERE ts_code=%s", (code,))
            return [str(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _prewarm_workers() -> int:
    """预热**单票**并发数（env `WOLF_MEMBER_PREWARM_WORKERS`，默认 **1**）。

    2026-09-20 用户拍板（有实测依据）：13101 是**串行/单通道**服务 —— 1 并发 2.1–3.1s、
    2 并发 2.9/6.0s、6 并发 = 一个 2.6s + 五个 9.1s（复测 13.0×5）。串行服务下并发**不涨吞吐**
    （恒定 ≈0.23–0.4 次/秒），只把队尾等待拉到 5×2.5–13s；而 `WOLF_MEMBER_LLM_TIMEOUT` 原为 8s
    ⇒ **结构上 5/6 必超时**；超时 = fail-open 放行 + 负缓存 ⇒ 主营校验（含 ④）**静默失效**。
    实测失败率：0105–0113 = 0–14%，0116 起崩到 92–98%。
    ⇒ 现在**主路径是批量**（`theme_member_batch`：一次判 20–40 只、请求数降一个数量级），
      单票只作**兜底**，因此默认 1 并发（串行）即可，队尾等待=单发时长（2–7s），20s 超时不会触发。
    """
    try:
        return max(1, int(os.getenv("WOLF_MEMBER_PREWARM_WORKERS", "1")))
    except Exception:
        return 1


def prefetch(pairs, workers: Optional[int] = None) -> int:
    """预热「主营/主类」判定缓存（pairs = [(symbol, theme), ...]）。返回预热条数。

    2026-09-19 提速：布腿器逐票串行问 dsh（每票 ~2s）⇒ 0106 的 08:18 步骤实测 465s、0128 曾 958s。
    2026-09-20 改造（用户：「能批量校验而不是每次只校验一个吗」）：
      ① **批量优先** —— `theme_member_batch.warm()` 按主题一次判 N 只（实测 40 只 10.0s，
         请求数 344/天 → 9–18/天，全程单请求串行 ⇒ 不再有队尾等待、也不会撞 405）；
      ② **单票兜底** —— 批量里解析不到/异常的票，再用 `theme_member_llm.prefetch` 串行补（默认 1 并发）；
      ③ 结论都写回**单票同一缓存** ⇒ `approve()` 与 stock_confirm_judge 逐票路径照旧命中。
    任何异常都吞掉（fail-open，不影响布腿）。
    """
    try:
        from theme_member_llm import prefetch as _pf, concepts_of  # noqa: F401
    except Exception:
        try:
            from theme_member_llm import prefetch as _pf
        except Exception:
            return 0
    try:
        from theme_main_class import concepts_of as _cof
    except Exception:
        _cof = None
    items = []
    for pr in (pairs or []):
        try:
            sym, th = pr[0], pr[1]
        except Exception:
            continue
        cs = _cof(str(sym)) if _cof else []
        items.append((str(sym), cs, str(th)))
    if workers is None:
        workers = _prewarm_workers()          # 未显式指定 ⇒ 读 env（默认 1，单票兜底用）

    # ① 批量优先（按主题分组，一批 20–40 只）
    _batched = set()
    _failed = set()          # 批量判定失败且已负缓存整批的票（fail-fast 时才非空）
    try:
        import theme_member_batch as _MB
        if _MB.enabled():
            _by_theme = {}
            for sym, cs, th in items:
                _by_theme.setdefault(th, []).append((sym, cs))
            for th, its in _by_theme.items():
                _st = _MB.warm(its, th)
                for _sym in (_st.get("failed_syms") or []):      # fail-fast：批量失败的票不再逐票兜底
                    _failed.add((str(_sym), th))
                if _st.get("calls") or _st.get("ok"):
                    # 该主题下已进缓存的（含主类定案）都算预热完成，余额走单票兜底
                    try:
                        from theme_member_llm import cached as _cached
                        for sym, cs in its:
                            if _cached(sym, cs, th) is not None:
                                _batched.add((sym, th))
                    except Exception:
                        pass
    except Exception:
        pass

    # ② 单票兜底：批量没覆盖到的
    _left = [it for it in items if (it[0], it[2]) not in _batched and (it[0], it[2]) not in _failed]
    if not _left:
        return len(items)
    return len(_batched) + int(_pf(_left, workers=workers) or 0)


def approve(symbol: str, theme: str, concepts: Optional[List[str]] = None, stage: str = "",
            board: Any = None) -> Tuple[bool, str]:
    """腿批准：① 主营校验(dsh) ② 可买门(theme_buyable) ③ 板块权限。全失败 fail-open。"""
    if not ON:
        return True, ""
    _STATS["calls"] += 1
    _t_call = time.time()
    # ⓪ 卫生过滤（退市 / ST / 北交所）——用户 2026-09-21「过滤里去掉退市，ST，北交所的票吧」。
    #   放**最前面**：纯代码判据（不调 LLM），而 ① 主营校验要走 dsh 串行隧道 ⇒ 先拦脏票能省下最贵的那一步。
    #   共享腿闸 ⇒ 所有布腿来源都被覆盖（实测回测腿里确实混进过 7 只北交所 + 3 只 ST）。
    #   as-of 正确：回测用 namechange 重建当日简称（不是今天的名字）；生产没 tag ⇒ 用当下快照（正确口径）。
    #   开关默认关（universe_clean.enabled()），失败 fail-open。
    _tp = time.time()
    try:
        import importlib as _ilUC
        _uc = _ilUC.import_module("universe_clean")
        if _uc.enabled():
            _ok3, _why3 = _uc.judge(str(symbol))
            if not _ok3:
                _tick("dirty", _tp)
                _STATS["rej_dirty"] = _STATS.get("rej_dirty", 0) + 1
                return False, "卫生过滤=%s" % _why3
    except Exception:
        _STATS["err"] += 1
    _tick("dirty", _tp)
    # ① 主营校验（dsh 无思考模式）
    _tp = time.time()
    try:
        from theme_member_llm import is_member as _is_member
        _cs = list(concepts) if concepts else _concepts_of(symbol)
        _ok, _why = _is_member(str(symbol), _cs, str(theme))
        if not _ok:
            _tick("member", _tp)
            _STATS["rej_member"] += 1
            _record_member_reject(str(symbol), str(theme), _cs, str(_why))
            # 措辞区分来源（2026-09-19）：确定性主类规则的判词（"主类:…"）不要写成 "dsh="，否则审计时
            # 会误以为是 LLM 判的（实测日志里出现过 "主营校验 dsh=主类:主类=…" 这种叠词）
            if str(_why).startswith("主类:"):
                return False, "主营/主类校验 %s（不属于 %s）" % (str(_why)[3:], theme)
            return False, "主营校验 dsh=%s（不属于 %s）" % (_why, theme)
    except Exception:
        _STATS["err"] += 1
    _tick("member", _tp)
    # ② 可买门
    _tp = time.time()
    try:
        from wolf_context import theme_buyable
        _ok2, _why2 = theme_buyable(str(theme))
        if not _ok2:
            _tick("gate", _tp)
            _STATS["rej_gate"] += 1
            return False, "可买门=%s" % (str(_why2)[:120],)
    except Exception:
        _STATS["err"] += 1
    _tick("gate", _tp)
    # ③ 板块权限
    _tp = time.time()
    try:
        if callable(board) and not board(str(symbol)):
            _tick("board", _tp)
            _STATS["rej_board"] += 1
            return False, "板块权限不通过"
    except Exception:
        _STATS["err"] += 1
    _tick("board", _tp)
    # ④ 个股破位/急杀（语料「不接急杀」；仅档位闸打开时生效）
    _tp = time.time()
    if TIER_GATE_ON:
        try:
            _bk, _bw = symbol_broken(str(symbol))
            # ★ 账本 §9.512 ✓（用户「改成逐日重判」✓）：**「急杀式破前低」不再一票否决整条腿** ✓
            #   为什么 ✓（§9.511 实测 ✓）：该判据看的是 **cut 日（例 0226 量比 1.06 未缩量 ✗）**，
            #     而**信号在后续**：0227 = **缩量破前低（量比 0.86 ✓）**、
            #     0302 盘中 09:30 = **缩量回踩前低（量比 0.41 ✓✓）** ⇒ ⇒ 整条腿被提前划掉 ⇒ 连买点一起丢 ✗
            #   语义 ✓：**该日不买即可，不该否掉这条腿** ✓ —— 由**盘中条件（254/255）逐日重判** ✓
            #   开关 ✓：`WOLF_DIP_BREAKLOW_NODROP`（**默认 1** ✓，置 0 ＝ 恢复一票否决 ✓）
            try:
                if ("急杀式破前低" in str(_bw)
                        and str(os.getenv("WOLF_DIP_BREAKLOW_NODROP", "1")).strip().lower() in ("1", "true", "yes", "on")):
                    print("[leg_gate] %s 破前低未缩量 ⇒ **不否决整条腿**（逐日重判 ✓ §9.512）：%s"
                          % (symbol, str(_bw)[:90]), flush=True)
                    _bk, _bw = False, ""
            except Exception:
                pass
            if _bk:
                _tick("break_index", _tp)
                _STATS["rej_break"] += 1
                return False, _bw
        except Exception:
            _STATS["err"] += 1
    _tick("break_index", _tp)
    if _SLOW_LOG and (time.time() - _t_call) > 3.0:
        print("[leg_gate] 慢调用 %.1fs symbol=%s theme=%s 分相=%s"
              % (time.time() - _t_call, symbol, theme,
                 {k: round(v, 1) for k, v in _TIMING.items()}), flush=True)
    return True, ""
