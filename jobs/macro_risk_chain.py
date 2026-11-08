#!/usr/bin/env python
from __future__ import annotations
import sys
from chain_util import run_steps

def main():
    return run_steps([
        ("系统性风险", "apps/main_line/build_systemic_inputs.py", []),
        ("宏观状态", "apps/main_line/build_macro_state.py", []),
    ])

if __name__ == "__main__":
    sys.exit(main())
