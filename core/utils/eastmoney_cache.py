#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东财实时接口缓存模块

每次成功调用东财 API 后将结果存入 JSON 缓存文件（按日存储）。
当所有重试都被拒绝连接时，返回本日上一次存储的数据，并标注缓存时点。

用法:
    from core.utils.eastmoney_cache import EMCache
    cache = EMCache()
    
    # 写入
    cache.save("market_outflow", {"main_net": -850.5, "total_turnover": 12000})
    cache.save("concept_flow", concept_data_list, subtype="concept")
    cache.save("concept_flow", industry_data_list, subtype="industry")
    
    # 读取（失败时回退）
    data, meta = cache.load_with_fallback("market_outflow")
    if meta["from_cache"]:
        print(f"⚠️ 使用缓存数据，时点: {meta['cached_at']}")
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict, Tuple, Union, List

# 缓存目录
try:
    from workspace_detector import get_data_dir
    CACHE_DIR = get_data_dir() / "eastmoney_cache"
except ImportError:
    CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "eastmoney_cache"

CACHE_DIR.mkdir(parents=True, exist_ok=True)


class EMCache:
    """东财实时数据缓存，按日分文件存储"""
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}
    
    def _today_file(self) -> Path:
        return CACHE_DIR / f"{datetime.now().strftime('%Y%m%d')}.json"
    
    def _load_today(self) -> dict:
        """加载今天的缓存文件，失败返回空"""
        f = self._today_file()
        if f.exists():
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    return json.load(fp)
            except (json.JSONDecodeError, IOError):
                pass
        return {}
    
    def _save_today(self, data: dict) -> None:
        """保存到今天的缓存文件"""
        try:
            with open(self._today_file(), 'w', encoding='utf-8') as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[EMCache] 写入失败: {e}", flush=True)
    
    def save(self, key: str, value: Any, subtype: str = None) -> None:
        """
        保存一份数据到今日缓存。
        
        Args:
            key: 数据类别（如 'market_outflow', 'concept_flow'）
            value: 要缓存的数据
            subtype: 子类别（如 concept 的 'concept' / 'industry'）
        """
        data = self._load_today()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if subtype:
            # 嵌套结构: data[key][subtype] = {"value": ..., "cached_at": ...}
            if key not in data:
                data[key] = {}
            data[key][subtype] = {"value": value, "cached_at": timestamp}
        else:
            data[key] = {"value": value, "cached_at": timestamp}
        
        self._save_today(data)
    
    def load(self, key: str, subtype: str = None) -> Tuple[Any, Dict]:
        """
        读取缓存数据。
        
        Returns:
            (value, metadata) — 如果无缓存则 (None, {"from_cache": False})
        """
        data = self._load_today()
        
        try:
            if subtype:
                entry = data.get(key, {}).get(subtype)
            else:
                entry = data.get(key)
            
            if entry and entry.get("value") is not None:
                return entry["value"], {
                    "from_cache": True,
                    "cached_at": entry.get("cached_at", "unknown"),
                    "key": key,
                    "subtype": subtype,
                }
        except (KeyError, TypeError):
            pass
        
        return None, {"from_cache": False}
    
    def load_with_fallback(self, key: str, subtype: str = None) -> Tuple[Any, Dict]:
        """
        同 load()，若缓存不存在返回 (None, {"from_cache": False})。
        调用方自行判断是否使用。
        """
        return self.load(key, subtype)
    
    def load_or_none(self, key: str, subtype: str = None) -> Optional[Any]:
        """仅返回缓存值，无缓存返回 None"""
        val, _ = self.load(key, subtype)
        return val
    
    def get_aged_minutes(self, key: str, subtype: str = None) -> Optional[float]:
        """获取缓存距今多少分钟，无缓存返回 None"""
        _, meta = self.load(key, subtype)
        if meta["from_cache"] and meta.get("cached_at"):
            try:
                cached_dt = datetime.strptime(meta["cached_at"], '%Y-%m-%d %H:%M:%S')
                return (datetime.now() - cached_dt).total_seconds() / 60
            except ValueError:
                pass
        return None
    
    def prune(self, keep_days: int = 3) -> int:
        """清理超过 N 天的缓存文件，返回删除数"""
        deleted = 0
        cutoff = datetime.now().strftime('%Y%m%d')
        for f in CACHE_DIR.glob("*.json"):
            try:
                date_str = f.stem
                if len(date_str) == 8 and date_str < cutoff:
                    f.unlink()
                    deleted += 1
            except (ValueError, OSError):
                pass
        return deleted


# 全局单例
_em_cache: Optional[EMCache] = None

def get_em_cache() -> EMCache:
    global _em_cache
    if _em_cache is None:
        _em_cache = EMCache()
    return _em_cache
