# 2026-11-08：main_line_state / position_class 改读 PG（state_pg）；找不到模块则完全不干预
try:
    import state_io as _state_io
except Exception:
    try:
        import os as _os_si, sys as _sys_si
        _r_si = _os_si.path.dirname(_os_si.path.dirname(_os_si.path.abspath(__file__)))
        _p_si = _os_si.path.join(_r_si, "apps", "main_line")
        if _p_si not in _sys_si.path:
            _sys_si.path.insert(0, _p_si)
        import state_io as _state_io
    except Exception:
        _state_io = None

# -*- coding: utf-8 -*-
"""daily_strategy_summary.py — 一次生成「当日策略摘要」：主线 + 波浪 + 宏观 + 风控 + 狼大操作口径。

确定性脚本：完全从 data/{main_line_state,wave_state,macro_state,systemic_risk}.json 拼装，
不用 LLM，避免 pi_analysis 叙事编号漂移/自相矛盾。用于替代零散的主线/波浪两份独立报告。
输出：stdout markdown（调度器推送QQ）+ 落盘 data/strategy_summary_<date>.md
用法：python -u jobs/daily_strategy_summary.py
"""
import os, sys, json, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
DATA = os.environ.get("DATA_DIR", "data")

LEVEL_NAMES = {"d1": "上升浪/反转大1浪", "d2": "调整大2浪", "d3": "主升级别大3浪",
               "d4": "大4浪", "d5": "末段衰竭", "down": "下跌浪"}
OP_GUIDE = {
    "build": "可建仓/追主升：主线内低吸埋伏、波段持仓可加仓，避免追高杀跌",
    "t_only": "只做T不新建仓：底仓不动，T仓按分时T出/正T低吸/黄线离场纪律高抛低吸",
    "side": "观望/调仓换股：不追主升、不满仓，等结构确认后再动",
    "defense": "防御不建仓：等待企稳/止跌确认，规避C杀，只保留底仓",
    "exit": "兑现降仓：反弹即减、控制回撤，不再开新仓",
}

def load(name):
    _pg_si = _state_io.maybe_load(name) if _state_io else None
    if _pg_si is not None:
        return _pg_si
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def fnum(d, k, default="?"):
    v = d.get(k)
    return round(float(v), 2) if isinstance(v, (int, float)) else (v if v is not None else default)


def latest_p3():
    """返回 (date, mainline, track, theme)；无 P3 数据返回 (None, {}, {}, {})。"""
    base = os.path.join(DATA, "mainline")
    try:
        dirs = sorted([d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))])
    except Exception:
        return None, {}, {}, {}
    for d in reversed(dirs):
        p = os.path.join(base, d)
        ml = load(os.path.join("mainline", d, "mainline.json"))
        if ml:
            return d, ml, load(os.path.join("mainline", d, "track.json")), load(os.path.join("mainline", d, "theme.json"))
    return None, {}, {}, {}

