#!/usr/bin/env python
from __future__ import annotations
import sys
from chain_util import run_steps

def main():
    return run_steps([
        ("入场时机", "jobs/entry_timing.py", []),
        ("主升浪涨停池", "jobs/main_wave_analyzer.py", ["--candidates", "zt", "--limit", "8"]),
        ("浪型结构预警", "jobs/wave_structure_alert.py", []),
        ("策略建议", "jobs/wave_advice_agent.py", []),
    ])

if __name__ == "__main__":
    sys.exit(main())
