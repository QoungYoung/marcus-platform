# -*- coding: utf-8 -*-
"""material_entry.py — ① 材料"大级别买点再开"执行链路 (P2 轮动遗留)
狼大2026-09-02: "科技剩半导体材料和存储有点剩余空间…再次破新低后就是大级别买点了…等大级别买点就买材料(下半年既定计划)"
链路: R7 业绩月日历 → wave 大级别转 side/build(破位后企稳) → 材料概念 confirm_chain 确认 → 建仓(信号层)
输入(点内文件): data/wave_state.json / data/position_class_result.json / data/main_line_state.json
输出: {status: ready|watch|wait, conditions:[{name,ok,note}], reason}
用法: python apps/main_line/material_entry.py   (或在 wave/position 刷新后调用)
"""
import os, json, datetime

DATA = os.environ.get("DATA_DIR", "data")
MATERIAL_KWS = ["半导体材料", "光刻胶", "光刻机(胶)", "碳基材料"]

def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def _cond(name, ok, note):
    return {"name": name, "ok": bool(ok), "note": note}

def after_earnings_season(day=None):
    """业绩月(4/7-8/10)之后才进入机构调仓/埋伏窗口(R7)"""
    day = day or datetime.date.today()
    return day.month not in (4, 8) or (day.month == 4 and day.day > 25) or (day.month == 8 and day.day > 25)

def big_level_ok(wave):
    """大级别买点: wave 转 build；或 4-x 筑底转 side 后等待确认。4-x/t_only/defense → wait"""
    op = (wave or {}).get("operation") or ""
    sub = str((wave or {}).get("sub_level") or "")
    if op == "build":
        return True, "主升/买点开启"
    if op == "side" and any(k in sub for k in ("W底", "筑底", "4-3", "C杀末")):
        return True, "底部结构+观望转调仓: 可埋伏"
    return False, "当前 op=%s sub=%s — 等转 build 或筑底 side" % (op or "?", sub or "?")

def material_confirm(pos, kws=MATERIAL_KWS):
    """材料概念确认链: 任一概念进入 确认/突破候选 → 可执行"""
    hits = [v for v in pos.values() if isinstance(v, dict) and v.get("name") and
            any(k in str(v["name"]) for k in kws)]
    if not hits:
        return False, "材料概念无记录"
    stages = [((v.get("confirm_chain") or {}).get("stage") or "下跌中") for v in hits]
    ok = any(s in ("确认", "突破候选") for s in stages)
    return ok, "材料概念 n=%d stages=%s" % (len(hits), sorted(set(stages))[:6])

def index_ok(pos):
    vals = [v for v in pos.values() if isinstance(v, dict) and v.get("signals")]
    for v in vals:
        cc = v.get("signals") or {}
        if cc.get("index_confirm") in ("确认", "突破候选"):
            return True, cc.get("index_confirm")
    return False, "指数确认未触发"

def evaluate(day=None):
    wave = load(os.path.join(DATA, "wave_state.json"))
    pos = load(os.path.join(DATA, "position_class_result.json"))
    conds = [
        _cond("R7 业绩月已过", after_earnings_season(day), "当前月份=%d" % (day or datetime.date.today()).month),
        _cond("大级别买点(wave转build/筑底side)", big_level_ok(wave)[0], big_level_ok(wave)[1]),
        _cond("指数确认链(企稳/突破)", index_ok(pos)[0], index_ok(pos)[1]),
        _cond("材料概念 confirm(确认/突破候选)", material_confirm(pos)[0], material_confirm(pos)[1]),
    ]
    ok_all = all(c["ok"] for c in conds)
    ready = ok_all
    if not ready:
        # 大级别刚转 side + 指数未确认但材料缩量止跌 → watch
        bl = big_level_ok(wave)[0]
        if bl and any(c["name"].startswith("指数") and not c["ok"] for c in conds):
            ready = False
    return {"status": "ready" if ready else ("watch" if bl_side_watch(wave) else "wait"),
            "conditions": conds,
            "date": str((day or datetime.date.today())),
            "reason": "材料大级别买点链路: " + ("满足→可执行建仓(国算/液冷继续加, 材料开仓)" if ready else "等待确认链")}

def bl_side_watch(wave):
    op = (wave or {}).get("operation") or ""
    return op in ("side", "build")

def main():
    out = evaluate()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    try:
        json.dump(out, open(os.path.join(DATA, "material_entry_status.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
