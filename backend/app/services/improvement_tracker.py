#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
改进追踪系统（P2）— 记录专家组建议的执行状态，防止「设计→部署」链条断裂。

存储: data/improvement_tracker.json
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def _get_data_dir() -> Path:
    try:
        from workspace_detector import DATA_DIR
        return Path(str(DATA_DIR))
    except Exception:
        return Path(__file__).parent.parent.parent.parent / "data"


def _get_tracker_path() -> Path:
    return _get_data_dir() / "improvement_tracker.json"


def _load() -> list:
    path = _get_tracker_path()
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(items: list):
    path = _get_tracker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add_improvement(
    title: str,
    priority: str,  # P0 / P1 / P2
    category: str,  # 止损规则 / 数据管道 / 策略链 / 调度器 / 基础设施
    source: str = "panel",  # panel / manual / scheduler
    owner: str = "Marcus",
    details: str = "",
):
    """添加一条改进建议"""
    items = _load()
    items.append({
        "id": f"IMP-{datetime.now().strftime('%y%m%d')}-{len(items)+1:03d}",
        "title": title,
        "priority": priority,
        "category": category,
        "source": source,
        "owner": owner,
        "status": "open",
        "details": details,
        "created_at": datetime.now().isoformat(),
        "resolved_at": None,
        "verification": "",
    })
    _save(items)
    return items[-1]["id"]


def mark_resolved(imp_id: str, verification: str = ""):
    """标记为已完成"""
    items = _load()
    for item in items:
        if item["id"] == imp_id:
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now().isoformat()
            item["verification"] = verification
            _save(items)
            return True
    return False


def mark_rejected(imp_id: str, reason: str = ""):
    """标记为已拒绝"""
    items = _load()
    for item in items:
        if item["id"] == imp_id:
            item["status"] = "rejected"
            item["resolved_at"] = datetime.now().isoformat()
            item["verification"] = reason
            _save(items)
            return True
    return False


def get_pending() -> list:
    """获取所有待处理的改进"""
    return [i for i in _load() if i["status"] == "open"]


def get_stats() -> dict:
    """获取改进统计"""
    items = _load()
    total = len(items)
    resolved = sum(1 for i in items if i["status"] == "resolved")
    open_count = sum(1 for i in items if i["status"] == "open")
    rejected = sum(1 for i in items if i["status"] == "rejected")
    return {
        "total": total,
        "resolved": resolved,
        "open": open_count,
        "rejected": rejected,
        "execution_rate": round(resolved / total * 100, 1) if total > 0 else 0,
    }


# ── 初始化：记录本次专家组评审的关键发现 ──

def seed_panel_findings():
    """将本次群聊评审发现写入追踪表"""
    findings = [
        ("P0", "止损规则", "修复板块背离公式：3x→差值法", "panel"),
        ("P0", "数据管道", "修复 HWM 数据流：监控器内直接更新", "panel"),
        ("P0", "止损规则", "合并规则0b与规则2：消除保本逻辑重叠", "panel"),
        ("P0", "止损规则", "定义多规则同时触发的冲突解决SOP", "panel"),
        ("P1", "止损规则", "规则0a锚点动态上移：max(阶段底×0.97, HWM×0.90)", "panel"),
        ("P1", "止损规则", "规则3改为个股vs大盘相对表现判定", "panel"),
        ("P1", "止损规则", "规则2回吐比例收紧：≥8%→+6%/≥5%→+3.5%", "panel"),
        ("P2", "基础设施", "建立改进追踪机制（本模块）", "panel"),
        ("P2", "策略链", "积累≥100笔交易后重新校准阈值", "panel"),
        ("P2", "调度器", "解决资金闲置率>90%问题：审查选股管线", "panel"),
    ]

    existing = {i["title"] for i in _load()}
    for priority, category, title, source in findings:
        if title not in existing:
            add_improvement(title, priority, category, source=source)
