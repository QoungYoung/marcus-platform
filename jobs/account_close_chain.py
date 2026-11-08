#!/usr/bin/env python
from __future__ import annotations
import sys
from chain_util import run_steps

def main():
    return run_steps([
        ("净值快照", "scripts/snapshot_portfolio.py", []),
        ("现金对账", "jobs/recon_account_cash.py", []),
    ])

if __name__ == "__main__":
    sys.exit(main())
