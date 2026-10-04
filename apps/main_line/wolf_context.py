# -*- coding: utf-8 -*-
"""wolf_context.py — 狼大"分语境"闸门（P1-3, 2026-09-10; 2026-09-10 晚 **重大更正**）

## 用途
判定 253（指数 5min 急杀 → 低吸）当前**是否允许**。

狼大语义（分语境，非时钟窗）:
    筑底 / 上行  → 急杀可以接
    未筑底（下跌中 / 诱多反弹 / 最后一跌）→ 不该在急杀那一刻接

狼大原话（检索脚本 .dsh-tmp/wolfbt/gitwork/wolf_evidence*.py）:
  · 2025-06-05「要么继续震荡，但是**主线板块筑底行情**…**急杀可以买，缓跌不买**」
  · 2025-07-28「现在不是大盘跳水要不要出，而是**跳水了你高位减仓的钱敢不敢买**才对」
  · 2026-05-15「只要磨出底部结构 就是抄底迹象…**肯定不是急杀的时候买啊**」
  · 2026-01-13「说的是**主线题材 题材 题材**」

## ⚠️ 更正说明（本文件第一版做错了参照系）
第一版（commit 16a68aa）**只用大盘浪**（`wave_state.json` = 上证指数 000001.SH）判语境。
这是错的 —— 本项目对此已有明确原则, 见 `backend/app/services/trade_graph.py:675`:

    ⚠️ **主线自身浪型（重要: 主题浪, 不同于上方大盘 wave_context）**：
    → 该主线为主升 3 浪运行中(已确认), **不因大盘 t_only(4-2) 一刀切禁建**；
      按回调低吸模式(254 dip_prev_low/253)在其回调位建底仓, 不追高。

而 2026-09-10 当时大盘恰为 `d4/4-2/t_only` → 第一版会把**所有 253 一刀切禁掉**, 正是该注释警告之事。
且狼大 2025-06-05 说的也是"**主线板块**筑底行情", 参照系是板块而非大盘。

## 正确参照系：两个浪，分工不同
  1. **主题浪（主判据）** —— 决定"这个方向的急杀能不能接"。
     数据源：`trend_confirm_<date>_long.json` 的 `themes[*].track_a.stage`（机读, 优于解析自由文本），
             辅以 `mainline_gate_<date>.json` 的 `verdict`。
  2. **大盘浪（仅系统性护栏）** —— 只拦"真正系统性下跌", **不参与方向判定**。
     明确**不拦**：`4-2`（B反）/`t_only`/`4-4` —— 依上述项目原则。
     仅拦：level=down, 或 sub_level ∈ {C杀, 衰竭浪, 双头/M顶, 4-5, 失败5}（狼大: 最后一跌/防御等企稳）。

## 环境变量
  WOLF_253_CONTEXT=1            启用本闸门（**默认 1 = 启用**; 置 0 关闭）
  WOLF_253_CONTEXT_UNKNOWN=allow  主题浪数据缺失时放行（默认 allow —— 见下方 fail 策略）
  WOLF_253_CONTEXT_LOG=1        打印放行原因

## fail 策略（与第一版相反，此处有意为之）
  · 主题浪数据**缺失/不可读** → **放行**（allow）。理由：数据缺失是运维问题，不应把整条 253 买路封死；
    且 253 另有 regime GATE / 网关硬闸门等多层把关。
  · 大盘处于系统性下跌 → 拦（有明确狼大依据：最后一跌/防御等企稳）。
"""
import json
import os


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


DATA = os.environ.get("DATA_DIR", "/app/data")

# ── 主题浪：trend_confirm 的 track_a.stage 语义（见 trend_confirm.py TREND_CFG / 分层）──
# confirmed     = 主升/筑底结构成立（低点抬高+2浪不破前低+再创新高）→ 允许
# suspect       = 创新高但2浪形态不全（疑似V反/平台），等结构确认      → 不允许
# not_confirmed = 结构未确认                                        → 不允许
# window_limited= 历史不足，不硬判                                   → 不允许（宁可不接）
THEME_STAGE_ALLOW = {"confirmed"}

