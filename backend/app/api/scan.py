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

        # === v1.5: Pi 是唯一决策者，scan stance 仅作参考输入 ===
        pi_analysis = _get_latest_pi_analysis(workspace, date)

        return {
            "file": str(scan_file),
            "scan_count": len(lines),
            "timestamp": last_scan.get('timestamp', ''),
            # scan 系统的参考数据（仅作输入，不绑定 Pi 判断）
            "scan_stance": stance,
            "scan_position_limit": position_limit,
            # Pi 的权威立场（v1.5: Pi 综合分析后决定）
            "market_stance": pi_analysis.get('stance', stance) if pi_analysis else stance,
            "position_limit": pi_analysis.get('position_limit', position_limit) if pi_analysis else position_limit,
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


@router.get("/pi-analysis")
async def get_pi_analysis_history(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD，默认本周一"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD，默认今天"),
):
    """
    按日期范围查询 Pi 分析历史记录。

    返回指定日期范围内 `memory/pi-analysis-logs/` 下所有 Pi 分析报告。
    每周反思任务通过此端点获取整周全部 Pi 分析记录。

    返回格式:
    {
        "date_range": {"start": "...", "end": "..."},
        "days_count": N,
        "total_records": N,
        "records": [
            {"date": "2026-06-01", "timestamp": "...", "task_name": "...", "stance": "...", "position_limit": N, "reason": "...", "report": "..."}
        ]
    }
    """
    from datetime import timedelta

    workspace = _get_workspace()
    analysis_dir = workspace / "memory" / "pi-analysis-logs"

    if not analysis_dir.exists():
        return {
            "date_range": {"start": start_date or "auto", "end": end_date or "auto"},
            "days_count": 0,
            "total_records": 0,
            "records": [],
        }

    # 解析日期范围
    today = datetime.now()
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date 格式错误，应为 YYYY-MM-DD")
    else:
        end_dt = today

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date 格式错误，应为 YYYY-MM-DD")
    else:
        # 默认本周一
        start_dt = today - timedelta(days=today.weekday())

    # 收集范围内所有 analysis 文件
    all_records = []
    current = start_dt
    while current <= end_dt:
        date_str = current.strftime("%Y-%m-%d")
        target = analysis_dir / f"{date_str}-analysis.jsonl"
        if target.exists():
            try:
                with open(target, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            record["date"] = date_str
                            all_records.append(record)
            except Exception:
                pass
        current += timedelta(days=1)

    # 按时间排序
    all_records.sort(key=lambda r: r.get("timestamp", ""))

    return {
        "date_range": {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": end_dt.strftime("%Y-%m-%d"),
        },
        "days_count": len(set(r["date"] for r in all_records)),
        "total_records": len(all_records),
        "records": all_records,
    }


@router.get("/trade-reports")
async def get_trade_history(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD，默认本周一"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD，默认今天"),
):
    """
    按日期范围查询 Pi 交易执行报告。

    返回指定日期范围内 `memory/trade-reports/` 下所有交易报告。
    每周反思任务通过此端点获取整周全部交易执行记录，
    用于评估策略执行质量（买卖决策、仓位变化、组合逻辑）。

    返回格式:
    {
        "date_range": {"start": "...", "end": "..."},
        "days_count": N,
        "total_records": N,
        "records": [
            {"date": "2026-06-01", "timestamp": "...", "task_id": "...", "stance": "...", "position_limit": N, "reason": "...", "report": "..."}
        ]
    }
    """
    from datetime import timedelta

    workspace = _get_workspace()
    trade_dir = workspace / "memory" / "trade-reports"

    if not trade_dir.exists():
        return {
            "date_range": {"start": start_date or "auto", "end": end_date or "auto"},
            "days_count": 0,
            "total_records": 0,
            "records": [],
        }

    # 解析日期范围
    today = datetime.now()
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date 格式错误，应为 YYYY-MM-DD")
    else:
        end_dt = today

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date 格式错误，应为 YYYY-MM-DD")
    else:
        start_dt = today - timedelta(days=today.weekday())

    # 收集范围内所有 trade-reports 文件
    all_records = []
    current = start_dt
    while current <= end_dt:
        date_str = current.strftime("%Y-%m-%d")
        target = trade_dir / f"{date_str}-trades.jsonl"
        if target.exists():
            try:
                with open(target, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            record["date"] = date_str
                            all_records.append(record)
            except Exception:
                pass
        current += timedelta(days=1)

    # 按时间排序
    all_records.sort(key=lambda r: r.get("timestamp", ""))

    return {
        "date_range": {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": end_dt.strftime("%Y-%m-%d"),
        },
        "days_count": len(set(r["date"] for r in all_records)),
        "total_records": len(all_records),
        "records": all_records,
    }


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
