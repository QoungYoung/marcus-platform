#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 个股新闻催化剂追踪器
P0 改进核心模块

功能:
- 追踪个股新闻催化剂状态
- 为每只候选股建立"新闻档案"
- 催化剂时效管理（2周无催化自动降级）
- 为选股和交易提供个股级别新闻依据

数据存储: data/news_catalysts.json

来源: 从 workspace-marcus 项目移植，适配 marcus-platform 路径
"""

import json
import os
import sys
import signal
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from concurrent import futures

# ===== 路径适配 =====
# 此文件位于 apps/news/，往上两级是项目根目录 marcus-platform/
WORKSPACE = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE / "data"
CATALYST_FILE = DATA_DIR / "news_catalysts.json"
NEWS_DB = DATA_DIR / "news.db"
STOCK_POOL_DB = DATA_DIR / "stock_pool.db"

# 确保 core/deepseek 在搜索路径中
sys.path.insert(0, str(WORKSPACE / "core" / "deepseek"))

# 催化剂配置
CATALYST_CONFIG = {
    'expiry_days': 14,          # 超过14天无催化自动降级
    'min_news_score': 40,       # 有意义的新闻最低分数
    'strong_catalyst_score': 70, # 强催化剂分数门槛
    'max_catalysts': 200,       # 最多追踪200只股票
}

# AI 置信度门槛（低于此值降级到关键词）
AI_CONFIDENCE_THRESHOLD = 0.6


def _load_catalysts() -> Dict:
    """加载催化剂数据"""
    if not CATALYST_FILE.exists():
        return {}
    try:
        with open(CATALYST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_catalysts(data: Dict):
    """保存催化剂数据"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CATALYST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _get_ai_score(title: str, content: str = '') -> Optional[Dict]:
    """
    调用 DeepSeek AI 分析新闻情绪（带10秒超时保护）

    返回: {
        'news_score': 0-100,  # AI情绪分
        'event_type': str,     # 事件类型
        'confidence': 0-1       # AI置信度
    }
    或 None（AI失败/超时）
    """
    AI_TIMEOUT = 10  # 秒

    try:
        # 从同目录的 news_collector 导入
        from news_collector import _ai_analyze_news

        # 超时装饰器（Linux signal 版）
        def timeout_handler(signum, frame):
            raise TimeoutError("AI 调用超时")

        # 尝试 signal 超时（仅 Linux 主线程）
        if sys.platform != 'win32' and threading.current_thread() == threading.main_thread():
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(AI_TIMEOUT)

        try:
            result = _ai_analyze_news(title, content)
        finally:
            if sys.platform != 'win32' and threading.current_thread() == threading.main_thread():
                signal.alarm(0)  # 取消 alarm

        if result and isinstance(result, dict):
            score = result.get('sentiment_score', 50)
            confidence = result.get('confidence', 0)
            event_type = result.get('event_type', '其他')
            return {
                'news_score': max(0, min(100, float(score))),
                'event_type': event_type,
                'confidence': float(confidence),
            }
        return None

    except (TimeoutError, Exception) as e:
        print(f"[_get_ai_score] AI 分析失败（{type(e).__name__}）: {e}", file=sys.stderr)
        return None


def _get_ai_scores_batch(items: List[Dict]) -> List[Optional[Dict]]:
    """
    批量调用 DeepSeek 分析多条新闻（一次 API 调用替代 N 次）。

    Args:
        items: [{'title': str, 'content': str}, ...]

    Returns:
        [{news_score, event_type, confidence}, ...]，与输入顺序对应
    """
    if not items:
        return []

    try:
        from deepseek_analyzer import _call_deepseek_api as _call_api

        # 构建批量分析 prompt
        news_texts = []
        for i, item in enumerate(items):
            news_texts.append(f"[{i+1}] {item['title'][:80]}")

        system_prompt = """你是 A 股新闻分析师。对每条新闻判断情绪和事件类型。
输出 JSON 数组，每条新闻一个对象：
[{"news_score": 0-100, "event_type": "业绩/政策/合作/利空/其他", "confidence": 0-1}]
评分标准：70+利好，30-利空，50中性。"""

        user_prompt = "分析以下" + str(len(items)) + "条新闻：\n" + "\n".join(news_texts) + "\n\n只返回 JSON 数组。"

        result = _call_api(system_prompt, user_prompt)
        if result and isinstance(result, list):
            return [
                {
                    'news_score': max(0, min(100, float(r.get('news_score', 50)))),
                    'event_type': r.get('event_type', '其他'),
                    'confidence': float(r.get('confidence', 0)),
                }
                for r in result
            ]

    except Exception as e:
        print(f"[_get_ai_scores_batch] 批量分析失败: {e}", file=sys.stderr)

    # 失败返回全 None
    return [None] * len(items)


