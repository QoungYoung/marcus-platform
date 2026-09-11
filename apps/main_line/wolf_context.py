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

DATA = os.environ.get("DATA_DIR", "/app/data")

# ── 主题浪：trend_confirm 的 track_a.stage 语义（见 trend_confirm.py TREND_CFG / 分层）──
# confirmed     = 主升/筑底结构成立（低点抬高+2浪不破前低+再创新高）→ 允许
# suspect       = 创新高但2浪形态不全（疑似V反/平台），等结构确认      → 不允许
# not_confirmed = 结构未确认                                        → 不允许
# window_limited= 历史不足，不硬判                                   → 不允许（宁可不接）
THEME_STAGE_ALLOW = {"confirmed"}

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


def _latest(prefix):
    """取 DATA 下 <prefix><YYYYMMDD>[*].json 中日期最大者。

    注意命名不统一：`mainline_gate_<date>.json` 与 `trend_confirm_<date>_long.json`
    （后者带 `_long` 后缀）→ 不能按"固定切片长度"取日期，须正则抓前 8 位数字。
    """
    import glob
    import re
    best, bd = None, ""
    for f in glob.glob(os.path.join(DATA, prefix + "*.json")):
        m = re.match(r"(\d{8})", os.path.basename(f)[len(prefix):])
        if m and m.group(1) > bd:
            bd, best = m.group(1), f
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
    except Exception:
        pass
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
    n = int(days if days is not None else os.getenv("WOLF_THEME_FUND_DAYS", "3"))
    try:
        import sys as _s
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in _s.path:
            _s.path.insert(0, _p)
        from fusion_mainline import THEME_CONCEPTS
        cons = THEME_CONCEPTS.get(theme) or []
    except Exception:
        return False, "主题概念表不可用 → 放行"
    by = _concept_hist_by_name()
    if not by:
        return False, "concept_hist 不可用 → 放行"
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
        return False, "主题内可用资金序列不足(%d) → 放行" % len(series)
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
    if stage not in THEME_STAGE_ALLOW:
        return False, "结构未确认: 主题[%s] stage=%s verdict=%s（不在急杀那一刻接/不新开）" % (
            theme, stage, st.get("verdict"))
    if os.getenv("WOLF_THEME_FUND", "1").strip() in ("0", "false", "no"):
        return True, "主题[%s] 结构已确认(stage=%s); 资金维度已关闭(WOLF_THEME_FUND=0)" % (theme, stage)
    dg, dr = theme_fund_danger(theme)
    if dg:
        return False, dr
    return True, "主题[%s] 可买: stage=%s verdict=%s | %s" % (theme, stage, st.get("verdict"), dr)


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
        try:
            d1 = _dt.date(int(wd[:4]), int(wd[4:6]), int(wd[6:8]))
            d2 = _dt.date(int(str(today)[:4]), int(str(today)[4:6]), int(str(today)[6:8]))
            if (d2 - d1).days > n:
                return False, "指数浪型数据过期(wave_state.date=%s, 距今 %d 天 > %d) → 不触发" % (
                    wd, (d2 - d1).days, n)
        except Exception:
            pass
    if lv in INDEX_STOP_LEVELS:
        return True, "指数大级别止损: level=%s（狼大2026-08-27: 不走大5浪而转为下跌1浪就止损, date=%s）" % (lv, wd or "?")
    return False, "指数级别=%s, 未转下跌 → 不触发" % lv


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
