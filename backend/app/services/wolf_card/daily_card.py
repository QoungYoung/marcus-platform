# -*- coding: utf-8 -*-
"""A 卡：regime + sleeves + directions + instrument watch + negative + no_trade。"""
from __future__ import annotations
import json
from typing import Any, Dict, List

try:
    from .regime_gate import build_gate
    from .direction_watch import get_directions
    from .instrument_watch import watch
except ImportError:  # 直接脚本运行
    from regime_gate import build_gate
    from direction_watch import get_directions
    from instrument_watch import watch


def build_card(date8: str, top_dirs: int = 3, directions: List[str] | None = None,
               top_etf: int = 5, top_stock: int = 10) -> Dict[str, Any]:
    g = build_gate(date8)
    dw = get_directions(date8, top=top_dirs, override=directions)
    # sleeve 必须落在 active_sleeves 里；side=观望/调仓，不能默认 core
    active = g.get("active_sleeves") or []
    if g.get("allow_new_base") and "core" in active:
        sleeve = "core"
    elif g.get("allow_t"):
        sleeve = "T"
    else:
        sleeve = None
    instruments = []
    if sleeve and not g.get("no_trade"):
        for d in dw.get("directions", []):
            w = watch(d["theme"], date8, sleeve=sleeve, top_etf=top_etf, top_stock=top_stock)
            instruments.append({"theme": d["theme"], "stage": d.get("stage"),
                                "etf": w.get("etf", []), "stocks": w.get("stocks", []),
                                "quality": w.get("quality")})
    # 去重（同 code 只留第一次）
    seen, uniq = set(), []
    for it in instruments:
        it["etf"] = [x for x in it["etf"] if not (x["code"] in seen or seen.add(x["code"]))]
        it["stocks"] = [x for x in it["stocks"] if not (x["code"] in seen or seen.add(x["code"]))]
        uniq.append(it)
    instruments = uniq
    n_etf = sum(len(x["etf"]) for x in instruments)
    n_stock = sum(len(x["stocks"]) for x in instruments)
    stale_dir = (dw.get("quality") not in ("pit_market", "pit_prev_close", "explicit"))
    no_trade = bool(g.get("no_trade") or stale_dir or (n_etf + n_stock) == 0)
    card = {
        "date": date8,
        "regime": {k: g.get(k) for k in ("regime", "operation", "mode", "sub_level", "family", "stage",
                                          "status", "confidence", "allow_new_base", "confirm_needed",
                                          "allow_t", "max_position", "active_sleeves", "no_trade", "data_quality")},
        "directions": dw,
        "watch_directions": dw.get("watch_directions", []),
        "sleeve_used": sleeve,
        "instruments": instruments,
        "counts": {"etf": n_etf, "stocks": n_stock},
        "no_trade": no_trade,
        "reason": ("regime_no_trade" if g.get("no_trade") else
                   "direction_stale" if stale_dir else
                   "no_instruments" if (n_etf + n_stock) == 0 else None),
    }
    return card


def to_markdown(c: Dict[str, Any]) -> str:
    r = c["regime"]
    lines = ["# 狼大决策卡 %s" % c["date"], "",
             "**Regime**：%s / %s / %s；sleeves=%s；allow_new=%s confirm=%s allow_T=%s；max_pos=%s" % (
                 r.get("regime"), r.get("operation"), r.get("mode") or "-",
                 ",".join(r.get("active_sleeves") or []), r.get("allow_new_base"),
                 r.get("confirm_needed"), r.get("allow_t"), r.get("max_position")), ""]
    d = c["directions"]
    lines.append("**方向**（%s / asof=%s）：" % (d.get("quality"), d.get("asof")))
    for x in d.get("directions", []):
        lines.append("- %s｜%s｜share5=%.2f r20_rank=%s r5_rank=%s" % (
            x["theme"], x.get("stage"), x.get("share5", 0), x.get("r20_rank"), x.get("r5_rank")))
    if c.get("watch_directions"):
        lines.append("**低位/动量观察**：" + "；".join("%s(%s)" % (x["theme"], x.get("stage")) for x in c["watch_directions"]))
    lines.append("")
    for it in c.get("instruments", []):
        lines.append("## %s（%s）" % (it["theme"], it.get("stage")))
        if it.get("etf"):
            lines.append("- ETF：" + "；".join("%s %s[%s]" % (x["code"], x["name"], x.get("sleeve")) for x in it["etf"]))
        if it.get("stocks"):
            lines.append("- 个股：" + "；".join("%s %s(%s/%s)" % (
                x["code"], x["name"], x.get("role"), x.get("negative_status")) for x in it["stocks"][:10]))
        lines.append("")
    lines.append("**No trade：%s**（%s）" % (c.get("no_trade"), c.get("reason") or "-"))
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--directions", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ov = [x.strip() for x in a.directions.split(",") if x.strip()] or None
    c = build_card(a.date, directions=ov)
    print(json.dumps(c, ensure_ascii=False, indent=1) if a.json else to_markdown(c))
