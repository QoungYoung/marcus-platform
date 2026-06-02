# -*- coding: utf-8 -*-
"""
Scan Report API — 提供盘中扫描报告给 Pi Agent 交易决策
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/scan", tags=["Scan"])


def _get_workspace() -> Path:
    """获取 workspace 路径"""
    if hasattr(settings, 'workspace_path'):
        return settings.workspace_path
    return Path(__file__).parent.parent.parent.parent.parent


def _find_latest_scan_file(workspace: Path, date_str: str = None) -> Optional[Path]:
    """查找最新的扫描报告文件"""
    scan_dir = workspace / "memory" / "market-scan-logs"
    if not scan_dir.exists():
        return None

    if date_str:
        target = scan_dir / f"{date_str}-scans.jsonl"
        if target.exists():
            return target
        return None

    # 查找最近的文件
    jsonl_files = sorted(scan_dir.glob("*-scans.jsonl"), reverse=True)
    return jsonl_files[0] if jsonl_files else None


def _get_latest_pi_analysis(workspace: Path, date_str: str = None) -> Optional[dict]:
    """获取最新的 Pi 分析报告（由 _call_pi_analysis 持久化）"""
    analysis_dir = workspace / "memory" / "pi-analysis-logs"
    if not analysis_dir.exists():
        return None

    if date_str:
        target = analysis_dir / f"{date_str}-analysis.jsonl"
    else:
        # 查找最近的文件
        jsonl_files = sorted(analysis_dir.glob("*-analysis.jsonl"), reverse=True)
        target = jsonl_files[0] if jsonl_files else None

    if not target or not target.exists():
        return None

    try:
        lines = []
        with open(target, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
        if not lines:
            return None
        return json.loads(lines[-1])
    except Exception:
        return None


@router.get("/latest")
async def get_latest_scan_report(
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天"),
):
    """
    获取最新的盘中扫描报告。

    返回最后一条扫描记录，包含：
    - report: Markdown 格式的完整扫描报告
    - timestamp: 扫描时间
    - hot_concepts: 热门概念板块
    - watchlist: 候选观察列表
    - market_stance: 市场立场 (green/yellow/red)
    - position_limit: 仓位上限
    """
    workspace = _get_workspace()

    scan_file = _find_latest_scan_file(workspace, date)
    if not scan_file:
        raise HTTPException(
            status_code=404,
            detail=f"暂无扫描报告。请确保盘中扫描任务已运行。" +
                    (f" 查找路径: {workspace}/memory/market-scan-logs/" if not scan_file else "")
        )

    try:
        lines = []
        with open(scan_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)

        if not lines:
            raise HTTPException(status_code=404, detail="扫描报告文件为空")

        # 取最后一条
        last_scan = json.loads(lines[-1])

        # 尝试解析 market_stance
        stance = 'yellow'
        if last_scan.get('adjusted_strategy') and last_scan['adjusted_strategy'].get('stance'):
            stance = last_scan['adjusted_strategy']['stance']
        elif last_scan.get('scan_result') and last_scan['scan_result'].get('stance'):
            stance = last_scan['scan_result']['stance']
        elif last_scan.get('market_stance'):
            stance = last_scan['market_stance']
        elif last_scan.get('stance_code'):
            stance = last_scan['stance_code']

        # 尝试获取 position_limit
        position_limit = 60
        if last_scan.get('adjusted_strategy') and last_scan['adjusted_strategy'].get('position_limit') is not None:
            position_limit = last_scan['adjusted_strategy']['position_limit']
        elif last_scan.get('position_limit') is not None:
            position_limit = last_scan['position_limit']

        # 获取 watchlist
        watchlist = last_scan.get('watchlist', [])

        # 获取 hot_concepts
        hot_concepts = last_scan.get('hot_concepts', [])

        report = last_scan.get('report', '')

        # === 同时读取最新的 Pi 分析报告 ===
        pi_analysis = _get_latest_pi_analysis(workspace, date)
        # Pi 分析覆盖系统立场（更权威）
        if pi_analysis:
            stance = pi_analysis.get('stance', stance)
            position_limit = pi_analysis.get('position_limit', position_limit)

        return {
            "file": str(scan_file),
            "scan_count": len(lines),
            "timestamp": last_scan.get('timestamp', ''),
            "market_stance": stance,
            "position_limit": position_limit,
            "hot_concepts": hot_concepts[:10] if isinstance(hot_concepts, list) else hot_concepts,
            "watchlist": watchlist[:15] if isinstance(watchlist, list) else watchlist,
            "report": report,
            "pi_analysis": {
                "timestamp": pi_analysis.get('timestamp', ''),
                "stance": pi_analysis.get('stance', stance),
                "position_limit": pi_analysis.get('position_limit', position_limit),
                "reason": pi_analysis.get('reason', ''),
                "report": pi_analysis.get('report', ''),
            } if pi_analysis else None,
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"扫描报告 JSON 解析错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_scan_history(
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
):
    """
    获取盘中扫描历史记录。

    返回当天所有扫描的时间戳摘要，用于 Pi Agent 了解市场变化节奏。
    """
    workspace = _get_workspace()

    scan_file = _find_latest_scan_file(workspace, date)
    if not scan_file:
        raise HTTPException(status_code=404, detail="暂无扫描历史")

    try:
        scans = []
        with open(scan_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    scan = json.loads(line)
                    stance = 'yellow'
                    if scan.get('adjusted_strategy') and scan['adjusted_strategy'].get('stance'):
                        stance = scan['adjusted_strategy']['stance']
                    elif scan.get('market_stance'):
                        stance = scan['market_stance']

                    scans.append({
                        "timestamp": scan.get('timestamp', ''),
                        "market_stance": stance,
                        "hot_concepts": scan.get('hot_concepts', [])[:5] if isinstance(scan.get('hot_concepts'), list) else [],
                        "watchlist_count": len(scan.get('watchlist', [])),
                    })

        return {
            "file": str(scan_file),
            "total_scans": len(scans),
            "scans": scans[-limit:],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
