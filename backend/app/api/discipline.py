# -*- coding: utf-8 -*-
"""狼大纪律配置 API（2026-09-11 落库后新增）。

背景：配置此前散在两个 json（`config/wolf_discipline.json` 与运行时实际读的
`DATA_DIR/wolf_discipline.json`），改一份漏一份会导致"生效值与预期不符"（当天真实踩到）。
→ 现在 **Postgres 的 `wolf_discipline_config`(id=1, JSONB) 是唯一事实来源**，
  `wolf_discipline._cfg()` 读序 = DB → 文件 → 内置默认（空表自动播种，DB 抖动有熔断）。
本路由提供：看当前生效配置与来源 / 改配置（按段合并）/ 看当前仓位分档判定。
"""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app.services import wolf_discipline as WD

router = APIRouter(prefix="/discipline", tags=["WolfDiscipline"])

_SECTIONS = ("weekend_de_risk", "board_half", "profit_take", "position_cap")


@router.get("/config")
def get_config():
    """当前生效的狼大纪律配置 + 来源（db / db(seeded) / file / default）。"""
    cfg = WD._cfg()
    return {"source": WD.config_source(),
            "db_enabled": os.getenv("WOLF_DISCIPLINE_CFG_DB", "1") not in ("0", "false", "no"),
            "config": cfg,
            "effective": {
                "tier_targets": {op: WD.tier_target_pct(op)
                                 for op in ("build", "t_only", "side", "defense", "exit")},
                "tier_floor": {op: WD.tier_floor_pct(op)
                               for op in ("build", "t_only", "side", "defense", "exit")},
                "current_operation": WD.current_operation(),
            }}


@router.put("/config")
def put_config(payload: Dict[str, Any] = Body(...), updated_by: str = Query("api")):
    """**按段合并**更新配置并落库（不传的段保持原样）。只接受已知段名，避免写坏整份配置。

    例：PUT {"position_cap": {"tier_targets": {"defense": 25}}}
    """
    unknown = [k for k in payload if k not in _SECTIONS]
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"未知配置段 {unknown}；只允许 {list(_SECTIONS)}")
    if not payload:
        raise HTTPException(status_code=400, detail="空 payload")
    # **递归**合并（浅合并会把 tier_targets 这类嵌套块整体替换 → 静默丢档位）
    merged = WD.deep_merge(WD._cfg(), payload)
    ok = WD.save_cfg(merged, updated_by=updated_by)
    if not ok:
        raise HTTPException(status_code=503,
                            detail="落库失败（DB 不可用或熔断中）；已只写本地文件，配置未生效")
    return {"ok": True, "source": WD.config_source(),
            "effective": {"tier_targets": {op: WD.tier_target_pct(op)
                                           for op in ("build", "t_only", "side", "defense", "exit")}}}


@router.get("/position")
def position_status(ratio: Optional[float] = Query(None, description="总仓位%（不传则用 t 账户实际值）"),
                    operation: Optional[str] = Query(None, description="覆盖浪型档位（调试用）")):
    """当前仓位 vs 分档目标/下限（狼大 2026-01-17 分档 + 2025-08-11 下限）。"""
    pf = None
    if ratio is None:
        try:
            from sqlalchemy import text
            from app.database import SessionLocal
            from app.services.t_gateway import t_net_asset
            acct = os.getenv("WOLF_DISCIPLINE_ACCOUNT", "t")
            db = SessionLocal()
            try:
                row = db.execute(text(
                    "SELECT available_cash FROM paper_account_info WHERE account_id = :a"
                ), {"a": acct}).mappings().first()
                cash = float((row or {}).get("available_cash") or 0)
            finally:
                db.close()
            total = float(t_net_asset(acct) or 0)
            if total > 0:
                pf = {"cash": cash, "total_asset": total, "positions": []}
        except Exception as e:
            print(f"[discipline] 读账户失败, 退回 ratio 参数: {str(e)[:80]}")
            pf = None
    if pf is None:
        r = float(ratio if ratio is not None else 0.0)
        pf = {"cash": 100.0 - r, "total_asset": 100.0, "positions": []}
    out = WD.position_cap(pf, operation=operation)
    out["portfolio_used"] = {"total_asset": pf["total_asset"], "cash": pf["cash"]}
    out["operation"] = operation or WD.current_operation()
    return out
