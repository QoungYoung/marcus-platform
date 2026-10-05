# -*- coding: utf-8 -*-
"""alert_exceptions.py —— **在"异常抛出点"接告警**（账本 §9.624 ✓）

## 用户的判断（对的 ✓）

> 「不是，抛出异常来你推送给我不就行吗，怎么还接 print，这个 sqlite 也不是 print 啊」

- ★ **接 print / 接 logging 都不够** ✗：`sqlite3.OperationalError` 是 **`raise` 出来的** ✓，
  被上层 `except` 接住后**打印子进程输出** ✗ ⇒ **日志桥拦不到它** ✗
- ★ **未捕获**的异常有 `sys.excepthook` ✓（会推 ✓）；**被 `try/except` 吞掉/降级**的 ✗
  ⇒ **到不了 excepthook** ✗✓ ⇒ 于是"**看着在跑、其实某道判据没跑**" ✗

## 做法 ✓

用 **`sys.monitoring`**（CPython 3.12+ ✓）监听 **`RAISE` 事件** ✗ —— 它**在异常抛出瞬间**触发 ✓，
**无论后面是否被 catch** ✓ ⇒ ⇒ 真正做到"**抛出异常就推给你**" ✓
- **控噪** ✓（关键 ✗）：①忽略控制流异常（`StopIteration`／`GeneratorExit`／`KeyboardInterrupt`／
  `SystemExit`／`asyncio.CancelledError` ✓）②**按 (文件,行,类型) 硬去重** ✓（默认 600 秒一次 ✓，
  并累计次数 ✓）③**只报"首次"** ✓ ⇒ 输出量有界 ✓
- 开关 ✓：`WOLF_ALERT_ON_RAISE=1`（**库内默认 0** ⇒ 生产零影响 ✓）
- 另配 ✓：`subprocess.run` 的 **rc≠0** 也统一接上 ✓（子进程失败 = 抛不出来的那类 ✗）
"""
from __future__ import annotations
import os
import sys
import threading
import time

_STATE = {"installed": False, "seen": {}, "counts": {}}
_LOCK = threading.Lock()
_IGNORE = ("StopIteration", "GeneratorExit", "KeyboardInterrupt", "SystemExit", "CancelledError")
_DEDUP_SEC = 600.0
_last_gc = [0.0]


def _should_ignore(exc_type) -> bool:
    name = getattr(exc_type, "__name__", str(exc_type))
    return any(x in name for x in _IGNORE)


# ★ 账本 §9.640 ✓（用户贴来 QQ 里的 pandas/vnpy 噪音 ✗）：
#   **"预期内的 raise"**（可选依赖探测／monkey-patch 探针／`getattr` 守卫 ✓）
#   不该推 QQ ✗ ⇒ 只**落盘**（看板可查 ✓）：
#     · `ImportError`／`ModuleNotFoundError` ⇒ 可选依赖守卫 ✓（`vnpy`／`core.xueqiu_engine` ✓）
#     · `AttributeError` ⇒ 探针式 `getattr`（我们自己的 shim ✓，实测 `__init__.py:19` ✓）
#   其余（`ValueError`／`KeyError`／`OperationalError` … ✓）⇒ 照常**推 QQ** ✓
# ★ 追加 ✓（用户贴来的那屏 ✓）：`FileNotFoundError` 也是**降级类** ✗
#   （可选文件缺失 ⇒ 走兜底 ✓，实测 `wolf_weekend_hedge.json`／`round_trip_state.json`／
#     `trigger_mute_<日>.json`／`stock_5m_bt/<code>.json` ✓）⇒ **只落盘、不推 QQ** ✓
# ★ 追加 ✓（用户再贴一屏 ✓）：
#   · **`NetOffline`** ⇒ 回测**故意**断网时抛的（`BT_NET_OFFLINE` ✓，
#     实测 `wolf_index_context.py:104 … datahubco.com/…/index_member_all` ✓）
#     ⇒ **预期行为** ✗ ⇒ 只落盘 ✓
#   · `No module named 'core.realtime_indicators'`（`bt_agent_tools.py:275` ✓）⇒ 已被 ImportError 覆盖 ✓
_QUIET_TYPES = ("ImportError", "ModuleNotFoundError", "AttributeError", "FileNotFoundError",
                "JSONDecodeError", "UnicodeDecodeError", "NetOffline", "TushareRelayError")


def _emit(kind: str, where: str, exc) -> None:
    """按 (where, 类型) 去重后转发给 alert_hub.note（落盘 ＋ 推 QQ ✓）。"""
    try:
        etype = type(exc).__name__ if not isinstance(exc, str) else "str"
        key = "%s|%s|%s" % (kind, where, etype)
        now = time.time()
        with _LOCK:
            last = _STATE["seen"].get(key, 0.0)
            _STATE["counts"][key] = _STATE["counts"].get(key, 0) + 1
            if now - last < _DEDUP_SEC:
                return
            _STATE["seen"][key] = now
            n = _STATE["counts"][key]
            _STATE["counts"][key] = 0
            # 轻量 GC：防止长期运行字典膨胀 ✓
            if len(_STATE["seen"]) > 4000 and now - _last_gc[0] > 60:
                _STATE["seen"] = {k: v for k, v in _STATE["seen"].items() if now - v < _DEDUP_SEC}
                _last_gc[0] = now
        try:
            from app.services import alert_hub as _ah
        except Exception:
            _ah = None
        if _ah is None:
            return
        _quiet = any(t in type(exc).__name__ for t in _QUIET_TYPES)
        if _quiet:
            _ah.note_silent(where, msg="%s×%d：%s" % (kind, max(1, n), str(exc)[:260]))   # 只落盘 ✓
        else:
            _ah.note(where, msg="%s×%d：%s" % (kind, max(1, n), str(exc)[:260]))          # 落盘 ＋ 推 ✓
    except Exception as _e:
        try:
            sys.stderr.write("[alert_exc] emit 失败: %s\n" % str(_e)[:80])
        except Exception as _e_r:
            sys.stderr.write("[alert_exc] 内部异常: %s\n" % str(_e_r)[:80])


