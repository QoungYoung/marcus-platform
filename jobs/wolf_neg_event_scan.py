#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wolf_neg_event_scan.py — 层①「无利空」公告标记（2026-09-11，用户要求直接对接、不设观察期）。

干什么：拉当日**全市场公告** → 过滤出当前持仓 → 按类型/标题白名单判「意外事件/黑天鹅级利空」
        → 写 data/wolf_negative_events.json（读侧 wolf_early_stop.negative_event 已就绪，无需改）。
谁在用：t_monitor._check_stop_loss（① 建仓初期结构止损）与 _check_logic_time_stop（①后半句）——
        有标记时**不套结构线**、退回 stop_loss_price（狼大 2026-03-05「是意外事件，黑天鹅那种」）。

判据与安全边界见 backend/app/services/wolf_neg_event.py 模块头（**宁可漏不可滥**：
误报=该止不止很危险；漏报=少豁免一次仍按①止损，安全）。

用法:
  python -u jobs/wolf_neg_event_scan.py            # 真跑（写文件）
  python -u jobs/wolf_neg_event_scan.py --dry-run  # 只看命中，不写
  python -u jobs/wolf_neg_event_scan.py --json     # 额外打印摘要 JSON
环境变量:
  WOLF_NEG_EVENT_SCAN=0  关闭本任务（等价不调度）
  WOLF_NEG_EVENT_ACCOUNT 取哪个账户的持仓（默认 stock）
  DATA_DIR               数据目录（默认 /app/data）
"""
import os
import sys

# 路径：容器里仓库根是 /app（backend/app → /app/app、apps → /app/apps、jobs → /app/jobs）；
# 本地是 <repo>/backend + <repo>/apps/main_line。**逐个判断存在再插入**——
# 踩过的坑：容器里 sys.path[0] 是"脚本所在目录"（/app/jobs），cwd 不会自动加，
# 所以必须显式把 /app 放进去，否则 `import app` 直接 ModuleNotFoundError（首次部署即踩）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _c in ("/app", os.path.join(_ROOT, "backend"), _ROOT,
           "/app/apps/main_line", os.path.join(_ROOT, "apps", "main_line")):
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)


def _positions(account: str):
    """当前持仓 symbol 列表。优先 t_pool（backend 服务内），失败退回 DB 直查。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT symbol FROM paper_positions WHERE account_id = :a AND volume > 0"),
                {"a": account}).fetchall()
            return [r[0] for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[wolf_neg_event_scan] 读持仓失败: {str(e)[:120]}", file=sys.stderr)
        return []


def main() -> int:
    if os.getenv("WOLF_NEG_EVENT_SCAN", "1").strip() in ("0", "false", "no"):
        print("WOLF_NEG_EVENT_SCAN=0 → 跳过")
        return 0
    dry = "--dry-run" in sys.argv
    account = os.getenv("WOLF_NEG_EVENT_ACCOUNT", "stock")
    syms = _positions(account)
    print(f"[wolf_neg_event_scan] 账户={account} 持仓 {len(syms)} 只: {syms}")
    if not syms:
        print("[wolf_neg_event_scan] 无持仓 → 无事可做")
        return 0
    from app.services.wolf_neg_event import scan_and_mark
    res = scan_and_mark(syms, dry_run=dry)
    if "--json" in sys.argv:
        import json
        print(json.dumps({k: v for k, v in res.items() if k != "markers"}, ensure_ascii=False))
    if res.get("ok") and not res.get("touched"):
        print("[wolf_neg_event_scan] 持仓标的今日无意外事件级公告（无变动，未改文件）")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