def _get_stock_news(code: str, stock_name: str = "") -> Optional[Dict]:
    """
    获取个股实时新闻（news.db 优先，akshare+DeepSeek 兜底）

    策略：
    1. news.db 有 48h 内新闻 → 直接用（ms级，无外部 API）
    2. news.db 无新闻 → akshare + DeepSeek AI（保留原有逻辑）

    核心改进：
    1. 多新闻评分：取 top-3 新闻分别 AI 打分，用最低分（防利空被埋）
    2. 只有 score >= 60 的新闻才能重置催化剂时钟
    3. 扩展风险关键词，增加负向修饰词检测
    4. 公司专属检测：新闻内容包含公司名称 >= 2 次才算专属催化
    """
    import warnings
    warnings.filterwarnings('ignore')

    clean_code = code[2:] if code.startswith(('SH', 'SZ', 'sh', 'sz')) else code

    # ===== Step 1：news.db 快速查询（优先） =====
    try:
        if NEWS_DB.exists():
            import sqlite3
            conn = sqlite3.connect(str(NEWS_DB))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
            c.execute(
                "SELECT keyword, sentiment, title, publish_time FROM news "
                "WHERE keyword = ? AND publish_time >= ? ORDER BY publish_time DESC LIMIT 20",
                (clean_code, cutoff)
            )
            rows = c.fetchall()
            conn.close()

            if rows:
                # 用 news.db 数据构造返回值
                catalyst_kw = ["增长", "超预期", "业绩", "中标", "合作", "突破", "创新",
                               "涨停", "大涨", "反弹", "订单", "签约", "投产", "量产",
                               "政策", "获批", "入选", "独家", "首发", "发布", "新品"]
                risk_kw = ["亏损", "处罚", "调查", "诉讼", "跌停", "减持", "ST",
                           "下滑", "降薪", "裁员", "关停", "造假", "退市", "逾期", "违约"]

                titles = [r['title'] for r in rows]
                sentiments = [r['sentiment'] for r in rows]
                pos_count = sum(1 for s in sentiments if s == 'positive')
                neg_count = sum(1 for s in sentiments if s == 'negative')
                total = len(rows)

                all_pos_kw, all_neg_kw = [], []
                for title in titles:
                    for kw in catalyst_kw:
                        if kw in title and kw not in all_pos_kw:
                            all_pos_kw.append(kw)
                    for kw in risk_kw:
                        if kw in title and kw not in all_neg_kw:
                            all_neg_kw.append(kw)

                kw_score = 50 + (pos_count - neg_count) / max(total, 1) * 30
                kw_score = max(0, min(100, kw_score))
                news_score = kw_score
                sentiment = 'positive' if pos_count > neg_count else ('negative' if neg_count > pos_count else 'neutral')

                print(f"[_get_stock_news] {code} news.db命中({total}条) → score={news_score:.0f} [无API]", file=sys.stderr)
                return {
                    'news_count': total,
                    'news_score': round(news_score, 1),
                    'ai_min_score': None,
                    'ai_avg_score': None,
                    'ai_scores_detail': [],
                    'kw_score': round(kw_score, 1),
                    'score_source': 'news_db',
                    'ai_confidence': 0.0,
                    'event_type': '其他',
                    'sentiment': sentiment,
                    'latest_title': titles[0] if titles else '',
                    'latest_time': rows[0]['publish_time'] if rows else '',
                    'catalyst_keywords': all_pos_kw[:8],
                    'risk_keywords': all_neg_kw,
                    'positive_count': pos_count,
                    'negative_count': neg_count,
                    'is_positive_catalyst': news_score >= 60,
                }
    except Exception as e:
        print(f"[_get_stock_news] {code} news.db 查询失败: {e}", file=sys.stderr)

    # ===== Step 2：news.db 无数据 → akshare + DeepSeek =====
    try:
        import akshare as ak
        import signal as _signal

        def _timeout_handler(signum, frame):
            raise TimeoutError("akshare timeout")

        # 添加 5 秒超时保护，防止 Eastmoney 挂起
        if sys.platform != 'win32':
            _old = _signal.signal(_signal.SIGALRM, _timeout_handler)
            _signal.alarm(5)
        try:
            df = ak.stock_news_em(symbol=clean_code)
        finally:
            if sys.platform != 'win32':
                _signal.alarm(0)
                _signal.signal(_signal.SIGALRM, _old)
        if df is None or len(df) == 0:
            return None

        # ===== 关键词库 =====
        catalyst_keywords = [
            "增长", "超预期", "业绩", "中标", "合作", "突破", "创新",
            "涨停", "大涨", "反弹", "订单", "签约", "投产", "量产",
            "政策", "获批", "入选", "独家", "首发", "发布", "新品",
            "扩产", "收购", "增持", "回购", "分红", "摘帽"
        ]
        # 风险关键词（利空信号）
        risk_keywords = [
            "亏损", "处罚", "调查", "诉讼", "跌停", "减持", "ST",
            "下滑", "降薪", "裁员", "关停", "造假", "造假", "退市",
            "逾期", "违约", "债务", "查封", "整改"
        ]

        total = min(len(df), 20)
        news_items = []

        for _, row in df.head(total).iterrows():
            title = str(row.get('新闻标题', row.get('关键词', '')))
            content = str(row.get('新闻内容', ''))[:500]
            pub_time = str(row.get('发布时间', ''))[:16]
            text = title + ' ' + content

            # 关键词命中
            pos_kw = [kw for kw in catalyst_keywords if kw in text]
            neg_kw = [kw for kw in risk_keywords if kw in text]
            cat_found = list(dict.fromkeys(pos_kw))  # 去重保留顺序

            # 公司专属检测：新闻提及公司名称次数（stock_name 来自上层传入）
            is_company_specific = False
            if stock_name and len(stock_name) >= 2:
                count = text.count(stock_name)
                is_company_specific = count >= 2

            news_items.append({
                'title': title[:100],
                'content': content[:200],
                'pub_time': pub_time,
                'pos_kw': cat_found,
                'neg_kw': neg_kw,
                'is_company_specific': is_company_specific,
            })

        if not news_items:
            return None

        # ===== AI 多新闻评分（核心改进 v4）=====
        # 策略：4条新闻 → 取最小分（最保守，防利空被埋）
        # 组成：最新1条 + 最高优先2条 + 含最多利空关键词1条
        scored_items = []
        for item in news_items:
            priority = 0
            if item['is_company_specific']:
                priority += 10
            priority += len(item['pos_kw']) * 2
            priority -= len(item['neg_kw']) * 5
            scored_items.append((priority, item))

        scored_items.sort(key=lambda x: x[0], reverse=True)

        sampled = []
        sampled_titles = set()

        def _add_sample(item):
            key = item['title'][:30]
            if key not in sampled_titles:
                sampled.append(item)
                sampled_titles.add(key)

        # 1. 强制：发布时间最新
        if news_items:
            latest_item = max(news_items, key=lambda x: x['pub_time'])
            _add_sample(latest_item)

        # 2. 强制：含最多利空关键词（即使在最底部也要抓）
        if news_items:
            worst_item = max(news_items, key=lambda x: len(x['neg_kw']))
            _add_sample(worst_item)

        # 3. 补满4条：最高优先级（已含latest/worst则跳过）
        for priority, item in scored_items:
            if len(sampled) >= 4:
                break
            _add_sample(item)

        # ===== 批量 AI 评分（一次 API 调用替代逐个分析）=====
        batch_results = _get_ai_scores_batch(sampled)

        ai_scores = []
        ai_confidences = []
        event_types = []
        best_title = ""
        best_score = 0
        best_conf = 0

        for i, item in enumerate(sampled):
            result = batch_results[i] if i < len(batch_results) else None
            if result and result.get('confidence', 0) >= AI_CONFIDENCE_THRESHOLD:
                score = result['news_score']
                conf = result['confidence']
                ai_scores.append(score)
                ai_confidences.append(conf)
                event_types.append(result.get('event_type', '其他'))
                if score > best_score:
                    best_score = score
                    best_conf = conf
                    best_title = item['title']

        # 加权综合评分（防止单一指标主导）
        # 逻辑：AI均值 + 极值调整
        #   极低分(≤40)说明有实质利空 → 严惩
        #   极高分(≥70)说明有明确利好 → 奖励
        #   正面新闻占比高 → 奖励
        #   负面新闻占比高 → 惩罚
        if ai_scores:
            avg_ai_score = sum(ai_scores) / len(ai_scores)
            min_ai = min(ai_scores)
            max_ai = max(ai_scores)
            avg_conf = sum(ai_confidences) / len(ai_confidences)

            # 极值调整
            if min_ai <= 40:
                adjustment = -10   # 有实质利空
            elif max_ai >= 75:
                adjustment = +5    # 有明确利好
            else:
                adjustment = 0

            # 正负信号占比调整（从采样项的关键词判断）
            pos_count = sum(1 for item in sampled if item['pos_kw'])
            neg_count = sum(1 for item in sampled if item['neg_kw'])
            if neg_count > pos_count:
                adjustment -= 5
            elif pos_count > neg_count + 1:
                adjustment += 3

            final_ai_score = max(0, min(100, avg_ai_score + adjustment))
            primary_event = event_types[ai_scores.index(min_ai)]

            kw_score = 50 + (sum(1 for i in news_items if i['pos_kw']) -
                             sum(1 for i in news_items if i['neg_kw'])) / max(total, 1) * 30
            kw_score = max(0, min(100, kw_score))

            if avg_conf >= AI_CONFIDENCE_THRESHOLD:
                news_score = final_ai_score
                score_source = 'ai_weighted'  # 加权综合评分
                ai_confidence = round(avg_conf, 3)
                print(f"[_get_stock_news] {code} AI({len(ai_scores)}条) → "
                      f"scores={ai_scores}, avg={avg_ai_score:.0f}, adj={adjustment:+.0f}, "
                      f"final={final_ai_score:.0f}, conf={avg_conf:.2f}", file=sys.stderr)
            else:
                news_score = kw_score
                score_source = 'keyword'
                ai_confidence = 0.0
                primary_event = '其他'
                print(f"[_get_stock_news] {code} AI conf={avg_conf:.2f} < {AI_CONFIDENCE_THRESHOLD}，"
                      f"降级关键词 score={kw_score:.0f}", file=sys.stderr)
        else:
            kw_score = 50 + (sum(1 for i in news_items if i['pos_kw']) -
                             sum(1 for i in news_items if i['neg_kw'])) / max(total, 1) * 30
            kw_score = max(0, min(100, kw_score))
            news_score = kw_score
            score_source = 'keyword'
            ai_confidence = 0.0
            primary_event = '其他'
            best_title = news_items[0]['title']
            print(f"[_get_stock_news] {code} 无AI结果，关键词 score={kw_score:.0f}", file=sys.stderr)

        # 催化剂关键词（从全部20条中汇总）
        all_pos_kw = []
        all_neg_kw = []
        for item in news_items:
            for kw in item['pos_kw']:
                if kw not in all_pos_kw:
                    all_pos_kw.append(kw)
            for kw in item['neg_kw']:
                if kw not in all_neg_kw:
                    all_neg_kw.append(kw)

        sentiment = 'positive' if news_score > 60 else ('negative' if news_score < 40 else 'neutral')

        return {
            'news_count': total,
            'news_score': round(news_score, 1),
            'ai_min_score': round(min(ai_scores), 1) if ai_scores else None,
            'ai_avg_score': round(sum(ai_scores) / len(ai_scores), 1) if ai_scores else None,
            'ai_scores_detail': [round(s, 1) for s in ai_scores],
            'kw_score': round(kw_score, 1),
            'score_source': score_source,
            'ai_confidence': ai_confidence,
            'event_type': primary_event,
            'sentiment': sentiment,
            'latest_title': best_title,
            'latest_time': max(item['pub_time'] for item in news_items) if news_items else '',
            'catalyst_keywords': all_pos_kw[:8],
            'risk_keywords': all_neg_kw,
            'positive_count': len(all_pos_kw),
            'negative_count': len(all_neg_kw),
            # P0 修复：只有 score >= 60 才视为有效催化
            'is_positive_catalyst': news_score >= 60,
        }
    except Exception as e:
        print(f"[_get_stock_news] {code} 失败: {e}")
        return None


