#!/usr/bin/env python
from __future__ import annotations
import datetime, sys
from chain_util import run_steps

def main():
    d = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else datetime.datetime.now().strftime("%Y%m%d")
    return run_steps([
        ("L0 月度主线", "jobs/mainline_monthly.py", ["--date", d]),
        ("L1 赛道", "jobs/track_weekly.py", ["--date", d]),
        ("L2 主题", "jobs/theme_daily.py", ["--date", d]),
    ])

if __name__ == "__main__":
    sys.exit(main())
