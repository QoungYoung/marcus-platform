# -*- coding: utf-8 -*-
"""
Centralized configuration management for Marcus Platform.
All configuration must come from environment variables (never hardcoded).
"""
import os
import platform
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Prefix
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql://marcus:marcus123@localhost:5432/marcus_trading"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"

    # Marcus Workspace - auto-detected if not set
    MARCUS_WORKSPACE: str = ""

    # API Keys (must be set in environment)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_HOST: str = "api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    # Tushare 数据源（2026-09-13 起）：datahubco 基础接口 + promax 聚合接口
    # 旧 gzcloud 代理 token 已失效，TUSHARE_* 仅作 legacy 兜底（TUSHARE_SOURCE=legacy）
    DATAHUBCO_API_URL: str = "http://datahubco.com/app-api/openapi/v1/tushare"
    DATAHUBCO_API_KEY: str = ""
    PROMAX_URL: str = "https://pcd.mobcvb.cn/tushare/pro"
    PROMAX_API_KEY: str = ""
    TUSHARE_SOURCE: str = "auto"          # auto / datahubco / promax / legacy
    TUSHARE_RELAY_TIMEOUT: int = 30
    TUSHARE_RELAY_ATTEMPTS: int = 3
    TUSHARE_RELAY_PAGE_SIZE: int = 5000
    TUSHARE_RELAY_CACHE_TTL: int = 300
    # deprecated: gzcloud 代理（token 已失效）
    TUSHARE_TOKEN: str = ""
    TUSHARE_API_URL: str = ""
    XUEQIU_TOKEN: str = ""

    # QQ Bot
    QQ_APP_ID: str = ""
    QQ_APP_SECRET: str = ""
    QQ_BOT_ENABLED: bool = False
    QQ_BOT_RECIPIENT: str = ""
    PI_SERVER_URL: str = "http://localhost:3001/chat"

    # Trading Engine
    ENGINE_BACKEND: str = "vnpy"  # "vnpy" (VN.PY PaperAccount) or "paper" (legacy PaperTradingEngine)

    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    FRONTEND_PORT: int = 3000

    # Golden Pit Sector Split (黄金坑板块拆分: 588000/159915 仅作择时指导, 坑内资金配置板块 ETF)
    GOLDEN_PIT_SECTOR_SPLIT_ENABLED: bool = False   # 灰度开关: false=dry-run 展示选筹; true=板块 ETF 下单(宽基不再直接买入)
    GOLDEN_PIT_SECTOR_TOP_N: int = 2                # 坑内选筹 TOP N 板块
    GOLDEN_PIT_SECTOR_MAX_WEIGHT: float = 0.5       # 单板块权重上限(归一化后截断)
    GOLDEN_PIT_SECTOR_COMBO_W_OVS: float = 0.5      # combo 超跌分权重
    GOLDEN_PIT_SECTOR_COMBO_W_MF: float = 0.5       # combo 资金流分权重
    GOLDEN_PIT_SECTOR_OVS_DAYS: int = 120           # 超跌窗口(距N日高点回撤)
    GOLDEN_PIT_SECTOR_MF_DAYS: int = 5              # 资金流累计窗口(日)
    GOLDEN_PIT_SECTOR_MF_MA_DAYS: int = 20          # 资金流均值窗口(日)
    GOLDEN_PIT_SECTOR_MIN_VALID: int = 4            # 有效信号板块数下限(不足则空仓等待)
    GOLDEN_PIT_SECTOR_EXIT_DOWN_DAYS: int = 3       # 板块ETF二次拐点退出: 连续回落天数
    GOLDEN_PIT_SECTOR_SIGNAL_MODE: str = "greed"    # 板块选筹信号模式: greed=超跌+板块贪婪; moneyflow=超跌+资金流(回滚)
    GOLDEN_PIT_SECTOR_POOL_SOURCE: str = "tech7"   # 板块选筹池来源: tech7=7只场内科技ETF(tech-hardware贪婪,默认); prod10=原10板块(funds-greed,回滚)

    # ── 做T系统（t_account） ──
    T_ACCOUNT_ENABLED: bool = True              # 做T总开关
    T_ACCOUNT_INITIAL_CAPITAL: float = 200000   # 做T账户初始资金
    BRZE_URL: str = "https://tu.brze.top"       # brze tushare 代理
    BRZE_TOKEN: str = ""                        # brze token（.env 配置）
    T_MONITOR_INTERVAL: int = 30                # TMonitor 周期（秒）
    T_MONITOR_INITIAL_OFFSET: int = 20          # 错峰启动偏移（秒）
    T_MONITOR_MAX_WORKERS: int = 5              # 并发取价上限
    T_MONITOR_CORE_MAX: int = 20                # 核心底仓数量上限
    T_SLIPPAGE_PCT: float = 0.001               # 滑点参数化假设（simulated slippage estimate）
    T_MAX_SINGLE_ORDER_PCT: float = 0.05        # 单笔 ≤ 净值 5%
    T_DAILY_LOSS_BREAKER_PCT: float = 0.02      # 日亏 2% 熔断
    T_DAILY_LOSS_WARN_PCT: float = 0.01         # 日亏 1% 预警
    T_MAX_DAILY_TURNOVER_RATIO: float = 3.0     # 日回转额 ≤ 3×净值
    T_FLOOR_LOWER_RATIO: float = 0.5            # 底仓保留下限（市值/成本）
    T_STOP_LOSS_PCT: float = 0.03               # 做T止损 -3%
    T_EOD_TIME: str = "14:45"                   # 尾盘归平开始时间

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        case_sensitive=True,
        extra="ignore",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-detect MARCUS_WORKSPACE if not set
        if not self.MARCUS_WORKSPACE:
            self.MARCUS_WORKSPACE = self._detect_workspace()

    @staticmethod
    def _detect_workspace() -> Path:
        """Detect Marcus platform root path."""
        # Use marcus-platform directory (backend is at marcus-platform/backend/)
        return Path(__file__).parent.parent.parent

    @property
    def workspace_path(self) -> Path:
        return Path(self.MARCUS_WORKSPACE)

    @property
    def memory_dir(self) -> Path:
        return self.workspace_path / "memory"

    @property
    def skills_dir(self) -> Path:
        return self.workspace_path / "apps"

    @property
    def data_dir(self) -> Path:
        return self.workspace_path / "data"

    @property
    def vnpy_dir(self) -> Path:
        return self.workspace_path / "apps" / "paper-trading"

    @property
    def xueqiu_dir(self) -> Path:
        return self.workspace_path / "core"

    @property
    def akshare_dir(self) -> Path:
        return self.workspace_path / "apps" / "news"

    @property
    def marcus_integration_dir(self) -> Path:
        return self.workspace_path / "apps" / "integration"

    def get_deepseek_key(self) -> str:
        """Get DeepSeek API key, raising error if not set."""
        if not self.DEEPSEEK_API_KEY:
            raise EnvironmentError("DEEPSEEK_API_KEY must be set in environment or .env file")
        return self.DEEPSEEK_API_KEY

    def get_tushare_token(self) -> str:
        """[deprecated] 旧 gzcloud Tushare token。

        2026-09-13 起取数走 datahubco/promax 中继（X-API-Key），不再需要该 token。
        为兼容历史代码里 `if not token: return` 的守卫写法：当旧 token 为空但中继源已配置时，
        返回中继凭据占位符（仅用于真值判断，不会被当作 Tushare token 使用）。
        """
        if self.TUSHARE_TOKEN:
            return self.TUSHARE_TOKEN
        if self.DATAHUBCO_API_KEY or self.PROMAX_API_KEY:
            return "<relay:datahubco+promax>"
        raise EnvironmentError(
            "未配置 Tushare 数据源：请在 .env 配置 DATAHUBCO_API_KEY 和/或 PROMAX_API_KEY"
        )

    def has_tushare_source(self) -> bool:
        """是否配置了可用的 Tushare 数据源（datahubco / promax / legacy 任一）。"""
        return bool(
            (self.DATAHUBCO_API_KEY or self.PROMAX_API_KEY)
            or (self.TUSHARE_API_URL and self.TUSHARE_TOKEN)
        )

    def get_xueqiu_token(self) -> str:
        """Get Xueqiu token, raising error if not set."""
        if not self.XUEQIU_TOKEN:
            raise EnvironmentError("XUEQIU_TOKEN must be set in environment or .env file")
        return self.XUEQIU_TOKEN


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
