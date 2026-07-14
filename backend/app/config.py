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
    TUSHARE_TOKEN: str = ""
    TUSHARE_API_URL: str = ""  # 代理地址, 如 https://ts.gyzcloud.top/api
    XUEQIU_TOKEN: str = ""

    # QQ Bot
    QQ_APP_ID: str = ""
    QQ_APP_SECRET: str = ""
    QQ_BOT_ENABLED: bool = False
    QQ_BOT_RECIPIENT: str = ""
    PI_SERVER_URL: str = "http://localhost:3001/chat"

    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    FRONTEND_PORT: int = 3000

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
        """Get Tushare token, raising error if not set."""
        if not self.TUSHARE_TOKEN:
            raise EnvironmentError("TUSHARE_TOKEN must be set in environment or .env file")
        return self.TUSHARE_TOKEN

    def get_xueqiu_token(self) -> str:
        """Get Xueqiu token, raising error if not set."""
        if not self.XUEQIU_TOKEN:
            raise EnvironmentError("XUEQIU_TOKEN must be set in environment or .env file")
        return self.XUEQIU_TOKEN


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
