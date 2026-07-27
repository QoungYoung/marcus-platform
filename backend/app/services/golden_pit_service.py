# -*- coding: utf-8 -*-
"""黄金坑评分引擎 — 多因子加权模型判定市场底部区域。"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from app.services.arkvol_service import ArkvolService, ArkvolServiceError

logger = logging.getLogger(__name__)

FACTOR_CONFIG = [
    {"key": "a_share_sentiment", "name": "A股情绪", "page": "alla", "weight": 0.25,
     "desc": "全A贪婪指数反转，极度恐慌时得分高"},
    {"key": "low_52w", "name": "52周低位", "page": "low-52w-leverage", "weight": 0.25,
     "desc": "52周低位标的数量，大量标的触底时得分高"},
    {"key": "etf_deviation", "name": "ETF乖离率", "page": "gll", "weight": 0.20,
     "desc": "ETF负乖离占比，大面积超跌时得分高"},
    {"key": "fund_greed", "name": "基金贪婪", "page": "funds-greed", "weight": 0.15,
     "desc": "基金贪婪指数反转，基民恐慌时得分高"},
    {"key": "global_flow", "name": "全球资金流", "page": "global-capital-flow", "weight": 0.10,
     "desc": "全球风险偏好反转，避险时得分高"},
    {"key": "debt_temp", "name": "国债温度", "page": "debt", "weight": 0.05,
     "desc": "国债价格温度，债市避险浓时得分高"},
]

LEVELS = [
    (0, 30, "normal", "正常区域", "#22c55e"),
    (30, 50, "watch", "关注区域", "#eab308"),
    (50, 70, "alert", "预警区域", "#f97316"),
    (70, 101, "golden_pit", "黄金坑区域", "#ef4444"),
]


def _classify(score: float) -> Dict[str, str]:
    for lo, hi, level, label, color in LEVELS:
        if lo <= score < hi:
            return {"level": level, "label": label, "color": color}
    return {"level": "unknown", "label": "未知", "color": "#6b7280"}


def _invert_sentiment(score: Optional[float]) -> Optional[float]:
    """反转情绪分数: 0(极度恐慌)→100, 100(极度贪婪)→0"""
    if score is None:
        return None
    return round(100.0 - float(score), 1)


def _extract_sentiment_factor(data: Dict[str, Any]) -> Dict[str, Any]:
    """从ArkVol页面数据提取基于sentiment_score的因子。"""
    raw = data.get("sentiment_score")
    label = data.get("sentiment_label", "-")
    score = _invert_sentiment(raw)
    return {
        "raw": raw,
        "raw_label": label,
        "score": score or 0.0,
    }


def _extract_low_52w_factor(data: Dict[str, Any]) -> Dict[str, Any]:
    """从52周低位页面提取因子：以样本数量为信号强度。"""
    items = data.get("items") or []
    count = len(items) if isinstance(items, list) else 0
    # 经验阈值：1000 只≈极端高位，映射到 0-100
    normalized = min(100.0, round(count / 10.0, 1)) if count > 0 else 0.0
    return {"raw": count, "score": normalized, "raw_label": f"{count} 只"}


def _extract_etf_deviation_factor(data: Dict[str, Any]) -> Dict[str, Any]:
    """从ETF乖离率页面提取因子：负乖离标的占比。"""
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) == 0:
        return {"raw": 0, "score": 0.0, "raw_label": "无数据"}

    negative_count = 0
    total = len(items)
    for item in items:
        if not isinstance(item, dict):
            continue
        # 尝试多个可能的乖离率字段名
        deviation = item.get("deviation") or item.get("bias") or item.get("bias_rate") or item.get("乖离率")
        if deviation is not None:
            try:
                if float(deviation) < 0:
                    negative_count += 1
            except (ValueError, TypeError):
                pass

    pct = round(negative_count / total * 100, 1) if total > 0 else 0.0
    return {"raw": f"{negative_count}/{total}", "score": pct, "raw_label": f"{negative_count}/{total} 负乖离"}


def _extract_debt_factor(data: Dict[str, Any]) -> Dict[str, Any]:
    """从国债温度页面提取因子：温度高=债贵=避险浓，直接映射。"""
    raw = data.get("sentiment_score")
    label = data.get("sentiment_label", "-")
    score = float(raw) if raw is not None else 0.0
    return {"raw": raw, "raw_label": label, "score": round(score, 1)}


def _build_summary(composite: float, level_info: Dict[str, str],
                   factors: List[Dict[str, Any]], as_of: str) -> str:
    """生成自然语言解读。"""
    parts = [f"数据日期: {as_of}。"]
    parts.append(f"综合评分 {composite}/100，处于「{level_info['label']}」。")

    sorted_factors = sorted(factors, key=lambda f: f["score"], reverse=True)
    high_signals = [f for f in sorted_factors if f["score"] >= 60]
    if high_signals:
        names = "、".join(f"{f['name']}({f['score']})" for f in high_signals)
        parts.append(f"高分因子: {names}。")

    if composite >= 70:
        parts.append("多因子共振显示市场处于极端恐慌状态，历史上类似位置往往是中长期底部区域。")
    elif composite >= 50:
        parts.append("部分指标显示市场偏弱，建议持续关注。")
    else:
        parts.append("市场情绪正常，暂无明显的底部信号。")

    return "".join(parts)


class GoldenPitService:
    """黄金坑评分服务。"""

    def __init__(self, arkvol: Optional[ArkvolService] = None):
        self._arkvol = arkvol or ArkvolService()

    def get_score(self) -> Dict[str, Any]:
        """获取完整黄金坑评分。"""
        return self._compute()

    def get_factors(self) -> Dict[str, Any]:
        """仅获取各因子明细。"""
        result = self._compute()
        return {"as_of": result["as_of"], "factors": result["factors"]}

    def _compute(self) -> Dict[str, Any]:
        pages_needed = {cfg["page"] for cfg in FACTOR_CONFIG}

        # 并行获取所有页面数据
        page_data: Dict[str, Any] = {}
        errors: List[str] = []
        with ThreadPoolExecutor(max_workers=min(len(pages_needed), 6)) as executor:
            futures = {executor.submit(self._arkvol.fetch_page, page): page for page in pages_needed}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    page_data[page] = future.result()
                except ArkvolServiceError as exc:
                    logger.warning("ArkVol 页面 %s 获取失败: %s", page, exc)
                    errors.append(f"{page}: {exc}")

        if not page_data:
            raise ArkvolServiceError(f"所有 ArkVol 数据源均获取失败: {'; '.join(errors)}")

        as_of = ""
        factors = []
        composite = 0.0

        for cfg in FACTOR_CONFIG:
            data = page_data.get(cfg["page"])
            if data is None:
                factors.append({**cfg, "raw": None, "score": 0.0, "raw_label": "获取失败",
                                "error": True})
                continue

            if not as_of and data.get("as_of"):
                as_of = data["as_of"]

            if cfg["key"] == "low_52w":
                extracted = _extract_low_52w_factor(data)
            elif cfg["key"] == "etf_deviation":
                extracted = _extract_etf_deviation_factor(data)
            elif cfg["key"] == "debt_temp":
                extracted = _extract_debt_factor(data)
            else:
                extracted = _extract_sentiment_factor(data)

            weighted = round(extracted["score"] * cfg["weight"], 2)
            composite += weighted

            factors.append({
                "key": cfg["key"],
                "name": cfg["name"],
                "weight": cfg["weight"],
                "description": cfg["desc"],
                "raw": extracted["raw"],
                "raw_label": extracted.get("raw_label", "-"),
                "score": extracted["score"],
                "weighted": weighted,
            })

        composite = round(composite, 1)
        level_info = _classify(composite)
        summary = _build_summary(composite, level_info, factors, as_of or "未知")

        return {
            "score": composite,
            "level": level_info["level"],
            "level_label": level_info["label"],
            "level_color": level_info["color"],
            "as_of": as_of or "未知",
            "factors": factors,
            "summary": summary,
            "errors": errors if errors else None,
        }
