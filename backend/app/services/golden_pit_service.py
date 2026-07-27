# -*- coding: utf-8 -*-
"""黄金坑评分引擎 v2 — 按宽基指数分别追踪，三重确认底部区域。

模型来源: arkvol.com 作者「壬戍帅潘安」的三重判断体系:
  1. 蛋糕理论 (global capital flow) — A股资金外流达历史低位
  2. 宽基贪婪 (per-index greed) — 贪婪值 < 0.35 确认黄金坑, < 0.40 预警
  3. 细分板块 (sector fund greed) — 板块基金跌到极端值
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.arkvol_service import ArkvolService, ArkvolServiceError

logger = logging.getLogger(__name__)

# ── 跟踪的 A 股宽基指数 ──
CHINA_INDICES: Dict[str, Dict[str, Any]] = {
    "510050": {"name": "上证50",   "priority": 6},
    "510300": {"name": "沪深300",  "priority": 5},
    "510500": {"name": "中证500",  "priority": 4},
    "588000": {"name": "科创50",   "priority": 3},
    "159845": {"name": "中证1000", "priority": 2},
    "159915": {"name": "创业板指", "priority": 1},
}

GREED_WARNING = 0.40
GREED_GOLDEN_PIT = 0.35
PIT_WINDOW_DAYS = 15
PIT_MIDPOINT_DAYS = (7, 8)

STATUS_MAP = {
    "normal":     {"label": "正常",    "color": "#22c55e"},
    "warning":    {"label": "预警",    "color": "#f97316"},
    "golden_pit": {"label": "黄金坑",  "color": "#ef4444"},
}


def _trading_days_between(start_date: str, end_date: str) -> int:
    """估算两个日期之间的交易日数（简化为自然日 * 5/7）。"""
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        days = (d2 - d1).days
        # 粗略估算交易日：自然日 * 5/7
        return max(0, round(days * 5 / 7))
    except (ValueError, TypeError):
        return 0


def _add_trading_days(date_str: str, trading_days: int) -> str:
    """给定起始日期和交易日数，估算目标日期。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        cal_days = round(trading_days * 7 / 5)
        result = d + timedelta(days=cal_days)
        return result.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


