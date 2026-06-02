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
    system = platform.system()

    # 1. Check environment variable first
    env_path = os.getenv("MARCUS_WORKSPACE")
    if env_path:
        return Path(env_path)

    # 2. Windows default
    if system == "Windows":
        possible_paths = [
            Path("F:/pythonProject/AITrade/marcus-platform"),
            Path("F:/pythonProject/AITrade/workspace-marcus"),
            Path("C:/Users/fengx/projects/AITrade/marcus-platform"),
            Path("C:/Users/fengx/projects/AITrade/workspace-marcus"),
        ]
        for p in possible_paths:
            if p.exists():
                return p
        return possible_paths[0]

    # 3. Linux/Root default
    return Path("/root/.openclaw/workspace-marcus")

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
