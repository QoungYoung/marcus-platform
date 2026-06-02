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
        # Try common Windows paths (marcus-platform first, then workspace-marcus)
        possible_paths = [
            Path("F:/pythonProject/AITrade/marcus-platform"),
            Path("F:/pythonProject/AITrade/workspace-marcus"),
            Path("C:/Users/fengx/projects/AITrade/marcus-platform"),
            Path("C:/Users/fengx/projects/AITrade/workspace-marcus"),
        ]
        for p in possible_paths:
            if p.exists():
                return p
        # Fallback to marcus-platform
        return Path("F:/pythonProject/AITrade/marcus-platform")

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
