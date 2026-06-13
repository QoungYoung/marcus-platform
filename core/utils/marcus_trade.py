#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus × VN.PY 交易执行器 — 统一入口

⚠️ 此文件仅为重导入口，避免维护两份代码。
    所有实现位于 backend/app/core/trading/marcus_trade.py

用法:
    marcus-trade buy SH600519 1700 100 --reason "财报超预期"
    marcus-trade sell SH600519 1720 50 --reason "止盈"
    marcus-trade account
    marcus-trade positions
    marcus-trade history --limit 20
"""

import sys
from pathlib import Path

# 确保 backend 在 Python path 中
_backend_root = Path(__file__).resolve().parents[2] / "backend"
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

# 从唯一权威源重新导出所有内容
from app.core.trading.marcus_trade import (  # noqa: F401, E402
    MarcusVNPyExecutor,
    parse_float_chinese,
    main,
)

if __name__ == "__main__":
    main()