def update_catalyst_for_stock(code: str, name: str = "", price: float = 0) -> Dict:
    """
    更新单只股票的催化剂状态

    Args:
        code: 股票代码
        name: 股票名称
        price: 当前价格

    Returns:
        更新后的催化剂记录
    """
    catalysts = _load_catalysts()
    now = datetime.now()

    news_data = _get_stock_news(code)

    if code in catalysts:
        record = catalysts[code]
        record['update_time'] = now.isoformat()
        record['price'] = price
        if name:
            record['name'] = name
    else:
        record = {
            'code': code,
            'name': name or code,
            'symbol': f"SH{code}" if code.startswith('6') else f"SZ{code}",
            'added_time': now.isoformat(),
            'update_time': now.isoformat(),
            'price': price,
            'tag': '🟡',  # 默认观察中
            'catalyst_count': 0,
            'catalyst_keywords': [],
            'news_score': 0,
            'latest_news': '',
            'days_without_catalyst': 0,
            'score_source': 'keyword',  # 评分来源
            'ai_confidence': 0.0,         # AI置信度
        }

    if news_data:
        record['news_score'] = news_data.get('news_score', 0)
        record['sentiment'] = news_data.get('sentiment', 'neutral')
        record['catalyst_keywords'] = news_data.get('catalyst_keywords', [])
        record['catalyst_count'] = len(news_data.get('catalyst_keywords', []))
        record['latest_news'] = news_data.get('latest_title', '')
        record['latest_time'] = news_data.get('latest_time', '')
        record['score_source'] = news_data.get('score_source', 'keyword')
        record['ai_confidence'] = news_data.get('ai_confidence', 0.0)
        record['risk_keywords'] = news_data.get('risk_keywords', [])
        record['ai_scores_detail'] = news_data.get('ai_scores_detail', [])
        record['ai_min_score'] = news_data.get('ai_min_score')
        record['ai_avg_score'] = news_data.get('ai_avg_score')

        # P0 修复：只有 score >= 60 才视为实质性正面催化，重置时钟
        news_score = news_data.get('news_score', 0)
        is_positive = news_data.get('is_positive_catalyst', news_score >= 60)

        if is_positive:
            record['last_catalyst_time'] = now.isoformat()
            record['days_without_catalyst'] = 0
        else:
            last_cat = record.get('last_catalyst_time')
            if last_cat:
                try:
                    last_dt = datetime.fromisoformat(last_cat)
                    record['days_without_catalyst'] = (now - last_dt).days
                except Exception:
                    record['days_without_catalyst'] = 0
            else:
                record['days_without_catalyst'] = 0

        # 时效降级（tag）
        days_no_cat = record.get('days_without_catalyst', 0)
        if news_score < 40:
            record['tag'] = '🔴'  # 利空
        elif is_positive:
            record['tag'] = '🟢'  # 实质性催化
        elif days_no_cat >= CATALYST_CONFIG['expiry_days']:
            record['tag'] = '🔴'  # 过期
        elif days_no_cat >= 7:
            record['tag'] = '🟡'  # 观察中
        else:
            record['tag'] = '🟡'  # 中性催化（score 40-59，无实质利好）

    else:
        # 无新闻数据
        record['news_count'] = 0
        record['news_score'] = 0
        record['sentiment'] = 'neutral'
        record['catalyst_keywords'] = []
        record['catalyst_count'] = 0
        last_cat = record.get('last_catalyst_time')
        if last_cat:
            try:
                last_dt = datetime.fromisoformat(last_cat)
                record['days_without_catalyst'] = (now - last_dt).days
            except Exception:
                record['days_without_catalyst'] = 0
        if record.get('days_without_catalyst', 0) >= CATALYST_CONFIG['expiry_days']:
            record['tag'] = '🔴'
        elif record.get('days_without_catalyst', 0) >= 7:
            record['tag'] = '🟡'

    catalysts[code] = record
    _save_catalysts(catalysts)
    return record