def _install_monitoring() -> bool:
    """RAISE 事件（异常抛出瞬间 ✓，含被 catch 的 ✓）。"""
    mon = getattr(sys, "monitoring", None)
    if mon is None:
        return False
    TOOL = 3  # 任意未占用槽位 ✓（PROFILER_ID 等由解释器保留 ✓）

    # ★ 只报"我们自己的代码" ✓（否则会被标准库/解释器的控制流异常淹没 ✗：
    #   实测噪音来自 `<frozen os>`／`_collections_abc`／`weakref` 等 ✓）
    # ★ 账本 §9.640 ✓（用户贴来 QQ 里的 pandas 噪音 ✗）：
    #   旧写法 `_OURS = ("/jobs/", "/backend/app/", "/apps/", "/core/", "/main_line/")` **有漏洞** ✗：
    #     `/core/` 会匹配 **`site-packages/pandas/core/dtypes.py`** ✗
    #     ⇒ pandas/numpy 内部的**控制流异常**（`CategoricalDtype from 'M8'` 之类 ✓）
    #       被当成"自家代码"报了出来 ✗ ⇒ **QQ 被刷屏** ✗✓
    #   ⇒ 改为：**只认"仓库根目录前缀"** ✓（安装时按本文件位置算出 ✓，天然可移植 ✓）
    #     ＋ 明确**排除**第三方目录（`site-packages`／`dist-packages`／`.venv` ✓）
    _ROOT_PREFIX = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
    _THIRD = ("/site-packages/", "/dist-packages/", "/.venv/", "/node_modules/")

    def _is_ours(fs: str) -> bool:
        if fs.startswith("<"):
            return False
        if not fs.startswith(_ROOT_PREFIX):
            return False
        if any(t in fs for t in _THIRD):
            return False
        return True

    def _on_raise(code, offset, exc):
        try:
            if _should_ignore(type(exc)):
                return
            f = getattr(code, "co_filename", "?")
            fs = str(f)
            if not _is_ours(fs):
                return                      # 第三方/标准库/解释器/临时串 ✗ ⇒ 不报 ✓
            if isinstance(exc, (ModuleNotFoundError, ImportError)):
                return                      # ★ 可选依赖的 import 守卫是**预期**行为 ✗（如 vnpy ✓）
            name = os.path.basename(fs)
            line = getattr(code, "co_firstlineno", 0)
            _emit("raise", "%s:%s" % (name, line), exc)
        except Exception as _e_or:
            # ★ 回调自身绝不抛 ✓，但**留痕** ✓（防回潮门要求；stderr 不经 logger ⇒ 不递归 ✓）
            sys.stderr.write("[alert_exc] on_raise 失败: %s\n" % str(_e_or)[:80])

    try:
        mon.use_tool_id(TOOL, "marcus-alert")
        mon.register_callback(TOOL, mon.events.RAISE, _on_raise)
        mon.set_events(TOOL, mon.events.RAISE)
        _STATE["monitoring"] = TOOL
        return True
    except Exception as e:
        sys.stderr.write("[alert_exc] monitoring 安装失败: %s\n" % str(e)[:90])
        return False


def _install_subprocess_rc() -> bool:
    """`subprocess.run/Popen` ⇒ **rc≠0 时推一条** ✓（子进程无法"抛"给父进程 ✗）。"""
    try:
        import subprocess as _sp
        if getattr(_sp.run, "_marcus_wrapped", False):
            return True
        _orig_run = _sp.run

        def _run(*a, **kw):
            r = _orig_run(*a, **kw)
            try:
                rc = int(getattr(r, "returncode", 0) or 0)
                if rc != 0:
                    cmd = a[0] if a else kw.get("args")
                    if isinstance(cmd, (list, tuple)):
                        cmd = " ".join(os.path.basename(str(x)) for x in cmd[:4])
                    _emit("subprocess", "subprocess.run", "rc=%s cmd=%s" % (rc, str(cmd)[:120]))
            except Exception as _e_s:
                print("[silent-fix] %s: %s" % (__name__, str(_e_s)[:70]), flush=True)
            return r

        _run._marcus_wrapped = True
        _sp.run = _run
        return True
    except Exception as e:
        sys.stderr.write("[alert_exc] subprocess 包装失败: %s\n" % str(e)[:90])
        return False


def install(level: int = 0) -> bool:
    """幂等安装 ✓；返回是否安装 ✓（开关 `WOLF_ALERT_ON_RAISE` 默认 0 ✓）。"""
    if _STATE["installed"]:
        return True
    if str(os.getenv("WOLF_ALERT_ON_RAISE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return False
    ok_mon = _install_monitoring()
    ok_sp = _install_subprocess_rc()
    _STATE["installed"] = True
    sys.stderr.write("[alert_exc] ✅ 异常抛出点已接告警 ✓（monitoring=%s ✓｜subprocess rc=%s ✓）\n"
                     % (ok_mon, ok_sp))
    return ok_mon or ok_sp


if __name__ == "__main__":
    print(install())
