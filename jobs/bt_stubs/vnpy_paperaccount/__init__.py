# -*- coding: utf-8 -*-
"""`vnpy_paperaccount.PaperAccountApp` 最小替身（回测不挂载该 App）。"""


class PaperAccountApp:
    app_name = "PaperAccount"
    app_module = None
    app_path = None
    display_name = "模拟交易"

    def __init__(self, main_engine=None, event_engine=None, *a, **kw):
        self.main_engine = main_engine
        self.event_engine = event_engine

    def init_engine(self):
        return self

    def init_app(self):
        return None
