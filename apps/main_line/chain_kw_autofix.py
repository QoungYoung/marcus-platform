# -*- coding: utf-8 -*-
"""chain_kw_autofix.py — kw 自举自动固化(去人审, 2026-09-08)
读 chain_kw_suggestions.json(AI 裁决 suggested_kw 累积), conf>=MIN_CONF(0.9) 的词自动并入
chain_map.py SEED 对应 (theme, seg.label) 段 kw; 低置信保留等更强证据。git 留痕可回滚。
env: REPO_ROOT(默认 /home/fengx/marcus-platform), SUGGESTIONS(默认 /tmp/chain_kw_suggestions.json)
"""

import ast, json, io, sys
import os
root = os.environ.get('REPO_ROOT', '/home/fengx/marcus-platform')
p = root + '/apps/main_line/chain_map.py'
code = open(p, encoding='utf-8').read()
s0 = code.index('SEED = {')
s1 = code.index('\n}\n', s0) + 3
seed_txt = code[s0 + len('SEED = '):s1]
seed = ast.literal_eval(seed_txt)
kw = json.load(open(os.environ.get('SUGGESTIONS', '/tmp/chain_kw_suggestions.json'), encoding='utf-8'))['entries']
MIN_CONF = 0.9
added = 0
per_seg = {}
for e in kw:
    if (e.get('confidence') or 0) < MIN_CONF:
        continue
    theme = e.get('theme'); seg_label = e.get('seg'); words = e.get('kw') or []
    if theme not in seed: continue
    for seg in seed[theme]:
        if seg.get('label') == seg_label:
            seg_kw = set(seg.get('kw') or [])
            new = [w for w in words if w not in seg_kw]
            if new:
                seg['kw'] = (seg.get('kw') or []) + new
                added += len(new)
                per_seg.setdefault((theme, seg_label), []).extend(new)
            break
# 重建 SEED 块
lines = ['SEED = {']
for theme, segs in seed.items():
    t = json.dumps(theme, ensure_ascii=False)
    segtxt = json.dumps(segs, ensure_ascii=False, indent=1)
    segtxt = '\n'.join(' ' + l if l else l for l in segtxt.split('\n'))
    lines.append(' ' + t + ': ' + segtxt + ',')
lines.append('}')
block = '\n'.join(lines)
code = code[:s0] + block + '\n' + code[s1:]
open(p, 'w', encoding='utf-8').write(code)
print('kw words auto-added:', added)
for k, v in list(per_seg.items())[:8]: print(' ', k, '->', v)
