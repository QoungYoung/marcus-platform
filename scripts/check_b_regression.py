#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_b_regression.py — 用 20261028 known-good 基线检查当前 B 输出。"""
import json,sys
BASE='data/backtest/recovered_b_20261028.json'
CUR='data/backtest/ab_local_20261028.json'
base=json.load(open(BASE)); cur=json.load(open(CUR))
b=cur.get('B') or {}
sym=(b.get('roles') or {}).get('leader')
target=b.get('target')
ok=(target==base['target'] and sym==base['roles']['leader'])
print(json.dumps({'baseline':{'target':base['target'],'leader':base['roles']['leader']},
 'current':{'target':target,'leader':sym},'ok':ok},ensure_ascii=False,indent=1))
raise SystemExit(0 if ok else 1)
