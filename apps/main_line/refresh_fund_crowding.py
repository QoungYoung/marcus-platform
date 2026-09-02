# -*- coding: utf-8 -*-
"""refresh_fund_crowding.py — 季度基金持仓+拥挤度刷新（供 tasks.yaml fund_crowding_refresh 调用）
步骤: 1) build_fund_holdings(自动最近份额日+上一季度末 end_date) 2) build_crowding(读 holdings 最大 end_date 聚合真实拥挤)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_fund_holdings as bfh
import build_crowding as bc

def main():
    print("== step1 fund_portfolio Q 回填 ==", flush=True)
    rc = bfh.main()
    if rc: return rc
    print("== step2 真实拥挤度聚合 ==", flush=True)
    rc2 = bc.main()
    return rc2

if __name__ == "__main__":
    raise SystemExit(main())
