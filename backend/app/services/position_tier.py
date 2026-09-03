# -*- coding: utf-8 -*-
"""P3 三仓档位模型 · 纯规则服务（v0，2026-09-03）。

底仓/T仓/现金 × 浪型档位（wave operation: build/t_only/side/defense/exit）→
判定一次“买入意图”在当前浪型下是否允许，以及对应仓位上限与现金底线。

- intent: new_base(新开底仓) / add_base(已有底仓加厚) / refill_base(T资格回补,当前无持仓但属T宇宙) / t_refill(T仓日内低吸)
- 输入显式注入，便于回测/单测；默认 wave_state 从 data/wave_state.json 读取（与 p2_entry_gate 同源）。
- 规则初值: config/p3_position_tiers.json（本地权威，同步服务器；代码内置 DEFAULTS 兜底）。
- 灰度: P3_TIER_MODE=0 只观察(dry-run) / =1 由接线方按 intent_allowed 硬拦。
"""
import os
import json

DEFAULT_CFG = {
    "version": "2026-09-03",
    "wave": {
        "build": {"base_mode": "new", "cash_floor_pct": 25,
                  "intents": {"new_base": {"allow": True, "cap_pct": 10},
                              "add_base": {"allow": True, "cap_pct": 10},
                              "refill_base": {"allow": True, "cap_pct": 8},
                              "t_refill": {"allow": True, "cap_pct": 5}}},
        "t_only": {"base_mode": "refill_only", "cash_floor_pct": 35,
                   "intents": {"new_base": {"allow": False, "cap_pct": 0},
                               "add_base": {"allow": True, "cap_pct": 5},
                               "refill_base": {"allow": True, "cap_pct": 8},
                               "t_refill": {"allow": True, "cap_pct": 5}}},
        "side": {"base_mode": "ambush", "cash_floor_pct": 30,
                 "intents": {"new_base": {"allow": True, "cap_pct": 3},
                             "add_base": {"allow": True, "cap_pct": 3},
                             "refill_base": {"allow": True, "cap_pct": 5},
                             "t_refill": {"allow": True, "cap_pct": 5}}},
        "defense": {"base_mode": "hold_refill", "cash_floor_pct": 45,
                    "intents": {"new_base": {"allow": False, "cap_pct": 0},
                                "add_base": {"allow": True, "cap_pct": 3},
                                "refill_base": {"allow": True, "cap_pct": 3},
                                "t_refill": {"allow": True, "cap_pct": 5}}},
        "exit": {"base_mode": "reduce_only", "cash_floor_pct": 50,
                 "intents": {"new_base": {"allow": False, "cap_pct": 0},
                             "add_base": {"allow": False, "cap_pct": 0},
                             "refill_base": {"allow": False, "cap_pct": 0},
                             "t_refill": {"allow": True, "cap_pct": 5}}},
    },
}

ALL_INTENTS = ("new_base", "add_base", "refill_base", "t_refill")


def _workspace():
    try:
        from app.config import get_settings
        return str(get_settings().workspace_path)
    except Exception:
        return os.environ.get("MARCUS_WORKSPACE", "/app")


def _load_cfg():
    ws = _workspace()
    for sub in ("config", "data"):
        p = os.path.join(ws, sub, "p3_position_tiers.json")
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                if d.get("wave"):
                    return d
            except Exception:
                pass
    return DEFAULT_CFG


def read_wave_state():
    """读生产 wave_state.json（与 p2_entry_gate._wave_decision 同源）。"""
    p = os.path.join(_workspace(), "data", "wave_state.json")
    try:
        st = json.load(open(p, encoding="utf-8"))
        op = str(st.get("operation") or "side").lower()
        return {"level": st.get("level") or "?", "sub_level": st.get("sub_level") or "", "operation": op}
    except Exception:
        return None


def wave_mode(operation=None):
    op = (operation or "").strip().lower()
    return op if op in ("build", "t_only", "side", "defense", "exit") else "side"


def _need_ok(need, ctx):
    has = bool(ctx.get("has_base"))
    tuni = bool(ctx.get("t_universe"))
    main = bool(ctx.get("mainline_dir"))
    rel = bool(ctx.get("rel_low"))
    return {
        "has_base": has,
        "t_universe": tuni,
        "mainline_dir": main,
        "rel_low": rel,
        "has_base_or_mainline": has or main,
        "has_base_or_t_universe": has or tuni,
        "rel_low_or_mainline": rel or main,
        "mainline_or_rel_low": main or rel,
    }.get(need, True)