def mk_report():
    wv = load("wave_state.json")
    ml = load("main_line_state.json")
    macro = load("macro_state.json")
    sr = load("systemic_risk.json")
    today = datetime.date.today().strftime("%Y-%m-%d")

    f = wv.get("features") or {}
    mk = macro.get("market") or f.get("market") or {}
    gjd = mk.get("gjd") or {}
    close = f.get("close")
    bars = []
    try:
        from app.services.index_daily_store import load_bars
        bars = load_bars("000001.SH")
    except Exception:
        pass
    closes = [float(x["close"]) for x in bars] if bars else []
    highs = [float(x["high"]) for x in bars] if bars else []
    lows = [float(x["low"]) for x in bars] if bars else []

    def _ret(n):
        if len(closes) > n and closes[-1-n]:
            return round((closes[-1] / closes[-1-n] - 1) * 100, 2)
        return mk.get("idx_r%d" % n, "?")

    def _ma(n):
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else "?"

    r5 = _ret(5); r20 = _ret(20)
    ma5, ma20, ma60, ma200 = _ma(5), _ma(20), _ma(60), _ma(200)
    if close is None:
        close = closes[-1] if closes else "?"
    if ma20 == "?":
        ma20 = f.get("ma20", "?")
    if ma60 == "?":
        ma60 = f.get("ma60", "?")
    if closes:
        lo = min(closes[-120:]); hi = max(closes[-120:])
        pos = round((closes[-1] - lo) / (hi - lo) * 100, 2) if hi > lo else "?"
        vr = round(sum(closes[-5:]) / max(1e-9, sum(closes[-60:]) / 12), 2)
    else:
        pos = f.get("pos_in_box", "?")
        vr = f.get("vol_ratio20", "?")
    vol_ratio = mk.get("vol_ratio_5_60", vr)
    vol_pct = mk.get("vol_pct120", "?")
    prior_peak = f.get("prior_peak"); prior_date = f.get("prior_peak_date")
    origin = f.get("origin"); origin_date = f.get("origin_date")
    vs_high = round((close / prior_peak - 1) * 100, 2) if isinstance(close, (int, float)) and prior_peak else "?"
    vs_low = round((close / origin - 1) * 100, 2) if isinstance(close, (int, float)) and origin else "?"

    L = []
    L.append("# 当日策略摘要（%s · 盘前）" % today)
    L.append("> 由 主线判定 + 波浪判定 + 宏观 + 风控 合并生成（确定性，读 data/*.json + PG index_daily，非LLM叙事）")
    L.append("")

    # ── 一、大盘波浪 ──
    L.append("## 一、大盘波浪（wave_state）")
    level = wv.get("level") or "?"
    sub = wv.get("sub_level") or "?"
    op = wv.get("operation") or "?"
    conf = wv.get("confidence")
    L.append("- 大级别：**%s**（%s）｜子浪：**%s**｜操作：**%s**（confidence %s）｜判定日期：%s" % (
        level, LEVEL_NAMES.get(level, level), sub, op, conf,
        f.get("date") or wv.get("date") or "?"))
    L.append("- 收盘 %s ｜ 5日 %s%% ｜ 20日 %s%% ｜ 箱体位置 %s%% ｜ 量比5/60 %s ｜ 120日量分位 %s%%" % (
        close, r5, r20, pos, vol_ratio, vol_pct))
    L.append("- MA5/20/60/200: %s / %s / %s / %s ｜ 距前高 %s%% ｜ 距前低 %s%%" % (
        ma5, ma20, ma60, ma200, vs_high, vs_low))
    _north = mk.get("north_5d")
    _north_txt = "无数据" if _north is None else (str(round(float(_north), 2)) + "万")
    L.append("- GJD: 510300 20日 %s%% ｜ 510050 20日 %s%%（净赎回=撤）｜ 两融净买 %s亿(20日 %s%%) ｜ 北向5日 %s" % (
        fnum(gjd, "sh300_chg20"), fnum(gjd, "sh50_chg20"),
        fnum(mk, "margin_net_buy"), fnum(mk, "margin_20d_chg"), _north_txt))
    L.append("- 锚点：前高 %s(%s) ｜ 前低 %s(%s) ｜ 双底 %s" % (
        prior_date or "?", prior_peak if prior_peak is not None else "?",
        origin_date or "?", origin if origin is not None else "?",
        f.get("w_bottom") or "无"))
    rsn = (wv.get("reasons") or "")[:260].replace("\n", " ")
    if rsn:
        L.append("- 判定理由：%s" % rsn)
    L.append("")

    # ── 二、主线判定（P3: L0→L1→L2） ──
    p3_date, p3_ml, p3_tr, p3_th = latest_p3()
    L.append("## 二、主线判定（P3：L0→L1→L2）")
    if p3_ml:
        mainline = p3_ml.get("name") or p3_ml.get("mainline") or "?"
        L.append("- 数据日：%s ｜ L0：**%s / %s** ｜ exists=%s active=%s tradeable=%s status=%s" % (
            p3_date, p3_ml.get("name") or p3_ml.get("mainline") or "?",
            p3_ml.get("sub_mainline_name") or p3_ml.get("sub_mainline") or "?",
            p3_ml.get("mainline_exists"), p3_ml.get("mainline_active"),
            p3_ml.get("tradeable"), p3_ml.get("status")))
        L.append("- L0 强度：abs_ok=%s rel_ok=%s cov_ok=%s ｜ benchmark_ret20=%s%%" % (
            p3_ml.get("abs_ok"), p3_ml.get("rel_ok"), p3_ml.get("cov_ok"),
            round(float(p3_ml.get("benchmark_ret20") or 0) * 100, 2)))
        for label, key in (("L1 focus", "focus"), ("L1 trial", "trial"), ("L1 watch", "watch")):
            arr = p3_tr.get(key) or []
            if arr:
                L.append("- %s：%s" % (label, "、".join("%s(%.2f)" % (x.get("name") or x.get("id"), float(x.get("score") or 0)) for x in arr[:6])))
        for label, key in (("L2 trial", "trial"), ("L2 watch", "watch")):
            arr = p3_th.get(key) or []
            if arr:
                L.append("- %s：%s" % (label, "、".join("%s(%.2f)" % (x.get("name") or x.get("id"), float(x.get("score") or 0)) for x in arr[:8])))
        L.append("- 规则：只做 L0 主线；L1/L2 只用来选赛道/主题；L1/L2 trial 不等于可买，仍需 wave/P3/caps 放行。")
    else:
        mainline = "无 P3 数据"
        L.append("- 无 P3 数据（data/mainline/*/mainline.json 缺失）")
    L.append("")

    # ── 三、宏观开关 ──
    L.append("## 三、宏观/机构开关（macro_state）")
    L.append("- 采集日期：%s" % (macro.get("date") or "?"))
    mtext = macro.get("macro_switches_text") or ""
    flags = (macro.get("macro_switches") or {}).get("flags") or []
    if mtext:
        L.append(mtext)
    else:
        L.append("- 开关触发：%s" % ("、".join(flags) if flags else "未触发异常(中性)"))
    L.append("")

    # ── 四、系统性风险 ──
    L.append("## 四、系统性风险（systemic_risk）")
    L.append("- level %s：%s" % (sr.get("level") or 0, sr.get("advice") or "无"))
    L.append("")

    # ── 五、狼大操作口径 ──
    L.append("## 五、狼大操作口径（wave operation=%s）" % op)
    L.append("- %s" % OP_GUIDE.get(op, OP_GUIDE.get("side", "?")))
    L.append("- 硬门：wave_level_gate(operation∈defense/exit→不建仓) + P2 Gate(浪型/宏观) + 三仓档位 + 周末降仓")

    # ── 六、综合 ──
    L.append("")
    L.append("## 六、综合一句话")
    L.append("- 大级别 **%s/%s（%s）**，主线 **%s** —— %s" % (
        level, sub, op, mainline, OP_GUIDE.get(op, "")))
    return "\n".join(L) + "\n"

def main():
    txt = mk_report()
    print(txt)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "strategy_summary_%s.md" % datetime.date.today().strftime("%Y%m%d")), "w", encoding="utf-8") as fp:
            fp.write(txt)
    except Exception as e:
        print("[summary] save failed:", e, file=sys.stderr)

if __name__ == "__main__":
    main()