def get_catalyst_status(code: str) -> Optional[Dict]:
    """查询某只股票的催化剂状态（不触发更新）"""
    catalysts = _load_catalysts()
    return catalysts.get(code)


def has_valid_catalyst(code: str) -> bool:
    """
    判断某只股票是否有有效催化剂（供 auto_trade_patch 直接调用）

    有效条件（修复版）：news_score >= 60 且 7天内有 score>=60 的实质性催化
    """
    try:
        from datetime import datetime as dt
        cat = get_catalyst_status(code)
        if not cat:
            return False
        score = cat.get('news_score', 0)
        last_cat = cat.get('last_catalyst_time', '')
        # score < 40 → 🔴利空 / score 40-59 → 🟡中性 / score >= 60 → 🟢实质催化
        is_positive = score >= 60  # 只看 score，不依赖 tag（tag可能滞后的旧值）
        if not is_positive:
            return False
        if not last_cat:
            return False
        try:
            last_dt = dt.fromisoformat(last_cat.replace('Z', ''))
            days_no_cat = (dt.now() - last_dt).days
        except Exception:
            days_no_cat = 999
        return days_no_cat <= 7
    except Exception:
        return False


def batch_update_catalysts(codes: List[str], progress: bool = True) -> Dict[str, Dict]:
    """批量更新多只股票的催化剂状态"""
    results = {}

    # 预批量获取股票名称：优先从 stock_pool.db 查（权威来源），无则降级 Xueqiu
    name_map = {}
    if codes:
        # Step 1: stock_pool.db 批量查（覆盖全A股，含科创板+创业板+北交所）
        try:
            import sqlite3 as _sq3
            if STOCK_POOL_DB.exists():
                _conn = _sq3.connect(str(STOCK_POOL_DB))
                placeholders = ','.join(['?' for _ in codes])
                rows = _conn.execute(
                    f"SELECT symbol, name FROM stock_pool WHERE symbol IN ({placeholders})",
                    codes
                ).fetchall()
                _conn.close()
                name_map = {r[0]: r[1] for r in rows}
                print(f"[催化剂批量] ✅ stock_pool 命中 {len(name_map)} 只", file=sys.stderr)
        except Exception as e:
            print(f"[催化剂批量] ⚠️ stock_pool 查询失败: {e}", file=sys.stderr)

        # Step 2: Xueqiu 补漏（stock_pool 没有的股票）
        missing = [c for c in codes if c not in name_map]
        if missing:
            try:
                sys.path.insert(0, str(WORKSPACE / "core"))
                from xueqiu_engine import XueqiuEngine
                xq = XueqiuEngine(config_file=str(WORKSPACE / "core" / "config.json"))
                syms = [f"SH{c}" if c.startswith(('0', '6')) else f"SZ{c}" for c in missing]
                quotes = xq.get_stock_quotes(syms) or {}
                for sym, q in quotes.items():
                    code_raw = sym[2:]
                    name_map[code_raw] = q.get('name', code_raw)
                print(f"[催化剂批量] ✅ Xueqiu 补漏 {len(missing)} 只", file=sys.stderr)
            except Exception as e:
                print(f"[催化剂批量] ⚠️ Xueqiu 补漏失败: {e}", file=sys.stderr)

    total = len(codes)
    for i, code in enumerate(codes):
        if progress and (i + 1) % 10 == 0:
            print(f"[催化剂批量] {i+1}/{total}", file=sys.stderr)
        try:
            name = name_map.get(code, "")
            result = update_catalyst_for_stock(code, name=name)
            results[code] = result
        except Exception as e:
            print(f"[催化剂批量] {code} 失败: {e}", file=sys.stderr)
            results[code] = None
    return results


