# -*- coding: utf-8 -*-
"""no_t_gate.py — 板块级不做T门(G3, 2026-09-07 实现)

读盘前 sector_g3_state.json：持仓所属主题处'洗盘收敛期' → 自动 T出(存量T仓)被拦(DRY/放行开关)。
语义：狼大'主力洗TMT(降成交额波动率)洗干净才有下一波'——收敛期不 T出, 低吸+拿住; 等放量脱离收敛再恢复。
"""
import json, os, time

DATA = os.environ.get("DATA_DIR", "/app/data")
_STATE = None
_STATE_TS = 0.0


def _load_state(state_file=None):
    global _STATE, _STATE_TS
    now = time.time()
    if _STATE is not None and now - _STATE_TS < 60:
        return _STATE
    p = state_file or os.path.join(DATA, "sector_g3_state.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        _STATE, _STATE_TS = d, now
        return d
    except Exception:
        return None


def g3_sell_blocked(symbol, state_file=None, dry: bool = False):
    """板块 G3 收敛期 → 拦该持仓自动 T出。返回 (blocked, reason)。
    无 state 文件/未判出收敛 → 放行(不拦, 容错)。dry=True 只报告不执行(调用方决定)。"""
    st = _load_state(state_file)
    if not st or not isinstance(st, dict):
        return False, "no_g3_state(放行)"
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from sector_g3 import symbol_themes
    except Exception:
        return False, "no_symbol_themes(放行)"
    themes = symbol_themes(symbol)
    if not themes:
        return False, "no_theme(放行)"
    hit = [t for t in themes if isinstance(st.get(t), dict) and st[t].get("converged")]
    if hit:
        return True, "G3板块洗盘收敛期(不T出): " + "/".join(hit)
    return False, "板块未收敛(放行): " + "/".join(themes)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "SH588170"
    print(sym, g3_sell_blocked(sym))