def _cap_by_needs(cap_pct, needs, ctx):
    """带条件动作的档位上限：满足全部 needs 才返回原 cap，否则 0（不允许）。"""
    if all(_need_ok(n, ctx) for n in (needs or [])):
        return float(cap_pct or 0)
    return 0.0


def three_tier_gate(ts_code=None, wave_state=None, intent="new_base",
                    has_base=None, t_universe=False, rel_low=None,
                    mainline_dir=False, cfg=None):
    """三仓档位决策。返回 dict（纯函数）。

    ctx: has_base=当前是否已有该标的底仓；t_universe=该标的是否在做T宇宙(有活跃T腿/历史T标的)；
         rel_low=相对主线是否低位(rotation 可埋伏)；mainline_dir=是否主线方向(候选/持仓属主线)。
    """
    intent = intent if intent in ALL_INTENTS else "new_base"
    cfg = cfg or _load_cfg()
    w = wave_state or read_wave_state() or {}
    op = wave_mode(w.get("operation"))
    op_cfg = (cfg.get("wave") or {}).get(op) or (DEFAULT_CFG["wave"].get(op) or {})
    ctx = {"has_base": bool(has_base), "t_universe": bool(t_universe),
           "rel_low": bool(rel_low), "mainline_dir": bool(mainline_dir)}

    intents = op_cfg.get("intents") or {}
    rule = intents.get(intent) or {"allow": False, "cap_pct": 0, "needs": []}
    needs = rule.get("needs") or []
    cap = _cap_by_needs(rule.get("cap_pct"), needs, ctx) if rule.get("allow") else 0.0
    allowed = bool(rule.get("allow") and (not needs or cap > 0))

    tier_map = {"new_base": "BASE_NEW", "add_base": "BASE_ADD",
                "refill_base": "T_REFILL", "t_refill": "T_REFILL"}
    reasons = []
    if allowed:
        reasons.append("P3档位%s: %s允许%s cap≤%s%%" % (op, op_cfg.get("base_mode"), intent, cap))
    else:
        why = "需满足: %s" % " 且 ".join(needs) if needs else "当前浪型禁止"
        reasons.append("P3档位%s(%s): 不允许%s(%s)" % (op, op_cfg.get("base_mode"), intent, why))

    allowed_actions = []
    for it in ALL_INTENTS:
        r = (intents.get(it) or {})
        c2 = _cap_by_needs(r.get("cap_pct"), r.get("needs") or [], ctx) if r.get("allow") else 0.0
        if r.get("allow") and (not r.get("needs") or c2 > 0):
            allowed_actions.append(it)

    return {
        "ts_code": ts_code or "",
        "wave": {"operation": op, "sub_level": w.get("sub_level") or "", "level": w.get("level") or ""},
        "base_mode": op_cfg.get("base_mode", op),
        "intent": intent,
        "intent_allowed": allowed,
        "cap_pct": round(cap, 1),
        "cash_floor_pct": float(op_cfg.get("cash_floor_pct") or 0),
        "tier": tier_map[intent] if allowed else "REJECT",
        "allowed_actions": allowed_actions,
        "context": dict(ctx),
        "reasons": reasons,
    }


def tier_mode_enabled():
    """P3_TIER_MODE=1 → 接线方按 intent_allowed 硬拦；默认 0=dry-run 只观察。"""
    return os.getenv("P3_TIER_MODE", "0").strip().lower() in ("1", "true", "yes", "on")


def summarize(decision):
    if not decision:
        return ""
    w = decision.get("wave") or {}
    return "[P3三仓] 浪型%s/%s %s | %s意图=%s cap≤%s%% 现金底线%s%% | %s" % (
        w.get("level") or "?", w.get("sub_level") or "?", w.get("operation") or "?",
        "✅允许" if decision.get("intent_allowed") else "❌拒绝",
        decision.get("intent"), decision.get("cap_pct"), decision.get("cash_floor_pct"),
        "; ".join(decision.get("reasons") or []))


if __name__ == "__main__":
    import json as _j
    for op in ("build", "t_only", "side", "defense", "exit"):
        d = three_tier_gate(wave_state={"operation": op, "sub_level": "x"}, intent="new_base", has_base=False)
        print(op, "new_base →", d["intent_allowed"], d["cap_pct"])
        d = three_tier_gate(wave_state={"operation": op, "sub_level": "x"}, intent="refill_base", has_base=False, t_universe=True)
        print(op, "refill(T宇宙) →", d["intent_allowed"], d["cap_pct"])
