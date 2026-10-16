# -*- coding: utf-8 -*-
"""event_alert.py — 重要会议/事件的 T-5 / T-3 / T-1 提醒（事实层 ✓ 建议由 agent 写 ✓）

用户 2026-10-16 拍板：不要统计规则，**让 agent 自己收数据给建议** ✓；
  提醒节奏 = 每次重要事件（tier>=3：CPI/非农/FOMC ✓）的 **前 5 / 3 / 1 个交易日** ✓。

本脚本只做**事实层**（可核对 ✓）：
  · 用 relay trade_cal 取未来交易日 ✓（不猜日历 ✓）
  · 算「今天距下一个 tier>=3 事件还有几个交易日」✓
  · 落在 {5,3,1} ⇒ 输出 data/event_alert_<today>.json（含事件信息 + 风险面板读数摘要 ✓）
  · 不在提醒日 ⇒ 什么都不写（push 任务自然 no-op ✓ fail-open）
建议/推送文案由 agent（或人）写进 data/event_alert_<today>.md ✓，再由 push_macro_briefing.py 推 QQ ✓。
用法：python jobs/event_alert.py [--asof YYYYMMDD] [--force]（--force 用于演练 ✓）
"""
import argparse, datetime as dt, json, os, sys

sys.path.insert(0, "core")
DATA = os.environ.get("DATA_DIR", "data")


def trading_days(start8, end8):
    from tushare_relay import relay_query
    df = relay_query("trade_cal", exchange="SSE", start_date=start8, end_date=end8)
    ds = []
    for _, r in df.iterrows():
        try:
            d = str(r.get("cal_date") or "").replace("-", "")
            if int(r.get("is_open") or 0) == 1 and len(d) == 8:
                ds.append(d)
        except Exception:
            pass
    return sorted(ds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--force", action="store_true", help="演练：不在 5/3/1 也输出 ✓")
    a = ap.parse_args()
    today = a.asof
    cal = json.load(open(os.path.join(DATA, "macro_calendar.json"), encoding="utf-8"))["events"]
    fut = sorted([e for e in cal if e.get("tier", 0) >= 3 and e["local"][:10].replace("-", "") > today], key=lambda e: e["ts"])
    if not fut:
        print("无未来 tier>=3 事件 ⇒ 跳过")
        return 0
    ev = fut[0]
    evday = ev["local"][:10].replace("-", "")
    ds = trading_days(today, evday)
    # 距事件的交易日数：从今天（含）数到事件日（含）之前
    off = len([d for d in ds if d < evday]) - (1 if today in ds else 0)
    print("今天 %s ｜ 下一个 tier>=3 事件：%s（%s，北京时 %s）｜ **T-%d**" % (
        today, ev.get("name_cn") or ev["release"], ev["ccy"], ev["local"][:16], off))
    if off not in (5, 3, 1) and not a.force:
        print("不在 T-5/T-3/T-1 ⇒ 不写文件（fail-open ✓）")
        return 0
    facts = {"date": today, "offset_trading_days": off, "forced": bool(a.force),
             "event": {"name": ev.get("name_cn") or ev["release"], "release": ev["release"], "ccy": ev["ccy"],
                       "beijing_time": ev["local"], "tier": ev["tier"], "src": ev.get("src", "fxmacrodata")},
             "trading_days_until": [d for d in ds if d < evday][-12:]}
    p = os.path.join(DATA, "risk_panel_inputs_%s.json" % today)
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        facts["panel_factors"] = [{"name": f["name"], "reading": f["reading"][:160]} for f in d.get("factors", [])]
        facts["panel_gaps"] = [g["item"] for g in d.get("gaps", [])]
    try:
        ws = json.load(open(os.path.join(DATA, "wave_state.json"), encoding="utf-8"))
        facts["wave_state"] = {k: ws.get(k) for k in ("date", "level", "sub_level", "operation", "mode")}
    except Exception:
        pass
    # 事件关联知识库 ✓（config/macro_event_impact.yaml：是什么/看什么/传导路径/关联板块 ✓）
    #   注意：那是**定性说明**（⛔ 未经我们数据验证的经验描述）⇒ 引用时必须带这句限定 ✓，不许当统计用 ✗
    try:
        import yaml
        imp = yaml.safe_load(open(os.path.join("config", "macro_event_impact.yaml"), encoding="utf-8"))["events"]
        rel = str(ev["release"])
        key = rel if rel in imp else None
        if not key:
            for k in imp:
                for tok in str(k).replace("/", " ").replace("_", " ").split():
                    if tok and tok in rel.replace("_", " "):
                        key = k
                        break
                if key:
                    break
        if key:
            facts["impact"] = imp[key]
        else:
            facts["impact_gap"] = "knowledge_base_missing: %s" % rel
    except Exception as e:
        facts["impact_gap"] = "load_failed: %s" % str(e)[:80]

    out = os.path.join(DATA, "event_alert_%s.json" % today)
    json.dump(facts, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入 %s ✓（建议文案请写 data/event_alert_%s.md ⇒ 由 push_macro_briefing 推 QQ ✓）" % (out, today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
