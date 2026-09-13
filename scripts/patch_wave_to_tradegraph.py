# -*- coding: utf-8 -*-
import os
p='backend/app/services/trade_graph.py'
NL=chr(10)
# 先用 .bak2 恢复(main_line 版, 正常工作)
if not os.path.exists(p+'.bak2'):
    print('no bak2'); raise SystemExit(1)
import shutil
shutil.copy(p+'.bak2', p)
lines=open(p,encoding='utf-8').read().split(NL)
helper = [
'def _read_wave_context() -> str:',
'    try:',
'        import sys',
'        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps"))',
'        from main_line import wave_level',
'        return wave_level.read_wave_context()',
'    except Exception:',
'        return ""',
'',
'',
]
# 1) 在 def node_fetch_context 前插入
idx=None
for i,l in enumerate(lines):
    if l.strip().startswith('def node_fetch_context'):
        idx=i; break
if idx is None: print('no node_fetch'); raise SystemExit(1)
out = lines[:idx] + helper + lines[idx:]
# 2) fetch_context 字典行(8空格 "main_line_context": _read_main_line_context(),) 后插入
for i,l in enumerate(out):
    if l.strip().startswith('"main_line_context":') and '_read_main_line_context' in l and 'return' not in l:
        out.insert(i+1, '        "wave_context": _read_wave_context(),')
        break
# 3) prompt 行(f"{state.get('main_line_context'...) ) 前插入 wave_context
for i,l in enumerate(out):
    if l.strip().startswith('f"{state.get(') and 'main_line_context' in l:
        out.insert(i, '        f"{state.get(' + chr(39) + 'wave_context' + chr(39) + ', chr(39)+chr(39))}"')
        break
open(p,'w',encoding='utf-8').write(NL.join(out))
print('done; wave_context count', NL.join(out).count('wave_context'))
