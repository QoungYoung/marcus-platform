# -*- coding: utf-8 -*-
"""`vnpy.trader.constant` 的最小替身：只需成员名与取值可比对（回测路径不构造 vnpy 订单）。

成员表按 backend/ 里实际用到的名字整理(grep -o "Exchange\.[A-Z_]*" 等)：
  Exchange: SSE/SZSE/BSE   Direction: LONG/SHORT/NET
  Status: SUCCESS/FAILED/REJECTED/SUBMITTING/RUNNING/PARTTRADED/NOTTRADED/CANCELLED/ALLTRADED
  OrderType: LIMIT   Offset: OPEN/CLOSE
"""
from enum import Enum


class _E(str, Enum):
    def __str__(self):
        return self.value


class Direction(_E):
    LONG = "多"
    SHORT = "空"
    NET = "净"


class Offset(_E):
    NONE = ""
    OPEN = "开"
    CLOSE = "平"
    CLOSETODAY = "平今"
    CLOSEYESTERDAY = "平昨"


class Status(_E):
    SUBMITTING = "提交中"
    NOTTRADED = "未成交"
    PARTTRADED = "部分成交"
    ALLTRADED = "全部成交"
    CANCELLED = "已撤销"
    REJECTED = "拒单"
    RUNNING = "运行中"
    SUCCESS = "成功"
    FAILED = "失败"


class OrderType(_E):
    LIMIT = "限价"
    MARKET = "市价"
    STOP = "停止"
    FAK = "FAK"
    FOK = "FOK"
    RFQ = "询价"


class Exchange(_E):
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"
    SHFE = "SHFE"
    CFFEX = "CFFEX"
    DCE = "DCE"
    CZCE = "CZCE"
    INE = "INE"
    LOCAL = "LOCAL"


class Interval(_E):
    MINUTE = "1m"
    HOUR = "1h"
    DAILY = "d"


class Product(_E):
    EQUITY = "股票"
    FUTURES = "期货"
    OPTION = "期权"
    ETF = "ETF"
    BOND = "债券"
