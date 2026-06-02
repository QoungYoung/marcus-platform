# -*- coding: utf-8 -*-
"""
Workspace Path Detector Utility for Marcus Platform

Provides paths for marcus-platform integration.
"""
import os
import platform
from pathlib import Path

def get_platform_root() -> Path:
    """Get marcus-platform root directory."""
    # Backend is at: marcus-platform/backend/app/core/trading/
    # Platform root is 3 levels up
    return Path(__file__).parent.parent.parent.parent.parent

def get_workspace() -> Path:
    """Get workspace path - uses marcus-platform."""
    return get_platform_root()

# Global paths
WORKSPACE = get_workspace()

def get_data_dir() -> Path:
    """Data directory containing stock_pool.db, trades.db, cache.db."""
    return WORKSPACE / "data"

def get_skills_dir() -> Path:
    """Skills directory."""
    return WORKSPACE / "skills"

def get_vnpy_dir() -> Path:
    return get_skills_dir() / "vnpy-paper-trading"

def get_xueqiu_dir() -> Path:
    return get_skills_dir() / "xueqiu-data-query"

def get_akshare_dir() -> Path:
    return get_skills_dir() / "akshare-news"

def get_marcus_integration_dir() -> Path:
    return get_skills_dir() / "marcus-vnpy-integration"

# Export as module-level variables for backward compatibility
VNPY_DIR = get_vnpy_dir()
XUEQIU_DIR = get_xueqiu_dir()
AKSHARE_DIR = get_akshare_dir()
MARCUS_INTEGRATION_DIR = get_marcus_integration_dir()
DATA_DIR = get_data_dir()