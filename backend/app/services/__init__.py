# -*- coding: utf-8 -*-
"""Services package

★ 2026-11-08：删掉 `from .scheduler_service import scheduler_service` 的包级再导出。

原因：它让 **任何** `import app.services.<anything>` 都以导入调度器为前提——
实测代价 2.8 秒，并且伴随三个副作用：读 788 条执行历史、装全局异常钩子、
起 alert_hub。后果是 `app.services.wave_store` 这种纯读模块也没法在作业进程里
轻量使用，逼得 5 个作业只能绕过它直接读文件。

全仓没有任何一处依赖包级再导出（已核对：0 处 `from app.services import scheduler_service`），
5 处调度器调用全部是显式的 `from app.services.scheduler_service import scheduler_service`，
所以删除是安全的。需要调度器请显式导入子模块。
"""

__all__: list[str] = []
