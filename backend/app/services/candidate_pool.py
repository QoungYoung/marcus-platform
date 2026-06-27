# -*- coding: utf-8 -*-
"""跨窗口候选池 — 捕获时机性拒绝标的，回调到位后优先推送给 Pi 重新评估。

状态机: waiting → ready → promoted/expired
- waiting: 时机性拒绝，等待回调
- ready: 回调到位（refresh 后 check_entry_filters 通过）
- promoted: Pi 已买入
- expired: 超时或结构恶化
"""

import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 出池条件（满足任一即不入池）──
# 这些是结构性缺陷，不会随时间改善
STRUCTURAL_BLOCK_CONDITIONS = {
    "hard_block": "硬拦截",
    "downgrade_multiplier_zero": "完全禁止",
    "layer1_failed": "技术面结构性排除",
    "macd_dead_no_converge": "MACD死叉未收敛",
    "layer2_failed": "主力资金出逃",
    "limit_up": "涨停",
}


class CandidatePool:
    """候选池单例，JSON 文件持久化到 data/candidate_pool.json"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            # 自动探测 workspace data 目录
            candidates = [
                Path(__file__).resolve().parents[3] / "data",        # backend/app/services → data
                Path(os.getcwd()) / "data",
            ]
            data_dir = None
            for c in candidates:
                if c.exists():
                    data_dir = str(c)
                    break
            if data_dir is None:
                data_dir = str(Path(os.getcwd()) / "data")
        self._file = Path(data_dir) / "candidate_pool.json"
        self._data = self._load()

    # ── 持久化 ──

    def _load(self) -> dict:
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.warning(f"[CandidatePool] Corrupt file, resetting")
        return {"candidates": [], "updated_at": ""}

    def _save(self, data: dict):
        data["updated_at"] = datetime.now().isoformat()
        self._data = data
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 查询 ──

    def get_waiting(self) -> list:
        return [e for e in self._data["candidates"] if e.get("status") == "waiting"]

    def get_ready(self) -> list:
        return [e for e in self._data["candidates"] if e.get("status") == "ready"]

    def get_all_active(self) -> list:
        return [e for e in self._data["candidates"] if e.get("status") in ("waiting", "ready")]

    def get_promoted(self) -> list:
        return [e for e in self._data["candidates"] if e.get("status") == "promoted"]

    def _find(self, symbol: str) -> Optional[dict]:
        sym = symbol.replace("SH", "").replace("SZ", "").replace("BJ", "")
        for e in self._data["candidates"]:
            es = e["symbol"].replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
            if es == sym:
                return e
        # 精确匹配
        for e in self._data["candidates"]:
            if e["symbol"] == symbol:
                return e
        return None

    # ── 入池判断 ──

    def maybe_capture(
        self,
        symbol: str,
        name: str = "",
        final_grade: str = "",
        downgrade_multiplier: float = 1.0,
        hard_block: bool = False,
        layer1_passed: bool = True,
        layer2_passed: bool = True,
        layer3_passed: bool = True,
        macd_status: str = "",
        macd_dif_converging: bool = False,
        change_pct: float = 0.0,
        layer1_downgrade: str = "",
        layer2_downgrade: str = "",
        layer3_downgrade: str = "",
        chain_name: str = "",
        chain_role: str = "",
        added_window: str = "",
    ) -> bool:
        """判断是否应入池（时机性拒绝），是则写入。返回 True 表示已入池/更新。"""
        # ── 排除条件检查 ──
        if final_grade == "pass":
            return False
        if downgrade_multiplier <= 0:
            return False
        if hard_block:
            return False
        if not layer1_passed:
            return False
        if macd_status == "死叉" and not macd_dif_converging:
            return False
        if not layer2_passed:
            return False
        if change_pct >= 9.5:
            return False

        # ── 构建拒绝原因列表 ──
        reject_reasons = [r for r in [layer1_downgrade, layer2_downgrade, layer3_downgrade] if r]

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        expire_day = self._add_trade_days(today, 2)

        # ── 已存在则更新 ──
        existing = self._find(symbol)
        if existing:
            existing["last_reject"] = {
                "final_grade": final_grade,
                "downgrade_multiplier": downgrade_multiplier,
                "layer1": "✅" if layer1_passed else "🚫",
                "layer2": "✅" if layer2_passed else "🚫",
                "layer3": "✅" if layer3_passed else "🚫",
                "hard_block": hard_block,
                "checked_at": now.isoformat(),
            }
            existing["checks_count"] = existing.get("checks_count", 0) + 1
            existing["reject_reasons"] = reject_reasons
            if existing.get("status") == "expired":
                existing["status"] = "waiting"  # 重新激活
                existing["expire_trade_day"] = expire_day
            self._save(self._data)
            return True

        # ── 新建入池 ──
        entry = {
            "symbol": symbol,
            "name": name,
            "status": "waiting",
            "added_at": now.isoformat(),
            "added_trade_day": today,
            "expire_trade_day": expire_day,
            "added_window": added_window,
            "chain_name": chain_name,
            "chain_role": chain_role,
            "reject_reasons": reject_reasons,
            "last_reject": {
                "final_grade": final_grade,
                "downgrade_multiplier": downgrade_multiplier,
                "layer1": "✅" if layer1_passed else "🚫",
                "layer2": "✅" if layer2_passed else "🚫",
                "layer3": "✅" if layer3_passed else "🚫",
                "hard_block": hard_block,
                "checked_at": now.isoformat(),
            },
            "checks_count": 1,
            "promoted_at": None,
        }
        self._data["candidates"].append(entry)
        self._save(self._data)
        return True

    # ── 状态变更 ──

    def mark_ready(self, symbol: str):
        e = self._find(symbol)
        if e and e.get("status") == "waiting":
            e["status"] = "ready"
            self._save(self._data)

    def mark_expired(self, symbol: str, reason: str = ""):
        e = self._find(symbol)
        if e:
            e["status"] = "expired"
            e["expire_reason"] = reason
            self._save(self._data)

    def mark_promoted(self, symbol: str):
        e = self._find(symbol)
        if e:
            e["status"] = "promoted"
            e["promoted_at"] = datetime.now().isoformat()
            self._save(self._data)

    def remove(self, symbol: str):
        e = self._find(symbol)
        if e:
            self._data["candidates"].remove(e)
            self._save(self._data)

    def add_manual(self, symbol: str, name: str = "") -> bool:
        existing = self._find(symbol)
        if existing and existing.get("status") in ("waiting", "ready"):
            return False  # 已存在
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        entry = {
            "symbol": symbol,
            "name": name,
            "status": "waiting",
            "added_at": now.isoformat(),
            "added_trade_day": today,
            "expire_trade_day": self._add_trade_days(today, 2),
            "added_window": "manual",
            "chain_name": "",
            "chain_role": "",
            "reject_reasons": ["手动添加"],
            "last_reject": {
                "final_grade": "manual",
                "downgrade_multiplier": 0.5,
                "layer1": "?",
                "layer2": "?",
                "layer3": "?",
                "hard_block": False,
                "checked_at": now.isoformat(),
            },
            "checks_count": 0,
            "promoted_at": None,
        }
        self._data["candidates"].append(entry)
        self._save(self._data)
        return True

    # ── 窗口前刷新 ──

    def refresh_all_sync(self) -> list:
        """在交易窗口前由 scheduler 调用，对每个 waiting 候选重跑 check_entry_filters。
        使用 asyncio.new_event_loop() 在同进程内直接调用端点函数。
        返回变为 ready 的 symbol 列表。"""
        waiting = self.get_waiting()
        if not waiting:
            return []

        import asyncio
        from app.api.indicator import check_entry_filters
        from app.models.indicator import EntryCheckRequest

        async def _refresh_one(entry):
            try:
                result = await check_entry_filters(
                    EntryCheckRequest(symbol=entry["symbol"])
                )
                return (entry, result)
            except Exception as exc:
                logger.debug(f"[CandidatePool] refresh failed for {entry['symbol']}: {exc}")
                return (entry, None)

        async def _refresh_all():
            return await asyncio.gather(*[_refresh_one(e) for e in waiting])

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(_refresh_all())
        finally:
            loop.close()

        newly_ready = []
        for entry, result in results:
            if result is None:
                continue
            sym = entry["symbol"]
            if result.final_grade == "pass":
                self.mark_ready(sym)
                newly_ready.append(sym)
                logger.info(f"[CandidatePool] {sym} → ready (filters passed)")
            elif result.hard_block or result.downgrade_multiplier <= 0:
                self.mark_expired(sym, f"结构恶化: {result.final_decision}")
                logger.info(f"[CandidatePool] {sym} → expired ({result.final_decision})")
            else:
                entry["last_reject"] = {
                    "final_grade": result.final_grade,
                    "downgrade_multiplier": result.downgrade_multiplier,
                    "layer1": result.layer1_tech.grade,
                    "layer2": result.layer2_capital.grade,
                    "layer3": result.layer3_overbought.grade,
                    "hard_block": result.hard_block,
                    "checked_at": datetime.now().isoformat(),
                }
                entry["checks_count"] = entry.get("checks_count", 0) + 1

        self._save(self._data)
        return newly_ready

    # ── 过期清理 ──

    def expire_stale(self) -> int:
        """清理超过 2 个交易日的 waiting 候选。返回清理数量。"""
        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        for e in self._data["candidates"]:
            if e.get("status") == "waiting":
                expire_day = e.get("expire_trade_day", "")
                if expire_day and expire_day <= today:
                    e["status"] = "expired"
                    e["expire_reason"] = "超2交易日未改善"
                    count += 1
        if count:
            self._save(self._data)
        return count

    # ── Pi 提示文本 ──

    def format_for_pi(self) -> str:
        """生成注入 Pi prompt 的候选池区块。无活跃候选时返回空字符串。

        展示两部分：
        1. 已被 CandidatePoolMonitor 自动建仓的标的（promoted），供 Pi 知晓
        2. 仍在等待中的标的（waiting），由监控器持续观察，Pi 无需主动建仓
        """
        promoted = self.get_promoted()
        waiting = self.get_waiting()
        if not promoted and not waiting:
            return ""

        lines = [
            f"📋 **跨窗口候选池**（{len(promoted)}只已自动建仓 / {len(waiting)}只等待回调中）",
            "| 状态 | 标的 | 名称 | 产业链 | 说明 |",
            "|:---:|------|------|--------|------|",
        ]

        for e in promoted:
            sym = e["symbol"]
            promoted_at = e.get("promoted_at", "")
            chain = f"{e.get('chain_name', '')}·{e.get('chain_role', '')}" if e.get("chain_name") else "—"
            lines.append(f"| 🟢 | {sym} | {e.get('name', '')} | {chain} | 已自动建仓 |")

        for e in waiting:
            sym = e["symbol"]
            reasons = " → ".join(e.get("reject_reasons", ["—"]))
            chain = f"{e.get('chain_name', '')}·{e.get('chain_role', '')}" if e.get("chain_name") else "—"
            lines.append(f"| ⏳ | {sym} | {e.get('name', '')} | {chain} | {reasons} |")

        lines.append("")
        if promoted:
            lines.append("🟢 以上标的已被 **CandidatePoolMonitor** 自动建仓（试探仓），你无需重复买入。")
        if waiting:
            lines.append("⏳ 以上标的正在被监控器实时观察（30s轮询），回调到位会自动建仓。你无需主动调用 check_entry_filters 强行建仓。")
        lines.append("---")
        return "\n".join(lines) + "\n"

    # ── 交易日计算 ──

    @staticmethod
    def _is_weekend(date_str: str) -> bool:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() >= 5

    def _add_trade_days(self, start_str: str, days: int) -> str:
        """从 start_str 开始加 days 个交易日，返回 YYYY-MM-DD。"""
        dt = datetime.strptime(start_str, "%Y-%m-%d")
        added = 0
        while added < days:
            dt += timedelta(days=1)
            if dt.weekday() < 5:  # Mon-Fri
                added += 1
        return dt.strftime("%Y-%m-%d")

    def _count_trade_days(self, start_str: str, end_str: str) -> int:
        """计算两个日期之间的交易日数（含首尾）。"""
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        if start > end:
            return 0
        count = 0
        current = start
        while current <= end:
            if current.weekday() < 5:
                count += 1
            current += timedelta(days=1)
        return count


# ── 单例 ──

_pool_instance: Optional[CandidatePool] = None


def get_candidate_pool() -> CandidatePool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = CandidatePool()
    return _pool_instance
