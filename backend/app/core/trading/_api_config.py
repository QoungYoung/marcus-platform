# -*- coding: utf-8 -*-
"""共享 API 配置 — 所有配置统一从项目根目录 .env 读取，与环境变量保持一致。

2026-09-13 变更：原 Tushare 取数统一走 gzcloud 代理（`TUSHARE_API_URL=https://ts.gyzcloud.top/api`），
该镜像 token 已过期（HTTP 401「Token无效或已过期」），所有派生路径（选股 / regime / 日线回填 /
分钟线）全部取不到数据。现统一改用 **datahubco（基础接口，RDS 快）+ promax（聚合接口）** 中继，
实现见 `core/tushare_relay.py`：

- datahubco：`DATAHUBCO_API_KEY`，80 个基础接口（daily/daily_basic/trade_cal/index_daily/fund_daily/…）
- promax   ：`PROMAX_API_KEY`，298 个聚合接口（stk_mins/rt_*/moneyflow_ind_dc/dc_*/ths_*/sw_daily/pro_bar/…）

`get_tushare_pro()` 的返回值与 `tushare.pro.client.DataApi` 兼容（`pro.daily(...)` /
`pro.query('daily', ...)` / `ts.pro_bar(api=pro, ...)` 均可用，返回 DataFrame），
因此原有调用点无需改动即可切到新接口。
"""
import os
import sys
from pathlib import Path


def _load_env():
    """从项目根目录加载 .env 文件到 os.environ（不覆盖已有的环境变量）。"""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / ".env"
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return
        current = current.parent


_load_env()

# ── DeepSeek API ──────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_HOST = os.getenv("DEEPSEEK_API_HOST", "api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── Tushare 数据源（中继） ────────────────────────────────
# 旧字段保留仅为兼容历史代码/配置，实际取数不再使用（见 core/tushare_relay.py）
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
TUSHARE_API_URL = os.getenv("TUSHARE_API_URL", "")  # deprecated: gzcloud 代理，token 已失效
# 外部调用有界超时（秒）：单次调用 <=10s，失败走缓存/降级（D3）
TUSHARE_TIMEOUT = int(os.getenv("TUSHARE_TIMEOUT", "10"))
# 中继单次 HTTP 超时（promax 上游偶发慢，默认 30s）
TUSHARE_RELAY_TIMEOUT = int(os.getenv("TUSHARE_RELAY_TIMEOUT", "30"))
# datahubco / promax 入口（可用 .env 覆盖）
DATAHUBCO_API_URL = os.getenv("DATAHUBCO_API_URL", "http://datahubco.com/app-api/openapi/v1/tushare")
PROMAX_URL = os.getenv("PROMAX_URL", "https://pcd.mobcvb.cn/tushare/pro")


def _load_relay_module():
    """定位并加载 `core/tushare_relay.py`（backend 与 jobs/apps 脚本共用同一份实现）。"""
    try:  # 已在 sys.path（core 目录）
        import tushare_relay  # type: ignore
        return tushare_relay
    except ImportError:
        pass

    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / "core" / "tushare_relay.py"
        if candidate.exists():
            if str(candidate.parent) not in sys.path:
                sys.path.insert(0, str(candidate.parent))
            import tushare_relay  # type: ignore
            return tushare_relay
        current = current.parent
    raise ImportError("找不到 core/tushare_relay.py：请确认仓库根目录 core/ 已随代码一起部署")


_relay_module = None


def _relay():
    global _relay_module
    if _relay_module is None:
        _relay_module = _load_relay_module()
    return _relay_module


def get_tushare_pro():
    """
    统一获取 Tushare 数据客户端（datahubco + promax 中继，替代已失效的 gzcloud 代理）。

    返回对象与 `tushare.pro.client.DataApi` 兼容：
      pro.daily(ts_code=..., start_date=..., end_date=...)  → DataFrame
      pro.query('daily', ts_code=...)                       → DataFrame
      ts.pro_bar(api=pro, ts_code=..., adj='qfq')           → DataFrame
    所有调用 `ts.pro_api()` 的地方都应改用此函数；接口路由/分页/重试由中继内部完成。
    无可用数据源时抛 EnvironmentError（API 层统一转 503）。
    """
    return _relay().get_relay()


def tushare_relay_ready() -> bool:
    """是否至少配置了一个可用数据源（DATAHUBCO_API_KEY / PROMAX_API_KEY）。"""
    try:
        return bool(_relay().available_sources())
    except Exception:
        return False


def ensure_tushare_ready() -> None:
    """无可用数据源时抛 EnvironmentError（供 API 层返回 503 配置错误）。"""
    if not tushare_relay_ready():
        raise EnvironmentError(
            "未配置 Tushare 数据源：请在 .env 配置 DATAHUBCO_API_KEY 和/或 PROMAX_API_KEY"
        )
