#!/usr/bin/env python
from __future__ import annotations
import sys
from chain_util import run_steps

def main():
    return run_steps([
        ("浪型状态", "apps/main_line/wave_ladder.py", ["--write", "--dated"]),
        ("浪型 history", "jobs/regen_wave_state_ladder.py", ["--apply", "--history-days", "90"]),
    ])

if __name__ == "__main__":
    sys.exit(main())
