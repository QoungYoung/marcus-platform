# -*- coding: utf-8 -*-
"""`PySide6.QtWidgets.QApplication` 最小替身（offscreen，回测不需要真正 GUI）。"""


class QApplication:
    _instance = None

    def __init__(self, *a, **kw):
        QApplication._instance = self

    @staticmethod
    def instance():
        return QApplication._instance

    def exec(self, *a, **kw):
        return 0

    def processEvents(self, *a, **kw):
        return None
