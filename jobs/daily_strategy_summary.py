# -*- coding: utf-8 -*-
"""daily_strategy_summary.py — 一次生成「当日策略摘要」：主线 + 波浪 + 宏观 + 风控 + 狼大操作口径。

确定性脚本：完全从 data/{main_line_state,wave_state,macro_state,systemic_risk}.json 拼装，
不用 LLM，避免 pi_analysis 叙事编号漂移/自相矛盾。用于替代零散的主线/波浪两份独立报告。
输出：stdout markdown（调度器推送QQ）+ 落盘 data/strategy_summary_<date>.md
用法：python -u jobs/daily_strategy_summary.py
"""
import os, sys, json, datetime

for _p in ("/app/app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")):
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
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def fnum(d, k, default="?"):
    v = d.get(k)
    return round(float(v), 2) if isinstance(v, (int, float)) else (v if v is not None else default)

def mk_report():
    wv = load("wave_state.json")
    ml = load("main_line_state.json")
    macro = load("macro_state.json")
    sr = load("systemic_risk.json")
    today = datetime.date.today().strftime("%Y-%m-%d")

    L = []
    L.append("# 当日策略摘要（%s · 盘前）" % today)
    L.append("> 由 主线判定 + 波浪判定 + 宏观 + 风控 合并生成（确定性，读 data/*.json，非LLM叙事）")
    L.append("")

    # ── 一、大盘波浪 ──
    L.append("## 一、大盘波浪（wave_state）")
    level = wv.get("level") or "?"
    sub = wv.get("sub_level") or "?"
    op = wv.get("operation") or "?"
    conf = wv.get("confidence")
    f = wv.get("features") or {}
    mk = f.get("market") or {}
    gjd = (mk.get("gjd") or {})
    anch = f.get("anchors") or {}
    L.append("- 大级别：**%s**（%s）｜子浪：**%s**｜操作：**%s**（confidence %s）｜判定日期：%s" % (
        level, LEVEL_NAMES.get(level, level), sub, op, conf,
        f.get("date") or wv.get("date") or "?"))
    L.append("- 收盘 %s ｜ 5日 %s%% ｜ 20日 %s%% ｜ 箱体位置 %s%% ｜ 量比5/60 %s ｜ 120日量分位 %s%%" % (
        fnum(f, "close"), fnum(mk, "idx_r5"), fnum(mk, "idx_r20"),
        fnum(f, "pos_in_box"), fnum(mk, "vol_ratio_5_60"), fnum(mk, "vol_pct120")))
    L.append("- MA5/20/60/200: %s / %s / %s / %s ｜ 距前高 %s%% ｜ 距前低 %s%%" % (
        fnum(f, "ma5"), fnum(f, "ma20"), fnum(f, "ma60"), fnum(f, "ma200"),
        fnum(anch, "vs_last_high"), fnum(anch, "vs_last_low")))
    L.append("- GJD: 510300 20日 %s%% ｜ 510050 20日 %s%%（净赎回=撤）｜ 两融净买 %s亿(20日 %s%%) ｜ 北向5日 %s万" % (
        fnum(gjd, "sh300_chg20"), fnum(gjd, "sh50_chg20"),
        fnum(mk, "margin_net_buy"), fnum(mk, "margin_20d_chg"), fnum(mk, "north_5d")))
    last_h = (anch.get("last_high") or {})
    last_l = (anch.get("last_low") or {})
    L.append("- 锚点：前高 %s(%s) ｜ 前低 %s(%s) ｜ 双底 %s" % (
        last_h.get("date", "?"), last_h.get("value", "?"),
        last_l.get("date", "?"), last_l.get("value", "?"),
        anch.get("double_bottom") or "无"))
    rsn = (wv.get("reasons") or "")[:260].replace("\n", " ")
    if rsn:
        L.append("- 判定理由：%s" % rsn)
    L.append("")

    # ── 二、主线判定 ──
    L.append("## 二、主线判定（main_line_state）")
    mainline = ml.get("main_line") or "?"
    fusion = ml.get("fusion") or {}
    msc = (fusion.get(mainline) or {})
    L.append("- 主线：**%s**（融合分 %s，catalyst %s，fund %s，rel %s，conc %s）｜候选：%s" % (
        mainline, fnum(msc, "score"), fnum(msc, "catalyst"), fnum(msc, "fund"),
        fnum(msc, "rel"), fnum(msc, "conc"), "、".join(ml.get("candidates") or []) or "无"))
    top = sorted(fusion.items(), key=lambda kv: -(float(kv[1].get("score") or 0)))[:4]
    if top:
        L.append("- 前4主题分数：")
        for th, s in top:
            L.append("   - %s：%s（fund %s / rel %s / conc %s / cat %s）" % (
                th, fnum(s, "score"), fnum(s, "fund"), fnum(s, "rel"), fnum(s, "conc"), fnum(s, "catalyst")))
    cat_all_zero = all((s.get("catalyst") or 0) == 0 for s in fusion.values()) if fusion else True
    L.append("- 催化：%s" % ("全为0（无消息催化的存量博弈）" if cat_all_zero else "部分主题有催化（事件驱动）"))
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