class GoldenPitService:
    """黄金坑评分服务 v2 — 逐宽基指数追踪。"""

    def __init__(self, arkvol: Optional[ArkvolService] = None):
        self._arkvol = arkvol or ArkvolService()
        self._last_known_greed: Dict[str, float] = {}  # 用于盘中阈值穿越检测

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """获取完整的 per-index 黄金坑状态 + 窗口信息 + 三重确认 + 预测。"""
        alla_data = self._arkvol.fetch_page("alla")
        indices = self._extract_china_indices(alla_data)
        as_of = alla_data.get("as_of", "")

        # 并行获取三重确认所需数据
        confirmation = {}
        prediction = None
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_conf = executor.submit(self._compute_triple_confirmation, indices)
            f_pred = executor.submit(self._predict_next_entry, indices)
            confirmation = f_conf.result()
            prediction = f_pred.result()

        window = self._detect_golden_pit_window(indices)
        summary = self._build_v2_summary(indices, window, confirmation, prediction)

        return {
            "as_of": as_of,
            "golden_pit_window": window,
            "indices": indices,
            "triple_confirmation": confirmation,
            "prediction": prediction,
            "summary": summary,
        }

    def get_history(self, index: str = "all", days: int = 60) -> Dict[str, Any]:
        """获取历史贪婪值趋势数据，用于前端折线图。"""
        alla_data = self._arkvol.fetch_page("alla")
        series_data = alla_data.get("original_page_data", {}).get("series", {}).get("data", {})
        if not series_data:
            return {"as_of": alla_data.get("as_of", ""), "series": {}, "indices": {}}

        index_names = {code: cfg["name"] for code, cfg in CHINA_INDICES.items()}
        result_series: Dict[str, List[Dict]] = {}
        result_indices: Dict[str, str] = {}

        for code, name in index_names.items():
            if index != "all" and code != index:
                continue
            raw = series_data.get(code, [])
            if raw:
                sorted_data = sorted(raw, key=lambda x: x.get("date", ""))
                result_series[code] = sorted_data[-days:] if len(sorted_data) > days else sorted_data
                result_indices[code] = name

        return {
            "as_of": alla_data.get("as_of", ""),
            "series": result_series,
            "indices": result_indices,
        }

    def get_snapshots(self, days: int = 30) -> List[Dict[str, Any]]:
        """从数据库读取历史快照。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                rows = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date >= cutoff)
                    .order_by(GoldenPitSnapshot.date.asc(), GoldenPitSnapshot.fund_code.asc())
                    .all()
                )
                return [
                    {
                        "date": r.date,
                        "fund_code": r.fund_code,
                        "index_name": r.index_name,
                        "greed_value": r.greed_value,
                        "close_price": r.close_price,
                        "percentile": r.percentile,
                        "status": r.status,
                        "decline_rate_5d": r.decline_rate_5d,
                    }
                    for r in rows
                ]
            finally:
                db.close()
        except Exception as e:
            logger.warning("读取黄金坑快照失败: %s", e)
            return []

    def save_daily_snapshot(self) -> List[Any]:
        """保存每日快照到数据库。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            status = self.get_status()
            today = status["as_of"] or datetime.now().strftime("%Y-%m-%d")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            db = SessionLocal()
            snapshots = []
            try:
                for idx in status["indices"]:
                    snap = GoldenPitSnapshot(
                        date=today,
                        fund_code=idx["fund_code"],
                        index_name=idx["index_name"],
                        greed_value=idx["greed"],
                        close_price=idx.get("close"),
                        percentile=idx.get("percentile"),
                        status=idx["status"],
                        decline_rate_5d=idx.get("decline_rate"),
                        created_at=now,
                    )
                    db.add(snap)
                    snapshots.append(snap)
                db.commit()
                logger.info("黄金坑快照已保存: %s, %d 条", today, len(snapshots))
            finally:
                db.close()
            return snapshots
        except Exception as e:
            logger.error("保存黄金坑快照失败: %s", e)
            return []

    # ═══════════════════════════════════════════════════════════════
    # QQ 报告与预警
    # ═══════════════════════════════════════════════════════════════

    def format_morning_report(self, status: Optional[Dict[str, Any]] = None) -> str:
        """生成 QQ 盘前报告 (8:50 AM)。"""
        if status is None:
            status = self.get_status()

        as_of = status["as_of"]
        window = status["golden_pit_window"]
        indices = status["indices"]
        conf = status["triple_confirmation"]
        pred = status["prediction"]

        lines = [f"📊 黄金坑盘前报告 — {as_of}", "━━━━━━━━━━━━━━━━━━━", ""]

        # 按 priority 排序显示
        sorted_indices = sorted(indices, key=lambda x: x["priority"])
        pit_count = 0
        for idx in sorted_indices:
            icon = {"golden_pit": "🔴", "warning": "🟠", "normal": "🟢"}.get(idx["status"], "⚪")
            detail = ""
            if idx["status"] == "golden_pit" and idx.get("entry_date"):
                pit_count += 1
                detail = f" ({idx['entry_date']}入坑，第{idx.get('days_in_pit', '?')}天)"
            elif idx["status"] == "warning" and idx.get("days_to_pit"):
                detail = f" (预计{idx['eta_date']}入坑)"
            elif idx.get("decline_rate"):
                detail = f" (日跌{idx['decline_rate']:.3f})"
            lines.append(f"{icon} {idx['index_name']:6s} {idx['greed']:.2f}  {STATUS_MAP[idx['status']]['label']}{detail}")

        lines.append("")

        if window["active"]:
            lines.append(
                f"📍 窗口：第{window['current_day']}/{PIT_WINDOW_DAYS}天  "
                f"转折点:{window['midpoint_date']}  出口:{window['exit_date']}"
            )
            # 进度条
            progress_pct = min(100, int(window["current_day"] / PIT_WINDOW_DAYS * 100))
            bar_len = 20
            filled = max(1, int(bar_len * progress_pct / 100))
            bar = "█" * filled + "░" * (bar_len - filled)
            midpoint_pos = int(bar_len * PIT_MIDPOINT_DAYS[0] / PIT_WINDOW_DAYS)
            lines.append(f"   {bar}  {progress_pct}%")
        else:
            lines.append("📍 当前无活跃黄金坑窗口")

        lines.append("")

        # 三重确认
        l1 = conf["layer1"]
        l2 = conf["layer2"]
        l3 = conf["layer3"]
        lines.append(f"{'☑' if l1['confirmed'] else '☐'} 蛋糕理论: {l1['status']}")
        lines.append(f"{'☑' if l2['confirmed'] else '☐'} 宽基确认: {l2['status']}")
        lines.append(f"{'☑' if l3['confirmed'] else '☐'} 细分板块: {l3['status']}")

        if pred and pred.get("next_index"):
            lines.append(f"💡 预测: {pred['next_index']} 预计 {pred['eta_days']} 天后入坑 ({pred['eta_date']})")
        elif window["active"]:
            lines.append("💡 转折点附近可大额定投，窗口末期小额定投")

        return "\n".join(lines)

    def check_threshold_crossings(self) -> List[str]:
        """检测阈值穿越，返回需要推送的预警消息列表。"""
        status = self.get_status()
        indices = status["indices"]
        alerts = []

        # 加载昨日快照用于对比
        prev_greed = self._load_previous_greed()

        for idx in indices:
            code = idx["fund_code"]
            current = idx["greed"]
            prev = prev_greed.get(code) or self._last_known_greed.get(code)

            # 更新内存记录
            self._last_known_greed[code] = current

            if prev is None:
                continue

            # 检测 0.40 预警线穿越
            if current < GREED_WARNING <= prev:
                alerts.append(
                    f"⚠️ {idx['index_name']} 贪婪值 {current:.2f} 跌破0.40预警线\n"
                    f"   📉 日跌速：{idx.get('decline_rate', 0):.3f}  "
                    f"预计 {idx.get('eta_date', '?')} 跌破0.35入坑"
                )

            # 检测 0.35 黄金坑确认
            if current < GREED_GOLDEN_PIT <= prev:
                window = status["golden_pit_window"]
                alerts.append(
                    f"🔴 {idx['index_name']} 贪婪值 {current:.2f} 进入黄金坑！\n"
                    f"   📍 窗口：{window['start_date']} - {window['exit_date']}（{PIT_WINDOW_DAYS}交易日）\n"
                    f"   📍 转折点预计：{window['midpoint_date']}\n"
                    f"   💡 建议关注 {idx['index_name']} 相关ETF定投机会"
                )

        return alerts

    def _load_previous_greed(self) -> Dict[str, float]:
        """从数据库加载上一个交易日的贪婪值。"""
        try:
            from app.database import SessionLocal
            from app.models.golden_pit import GoldenPitSnapshot

            db = SessionLocal()
            try:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                rows = (
                    db.query(GoldenPitSnapshot)
                    .filter(GoldenPitSnapshot.date == yesterday)
                    .all()
                )
                # 如果昨天没数据，尝试前天
                if not rows:
                    two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
                    rows = (
                        db.query(GoldenPitSnapshot)
                        .filter(GoldenPitSnapshot.date == two_days_ago)
                        .all()
                    )
                return {r.fund_code: r.greed_value for r in rows}
            finally:
                db.close()
        except Exception:
            return {}

    # ═══════════════════════════════════════════════════════════════
    # 内部计算方法
    # ═══════════════════════════════════════════════════════════════

    def _extract_china_indices(self, alla_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 alla 页面数据中提取 6 个 A 股宽基指数的当前状态和历史 series。"""
        # 当前快照
        items = alla_data.get("items", [])
        current_map: Dict[str, Dict] = {}
        for item in items:
            code = item.get("fund_code", "")
            if code in CHINA_INDICES:
                current_map[code] = {
                    "greed": float(item.get("greed", 0)),
                    "close": float(item.get("close", 0)),
                }

        # 历史 series
        series_data = alla_data.get("original_page_data", {}).get("series", {}).get("data", {})

        result = []
        for code, cfg in CHINA_INDICES.items():
            current = current_map.get(code, {})
            greed = current.get("greed", 0.0)
            close = current.get("close", 0.0)

            # 从 series 计算百分位和跌速
            raw_series = series_data.get(code, [])
            sorted_series = sorted(raw_series, key=lambda x: x.get("date", ""))

            percentile = self._calculate_percentile(greed, sorted_series)
            decline_rate = self._calculate_decline_rate(sorted_series)

            status = "normal"
            if greed < GREED_GOLDEN_PIT:
                status = "golden_pit"
            elif greed < GREED_WARNING:
                status = "warning"

            index_info = {
                "fund_code": code,
                "index_name": cfg["name"],
                "priority": cfg["priority"],
                "greed": round(greed, 4),
                "close": round(close, 4),
                "percentile": round(percentile, 1),
                "status": status,
                "decline_rate": round(decline_rate, 4),
                "days_to_pit": None,
                "eta_date": None,
                "entry_date": None,
                "days_in_pit": None,
            }

            # 如果在预警区，预测何时入坑
            if status == "warning" and decline_rate > 0.001:
                gap = greed - GREED_GOLDEN_PIT
                days_to = max(1, round(gap / decline_rate))
                index_info["days_to_pit"] = days_to
                index_info["eta_date"] = _add_trading_days(
                    alla_data.get("as_of", datetime.now().strftime("%Y-%m-%d")), days_to
                )

            result.append(index_info)

        return result

    def _calculate_percentile(self, current_greed: float, series: List[Dict]) -> float:
        """计算当前贪婪值在自身历史中的分位数（越低越恐慌）。"""
        if not series:
            return 50.0
        greeds = sorted([float(s.get("greed", 0)) for s in series])
        if not greeds or len(greeds) < 2:
            return 50.0
        count_below = sum(1 for g in greeds if g <= current_greed)
        return round(count_below / len(greeds) * 100, 1)

    def _calculate_decline_rate(self, series: List[Dict], window: int = 5) -> float:
        """计算最近 N 日的平均贪婪值日跌幅（正值=下跌，负值=上涨）。"""
        if len(series) < window + 1:
            return 0.0
        recent = sorted(series, key=lambda x: x.get("date", ""))[-window - 1:]
        greeds = [float(s.get("greed", 0)) for s in recent]
        if len(greeds) < 2:
            return 0.0
        total_decline = greeds[0] - greeds[-1]
        return round(total_decline / window, 4)

    def _detect_golden_pit_window(self, indices: List[Dict[str, Any]]) -> Dict[str, Any]:
        """检测是否有活跃的黄金坑窗口。首个跌破 0.35 的指数开窗。"""
        pit_indices = [i for i in indices if i["status"] == "golden_pit"]

        if not pit_indices:
            return {
                "active": False,
                "start_date": None,
                "leading_index": None,
                "current_day": 0,
                "midpoint_date": None,
                "exit_date": None,
            }

        # 以最早进入黄金坑的指数为准
        pit_indices.sort(key=lambda x: x.get("entry_date") or "9999")
        leader = pit_indices[0]

        start_date = leader.get("entry_date") or datetime.now().strftime("%Y-%m-%d")
        current_day = _trading_days_between(start_date, datetime.now().strftime("%Y-%m-%d")) + 1
        current_day = max(1, min(PIT_WINDOW_DAYS, current_day))

        midpoint_start = _add_trading_days(start_date, PIT_MIDPOINT_DAYS[0])
        midpoint_end = _add_trading_days(start_date, PIT_MIDPOINT_DAYS[1])
        exit_date = _add_trading_days(start_date, PIT_WINDOW_DAYS)

        # 记录各指数的入坑日和坑内天数
        for idx in pit_indices:
            idx["entry_date"] = idx.get("entry_date") or start_date
            idx["days_in_pit"] = _trading_days_between(idx["entry_date"], datetime.now().strftime("%Y-%m-%d")) + 1

        return {
            "active": True,
            "start_date": start_date,
            "leading_index": leader["index_name"],
            "current_day": current_day,
            "midpoint_date": f"{midpoint_start} - {midpoint_end}",
            "exit_date": exit_date,
        }

    def _compute_triple_confirmation(self, indices: List[Dict[str, Any]]) -> Dict[str, Any]:
        """三重确认状态。"""
        # Layer 1: 蛋糕理论 — 全球资金流向
        layer1 = {"label": "蛋糕理论", "status": "未知", "confirmed": False}
        try:
            gcf = self._arkvol.fetch_page("global-capital-flow")
            score = gcf.get("sentiment_score")
            if score is not None:
                score = float(score)
                # A股资金外流处历史低位时得分高(反转)，< 30 表示风险偏好极低
                if score < 30:
                    layer1 = {"label": "蛋糕理论", "status": "A股资金外流处历史低位", "confirmed": True}
                else:
                    layer1 = {"label": "蛋糕理论", "status": f"资金外流未到底 (score={score})", "confirmed": False}
        except Exception as e:
            layer1 = {"label": "蛋糕理论", "status": f"数据不可用: {e}", "confirmed": False}

        # Layer 2: 宽基贪婪
        pit_names = [i["index_name"] for i in indices if i["status"] == "golden_pit"]
        warning_names = [i["index_name"] for i in indices if i["status"] == "warning"]
        layer2_confirmed = len(pit_names) > 0
        layer2_status = f"{len(pit_names)}个在黄金坑" if pit_names else f"{len(warning_names)}个预警"
        if pit_names:
            layer2_status += f" ({', '.join(pit_names)})"
        layer2 = {
            "label": "宽基贪婪",
            "status": layer2_status,
            "confirmed": layer2_confirmed,
            "details": [f"{i['index_name']}: {i['status']}" for i in sorted(indices, key=lambda x: x["priority"])],
        }

        # Layer 3: 细分板块
        layer3 = {"label": "细分板块", "status": "未知", "confirmed": False}
        try:
            tech = self._arkvol.fetch_page("alla-tech")
            items = tech.get("items", [])
            if items:
                # 检查板块贪婪极端值: 有任意板块 greed < 0.35
                extreme_sectors = []
                for item in items:
                    greed = item.get("greed")
                    name = item.get("etf_name") or item.get("index_name") or item.get("name", "")
                    if greed is not None and float(greed) < GREED_GOLDEN_PIT:
                        extreme_sectors.append(name)
                if extreme_sectors:
                    layer3 = {"label": "细分板块", "status": f"{len(extreme_sectors)}个板块已入黄金坑", "confirmed": True}
                else:
                    layer3 = {"label": "细分板块", "status": "暂未触发", "confirmed": False}
        except Exception as e:
            layer3 = {"label": "细分板块", "status": f"数据不可用: {e}", "confirmed": False}

        return {"layer1": layer1, "layer2": layer2, "layer3": layer3}

    def _predict_next_entry(self, indices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """预测下一个进入黄金坑的指数。"""
        candidates = [
            i for i in indices
            if i["status"] != "golden_pit" and i.get("days_to_pit") is not None
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x.get("days_to_pit", 999))
        next_idx = candidates[0]
        return {
            "next_index": next_idx["index_name"],
            "eta_days": next_idx["days_to_pit"],
            "eta_date": next_idx["eta_date"],
            "decline_rate": next_idx["decline_rate"],
        }

    def _build_v2_summary(
        self,
        indices: List[Dict[str, Any]],
        window: Dict[str, Any],
        confirmation: Dict[str, Any],
        prediction: Optional[Dict[str, Any]],
    ) -> str:
        """生成 v2 自然语言解读。"""
        parts = []

        pit_indices = [i for i in indices if i["status"] == "golden_pit"]
        warning_indices = [i for i in indices if i["status"] == "warning"]

        if window["active"]:
            parts.append(f"当前处于黄金坑窗口第{window['current_day']}/{PIT_WINDOW_DAYS}天。")
            parts.append(f"入口:{window['start_date']}，领先指数:{window['leading_index']}。")
            parts.append(f"转折点预计:{window['midpoint_date']}，出口:{window['exit_date']}。")

            if window["current_day"] <= PIT_MIDPOINT_DAYS[0]:
                parts.append("当前处于大额定投期，建议加速建仓。")
            elif window["current_day"] <= PIT_MIDPOINT_DAYS[1]:
                parts.append("当前处于转折点附近，可能是最佳买点。")
            else:
                parts.append("窗口进入后半段，建议小额定投。")
        else:
            parts.append("当前无活跃黄金坑窗口。")
            if warning_indices:
                names = "、".join(i["index_name"] for i in warning_indices)
                parts.append(f"{names}处于预警区，密切关注是否跌破{GREED_GOLDEN_PIT}。")
            else:
                parts.append("各宽基指数情绪正常，暂无底部信号。")

        if prediction and prediction.get("next_index"):
            parts.append(f"预测: {prediction['next_index']} 预计 {prediction['eta_days']} 天后进入黄金坑。")

        layers_ok = sum(1 for k in ["layer1", "layer2", "layer3"] if confirmation[k]["confirmed"])
        if layers_ok == 3:
            parts.append("三重确认全部达成，黄金坑信号高度可靠。")
        elif layers_ok >= 2:
            parts.append(f"三重确认达成{layers_ok}/3，信号可靠性中等。")

        return "".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # 向后兼容 v1 API
    # ═══════════════════════════════════════════════════════════════

    def get_score(self) -> Dict[str, Any]:
        """v1 兼容: 返回简化综合评分。"""
        status = self.get_status()
        indices = status["indices"]
        # 将 per-index 状态转为综合评分: 按最差状态 + 平均贪婪值转换
        pit_count = sum(1 for i in indices if i["status"] == "golden_pit")
        warn_count = sum(1 for i in indices if i["status"] == "warning")
        avg_greed = sum(i["greed"] for i in indices) / len(indices) if indices else 0.5
        inverted = max(0, min(100, (1 - avg_greed) * 100))

        factors = [
            {
                "key": "per_index",
                "name": "宽基指数追踪",
                "weight": 1.0,
                "description": "逐指数贪婪值追踪",
                "raw": f"{pit_count}在坑/{warn_count}预警",
                "raw_label": f"{pit_count}在坑/{warn_count}预警",
                "score": round(inverted, 1),
                "weighted": round(inverted, 1),
            }
        ]

        return {
            "score": round(inverted, 1),
            "level": "golden_pit" if pit_count > 0 else ("alert" if warn_count > 0 else "normal"),
            "level_label": "黄金坑区域" if pit_count > 0 else ("预警区域" if warn_count > 0 else "正常区域"),
            "level_color": "#ef4444" if pit_count > 0 else ("#f97316" if warn_count > 0 else "#22c55e"),
            "as_of": status["as_of"],
            "factors": factors,
            "summary": status["summary"],
            "errors": None,
        }

    def get_factors(self) -> Dict[str, Any]:
        """v1 兼容: 返回因子明细。"""
        score = self.get_score()
        return {"as_of": score["as_of"], "factors": score["factors"]}