def update_catalysts_batch(codes: List[str], progress: bool = True) -> Dict[str, Dict]:
    """批量更新（别名，兼容 market_scan 等调用方）"""
    return batch_update_catalysts(codes, progress)


def batch_get_stock_news_with_ai(codes: List[str]) -> Dict[str, Dict]:
    """
    P2 批量获取多只股票的催化剂数据（DeepSeek 批量分析版）

    策略：
    1. news.db 有 48h 内新闻 → 直接用（ms级，无外部 API）
    2. news.db 无新闻 → akshare 并发获取 + DeepSeek 批量一次分析

    Args:
        codes: 股票代码列表

    Returns:
        {code: catalyst_record, ...}
    """
    results = {}
    code_to_name = {}  # {代码: 公司名}，Step 0 填充，Step 1 使用
    import warnings
    warnings.filterwarnings('ignore')

    # ===== Step 1: news.db 批量快速查询 =====
    cached, uncached = {}, []
    try:
        if NEWS_DB.exists():
            import sqlite3
            conn = sqlite3.connect(str(NEWS_DB))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")

            codes_for_db = list(code_to_name.values())
            if not codes_for_db:
                codes_for_db = [c.replace('SZ', '').replace('SH', '').replace('sz', '').replace('sh', '')
                                for c in codes]

            placeholders = ','.join(['?' for _ in codes_for_db])
            c.execute(
                f"SELECT keyword, sentiment, title, publish_time FROM news "
                f"WHERE keyword IN ({placeholders}) AND publish_time >= ? ORDER BY publish_time DESC",
                codes_for_db + [cutoff]
            )
            rows = c.fetchall()
            conn.close()

            name_to_code = {v: k for k, v in code_to_name.items()} if code_to_name else {}
            news_by_code = {code: [] for code in codes}
            for row in rows:
                name = row['keyword']
                if name in name_to_code:
                    stock_code = name_to_code[name]
                    if stock_code in news_by_code:
                        news_by_code[stock_code].append(row)

            catalyst_kw = ["增长", "超预期", "业绩", "中标", "合作", "突破", "创新",
                           "涨停", "大涨", "反弹", "订单", "签约", "投产", "量产",
                           "政策", "获批", "入选", "独家", "首发", "发布", "新品"]
            risk_kw = ["亏损", "处罚", "调查", "诉讼", "跌停", "减持", "ST",
                       "下滑", "降薪", "裁员", "关停", "造假", "退市", "逾期", "违约"]

            for code, news_items in news_by_code.items():
                if news_items:
                    titles = [r['title'] for r in news_items]
                    sentiments = [r['sentiment'] for r in news_items]
                    pos_count = sum(1 for s in sentiments if s == 'positive')
                    neg_count = sum(1 for s in sentiments if s == 'negative')
                    total = len(news_items)
                    kw_score = max(0, min(100, 50 + (pos_count - neg_count) / max(total, 1) * 30))
                    sentiment = 'positive' if pos_count > neg_count else ('negative' if neg_count > pos_count else 'neutral')

                    all_pos, all_neg = [], []
                    for title in titles:
                        for kw in catalyst_kw:
                            if kw in title and kw not in all_pos:
                                all_pos.append(kw)
                        for kw in risk_kw:
                            if kw in title and kw not in all_neg:
                                all_neg.append(kw)

                    cached[code] = {
                        'news_count': total,
                        'news_score': round(kw_score, 1),
                        'kw_score': round(kw_score, 1),
                        'score_source': 'news_db',
                        'ai_confidence': 0.0,
                        'event_type': '其他',
                        'sentiment': sentiment,
                        'latest_title': titles[0],
                        'latest_time': news_items[0]['publish_time'],
                        'catalyst_keywords': all_pos[:8],
                        'risk_keywords': all_neg,
                        'positive_count': pos_count,
                        'negative_count': neg_count,
                        'is_positive_catalyst': kw_score >= 60,
                    }
                    results[code] = cached[code]
                else:
                    uncached.append(code)
        else:
            uncached = codes
    except Exception as e:
        print(f"[批量催化剂] news.db 查询失败: {e}", file=sys.stderr)
        uncached = codes

    # ===== Step 0: DeepSeek 一次映射股票代码→公司名称 =====
    code_to_name = {}
    if uncached:
        try:
            import time as _time
            import urllib.request as _urllib
            import ssl as _ssl
            _ctx = _ssl.create_default_context()
            _api_key = os.getenv('DEEPSEEK_API_KEY', 'sk-svanyvfwiuhgcsfdlisclydwmckfuddxvtjupcqrcpkoyanq')
            _api_host = os.getenv('DEEPSEEK_API_HOST', 'api.siliconflow.cn')
            _model = os.getenv('DEEPSEEK_MODEL', 'deepseek-ai/DeepSeek-V3.2')

            _system = ("你是一个A股数据库助手。将股票代码转为A股公司名称（简称即可）。"
                       "严格按以下JSON格式返回，不要其他任何文字：\n"
                       '{"代码": "公司简称", ...}\n'
                       "示例：002156→通富微电，601138→工业富联，000988→华工科技")
            _user = "股票代码列表（6位数字）：\n" + "\n".join(uncached[:20])

            _req_body = {
                'model': _model,
                'messages': [{'role': 'system', 'content': _system},
                             {'role': 'user', 'content': _user}],
                'temperature': 0.1, 'max_tokens': 800, 'stream': False
            }
            _data = json.dumps(_req_body).encode('utf-8')
            _req = _urllib.Request(
                f'https://{_api_host}/v1/chat/completions',
                data=_data,
                headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {_api_key}'},
                method='POST'
            )
            _t0 = _time.time()
            with _urllib.urlopen(_req, context=_ctx, timeout=8) as _resp:
                _content = json.loads(_resp.read().decode('utf-8'))['choices'][0]['message']['content'].strip()
                if _content.startswith('```'):
                    _content = _content.split('```')[1]
                    if _content.startswith('json'):
                        _content = _content[4:]
                _raw = json.loads(_content)
                for _raw_code, _name in _raw.items():
                    _clean = _raw_code.replace('SZ', '').replace('SH', '').replace('sz', '').replace('sh', '')
                    code_to_name[_clean] = _name
            print(f"[催化剂映射] ✅ DeepSeek 映射 {len(code_to_name)} 只，耗时 {_time.time()-_t0:.1f}s", file=sys.stderr)
        except Exception as _e:
            print(f"[催化剂映射] ⚠️ DeepSeek 映射失败: {_e}，降级为代码查询", file=sys.stderr)

    # ===== Step 2: news.db 未命中 → akshare 并发获取新闻 =====
    if not uncached:
        return results

    print(f"[批量催化剂] news.db 命中 {len(cached)} 只，未命中 {len(uncached)} 只，开始 akshare 获取...", file=sys.stderr)

    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTTimeoutError
    akshare_results = {}

    def _fetch_one(code):
        try:
            import akshare as ak
            import re
            clean_code = code if code.isdigit() else re.sub(r'[^0-9]', '', code)
            query_name = code_to_name.get(clean_code, clean_code)
            df = ak.stock_news_em(symbol=query_name)
            if df is None or len(df) == 0:
                return code, []
            rows = []
            for _, row in df.head(10).iterrows():
                title = str(row.get('新闻标题', row.get('关键词', '')))
                pub_time = str(row.get('发布时间', ''))[:16]
                rows.append({'title': title[:100], 'pub_time': pub_time})
            return code, rows
        except Exception:
            return code, []

    TOTAL_TIMEOUT = 20

    def _run_batch():
        nonlocal uncached
        FETCH_TIMEOUT = 5
        with ThreadPoolExecutor(max_workers=min(len(uncached), 4)) as executor:
            future_map = {executor.submit(_fetch_one, c): c for c in uncached}
            done, not_done = futures.wait(set(future_map), timeout=min(len(uncached) * FETCH_TIMEOUT, 8))
            for fut in done:
                code = future_map[fut]
                news_items = fut.result()
                akshare_results[code] = news_items
            for fut in not_done:
                fut.cancel()
            if not_done:
                print(f"[批量催化剂] ⚠️ {len(not_done)} 只 akshare 超时", file=sys.stderr)

        # ===== Step 3: DeepSeek 批量一次分析 =====
        codes_with_news = [code for code, news in akshare_results.items() if news]
        if not codes_with_news:
            return

        system_prompt = (
            '你是一个财经新闻情绪分析师。对于每只股票的多条新闻，给出该股票的综合情绪分(0-100, 50=中性, >50利好, <50利空)。\n'
            '严格按以下JSON格式返回（不要其他任何文字）：\n'
            '{"股票代码": {"score": 分数, "sentiment": "positive/negative/neutral", "reason": "简要理由（不超过20字）"}, ...}'
        )
        user_prompt_lines = ["分析以下股票新闻（股票代码: 新闻列表）："]
        for code in codes_with_news[:8]:
            user_prompt_lines.append(f"【{code}】")
            news_items = akshare_results.get(code, [])
            if isinstance(news_items, list):
                for item in news_items[:4]:
                    if isinstance(item, dict) and 'pub_time' in item and 'title' in item:
                        user_prompt_lines.append(f"  - [{item['pub_time']}] {item['title']}")
        user_prompt = "\n".join(user_prompt_lines) + "\n"

        import time as time_module
        import urllib.request as _urllib
        import ssl as _ssl
        _context = _ssl.create_default_context()
        api_key = os.getenv('DEEPSEEK_API_KEY', 'sk-svanyvfwiuhgcsfdlisclydwmckfuddxvtjupcqrcpkoyanq')
        api_host = os.getenv('DEEPSEEK_API_HOST', 'api.siliconflow.cn')
        model = os.getenv('DEEPSEEK_MODEL', 'deepseek-ai/DeepSeek-V3.2')
        req_body = {
            'model': model, 'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ], 'temperature': 0.3, 'max_tokens': 1500, 'stream': False
        }
        data = json.dumps(req_body).encode('utf-8')
        req = _urllib.Request(
            f'https://{api_host}/v1/chat/completions',
            data=data,
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            method='POST'
        )
        start = time_module.time()
        with _urllib.urlopen(req, context=_context, timeout=10) as response:
            elapsed = time_module.time() - start
            resp_data = json.loads(response.read().decode('utf-8'))
            content_str = resp_data['choices'][0]['message']['content'].strip()
            if content_str.startswith('```'):
                content_str = content_str.split('```')[1]
                if content_str.startswith('json'):
                    content_str = content_str[4:]
            ai_scores_map = json.loads(content_str)
            for code, ai_result in ai_scores_map.items():
                if code in akshare_results and isinstance(akshare_results[code], list):
                    results[code] = {
                        'news_count': len(akshare_results[code]),
                        'news_score': max(0, min(100, ai_result.get('score', 50))),
                        'kw_score': 0, 'score_source': 'ai',
                        'ai_confidence': 0.7,
                        'event_type': '其他',
                        'sentiment': ai_result.get('sentiment', 'neutral'),
                        'latest_title': akshare_results[code][0].get('title', '') if akshare_results[code] else '',
                        'catalyst_keywords': [], 'risk_keywords': [],
                        'positive_count': 1 if ai_result.get('sentiment') == 'positive' else 0,
                        'negative_count': 1 if ai_result.get('sentiment') == 'negative' else 0,
                        'is_positive_catalyst': ai_result.get('score', 50) >= 60,
                    }
            print(f"[批量催化剂] ✅ DeepSeek 分析 {len(ai_scores_map)} 只，耗时 {elapsed:.1f}秒", file=sys.stderr)

    # 用线程池执行整个 batch，带总超时保护
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            fut = executor.submit(_run_batch)
            fut.result(timeout=TOTAL_TIMEOUT)
    except FTTimeoutError:
        print(f"[批量催化剂] ⚠️ 整体超时({TOTAL_TIMEOUT}s)，跳过 DeepSeek 分析", file=sys.stderr)
    except Exception as e:
        print(f"[批量催化剂] ⚠️ Step2/3 执行异常: {e}", file=sys.stderr)

    return results


