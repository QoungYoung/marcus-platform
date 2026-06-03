# -*- coding: utf-8 -*-
"""
Workspace Path Detector Utility

Provides cross-platform workspace path detection.
Replace all hardcoded "/root/.openclaw/workspace-marcus" with:
    from workspace_detector import WORKSPACE
"""
import os
import platform
from pathlib import Path

def get_workspace() -> Path:
    """Detect Marcus workspace path with cross-platform support."""
    # 1. Check environment variable first
    env_path = os.getenv("MARCUS_WORKSPACE")
    if env_path:
        return Path(env_path)

    # 2. Detect relative to this file: core/ -> marcus-platform/
    detected = Path(__file__).parent.parent
    if detected.exists():
        return detected

    # 3. Fallback for Docker: /app
    if Path("/app").exists():
        return Path("/app")

    # 4. Last resort
    return Path(".")

# Global workspace instance
WORKSPACE = get_workspace()

# Common subdirectories
def get_vnpy_dir() -> Path:
    return WORKSPACE / "apps" / "paper-trading"

def get_xueqiu_dir() -> Path:
    return WORKSPACE / "core"

def get_akshare_dir() -> Path:
    return WORKSPACE / "apps" / "news"

def get_data_dir() -> Path:
    return WORKSPACE / "data"

def get_scanner_dir() -> Path:
    return WORKSPACE / "apps" / "scanner"

def get_trader_dir() -> Path:
    return WORKSPACE / "apps" / "trader"

def get_review_dir() -> Path:
    return WORKSPACE / "apps" / "review"

def get_core_dir() -> Path:
    return WORKSPACE / "core"

def get_jobs_dir() -> Path:
    return WORKSPACE / "jobs"

# Common subdirectories (as constants for direct import)
VNPY_DIR = get_vnpy_dir()
XUEQIU_DIR = get_xueqiu_dir()
AKSHARE_DIR = get_akshare_dir()
DATA_DIR = get_data_dir()
MARCUS_INTEGRATION_DIR = WORKSPACE / "apps" / "integration"
