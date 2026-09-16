# -*- coding: utf-8 -*-
"""`vnpy.event` 的 `Event`/`EventEngine` 最小替身（回测不跑事件循环）。"""


class Event:
    def __init__(self, type=None, data=None):
        self.type = type
        self.data = data


class EventEngine:
    def __init__(self, *a, **kw):
        self._handlers = {}

    def register(self, type, handler):
        self._handlers.setdefault(type, []).append(handler)

    def unregister(self, type, handler):
        try:
            self._handlers.get(type, []).remove(handler)
        except ValueError:
            pass

    def put(self, event):
        for h in self._handlers.get(getattr(event, "type", None), []):
            try:
                h(event)
            except Exception:
                pass

    def start(self):
        return None

    def stop(self):
        return None
