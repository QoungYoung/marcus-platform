# -*- coding: utf-8 -*-
"""`vnpy.trader.engine` 的 `MainEngine` 替身：只保证"构造不炸"（回测不启动 vnpy）。"""
from vnpy.event import EventEngine


class MainEngine:
    def __init__(self, event_engine=None, *a, **kw):
        self.event_engine = event_engine or EventEngine()
        self.apps = {}
        self.gateways = {}
        self.engines = {}

    def add_app(self, app, *a, **kw):
        self.apps[getattr(app, "app_name", str(app))] = app

    def add_gateway(self, *a, **kw):
        return None

    def connect(self, *a, **kw):
        return None

    def subscribe(self, *a, **kw):
        return None

    def send_order(self, req):
        return ""

    def cancel_order(self, *a, **kw):
        return None

    def get_all_accounts(self):
        return []

    def get_all_positions(self):
        return []

    def get_all_orders(self):
        return []

    def get_all_trades(self):
        return []

    def close(self):
        return None