def get_catalyst_report(min_score: float = 40, max_count: int = 50) -> List[Dict]:
    """生成催化剂排名报告（供选股参考）"""
    catalysts = _load_catalysts()
    results = []
    for code, record in catalysts.items():
        if record.get('news_score', 0) >= min_score:
            results.append({
                'code': code,
                'name': record.get('name', code),
                'symbol': record.get('symbol', ''),
                'tag': record.get('tag', '🟡'),
                'news_score': record.get('news_score', 0),
                'kw_score': record.get('kw_score', 0),
                'score_source': record.get('score_source', 'keyword'),
                'ai_confidence': record.get('ai_confidence', 0.0),
                'catalyst_count': record.get('catalyst_count', 0),
                'catalyst_keywords': record.get('catalyst_keywords', []),
                'event_type': record.get('event_type', '其他'),
                'days_without_catalyst': record.get('days_without_catalyst', 0),
                'last_catalyst_time': record.get('last_catalyst_time', ''),
                'update_time': record.get('update_time', ''),
            })

    results.sort(key=lambda x: x.get('news_score', 0), reverse=True)
    return results[:max_count]


def prune_stale_catalysts(max_age_days: int = 30) -> int:
    """删除超过指定天数的过期催化剂记录"""
    catalysts = _load_catalysts()
    now = datetime.now()
    removed = 0
    for code, record in list(catalysts.items()):
        added = record.get('added_time', '')
        if added:
            try:
                added_dt = datetime.fromisoformat(added)
                if (now - added_dt).days > max_age_days:
                    del catalysts[code]
                    removed += 1
            except Exception:
                pass
    if removed > 0:
        _save_catalysts(catalysts)
    return removed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='催化剂追踪工具')
    parser.add_argument('--update', nargs='+', help='更新指定股票代码')
    parser.add_argument('--report', action='store_true', help='生成催化剂报告')
    parser.add_argument('--prune', action='store_true', help='清理过期记录')
    args = parser.parse_args()

    if args.update:
        print(f"正在更新 {len(args.update)} 只股票...")
        for code in args.update:
            result = update_catalyst_for_stock(code)
            print(f"  {code}: score={result.get('news_score', 0):.0f} {result.get('tag', '?')} "
                  f"[{result.get('score_source', '?')}] "
                  f"{result.get('catalyst_keywords', [])[:3]}")

    if args.report:
        report = get_catalyst_report()
        print(f"\n{'代码':<8} {'名称':<10} {'评分':<6} {'来源':<8} {'置信':<5} {'标签':<3} {'关键词'}")
        print("-" * 80)
        for r in report:
            print(f"{r['code']:<8} {r['name']:<10} {r['news_score']:<6.1f} "
                  f"{r.get('score_source','?'):<8} {r.get('ai_confidence',0):.2f}   "
                  f"{r['tag']}   {','.join(r.get('catalyst_keywords', [])[:3])}")

    if args.prune:
        removed = prune_stale_catalysts()
        print(f"已清理 {removed} 条过期记录")
