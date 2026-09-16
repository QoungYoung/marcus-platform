# -*- coding: utf-8 -*-
"""`vnpy.trader.object` 的最小替身：数据类只保留"能构造、能读属性"。
回测路径（gateway→paper engine）不会构造 vnpy 订单对象；若真被构造也不会影响落库。
"""
import dataclasses
from typing import Any


@dataclasses.dataclass
class BaseData:
    symbol: str = ""
    exchange: Any = None
    gateway_name: str = ""


@dataclasses.dataclass
class TickData(BaseData):
    datetime: Any = None
    name: str = ""
    last_price: float = 0.0
    volume: float = 0.0


@dataclasses.dataclass
class OrderRequest:
    symbol: str = ""
    exchange: Any = None
    direction: Any = None
    type: Any = None
    volume: float = 0.0
    price: float = 0.0
    offset: Any = None
    reference: str = ""


@dataclasses.dataclass
class ContractData(BaseData):
    name: str = ""
    product: Any = None
    size: float = 1.0
    pricetick: float = 0.01


@dataclasses.dataclass
class SubscribeRequest:
    symbol: str = ""
    exchange: Any = None


@dataclasses.dataclass
class OrderData(BaseData):
    orderid: str = ""
    direction: Any = None
    offset: Any = None
    price: float = 0.0
    volume: float = 0.0
    traded: float = 0.0
    status: Any = None
    datetime: Any = None
    reference: str = ""


@dataclasses.dataclass
class TradeData(BaseData):
    tradeid: str = ""
    orderid: str = ""
    direction: Any = None
    offset: Any = None
    price: float = 0.0
    volume: float = 0.0
    datetime: Any = None


@dataclasses.dataclass
class AccountData(BaseData):
    accountid: str = ""
    balance: float = 0.0
    frozen: float = 0.0


@dataclasses.dataclass
class PositionData(BaseData):
    direction: Any = None
    volume: float = 0.0
    frozen: float = 0.0
    price: float = 0.0
    pnl: float = 0.0
    yd_volume: float = 0.0
