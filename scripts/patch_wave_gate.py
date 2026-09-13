# -*- coding: utf-8 -*-
import os
p='backend/app/services/trade_graph.py'
NL=chr(10)
lines=open(p,encoding='utf-8').read().split(NL)

# 1) 在 _read_wave_context 之后插入 _wave_level_gate 帮助函数
helper = [
'def _wave_level_gate() -> dict:',
'    """按狼大规则: 读 wave_state, 返回 {level, gate}。gate: normal(主升可建仓)/t_only(4-4只做T)/defense(4-5调整下跌防御不建仓)。"""',
'    try:',
'        import os as _os, json as _json',
'        path = _os.path.join(_os.environ.get("DATA_DIR", "data"), "wave_state.json")',
'        if not _os.path.exists(path):',
'            return {"level": "未知", "gate": "normal"}',
'        st = _json.load(open(path, encoding="utf-8"))',
'        lvl = str(st.get("level") or "")',
'    except Exception:',
'        return {"level": "未知", "gate": "normal"}',
'    if any(k in lvl for k in ["4-5", "下跌", "调整", "C杀", "下杀", "防守"]):',
'        return {"level": lvl, "gate": "defense"}',
'    if any(k in lvl for k in ["4-4", "B反", "反弹"]):',
'        return {"level": lvl, "gate": "t_only"}',
'    if any(k in lvl for k in ["主升", "5浪", "加速"]):',
'        return {"level": lvl, "gate": "normal"}',
'    return {"level": lvl, "gate": "normal"}',
'',
]
# 插入到 def node_fetch_context 前
idx=None
for i,l in enumerate(lines):
    if l.strip().startswith('def node_fetch_context'):
        idx=i; break
if idx is None: print('no node_fetch'); raise SystemExit(1)
out = lines[:idx] + helper + lines[idx:]

# 2) 在 node_check_safety_gates 里接入浪型防御门(若 gate=defense 则 hard_blocked)
block_marker='    drawdown, blocked, reason = _check_drawdown(state[chr(39)+chr(39)])'
# 找 _check_drawdown 行
for i,l in enumerate(out):
    if '_check_drawdown(state[' in l and 'portfolio_json' in l:
        # 在其后插入浪型 gate 逻辑
        insert = [
            '    wl_gate = _wave_level_gate()',
            '    if wl_gate["gate"] == "defense":',
            '        logger.warning(f"[{eid}] [Graph] ⛔ 狼大浪型防御: {wl_gate[\"level\"]}，不建仓")',
            '        blocked = True',
            '        reason = f"狼大浪型={wl_gate[\"level\"]}（4-5/调整/下跌），防御不建仓"',
            '       ',
        ]
        out = out[:i+1] + insert + out[i+1:]
        break

open(p,'w',encoding='utf-8').write(NL.join(out))
print('done; _wave_level_gate count', NL.join(out).count('_wave_level_gate'))
