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

    # 2. Detect relative to this file: apps/paper-trading/ -> marcus-platform/
    detected = Path(__file__).parent.parent.parent
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
