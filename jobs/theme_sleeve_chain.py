#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_chain.py — 题材发现 + 选股一条链。"""
from __future__ import annotations
import argparse, os, subprocess, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(script, *args):
    p = os.path.join(ROOT, "jobs", script)
    cmd = [sys.executable, p, *args]
    print("[chain] run", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[chain] FAIL", script, r.returncode, flush=True)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    if not run("theme_sleeve_facts.py", "--date", a.date):
        return 1
    if not run("theme_sleeve_select.py", "--date", a.date):
        return 1
    print("[chain] OK", a.date, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
