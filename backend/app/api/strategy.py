# -*- coding: utf-8 -*-
"""
Strategy API endpoints.
"""
import sys
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models.strategy import StrategyResponse, ScanResponse, ScanHistoryResponse

settings = get_settings()

router = APIRouter(prefix="/strategy", tags=["Strategy"])


@router.get("/current", response_model=StrategyResponse)
async def get_current_strategy():
    """
    Get current trading strategy state.
    Includes stance, position limits, watchlist, and risk parameters.
    """
    import json

    # Read strategy state file
    state_file = settings.data_dir / "strategy_state.json"
    if not state_file.exists():
        return StrategyResponse(
            stance="UNKNOWN",
            stance_code="unknown",
            position_limit=80,
            stop_loss=-8,
            take_profit=20,
            trailing_stop=5,
            sentiment_score=50,
            sentiment_label="neutral",
            watchlist=[],
            updated_at=datetime.now(),
        )

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    # Get watchlist
    watchlist_file = settings.data_dir / "dynamic_watchlist.json"
    watchlist = []
    if watchlist_file.exists():
        with open(watchlist_file, "r", encoding="utf-8") as f:
            watchlist_data = json.load(f)
            watchlist = watchlist_data.get("candidates", [])[:10]

    return StrategyResponse(
        stance=state.get("stance", "UNKNOWN"),
        stance_code=state.get("stance_code", "unknown"),
        position_limit=state.get("position_limit", 80),
        stop_loss=state.get("stop_loss", -8),
        take_profit=state.get("take_profit", 20),
        trailing_stop=state.get("trailing_stop", 5),
        sentiment_score=state.get("sentiment_score", 50),
        sentiment_label=state.get("sentiment_label", "neutral"),
        gap_risk=state.get("gap_risk"),
        fund_flow=state.get("fund_flow"),
        watchlist=watchlist,
        updated_at=datetime.now(),
    )


@router.get("/scans", response_model=ScanHistoryResponse)
async def get_scan_history(
    limit: int = Query(10, ge=1, le=50, description="Number of records"),
):
    """
    Get recent market scan history.
    """
    import json
    import glob

    scan_files = sorted(
        glob.glob(str(settings.memory_dir / "market-scan-logs" / "*-scans.jsonl")),
        reverse=True,
    )[:1]

    scans = []
    if scan_files:
        scan_file = scan_files[0]
        with open(scan_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    scans.append(ScanResponse(
                        scan_time=datetime.fromisoformat(data.get("time", datetime.now().isoformat())),
                        stance=data.get("stance", "UNKNOWN"),
                        stance_code=data.get("stance_code", "unknown"),
                        position_limit=data.get("position_limit", 80),
                        sentiment_score=data.get("sentiment_score", 50),
                        hot_concepts=data.get("hot_concepts", data.get("hot_industries", [])),
                        watchlist=data.get("watchlist", []),
                        sector_allocation=data.get("sector_allocation", {}),
                        gap_risk=data.get("gap_risk"),
                    ))
                    if len(scans) >= limit:
                        break
                except Exception:
                    continue

    return ScanHistoryResponse(scans=scans, total=len(scans))
