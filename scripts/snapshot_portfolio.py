#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily portfolio snapshot script.

Called by the scheduler at market close (15:01 on weekdays).
Computes current total_asset with real-time market prices and persists
to PostgreSQL paper_daily_snapshot table.

Usage:
    python snapshot_portfolio.py [--date YYYY-MM-DD]
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Support both local dev (app inside PROJECT_ROOT/backend/)
# and Docker deployment (backend contents copied to PROJECT_ROOT/)
for candidate in [PROJECT_ROOT / "backend", PROJECT_ROOT]:
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break
else:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))  # fallback

from app.api.portfolio import save_daily_snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Daily portfolio snapshot (PostgreSQL)')
    parser.add_argument('--date', default=None, help='Target date YYYY-MM-DD (default: today)')
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime('%Y-%m-%d')
    result = save_daily_snapshot(target_date=target_date)

    if result.get('success'):
        print(f"[snapshot] {result['trade_date']} | "
              f"total_asset={result['total_asset']:,.2f} | "
              f"positions={result['position_count']} | "
              f"valuation={result['price_source']}")
        print(f"[snapshot] Done. total_asset={result['total_asset']:,.2f}")
    else:
        print(f"[snapshot] ERROR: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
