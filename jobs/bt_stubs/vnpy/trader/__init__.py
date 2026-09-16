# -*- coding: utf-8 -*-
"""回测用 **vnpy/PySide6 最小替身**（本机没装这两个 GUI 依赖）。

为什么可以替：`gateway_execute()` 构造的是 `MarcusVNPyExecutor(engine=..., account_id=...)`
——**不传 bridge** → `self.bridge is None` → 走 paper engine 落库路径（生产 `paper_trades` 就是这么写的）。
这里只是让 `app.core.trading.marcus_trade` 的**模块级 import** 能通过；
真正的 `VNPyBridge` 一旦被实例化，bt_prod_run 会立刻报错（见 `_forbid_bridge`）。
"""


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)

    class _Dummy:
        def __init__(self, *a, **kw):
            pass

        def __call__(self, *a, **kw):
            return self

    return type(name, (_Dummy,), {})
