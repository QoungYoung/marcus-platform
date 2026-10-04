# -*- coding: utf-8 -*-


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

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
        except ValueError as _e_sil1:
            _silent_alert("event.py:22", _e_sil1)

    def put(self, event):
        for h in self._handlers.get(getattr(event, "type", None), []):
            try:
                h(event)
            except Exception as _e_sil2:
                _silent_alert("event.py:29", _e_sil2)

    def start(self):
        return None

    def stop(self):
        return None
