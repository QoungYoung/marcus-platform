#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""exit_rule_probe.py — 读 snapshot JSON，跑确定性离场规则。只读，不下单。"""
from __future__ import annotations
import argparse, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
from app.services.exit_rule_engine import evaluate

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', required=True)
    a = ap.parse_args()
    d = json.load(open(a.snapshot, encoding='utf-8'))
    print(json.dumps(evaluate(d), ensure_ascii=False, indent=1))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
