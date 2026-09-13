# -*- coding: utf-8 -*-
import os
p='backend/app/services/trade_graph.py'
NL=chr(10)
lines=open(p,encoding='utf-8').read().split(NL)

helper = [
'def _read_main_line_context() -> str:',
'    """read main_line_state -> context block for trade prompt."""',
'    try:',
'        import json as _json',
'        path = os.getenv("MAIN_LINE_STATE_FILE", os.path.join(os.environ.get("DATA_DIR", "data"), "main_line_state.json"))',
'        if not os.path.exists(path):',
'            return ""',
'        with open(path, encoding="utf-8") as f:',
'            st = _json.load(f)',
'        ml = st.get("main_line") or "未知"',
'        cands = st.get("candidates") or []',
'        cat = st.get("catalyst") or {}',
'        cat_str = chr(44).join(f"{k}={v}" for k,v in cat.items() if v is not None)',
'        block = ("## 主线判定（main_line_state）" + NL',
'                 + "- 当前主线：" + str(ml) + NL',
'                 + "- 候选集（catalyst>=0.7，观察不建仓）：" + (", ".join(cands) if cands else "无") + NL',
'                 + "- catalyst_score：" + (cat_str or "无") + NL',
'                 + "- 决策：候选->观察(等确认)；候选+当日动量/资金转强(RS转正/突破前高/fund_acc>0/均线多头)->确认->可对主线方向建仓。" + NL + NL)',
'        return block',
'    except Exception:',
'        return ""',
'',
'',
]

# 1) 在 def node_fetch_context 前插入 helper
idx=None
for i,l in enumerate(lines):
    if l.strip().startswith('def node_fetch_context'):
        idx=i; break
if idx is None:
    print('未找到 node_fetch_context'); raise SystemExit(1)
out = lines[:idx] + helper + lines[idx:]

# 2) 在 fetch_context 返回里加 main_line_context
for i,l in enumerate(out):
    if 'trade_mode_instruction' in l and 'get_trade_instruction' in l:
        out.insert(i+1, '        "main_line_context": _read_main_line_context(),')
        break

# 3) 在 prompt 开头的 regime_context 行前加 main_line_context
ml_line = '        f"{state.get(' + chr(39) + 'main_line_context' + chr(39) + ', chr(39)+chr(39))}"'
for i,l in enumerate(out):
    if 'state[' in l and 'regime_context' in l and l.strip().startswith('f'):
        out.insert(i, ml_line)
        break

open(p,'w',encoding='utf-8').write(NL.join(out))
print('done; main_line_context count:', NL.join(out).count('main_line_context'))
