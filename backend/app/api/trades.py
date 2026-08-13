# -*- coding: utf-8 -*-
"""
Trades API endpoints.
"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import get_settings
from app.database import SessionLocal
from app.models.paper_trade import PaperTrade
from app.models.paper_trade import PaperAccount
from app.models.trade import TradeRequest, TradeResponse, OrderResponse, TradeHistoryResponse, VoidRequest, VoidResponse

settings = get_settings()

router = APIRouter(prefix="/trades", tags=["Trades"])

# Stock name cache
_stock_name_cache = {}


def _get_stock_name(symbol: str) -> str:
    """Lookup stock name from PostgreSQL stock_pool table."""
    if symbol in _stock_name_cache:
        return _stock_name_cache[symbol]

    try:
        from app.services.market_reference import get_stock_name
        name = get_stock_name(symbol)
        if name:
            _stock_name_cache[symbol] = name
            return name
    except Exception:
        pass

    _stock_name_cache[symbol] = symbol
    return symbol


def _validate_account(account: str) -> None:
    """校验账户是否存在且启用，未知账户返回 400。"""
    db = SessionLocal()
    try:
        ok = db.query(PaperAccount).filter(
            PaperAccount.account_id == account,
            PaperAccount.enabled == 1,
        ).first()
    finally:
        db.close()
    if not ok:
        raise HTTPException(status_code=400, detail=f"未知账户: {account}")


def _make_executor(request: Request, account: str = "stock"):
    """按账户创建执行器：stock 走 VN.PY bridge，其他账户走 PaperTradingEngine 直连。"""
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    bridge = getattr(request.app.state, 'vnpy_bridge', None)
    if account == "stock":
        return MarcusVNPyExecutor(bridge=bridge, account_id=account)
    from paper_engine import PaperTradingEngine
    from workspace_detector import DATA_DIR
    engine = PaperTradingEngine(data_dir=str(DATA_DIR), account_id=account)
    return MarcusVNPyExecutor(engine=engine, account_id=account)


@router.post("", response_model=TradeResponse)
async def execute_trade(trade: TradeRequest, request: Request):
    """
    Execute a trade (buy or sell).
    Note: This is paper trading - no real money involved.

    Returns detailed reason on failure/rejection, e.g.:
    - '资金不足' (insufficient funds)
    - '超过单笔最大仓位 (40%)' (exceeds max position per trade)
    - '无持仓' (no position to sell)
    - '卖出数量超过持仓' (sell volume exceeds holdings)
    - 'VN.PY 买入失败' / 'VN.PY 卖出失败' (engine failure)
    - 'VN.PY 撮合失败，资金已解冻' (match failure, funds unfrozen)
    """
    try:
        _validate_account(trade.account)
        executor = _make_executor(request, trade.account)

        if trade.side.lower() == "buy":
            result = executor.buy(
                symbol=trade.symbol,
                price=trade.price,
                volume=trade.volume,
                reason=trade.reason or "",
            )
        else:
            result = executor.sell(
                symbol=trade.symbol,
                price=trade.price,
                volume=trade.volume,
                reason=trade.reason or "",
            )

        # Extract reason/message from executor result
        fail_reason = result.get("reason", "")
        direction = "买入" if trade.side.lower() == "buy" else "卖出"
        
        # Build a detailed message for rejected/failed trades
        if result.get("status") == "rejected":
            detail_msg = fail_reason or "交易被拒绝"
            # Add context info if available
            if result.get("required"):
                detail_msg += f" (需要 ¥{result['required']:,.2f}"
            if result.get("available") is not None:
                detail_msg += f"，可用 ¥{result['available']:,.2f}"
            if result.get("required") or result.get("available") is not None:
                detail_msg += ")"
            print(f"[交易] ❌ {direction} {trade.symbol} 被拒绝: {detail_msg}", flush=True)
            return TradeResponse(
                order_id="",
                status="rejected",
                symbol=trade.symbol,
                direction=direction,
                price=trade.price,
                volume=trade.volume,
                amount=trade.price * trade.volume,
                timestamp=datetime.now(),
                reason=fail_reason,
                message=detail_msg,
            )
        
        if result.get("status") == "failed":
            detail_msg = fail_reason or "交易执行失败"
            print(f"[交易] ❌ {direction} {trade.symbol} 失败: {detail_msg}", flush=True)
            return TradeResponse(
                order_id="",
                status="failed",
                symbol=trade.symbol,
                direction=direction,
                price=trade.price,
                volume=trade.volume,
                amount=trade.price * trade.volume,
                timestamp=datetime.now(),
                reason=fail_reason,
                message=detail_msg,
            )

        # Success
        print(f"[交易] ✅ {direction} {trade.symbol} x{result.get('volume', trade.volume)} @ ¥{trade.price:.2f}", flush=True)
        return TradeResponse(
            order_id=result.get("order_id", ""),
            status=result.get("status", "executed"),
            symbol=trade.symbol,
            direction=direction,
            price=trade.price,
            volume=trade.volume,
            amount=trade.price * trade.volume,
            timestamp=datetime.now(),
            reason=fail_reason,
            message=f"交易成功: {direction} {trade.symbol} ¥{trade.price:.2f} × {trade.volume}",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=TradeHistoryResponse)
async def get_trade_history(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    limit: int = Query(20, ge=1, le=100, description="Number of records"),
    page: int = Query(1, ge=1, description="Page number"),
    account: str = Query("stock", description="账户标识"),
):
    """Get trade history from PostgreSQL."""
    db = SessionLocal()
    try:
        query = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        )
        if symbol:
            query = query.filter(PaperTrade.symbol == symbol)

        total = query.count()
        offset = (page - 1) * limit
        rows = query.order_by(PaperTrade.created_at.desc()).offset(offset).limit(limit).all()

        trades = []
        for row in rows:
            sym = row.symbol
            trades.append(OrderResponse(
                id=row.id,
                order_id=row.orderid,
                symbol=sym,
                name=_get_stock_name(sym),
                direction=row.direction,
                price=row.price,
                volume=row.volume,
                status="completed",
                traded=row.volume,
                created_at=datetime.fromisoformat(row.created_at),
                updated_at=datetime.fromisoformat(row.created_at),
                reason=row.reason or "",
            ))
    finally:
        db.close()

    return TradeHistoryResponse(
        trades=trades,
        total=total,
        page=page,
        page_size=limit,
    )


@router.get("/orders")
async def get_pending_orders(
    request: Request,
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: Optional[str] = Query(None, description="Filter by status: 提交中/未成交/部分成交/已撤销"),
    limit: int = Query(50, ge=1, le=200),
    account: str = Query("stock", description="账户标识"),
):
    """
    Get pending/active orders from the paper trading engine.
    Used by Pi agent to check order status before making new trades.
    """
    try:
        executor = _make_executor(request, account)
        if executor.engine is not None:
            orders = executor.engine.get_orders(symbol=symbol, status=status, limit=limit)
        elif executor.bridge:
            orders = executor.bridge.get_orders(symbol=symbol, status=status, limit=limit)
        else:
            orders = []
        return {
            "orders": orders,
            "count": len(orders),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{order_id}", response_model=OrderResponse)
async def get_trade(order_id: str, account: str = Query("stock", description="账户标识")):
    """Get specific trade by order ID from PostgreSQL."""
    db = SessionLocal()
    try:
        row = db.query(PaperTrade).filter(
            PaperTrade.orderid == order_id,
            PaperTrade.account_id == account,
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")

        trade = OrderResponse(
            order_id=row.orderid,
            symbol=row.symbol,
            direction=row.direction,
            price=row.price,
            volume=row.volume,
            status="completed",
            traded=row.volume,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.created_at),
        )
    finally:
        db.close()
    return trade


@router.get("/voided")
async def get_voided_trades(request: Request, account: str = Query("stock", description="账户标识")):
    """Get all voided (cancelled) trades."""
    try:
        executor = _make_executor(request, account)
        trades = executor.get_voided_trades()
        for t in trades:
            t["name"] = _get_stock_name(t["symbol"])
        return {"trades": trades, "total": len(trades)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{trade_id}/void", response_model=VoidResponse)
async def void_trade(trade_id: int, body: VoidRequest, request: Request,
                     account: str = Query("stock", description="账户标识")):
    """Void a trade (soft-delete, excluded from position calculation)."""
    try:
        executor = _make_executor(request, account)
        result = executor.void_trade(trade_id, body.reason)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return VoidResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{trade_id}/unvoid", response_model=VoidResponse)
async def unvoid_trade(trade_id: int, request: Request,
                       account: str = Query("stock", description="账户标识")):
    """Restore a voided trade."""
    try:
        executor = _make_executor(request, account)
        result = executor.unvoid_trade(trade_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return VoidResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request,
                       account: str = Query("stock", description="账户标识")):
    """
    Cancel a pending order by order ID.
    Only orders with status '提交中' or '未成交' can be cancelled.
    """
    try:
        executor = _make_executor(request, account)
        if executor.engine:
            success = executor.engine.cancel_order(order_id)
        else:
            # VN.PY bridge handles cancellations automatically on stop/timeout
            raise HTTPException(status_code=400, detail="VN.PY bridge 不支持手动撤单，订单会自动超时取消")
        if success:
            return {
                "status": "cancelled",
                "order_id": order_id,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            raise HTTPException(status_code=400, detail=f"无法撤销订单 {order_id}，可能已成交或不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