# ── 语料档位（2026-09-19 用户拍板）：把"结构未确认 ⇒ 硬拦"改成"档位" ──
# 语料依据：狼大 2026-01-17「主升趋势就75%以上…调整就50% 有风险就30 下跌趋势就不做」+「不接急杀」；
#   语料里**没有**"主题结构确认才买"这条硬闸。
# 实测（data/_bt_m56 0202→0331，按 trade id 去重）：二元硬闸把半导体/AI 主题的腿 100% 拦掉
#   （221 条 = 127 AI + 94 半导体，占被拦腿 60%），平均仓位只有 13~20%、现金 85% ⇒ 上涨吃不满。
# 档位口径：confirmed → 1.0（满档）；suspect/not_confirmed/window_limited → 0.5（半档）；
#   个股破位/急杀 → 0（不买，由 leg_gate 第 ④ 步按个股日线判）。
# 开关 WOLF_THEME_TIER_GATE：库内默认关（生产逐位不变），回测（BT_ASOF_FETCH）默认开。
TIER_GATE_ON = str(os.getenv("WOLF_THEME_TIER_GATE",
                             "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
THEME_STAGE_TIER = {"confirmed": 1.0, "suspect": 0.5, "not_confirmed": 0.5, "window_limited": 0.5}

# ── as-of 文件过滤（2026-09-19）──
# 背景 bug：本模块 `_latest()` 取 DATA 下**日期字符串最大**的文件，而回放沙箱每个天目录都带
#   repo 的 9 月桩（trend_confirm_20260904.json / theme_volfund_shadow_20260915.json），
#   比回放日"未来" ⇒ 闸读的是 9 月那份（未来函数）。打开本开关后跳过晚于"当日"的文件。
# 库内默认关（生产无未来文件 ⇒ 行为逐位不变），回测默认开。
ASOF_FILE_FILTER = str(os.getenv("WOLF_ASOF_FILE_FILTER",
                                 "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
_ASOF_SKIP_SEEN = {}

# ── 大盘浪：仅"真正系统性下跌"才拦（不参与方向判定）──
SYSTEMIC_BLOCK_SUB = {"C杀", "衰竭浪", "双头/M顶", "4-5", "失败5"}
SYSTEMIC_BLOCK_LEVEL = {"down"}

# ── ③ 指数大级别止损（2026-08-27 狼大原话）: 他说"只看指数大级别"→ 判据只取 level ──
INDEX_STOP_LEVELS = {"down"}


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _today8():
    """钉住的"当日"（回放里被 bt_run_pinned 偏移到 as-of 当天）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


def _latest(prefix):
    """取 DATA 下 <prefix><YYYYMMDD>[*].json 中日期最大者（`WOLF_ASOF_FILE_FILTER=1` 时跳过未来文件）。

    注意命名不统一：`mainline_gate_<date>.json` 与 `trend_confirm_<date>_long.json`
    （后者带 `_long` 后缀）→ 不能按"固定切片长度"取日期，须正则抓前 8 位数字。
    2026-09-19：加 as-of 过滤（否则会读到沙箱里 repo 的 9 月桩，见文件头注释）。
    """
    import glob
    import re
    best, bd = None, ""
    t8 = _today8() if ASOF_FILE_FILTER else ""
    skipped = []
    for f in glob.glob(os.path.join(DATA, prefix + "*.json")):
        m = re.match(r"(\d{8})", os.path.basename(f)[len(prefix):])
        if not m:
            continue
        if t8 and m.group(1) > t8:
            skipped.append(os.path.basename(f))
            continue
        if m.group(1) > bd:
            bd, best = m.group(1), f
    if skipped and _ASOF_SKIP_SEEN.get(prefix) != tuple(sorted(skipped)):
        _ASOF_SKIP_SEEN[prefix] = tuple(sorted(skipped))
        print("[wolf_context] as-of 过滤：跳过未来文件 %s（当日=%s，%d 个：%s）"
              % (prefix, t8, len(skipped), ", ".join(sorted(skipped)[:4])), flush=True)
    return best, bd


def load_wave():
    """大盘浪（上证指数）。"""
    return _load("wave_state.json")


def theme_of_symbol(symbol):
    """symbol(如 SH600001/SZ000001/600001.SH) → 主题名；取不到返回 None。

    优先级：stock_confirm_result.json（生产含 theme 字段）
          → stock_pool.db 的 stock_concept_map × THEME_CONCEPTS 反查（离线可用）。
    """
    if not symbol:
        return None
    s = str(symbol).strip().upper()
    ts = s[2:] + ("." + s[:2] if s[:2] in ("SH", "SZ", "BJ") else "") if len(s) >= 8 else s
    # 1) 生产 stock_confirm_result（子概念 → theme + stocks[].code）
    sc = _load("stock_confirm_result.json")
    for cname, v in (sc or {}).items():
        if not isinstance(v, dict):
            continue
        th = v.get("theme")
        if not th:
            continue
        for st in (v.get("stocks") or []):
            if str(st.get("code") or "").upper() in (ts, s):
                return th
    # 2) 离线反查：stock_pool.db + THEME_CONCEPTS
    try:
        import sys as _s
        import sqlite3
        for p in (os.getenv("STOCK_POOL_DB"),
                  os.path.join(DATA, "stock_pool.db"),
                  os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "apps", "paper-trading", "data", "stock_pool.db")):
            if not p or not os.path.exists(p):
                continue
            con = sqlite3.connect(p)
            rows = [r[0] for r in con.execute(
                "SELECT concept_name FROM stock_concept_map WHERE ts_code=?", (ts,))]
            con.close()
            if not rows:
                continue
            am = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "apps", "main_line")
            if am not in _s.path:
                _s.path.insert(0, am)
            from fusion_mainline import THEME_CONCEPTS
            cons = set(rows)
            for th, names in THEME_CONCEPTS.items():
                if cons & set(names):
                    return th
    except Exception as _e_sil1:
        _silent_alert("wolf_context.py:181", _e_sil1)
    return None


def theme_structure(theme):
    """主题浪状态 → dict(verdict, gate, stage, wave_text)。数据缺失返回 {}。"""
    if not theme:
        return {}
    out = {}
    gf, gd = _latest("mainline_gate_")
    if gf:
        g = _load(os.path.basename(gf))
        for r in (g.get("rows") or []):
            if str(r.get("theme")) == theme:
                out.update({"verdict": r.get("verdict"), "gate": r.get("gate"), "gate_date": gd})
                break
    tf, td = _latest("trend_confirm_")
    if tf:
        t = _load(os.path.basename(tf))
        for r in (t.get("themes") or []):
            if str(r.get("theme")) == theme:
                ta = r.get("track_a") or {}
                out.update({"stage": ta.get("stage"), "new_high": ta.get("new_high"),
                            "track_date": td, "track_b_ratio": (r.get("track_b") or {}).get("ratio")})
                break
    return out


# ── P2-2 主题资金危险（与 P1-3 的边界: P1-3 管结构, 本段管资金）──
_CH_CACHE = {"mtime": None, "by_name": None}


def _concept_hist_by_name():
    """concept_hist.json(约 8MB) 按 name 建索引, 进程内缓存(按 mtime 失效)。"""
    p = os.path.join(DATA, "concept_hist.json")
    try:
        mt = os.path.getmtime(p)
    except Exception:
        return {}
    if _CH_CACHE["mtime"] == mt and _CH_CACHE["by_name"] is not None:
        return _CH_CACHE["by_name"]
    try:
        h = _load("concept_hist.json")
        by = {}
        for v in (h or {}).values():
            if isinstance(v, dict) and v.get("name"):
                by[str(v["name"])] = v
        _CH_CACHE["mtime"] = mt
        _CH_CACHE["by_name"] = by
        return by
    except Exception:
        return {}


def _fund_data_missing(theme, why):
    """**资金数据读取失败**时的处置（2026-09-15 用户拍板）：默认停止买入 + QQ 通知。

    `WOLF_FUND_GATE_FAILCLOSED=0` → 恢复旧的 fail-open（视为无危险、不拦）。
    """
    try:
        import sys as _s3
        _p3 = os.path.dirname(os.path.abspath(__file__))
        if _p3 not in _s3.path:
            _s3.path.insert(0, _p3)
        from wolf_theme_vol_fund import failclosed_enabled as _fc, notify_once as _no
    except Exception:
        return False, "资金数据不可用(%s) → 放行（策略模块不可用）" % why
    if _fc():
        _no("theme_fund:%s" % theme, "[资金门] 主题[%s] 资金数据读取失败 → 已停止买入：%s" % (theme, why))
        return True, "板块资金数据读取失败 → 停止买入（fail-closed，2026-09-15 用户指示）: %s" % why
    return False, "资金数据不可用(%s) → 放行（WOLF_FUND_GATE_FAILCLOSED=0）" % why


def theme_fund_danger(theme, days=None):
    """主题**资金**危险判定（P2-2）→ (danger: bool, reason)。

    狼大 2025-03-06「你首先得判断现在大盘行情没有危险 **板块没有危险** 那就可以做」。
    信号 = 该主题主力净流入（concept_hist.net_amount，主题内概念取均值）**连续 N 日全部为负**。

    与 P1-3 的边界（避免造重复的门）：
      · P1-3 `theme_structure` 判**结构**（主题浪 track_a.stage）；
      · 本函数判**资金**（主力净流入的持续性）。
    两者由 `theme_buyable()` 合成为一个入口。

    fail-open：数据缺失 / 主题内概念样本不足 / 序列疑似前值填充（近 N 日全同）→ 视为**无危险**，
    不拦（数据问题不应封死买路，与 P1-3 的 fail 策略一致）。
    """
    # 2026-09-15 参数对齐（P2）：他的同族口径是 **5 日**（2025-06-16「资金没有**5日**连续流出的」），
    # 原默认 3 是自设（参数总账 §3-11）。调用方若显式传 days 不受影响；env 可覆写。
    # 2026-09-21（用户拍板 B 项）：补**相对口径** —— 2025-04-15 条件1
    #   「看前一日流入前5和流出前3的板块和个股…避开三日都是流出前3的板块个股」。
    #   开关 WOLF_FUND_GATE_MODE=abs（默认 ⇒ 本函数以下行为**逐位不变**）| rank。
    #   为什么需要：绝对口径（近 N 日净流入全为负）在普跌期会把几乎所有主题判危险 ——
    #   实测 T1 的 5,560 次趋势腿触发里 99.9% 被它挡掉。
    if str(os.getenv("WOLF_FUND_GATE_MODE", "abs")).strip().lower() in ("rank", "rank3", "rel"):
        try:
            from wolf_theme_vol_fund import theme_rank_danger as _trd
            return _trd(theme)
        except Exception as _e_rank:
            return False, "相对口径不可用(%s) → 放行" % str(_e_rank)[:60]
    n = int(days if days is not None else os.getenv("WOLF_THEME_FUND_DAYS", "5"))
    try:
        import sys as _s
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in _s.path:
            _s.path.insert(0, _p)
        from fusion_mainline import THEME_CONCEPTS
        cons = THEME_CONCEPTS.get(theme) or []
    except Exception:
        return False, "主题概念表不可用 → 放行"
    # 2026-09-15（用户指示）：
    #  ① 数据源先走 **relay 日频资金流**（`wolf_theme_vol_fund.theme_nets`，与 P1 同源；生产实测 concept_hist
    #     的 net_amount 是前值填充、且主题内可用序列长度为 0 → 这条门实际上天天放行）；
    #  ② 两边都拿不到 → 按 `WOLF_FUND_GATE_FAILCLOSED`（默认 1）**停止买入** + QQ 通知（置 0 恢复放行）。
    _nets = []
    try:
        import sys as _s2
        _p2 = os.path.dirname(os.path.abspath(__file__))
        if _p2 not in _s2.path:
            _s2.path.insert(0, _p2)
        from wolf_theme_vol_fund import theme_nets as _tn
        # ⚠️ 2026-09-19：不传 as_of ⇒ 旧行为拿库里"最近 n 个交易日"（回放里=未来 9 月数据，jan9 铁证）；
        #   现由 wolf_theme_vol_fund 在 WOLF_FUND_ASOF 打开时锚到**前一交易日**（用户口径：不要用当日的）。
        _nets = [x for x in (_tn(theme, days=n + 2) or []) if x is not None]
    except Exception as _e_nets:
        print("[wolf_context] theme_fund relay 序列不可用: %s" % str(_e_nets)[:80])
    if len(_nets) >= n and len(set(_nets[-n:])) > 1:
        _avg = _nets[-n:]
        if all(x < 0 for x in _avg):
            return True, "板块资金危险: 主题[%s] 主力净流入连续 %d 日为负(均值 %s 亿)" % (
                theme, n, ", ".join("%.2f" % (x / 1e4) for x in _avg))
        return False, "板块资金正常(近%d日均值 %s 亿)" % (n, ", ".join("%.2f" % (x / 1e4) for x in _avg))

    by = _concept_hist_by_name()
    if not by:
        return _fund_data_missing(theme, "concept_hist 不可用 且 relay 资金序列不足(%d)" % len(_nets))
    series = []
    for c in cons:
        v = by.get(c)
        if not isinstance(v, dict):
            continue
        na = [x for x in (v.get("net_amount") or []) if x is not None]
        if len(na) < max(2, n):
            continue
        tail = na[-n:]
        if len(set(tail)) == 1:          # 疑似前值填充(停牌/无数据) → 该概念不计
            continue
        series.append(tail)
    if len(series) < 2:
        return _fund_data_missing(theme, "主题内可用资金序列不足(%d)，relay 序列也不足(%d)"
                                  % (len(series), len(_nets)))
    avg = [sum(s[i] for s in series) / len(series) for i in range(n)]
    if all(x < 0 for x in avg):
        return True, "板块资金危险: 主题[%s] 主力净流入连续 %d 日为负(均值 %s 亿)" % (
            theme, n, ", ".join("%.2f" % (x / 1e8) for x in avg))
    return False, "板块资金正常(近%d日均值 %s 亿)" % (n, ", ".join("%.2f" % (x / 1e8) for x in avg))


def slow_decline(prev_days, days=None, min_drop_pct=None, max_drop_pct=None, flush_day_pct=None):
    """**缓跌通道**判定（P2-3, 2026-09-10）→ (is_slow_decline: bool, reason)。

    狼大 2025-06-05「要么带量突破 空翻多…要么继续震荡，但是主线板块筑底行情，那就保持30%-50%...
    **急杀可以买，缓跌不买**」；2025-07-28「跳水了你高位减仓的钱敢不敢买才对」。
    → 语义: **急跌(有单日急杀)可接; 缩量阴跌式的"缓跌"不接**（缓跌会一路磨下去，接了没反弹）。

    ⚠️ **阈值属系统自设**: 狼大只给了概念、未给数字。故以下阈值全部可配(env/参数),
       默认取保守侧, 并在下方逐条标注, 便于按回测再标定。

    判据（三条同时满足才算缓跌）:
      1. 窗口累计跌幅 ∈ [min_drop, max_drop] —— 有跌, 但不是"急跌"(超过 max 视为急杀, 反而可接);
      2. 窗口内**没有单日急杀**(无单日跌幅 ≥ flush_day_pct) —— 有急杀日则属"急杀", 不算缓跌;
      3. 量能萎缩 —— 近 2 日均量 < 前段均量 × shrink_ratio(默认 1.0, 即确实在缩量)。

    prev_days: {YYYYMMDD: {"close","high","low","vol"}}(t_monitor._prev_daily 的返回格式)。数据不足 → 不算缓跌(放行)。
    """
    n = int(days if days is not None else os.getenv("WOLF_SLOW_DECLINE_DAYS", "5"))
    min_drop = float(min_drop_pct if min_drop_pct is not None else os.getenv("WOLF_SLOW_DECLINE_MIN_PCT", "1.0"))
    max_drop = float(max_drop_pct if max_drop_pct is not None else os.getenv("WOLF_SLOW_DECLINE_MAX_PCT", "6.0"))
    flush = float(flush_day_pct if flush_day_pct is not None else os.getenv("WOLF_FLUSH_DAY_PCT", "-3.0"))
    try:
        shrink_ratio = float(os.getenv("WOLF_SLOW_DECLINE_SHRINK", "1.0"))
    except Exception:
        shrink_ratio = 1.0

    if not prev_days:
        return False, "无日线数据 → 不作为缓跌(放行)"
    rows = [prev_days[k] for k in sorted(prev_days)][-(n + 1):]
    if len(rows) < 3:
        return False, "日线不足(%d) → 不作为缓跌(放行)" % len(rows)
    closes = [float(r.get("close") or 0) for r in rows]
    if any(c <= 0 for c in closes):
        return False, "日线收盘异常 → 不作为缓跌(放行)"
    cum = (closes[-1] / closes[0] - 1) * 100.0
    # 1) 有跌但非急跌
    if not (-max_drop <= cum <= -min_drop):
        return False, "窗口累计 %.2f%% 不属缓跌区间(需 [-%.1f%%, -%.1f%%])" % (cum, max_drop, min_drop)
    # 2) 无单日急杀
    worst = 0.0
    for i in range(1, len(closes)):
        d = (closes[i] / closes[i - 1] - 1) * 100.0
        if d < worst:
            worst = d
    if worst <= flush:
        return False, "窗口内有单日急杀 %.2f%%(≤%.1f%%) → 属急杀非缓跌" % (worst, flush)
    # 3) 量能萎缩
    vols = [float(r.get("vol") or 0) for r in rows]
    if all(v > 0 for v in vols) and len(vols) >= 4:
        recent = sum(vols[-2:]) / 2.0
        prior = sum(vols[:-2]) / max(1, len(vols) - 2)
        if prior > 0 and recent >= prior * shrink_ratio:
            return False, "窗口内量能未萎缩(近2日均量 %.0f ≥ 前段 %.0f×%.2f) → 非缓跌" % (
                recent, prior, shrink_ratio)
    return True, "缓跌通道: 窗口累计 %.2f%%(无急杀日, 最差单日 %.2f%%), 量能萎缩 → 不接(狼大: 缓跌不买)" % (
        cum, worst)


# ── B：主题资格「结构未破 ∧ 指数未破位 ⇒ 不撤资格」（2026-09-19 用户拍板）────────────
# 背景（用户：狼大一月也在做半导体，为什么他确认了）：他的「确认」是**波浪/结构成立且趋势没破**
#   （2026-01-26「指数没破位就没有悲观的理由」、2026-01-27「趋势不破就不看空」；2025-12-30「下周开始做半导体」），
#   而我们的判据是 trend_confirm 的「近 5 日创 60 日新高」⇒ 1 月半导体高位回踩不创新高，天天 not_confirmed，
#   于是我们只能半档零星做。B 即把资格口径改回他的：**未创新低（结构未破）∧ 指数未破位 ⇒ 资格不撤**。
# 开关 WOLF_THEME_QUALIFY_STRUCT：库内默认关（生产逐位不变）、回测开。
QUALIFY_STRUCT_ON = str(os.getenv("WOLF_THEME_QUALIFY_STRUCT",
                                  "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
_QUAL_CACHE = {}


def _theme_index_series(theme):
    """主题等权合成指数（与 trend_confirm.theme_index 同口径，吃 concept_hist.json）。"""
    try:
        names = None
        try:
            from fusion_mainline import THEME_CONCEPTS
            names = THEME_CONCEPTS.get(theme) or []
        except Exception:
            names = []
        by = _concept_hist_by_name()
        series = [dict(by[n], name=n) for n in names if n in by]
        if not series:
            return None
        import sys as _s3
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in _s3.path:
            _s3.path.insert(0, _here)
        from trend_confirm import theme_index
        idx, _dates = theme_index(series, names)
        return idx
    except Exception:
        return None


def _index_not_broken(day=""):
    """指数未破位（复用 t_index_break.index_breakdown：收盘跌破 MA60/144/200 中 ≥2 根 = 破位）。"""
    try:
        import sys as _s4
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _be = os.path.join(_root, "backend")
        if _be not in _s4.path:
            _s4.path.insert(0, _be)
        from app.services.t_index_break import index_breakdown
        from datetime import datetime as _dt2
        j = index_breakdown(str(day or "").replace("-", "") or _dt2.now().strftime("%Y%m%d"))
        if j.get("broken"):
            return False, "指数破位(跌破 %s)" % (j.get("below"),)
        return True, "指数未破位"
    except Exception as e:
        return True, "指数破位判定失败(fail-open): %s" % str(e)[:40]


def theme_qualify_struct(theme, fresh_days=20, look_days=60, day=""):
    """B：主题资格不撤 = **未创新低（结构未破）∧ 指数未破位**。返回 (ok, why)。

    结构未破口径：主题等权指数「最近 fresh_days 日最低 ≥ 最近 look_days 日最低」（= 没跌破前低）。
    数据不足/异常 → (False, 原因)（fail-closed 到旧口径：仍按 stage 判，不会因此放行）。
    """
    if not QUALIFY_STRUCT_ON:
        return False, "B 关(WOLF_THEME_QUALIFY_STRUCT=0)"
    if not theme:
        return False, "主题未知"
    key = "%s|%d|%d|%s" % (theme, fresh_days, look_days, day)
    if key in _QUAL_CACHE:
        return _QUAL_CACHE[key]
    out = (False, "无法判定")
    try:
        idx = _theme_index_series(theme)
        if not idx or len(idx) < look_days:
            out = (False, "主题指数序列不足(%d)" % (len(idx or [])))
        else:
            # ⚠️ 2026-09-19 修：原写成「近 fresh 日最低 ≥ 近 look 日最低」，但 fresh 窗口是 look 窗口的
            #   子集 ⇒ 子集最小值恒 ≥ 全集最小值 ⇒ 该判据**永远为真**（结构判据形同虚设，只剩指数闸）。
            #   正确口径 = **近 fresh 日最低 vs 之前 (look-fresh) 日最低**：近期低点没跌穿更早的低点 ⇒ 未创新低。
            lo_fresh = min(idx[-fresh_days:])
            lo_prior = min(idx[-look_days:-fresh_days])
            if lo_fresh < lo_prior:
                out = (False, "结构已破：近 %d 日创新低(%.1f < 前 %d 日最低 %.1f)"
                       % (fresh_days, lo_fresh, look_days - fresh_days, lo_prior))
            else:
                ok_i, why_i = _index_not_broken(day)
                # ⚠️ 账本 §9.494 ✓（用户「直接改」✓）：**买侧忽略"指数破位"** ✓
                #   他的原话：「好票跌到事先画好的线（13/34/60/144）→ **提前挂单买、与指数无关**」✓
                #   量化（§9.493 ✓）：**破位期的低吸明显更好**（T+5 均值 +4.07% vs +0.81%、中位 +3.09% vs +0.31%、
                #   **左尾 1.1% vs 4.0%**）✓ ⇒ ⇒ "指数破位"只该用于**卖/止损** ✓
                #   开关 ✓：`WOLF_BUY_IGNORE_INDEX_BREAK`（**默认 1** ✓，置 0 ＝ 恢复旧口径 ✓）
                if str(os.getenv("WOLF_BUY_IGNORE_INDEX_BREAK", "1")).strip().lower() in ("1", "true", "yes", "on"):
                    ok_i, why_i = True, "买侧忽略指数破位 ✓(§9.494)"
                out = ((True, "资格不撤：未创新低(近%d日最低 %.1f ≥ 前%d日最低 %.1f) ∧ %s"
                        % (fresh_days, lo_fresh, look_days - fresh_days, lo_prior, why_i)) if ok_i
                       else (False, "结构未破但 %s" % why_i))
    except Exception as e:
        out = (False, "判定异常: %s" % str(e)[:50])
    _QUAL_CACHE[key] = out
    return out


def theme_tier_factor(theme, day=""):
    """主题档位系数（语料档位）：confirmed→1.0；suspect/not_confirmed/window_limited→0.5。

    数据缺失/异常 → 1.0（fail-open，维持"数据缺失不封死买路"的既有口径）。
    返回 (factor, reason)；`WOLF_THEME_TIER_GATE=0` 时恒为 1.0（库内默认，生产逐位不变）。
    """
    if not TIER_GATE_ON:
        return 1.0, "档位闸关(WOLF_THEME_TIER_GATE=0)"
    if not theme:
        return 1.0, "主题未知 → 满档(fail-open)"
    try:
        st = theme_structure(theme)
        stage = str(st.get("stage") or "")
        if not stage:
            return 1.0, "主题浪数据缺失 → 满档(fail-open)"
        if stage != "confirmed":
            _ok_b, _why_b = theme_qualify_struct(theme, day=day)
            if _ok_b:
                return 1.0, "主题[%s] stage=%s，但 %s ⇒ 仓位×1.0（B 资格不撤）" % (theme, stage, _why_b)
        f = float(THEME_STAGE_TIER.get(stage, 0.0))
        return f, "主题[%s] stage=%s ⇒ 仓位×%.1f" % (theme, stage, f)
    except Exception as e:
        return 1.0, "档位判定异常 → 满档(fail-open): %s" % str(e)[:60]


def theme_buyable(theme):
    """主题是否可买 = **结构**(P1-3) ∧ **资金**(P2-2)。**单一定义处**, 供 253 与布腿器共用。

    返回 (ok, reason)。结构未确认 → 拦; 结构确认但资金连续流出 → 拦。
    """
    if not theme:
        return True, "主题未知 → 放行(不封死买路)"
    st = theme_structure(theme)
    stage = str(st.get("stage") or "")
    if not stage:
        return True, "主题浪数据缺失(theme=%s) → 放行" % theme
    tier_note = ""
    if stage not in THEME_STAGE_ALLOW:
        # 档位闸打开时：不硬拦，改由"仓位系数"体现（语料档位；破位/急杀另由 leg_gate ④ 拦）；
        # **资金门（P2-2）照旧生效** —— 结构没确认只降档，不是无条件放行。
        _tier_ok = False
        if TIER_GATE_ON:
            _tf, _tfr = theme_tier_factor(theme)
            if _tf > 0:
                _tier_ok = True
                tier_note = " | 档位放行: %s" % _tfr
        if not _tier_ok:
            return False, "结构未确认: 主题[%s] stage=%s verdict=%s（不在急杀那一刻接/不新开）" % (
                theme, stage, st.get("verdict"))
    if os.getenv("WOLF_THEME_FUND", "1").strip() in ("0", "false", "no"):
        return True, "主题[%s] 结构已确认(stage=%s); 资金维度已关闭(WOLF_THEME_FUND=0)" % (theme, stage) + tier_note
    dg, dr = theme_fund_danger(theme)
    if dg:
        return False, dr
    # 2026-09-15 参数对齐（P1，**默认关**）：他称的「选板块的第一要素」
    #   2025-06-16「量能活跃(最近一周内至少2/3天数以上在10日量能以上)，资金没有5日连续流出的。」
    #   离线对照：我们的池内通过该门的子集后 5 日超额 +0.718%（n=177）vs 不通过 −0.080%（n=732）
    #   → 开启会改变行为（他的门 104/164 天非空，其余日子不布新腿），故默认 0，待拍板。
    try:
        from wolf_theme_vol_fund import (enabled as _vf_on, shadow_enabled as _vf_shadow,
                                         theme_volfund_ok as _vf_ok, shadow_record as _vf_rec)
        if _vf_on() or _vf_shadow():
            ok_vf, why_vf = _vf_ok(theme)
            if _vf_on() and not ok_vf:
                _vf_rec(theme, ok_vf, why_vf)
                return False, "选板块第一要素未过(2025-06-16「量能活跃+资金没有5日连续流出」): " + str(why_vf)
            if not _vf_on():
                _vf_rec(theme, ok_vf, why_vf)          # 影子：只记录
            dr = dr + " | " + ("[影子]" if not _vf_on() else "") + str(why_vf)
    except Exception as _e:
        print("[wolf_context] theme_volfund 检查异常: %s" % str(_e)[:80])
        try:
            import sys as _s4
            _p4 = os.path.dirname(os.path.abspath(__file__))
            if _p4 not in _s4.path:
                _s4.path.insert(0, _p4)
            from wolf_theme_vol_fund import failclosed_enabled as _fc4, notify_once as _no4
            if _fc4():
                _no4("theme_volfund_err:%s" % theme,
                     "[资金门] 主题[%s] 门检查异常 → 已停止买入：%s" % (theme, str(_e)[:80]))
                return False, ("选板块第一要素检查异常 → 停止买入（fail-closed，2026-09-15 用户指示）: %s"
                               % str(_e)[:100])
        except Exception as _e_sil2:
            _silent_alert("wolf_context.py:578", _e_sil2)
    return True, "主题[%s] 可买: stage=%s verdict=%s | %s%s" % (
        theme, stage, st.get("verdict"), dr, tier_note)


def index_level_stop(wave=None, today=None, max_stale_days=None):
    """**指数大级别止损**信号（狼大止损六层之③，2026-09-10, 依 2026-08-27 549楼原话）。

    狼大原话:
    「**只看指数大级别**如果不走大5浪而转为下跌1浪就止损 。。。。你们能不能看前面的啊。。」
    （2026-08-27，**就在本轮回测窗口内**）

    → 语义: 大4 调整之后本应走大5（主升末段）; 若指数**大级别转为下跌**而不是走大5, 则止损。
    → 他说"**只看指数大级别**" → 判据**只取 level**, 不掺 sub_level/operation（避免自造复合条件）。
       本仓 wave_agent 的 level 取值域为 d1/d2/d3/d4/d5/down → "转为下跌1浪" 即 **level == "down"**。

    **新鲜度护栏**: wave_state 由 08:10 产出; 若其 date 距 today 超过 max_stale_days(默认 3 自然日),
    视为数据过期 → **不触发**(避免拿过期浪型做清仓级动作), 并在 reason 中说明。

    返回 (should_stop: bool, reason: str)。
    """
    w = wave if wave is not None else load_wave()
    lv = str(w.get("level") or "").strip().lower()
    wd = str(w.get("date") or "").strip()
    if not lv:
        return False, "无指数浪型数据 → 不触发"
    # 新鲜度护栏
    try:
        n = int(max_stale_days if max_stale_days is not None else os.getenv("WOLF_INDEX_STOP_MAX_STALE_DAYS", "3"))
    except Exception:
        n = 3
    if today and wd and n >= 0:
        import datetime as _dt
        import re as _re2
        try:
            # ⚠️ 2026-09-14 修复：wave_state.json 的 date 是**带横杠**的（"2026-09-10"），
            # 原实现直接切字符串 wd[4:6] → "-0" → int("-0")=0 → date(2026,0,..) 抛异常 →
            # 被 except 吞掉 → **新鲜度护栏长期静默失效**（拿过期浪型做清仓级动作）。
            _w8 = _re2.sub(r"\D", "", wd)[:8]
            _t8 = _re2.sub(r"\D", "", str(today))[:8]
            if len(_w8) != 8 or len(_t8) != 8:
                raise ValueError("bad date")
            d1 = _dt.date(int(_w8[:4]), int(_w8[4:6]), int(_w8[6:8]))
            d2 = _dt.date(int(_t8[:4]), int(_t8[4:6]), int(_t8[6:8]))
            if (d2 - d1).days > n:
                return False, "指数浪型数据过期(wave_state.date=%s, 距今 %d 天 > %d) → 不触发" % (
                    wd, (d2 - d1).days, n)
        except Exception as _e_sil3:
            _silent_alert("wolf_context.py:626", _e_sil3)
    if lv in INDEX_STOP_LEVELS:
        return True, "指数大级别止损: level=%s（狼大2026-08-27: 不走大5浪而转为下跌1浪就止损, date=%s）" % (lv, wd or "?")
    return False, "指数级别=%s, 未转下跌 → 不触发" % lv


# ── ③层加宽（2026-09-14）：他说的"尾段/结束"在系统里主要是 d4 系列 + 顶态子浪，不只是 down ──
# 依据：关键点重放（jobs/check_wave_agent_turningpoints.py，28 条语料拐点声明/19 个日期）——
#   2026 年他 6 次"尾段/结束"→ 系统给出 d4/4-1·4-2·4-3 + defense/t_only；2022 年"反弹浪走完"→ d4/C杀、d4/4-5；
#   2021-01-21 → d3/3-5/exit。而原判据只认 level=='down' → 门槛比他的实际表达严得多（这就是"几乎不触发"的原因）。
INDEX_TOP_LEVELS = ("d4", "d5")                      # 顶态大级别（d4 大4回调 / d5 末段衰竭）
INDEX_TOP_SUBS = ("3-5", "4-1", "4-2", "4-3", "4-4", "4-5", "C杀", "双头", "衰竭", "失败5", "M顶")
INDEX_TOP_ACTION_CLEAR = ("down",)                   # 明确下跌 → 清仓级


def _top_sub_hit(sub, wave):
    """子浪是否命中顶态词表（含 d3 末段 3-5 与 4-x/C杀/双头/衰竭）。"""
    import re as _re
    sl = str(sub or "").strip()
    if not sl:
        return False
    for w in INDEX_TOP_SUBS:
        if w == "双头" or w == "衰竭":
            if w in sl:
                return True
        elif "M顶" in w:
            if "M顶" in sl or "双头" in sl:
                return True
        elif _re.match(r"^%s" % _re.escape(w), sl):
            return True
    return False


def index_top_state(wave=None, today=None, max_stale_days=None):
    """③层**加宽版**：指数顶态/下跌 → (action, why)，action ∈ {"clear", "reduce", None}。

    · `level == down`                        → **clear**（清的语义：原有清仓/收盘减半口径不变）
    · `level ∈ {d4, d5}`                     → **reduce**（顶态，减仓级）
    · `sub_level` 命中 {3-5, 4-x, C杀, 双头, 衰竭, 失败5} → **reduce**
    新鲜度护栏与 `index_level_stop` 完全一致（过期 → None）。开关 `WOLF_INDEX_TOP_WIDEN=0` 时
    退回旧口径（只认 down），调用方自行判断。
    """
    w = wave if wave is not None else load_wave()
    lv = str(w.get("level") or "").strip().lower()
    sl = str(w.get("sub_level") or "").strip()
    wd = str(w.get("date") or "").strip()
    if not lv:
        return None, "无指数浪型数据 → 不动作"
    try:
        n = int(max_stale_days if max_stale_days is not None else os.getenv("WOLF_INDEX_STOP_MAX_STALE_DAYS", "3"))
    except Exception:
        n = 3
    if today and wd and n >= 0:
        import datetime as _dt
        import re as _re2
        try:
            # ⚠️ 2026-09-14 修复：wave_state.json 的 date 是**带横杠**的（"2026-09-10"），
            # 原实现直接切字符串 wd[4:6] → "-0" → int("-0")=0 → date(2026,0,..) 抛异常 →
            # 被 except 吞掉 → **新鲜度护栏长期静默失效**（拿过期浪型做清仓级动作）。
            _w8 = _re2.sub(r"\D", "", wd)[:8]
            _t8 = _re2.sub(r"\D", "", str(today))[:8]
            if len(_w8) != 8 or len(_t8) != 8:
                raise ValueError("bad date")
            d1 = _dt.date(int(_w8[:4]), int(_w8[4:6]), int(_w8[6:8]))
            d2 = _dt.date(int(_t8[:4]), int(_t8[4:6]), int(_t8[6:8]))
            if (d2 - d1).days > n:
                return None, "指数浪型数据过期(wave_state.date=%s, 距今 %d 天 > %d) → 不动作" % (wd, (d2 - d1).days, n)
        except Exception as _e_sil4:
            _silent_alert("wolf_context.py:694", _e_sil4)
    if lv in INDEX_TOP_ACTION_CLEAR:
        return "clear", "指数大级别转下跌: level=%s（狼大2026-08-27「不走大5浪而转为下跌1浪就止损」, date=%s）" % (lv, wd or "?")
    if lv in INDEX_TOP_LEVELS:
        return "reduce", "指数顶态: level=%s/%s（狼大「尾段/结束」口径, 2026-09-14 关键点重放校准, date=%s）" % (lv, sl or "-", wd or "?")
    if _top_sub_hit(sl, w):
        return "reduce", "指数顶态子浪: sub_level=%s（level=%s, date=%s）" % (sl, lv, wd or "?")
    return None, "指数 level=%s/%s 未达顶态 → 不动作" % (lv, sl or "-")


def systemic_block(wave=None):
    """大盘是否处于"真正系统性下跌"（仅此情形拦 253）。返回 (blocked, reason)。"""
    w = wave if wave is not None else load_wave()
    lv = str(w.get("level") or "").strip()
    sl = str(w.get("sub_level") or "").strip()
    if lv in SYSTEMIC_BLOCK_LEVEL:
        return True, "大盘 level=%s（下跌段）" % lv
    if sl in SYSTEMIC_BLOCK_SUB:
        return True, "大盘 sub_level=%s（狼大: 最后一跌/防御等企稳）" % sl
    return False, ""


def m5dump_allowed(symbol=None, wave=None, prev_days=None):
    """253 是否允许 → (bool, reason)。

    判定：①大盘系统性护栏（仅真系统性下跌才拦）→ ②主题浪结构 ∧ 板块资金（theme_buyable）
          → ③**缓跌通道**(P2-3, 需传入 prev_days 才能判) → ④数据缺失放行。
    """
    if os.getenv("WOLF_253_CONTEXT", "1").strip() in ("0", "false", "no"):
        return True, "闸门关闭(WOLF_253_CONTEXT=0)"

    sb, sreason = systemic_block(wave)
    if sb:
        return False, "语境禁止（系统性风险）: %s" % sreason

    # P2-3(2026-09-10): 缓跌不买 —— 狼大 2025-06-05「急杀可以买，**缓跌不买**」。
    # 只在调用方提供了日线(prev_days)时才判; 缺失 → 跳过(放行), 不因缺数据封死买路。
    if prev_days and os.getenv("WOLF_SLOW_DECLINE", "1").strip() not in ("0", "false", "no"):
        _sd, _sdr = slow_decline(prev_days)
        if _sd:
            return False, _sdr

    theme = theme_of_symbol(symbol)
    st = theme_structure(theme)
    if not st or not st.get("stage"):
        if os.getenv("WOLF_253_CONTEXT_UNKNOWN", "allow").strip().lower() == "allow":
            return True, "主题浪数据缺失(theme=%s) → 放行" % theme
        return False, "主题浪数据缺失(theme=%s) → fail-closed" % theme

    stage = st.get("stage")
    verdict = st.get("verdict")
    if stage not in THEME_STAGE_ALLOW:
        return False, "语境禁止: 主题[%s] 结构 stage=%s verdict=%s（未筑底, 不在急杀那一刻接）" % (
            theme, stage, verdict)
    # 结构已确认 → 再叠加 P2-2 的资金危险维度(单一定义处: theme_buyable = 结构 ∧ 资金)
    return theme_buyable(theme)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else None
    th = theme_of_symbol(sym)
    print(json.dumps({"symbol": sym, "theme": th, "theme_structure": theme_structure(th),
                      "wave": {k: load_wave().get(k) for k in ("level", "sub_level", "operation")},
                      "m5dump_allowed": m5dump_allowed(sym)}, ensure_ascii=False, indent=1))
