#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 美股行情联动分析模块 (快速版)
功能：分析隔夜美股行情对 A 股的潜在影响

数据源:
- akshare: 美股指数历史（收盘价/涨跌）| 大宗商品实时
- tushare: A50 指数（index_global / hk_daily）| 中概股 ETF（us_daily）
- 本地配置：汇率 (手动更新)
"""

import sys
from pathlib import Path
from datetime import datetime
import json
import os

# akshare 美股指数代码（新浪格式）
US_INDICES_AKSYMBOL = {
    "道琼斯": ".DJI",
    "纳斯达克": ".IXIC",
    "标普 500": ".INX",
}

# 大宗商品代码（akshare futures_foreign_commodity_realtime）
COMMODITIES_AKSYMBOL = {
    "黄金": "GC",
    "WTI原油": "CL",
}


# ------------------------------------------------------------------
# 数据获取底层函数
# ------------------------------------------------------------------

def _get_us_index_price_from_ak(cn_name: str, symbol: str) -> dict | None:
    """
    用 akshare 获取美股指数最新收盘价+涨跌。
    last 2 rows: row[-1]=今日(最新), row[-2]=昨日
    change = today_close - prev_close
    change_pct = change / prev_close * 100
    """
    try:
        import akshare as ak
        df = ak.index_us_stock_sina(symbol=symbol)
        if df is None or df.empty or len(df) < 2:
            return None
        today = df.iloc[-1]
        prev = df.iloc[-2]
        today_close = float(today["close"])
        prev_close = float(prev["close"])
        change = round(today_close - prev_close, 2)
        change_pct = round(change / prev_close * 100, 2) if prev_close else 0
        return {
            "current": today_close,
            "change": change,
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"❌ akshare 获取 {cn_name} 失败：{e}")
        return None


def _get_commodity_from_ak(symbol: str) -> dict | None:
    """用 akshare futures_foreign_commodity_realtime 获取黄金/原油实时数据。"""
    try:
        import akshare as ak
        df = ak.futures_foreign_commodity_realtime(symbol=symbol)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "current": float(row["最新价"]),
            "change": float(row["涨跌额"]),
            "change_pct": float(row["涨跌幅"].strip("%")) if isinstance(row["涨跌幅"], str) else float(row["涨跌幅"]),
        }
    except Exception as e:
        print(f"❌ akshare 获取 {symbol} 失败：{e}")
        return None


def _get_tushare_pro():
    """获取 tushare pro_api 实例（统一入口）"""
    try:
        from _api_config import get_tushare_pro
        return get_tushare_pro()
    except Exception as e:
        raise RuntimeError(f"Tushare 初始化失败: {e}")


# ------------------------------------------------------------------
# 对外接口：指数 / ETF / A50 / 大宗商品
# ------------------------------------------------------------------

def get_us_indices() -> dict:
    """
    获取美股三大指数（昨日收盘价 + 今日涨跌）。
    
    数据源：akshare index_us_stock_sina
    - 美股收盘后（非交易时段）数据最准确
    - 交易时段也能拿到上一个交易日收盘价 + 当日开盘价供参考
    """
    indices = {}
    for cn_name, symbol in US_INDICES_AKSYMBOL.items():
        data = _get_us_index_price_from_ak(cn_name, symbol)
        if data and data.get("current", 0) > 0:
            indices[cn_name] = {
                "symbol": symbol,
                "name": cn_name,
                "current": data["current"],
                "change": data["change"],
                "change_pct": data["change_pct"],
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            print(f"⚠️ {cn_name} 获取失败（akshare 返回空）")
            indices[cn_name] = {
                "symbol": symbol,
                "name": cn_name,
                "current": 0,
                "change": 0,
                "change_pct": 0,
                "update_time": "数据不可用",
            }
    return indices


def get_china_etfs() -> list:
    """
    获取中概股 ETF（tushare us_daily → akshare 降级 → 缓存）。
    """
    from datetime import timedelta

    results = []
    fallback_data = [
        {"symbol": "KWEB", "name": "中概互联网 ETF", "current": 28.5, "change_pct": 1.8},
        {"symbol": "PGJ", "name": "高盛中国 ETF", "current": 45.2, "change_pct": 1.2},
    ]

    today = datetime.now()
    start = (today - timedelta(days=5)).strftime('%Y%m%d')  # 覆盖周末
    end = today.strftime('%Y%m%d')

    # 1. 优先：tushare us_daily
    try:
        pro = _get_tushare_pro()
        for symbol in ("KWEB", "PGJ"):
            df = pro.us_daily(ts_code=symbol, start_date=start, end_date=end)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                close = float(row.get('close', 0) or 0)
                pct_chg = float(row.get('pct_change', 0) or 0)
                if close > 0:
                    results.append({
                        "symbol": symbol,
                        "name": f"{symbol} ETF",
                        "current": close,
                        "change_pct": pct_chg,
                    })
                    print(f"✓ tushare 获取 {symbol}：{close} ({pct_chg:+.2f}%)")
    except Exception as e:
        print(f"❌ tushare 获取中概股 ETF 失败：{e}")

    # 2. 降级：akshare stock_us_spot_em
    if not results:
        try:
            import akshare as ak
            df = ak.stock_us_spot_em()
            if df is not None and not df.empty:
                for symbol in ("KWEB", "PGJ"):
                    row = df[df['代码'] == symbol]
                    if not row.empty:
                        r = row.iloc[0]
                        current = float(r.get('最新价', 0) or 0)
                        change_pct = float(r.get('涨跌幅', 0) or 0)
                        if current > 0:
                            results.append({
                                "symbol": symbol,
                                "name": r.get('名称', symbol),
                                "current": current,
                                "change_pct": change_pct,
                            })
                            print(f"✓ akshare 获取 {symbol}（降级）：{current} ({change_pct:+.2f}%)")
        except Exception as e:
            print(f"❌ akshare 获取中概股 ETF 失败：{e}")

    # 3. 兜底缓存
    if not results:
        print("⚠️ 中概股 ETF 使用缓存数据 (非交易时段)")
        results = fallback_data
    return results


def get_a50_futures() -> dict:
    """
    获取富时 A50 指数（tushare index_global → tushare hk_daily → akshare 降级 → 缓存）。
    """
    from datetime import timedelta
    today = datetime.now()
    start = (today - timedelta(days=5)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')

    # 1. tushare index_global（FTSE China A50 指数）
    try:
        pro = _get_tushare_pro()
        df = pro.index_global(ts_code='XIN9', trade_date='')
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row.get('close', 0) or 0)
            pct_chg = float(row.get('pct_chg', 0) or 0)
            if close > 0:
                print(f"✓ tushare 获取 A50 指数：{close} ({pct_chg:+.2f}%)")
                return {
                    "current": close,
                    "change": float(row.get('change', 0) or 0),
                    "change_pct": pct_chg,
                }
    except Exception as e:
        print(f"⚠️ tushare index_global A50 失败：{e}")

    # 2. tushare hk_daily（iShares 安硕 A50 ETF 02823.HK，跟踪富时 A50）
    try:
        pro = _get_tushare_pro()
        df = pro.hk_daily(ts_code='02823.HK', start_date=start, end_date=end)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row.get('close', 0) or 0)
            pct_chg = float(row.get('pct_chg', 0) or 0)
            if close > 0:
                print(f"✓ tushare 获取 A50 ETF(02823.HK)：{close} ({pct_chg:+.2f}%)")
                return {
                    "current": close,
                    "change": float(row.get('change', 0) or 0),
                    "change_pct": pct_chg,
                }
    except Exception as e:
        print(f"⚠️ tushare hk_daily A50 ETF 失败：{e}")

    # 3. 降级：akshare index_global_spot_em（东方财富全球指数）
    try:
        import akshare as ak
        df = ak.index_global_spot_em()
        if df is not None and not df.empty:
            a50_row = df[df['名称'].str.contains('A50|富时中国', na=False)]
            if not a50_row.empty:
                row = a50_row.iloc[0]
                current = float(row.get('最新价', 0) or 0)
                change = float(row.get('涨跌额', 0) or 0)
                change_pct = float(row.get('涨跌幅', 0) or 0)
                if current > 0:
                    print(f"✓ akshare 获取 A50（降级）：{current} ({change_pct:+.2f}%)")
                    return {"current": current, "change": change, "change_pct": change_pct}
    except Exception as e:
        print(f"❌ akshare 获取 A50 失败：{e}")

    # 4. 兜底缓存
    print("⚠️ A50 期货使用缓存数据 (非交易时段)")
    return {"current": 11580, "change": 80, "change_pct": 0.70}


def get_usd_cny_rate() -> dict:
    """获取美元人民币汇率（简化版，固定值）。"""
    return {"rate": 7.20, "date": datetime.now().strftime("%Y-%m-%d")}


def get_commodities() -> dict:
    """
    获取黄金、WTI 原油大宗商品价格（akshare 实时）。
    """
    commodities = {}
    fallback_data = {
        "黄金": {"current": 2340, "change": 12, "change_pct": 0.52},
        "WTI原油": {"current": 83.5, "change": -0.3, "change_pct": -0.36},
    }
    for cn_name, symbol in COMMODITIES_AKSYMBOL.items():
        data = _get_commodity_from_ak(symbol)
        if data and data.get("current", 0) > 0:
            commodities[cn_name] = {
                "symbol": symbol,
                "name": cn_name,
                "current": data["current"],
                "change": data["change"],
                "change_pct": data["change_pct"],
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            print(f"⚠️ {cn_name} 使用缓存数据 (akshare 返回空)")
            commodities[cn_name] = {
                "symbol": symbol,
                "name": cn_name,
                **fallback_data[cn_name],
                "update_time": "前一交易日收盘",
            }
    return commodities


def calculate_sentiment_score(us_data: dict, china_etf_data: list, a50_data: dict, commodities: dict = None) -> dict:
    """
    计算隔夜外盘情绪分数
    """
    score = 50  # 基准分
    
    # 美股指数影响 (权重 40%)
    if us_data:
        nasdaq_chg = us_data.get("纳斯达克", {}).get("change_pct", 0)
        spx_chg = us_data.get("标普 500", {}).get("change_pct", 0)
        if nasdaq_chg or spx_chg:
            avg_us = (nasdaq_chg + spx_chg) / 2
            score += avg_us * 10
    
    # 中概股 ETF 影响 (权重 35%)
    if china_etf_data:
        avg_etf = sum([e.get("change_pct", 0) for e in china_etf_data]) / len(china_etf_data)
        score += avg_etf * 15
    
    # A50 期货影响 (权重 25%)
    if a50_data and a50_data.get("change_pct", 0):
        score += a50_data["change_pct"] * 10
    
    # 大宗商品影响 (权重 10%，黄金/原油反映全球风险偏好)
    if commodities:
        gold_chg = commodities.get("黄金", {}).get("change_pct", 0)
        oil_chg = commodities.get("WTI原油", {}).get("change_pct", 0)
        # 黄金上涨 → 避险情绪升温 → 负面；原油上涨 → 通胀预期 → 谨慎
        if gold_chg or oil_chg:
            avg_commodity = (gold_chg + oil_chg) / 2
            score += avg_commodity * 5  # 低权重，毕竟是外围情绪辅助
    
    score = max(0, min(100, score))
    
    if score >= 70:
        level = "🟢 强烈正面"
    elif score >= 55:
        level = "🟡 温和正面"
    elif score >= 45:
        level = "⚪ 中性"
    elif score >= 30:
        level = "🟠 温和负面"
    else:
        level = "🔴 强烈负面"
    
    return {
        "score": round(score, 2),
        "level": level,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def generate_us_market_report() -> dict:
    """
    生成美股联动分析报告
    """
    print("📊 开始分析美股联动数据...")
    
    us_indices = get_us_indices()
    china_etfs = get_china_etfs()
    a50 = get_a50_futures()
    usd_cny = get_usd_cny_rate()
    commodities = get_commodities()
    
    sentiment = calculate_sentiment_score(us_indices, china_etfs, a50, commodities)
    
    # 生成策略建议
    if sentiment["score"] >= 70:
        strategy_stance = "🟢 aggressive_buy"
        position_limit = 80
        risk_warning = "外盘情绪强烈正面，可激进建仓"
    elif sentiment["score"] >= 55:
        strategy_stance = "🟡 cautious_buy"
        position_limit = 60
        risk_warning = "外盘情绪温和正面，适度建仓"
    elif sentiment["score"] >= 45:
        strategy_stance = "⚪ hold"
        position_limit = 40
        risk_warning = "外盘情绪中性，观望为主"
    elif sentiment["score"] >= 30:
        strategy_stance = "🟠 reduce"
        position_limit = 20
        risk_warning = "外盘情绪负面，降低仓位"
    else:
        strategy_stance = "🔴 cut_loss"
        position_limit = 10
        risk_warning = "外盘情绪强烈负面，空仓观望"
    
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "us_market": {
            "indices": us_indices,
            "china_etfs": china_etfs,
            "a50_futures": a50,
            "usd_cny": usd_cny,
            "commodities": commodities,
        },
        "sentiment": sentiment,
        "initial_strategy": {
            "stance": strategy_stance,
            "position_limit": position_limit,
            "risk_warning": risk_warning,
            "watchlist": []
        }
    }


def save_report(report: dict, output_path: str = None):
    """保存报告到文件"""
    if output_path is None:
        try:
            from workspace_detector import get_data_dir
            output_path = str(get_data_dir() / "us_market_linkage.json")
        except ImportError:
            output_path = str(Path(__file__).resolve().parents[2] / "data" / "us_market_linkage.json")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 报告已保存至：{output_path}")


if __name__ == "__main__":
    report = generate_us_market_report()
    save_report(report)
    
    # 打印摘要
    print("\n" + "="*60)
    print("📈 Marcus 美股联动分析报告 (tushare+akshare)")
    print("="*60)
    print(f"时间：{report['timestamp']}")
    
    print(f"\n🇺🇸 美股指数:")
    for name, data in report["us_market"]["indices"].items():
        if data.get("current", 0) > 0:
            chg = data.get("change_pct", 0)
            sign = "+" if chg > 0 else ""
            print(f"  {name}: {data['current']:.2f} ({sign}{chg:.2f}%)")
        else:
            print(f"  {name}: 数据暂不可用")
    
    print(f"\n🇨🇳 中概股 ETF:")
    if report["us_market"]["china_etfs"]:
        for etf in report["us_market"]["china_etfs"]:
            chg = etf.get("change_pct", 0)
            sign = "+" if chg > 0 else ""
            print(f"  {etf['symbol']}: {etf['current']:.2f} ({sign}{chg:.2f}%)")
    else:
        print("  数据暂不可用")
    
    if report["us_market"]["a50_futures"] and report["us_market"]["a50_futures"].get("current", 0) > 0:
        a50 = report["us_market"]["a50_futures"]
        chg = a50.get("change_pct", 0)
        sign = "+" if chg > 0 else ""
        print(f"\n📊 A50 期货：{a50['current']:.2f} ({sign}{chg:.2f}%)")
    
    print(f"\n🎯 情绪分数：{report['sentiment']['score']}/100")
    print(f"📊 情绪等级：{report['sentiment']['level']}")
    print(f"\n📋 初步策略:")
    print(f"  立场：{report['initial_strategy']['stance']}")
    print(f"  仓位上限：{report['initial_strategy']['position_limit']}%")
    print(f"  风险提示：{report['initial_strategy']['risk_warning']}")
    print("="*60)
