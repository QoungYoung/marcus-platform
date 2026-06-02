#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replace emoji in Python files with ASCII-safe equivalents for Windows GBK."""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

files = [
    r'f:\pythonProject\AITrade\marcus-platform\core\xueqiu_engine.py',
    r'f:\pythonProject\AITrade\marcus-platform\backend\app\core\trading\paper_engine.py',
    r'f:\pythonProject\AITrade\marcus-platform\apps\paper-trading\paper_engine.py',
]

replacements = {
    '\u2713': '[OK]',
    '\u2714': '[OK]',
    '\u274c': '[ERR]',
    '\u26a0': '[WARN]',
    '\u2705': '[OK]',
    '\u274e': '[ERR]',
    '\U0001f4c8': '[UP]',
    '\U0001f4c9': '[DOWN]',
    '\U0001f4ed': '[VIEW]',
    '\U0001f4ca': '[CHART]',
    '\U0001f4b0': '[CASH]',
    '\U0001f4b9': '[CHART]',
}

for filepath in files:
    if not os.path.exists(filepath):
        print(f'SKIP (not found): {filepath}')
        continue
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    count = 0
    for emoji, replacement in replacements.items():
        n = content.count(emoji)
        if n > 0:
            content = content.replace(emoji, replacement)
            count += n

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'{count} replacements in {os.path.basename(filepath)}')

print('\nDone.')
