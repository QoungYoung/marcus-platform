#!/usr/bin/env python3
"""
模拟今日自动加仓：对比新旧两种仓位计算方式。

新方式：calculate_position_quantity()（多层约束）
旧方式：(target_pct - current_pct) × total_asset（简单减法）
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from app.api.indicator import calculate_position_quantity
from app.api.portfolio import calculate_positions_from_db
from workspace_detector import DATA_DIR
from app.config import get_settings


def get_current_prices(symbols: list) -> dict:
    """获取实时价格"""
    prices = {}
    try:
        settings = get_settings()
        xueqiu_dir = settings.workspace_path / "core"
        xueqiu_config = xueqiu_dir / "config.json"
        sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(xueqiu_config))
        for sym in symbols:
            try:
                quote = engine.get_stock_quote(sym)
                if quote:
                    prices[sym] = {
                        'price': float(quote.get('current', 0)),
                        'name': quote.get('name', ''),
                        'change_pct': float(quote.get('percent', 0)),
                    }
            except Exception:
                pass
    except Exception as e:
        print(f"获取行情失败: {e}")
    return prices


def tier_evaluation(symbol: str, float_pnl_pct: float, current_tier: str) -> dict:
    """模拟层级评估"""
    if current_tier in ('probe', 'unknown', '') and float_pnl_pct >= 3.0:
        return {'action': 'UPGRADE_TO_SPRINT', 'target_tier': 'sprint', 'target_pct': 0.25}
    if current_tier in ('probe', 'unknown', '') and float_pnl_pct >= 1.0:
        return {'action': 'UPGRADE_TO_CONFIRM', 'target_tier': 'confirm', 'target_pct': 0.18}
    if current_tier == 'confirm' and float_pnl_pct >= 3.0:
        return {'action': 'UPGRADE_TO_SPRINT', 'target_tier': 'sprint', 'target_pct': 0.25}
    return None


def main():
    settings = get_settings()

    # 读持仓
    positions, account = calculate_positions_from_db()
    if not positions:
        print("无持仓")
        return

    total_asset = account.get('initial_capital', 1000000)
    available_cash = account.get('available_cash', 0)

    # 获取价格
    xq_symbols = []
    for p in positions:
        sym = p['symbol']
        # 转换为雪球格式
        if sym.startswith('SH') or sym.startswith('SZ') or sym.startswith('BJ'):
            xq_sym = sym
        elif sym.startswith('6'):
            xq_sym = f'SH{sym}'
        else:
            xq_sym = f'SZ{sym}'
        xq_symbols.append(xq_sym)

    prices = get_current_prices(xq_symbols)
    if not prices:
        print("无法获取实时价格")
        return

    # 计算总持仓市值
    total_position_mv = 0.0
    pos_data = []
    for p in positions:
        sym = p['symbol']
        xq_sym = _normalize_symbol(sym, xq_symbols)
        price_info = prices.get(xq_sym, {})
        current_price = price_info.get('price', p['avg_price'])
        name = price_info.get('name', p.get('name', sym))
        mv = current_price * p['volume']
        total_position_mv += mv
        float_pnl_pct = round((current_price - p['avg_price']) / p['avg_price'] * 100, 2) if p['avg_price'] > 0 else 0
        pos_data.append({
            'symbol': sym, 'xq_symbol': xq_sym, 'name': name,
            'volume': p['volume'], 'avg_price': round(p['avg_price'], 2),
            'current_price': current_price, 'mv': mv,
            'float_pnl_pct': float_pnl_pct,
        })

    # 修正 total_asset：用实际持仓市值 + 现金
    if total_position_mv > 0:
        adjusted_asset = available_cash + total_position_mv
        if adjusted_asset > total_asset * 0.5:
            total_asset = adjusted_asset

    # 获取 Pi stance（模拟，默认 yellow）
    pi_stance = 'yellow'
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        pi_conf = chain.get_pi_confirmation()
        if pi_conf:
            pi_stance = pi_conf.get('stance', 'yellow')
    except Exception:
        pass

    total_cap = {'green': 60.0, 'yellow': 50.0, 'red': 20.0}.get(pi_stance, 50.0)

    print("=" * 100)
    print(f"  今日加仓模拟 | 总资产: {total_asset:,.0f} | 可用资金: {available_cash:,.0f} | Pi立场: {pi_stance} | 总仓上限: {total_cap}%")
    print("=" * 100)

    # 模拟层级状态（从持久化文件读取）
    tier_states = {}
    try:
        import json
        tier_file = settings.data_dir.parent / "data" / "position_tiers.json"
        if tier_file.exists():
            with open(tier_file) as f:
                tier_states = json.load(f)
    except Exception:
        pass

    for pd in pos_data:
        sym = pd['symbol']
        current_tier = tier_states.get(sym, {}).get('tier', 'probe')
        eval_result = tier_evaluation(sym, pd['float_pnl_pct'], current_tier)

        if eval_result is None:
            print(f"\n  {pd['name']}({sym}) | 浮盈 {pd['float_pnl_pct']:+.2f}% | 层级 {current_tier} | 不触发升级")
            continue

        target_tier = eval_result['target_tier']
        target_pct = eval_result['target_pct']
        single_cap = {'probe': 10.0, 'confirm': 18.0, 'sprint': 25.0}[target_tier]

        # ── 新方式 ──
        result = calculate_position_quantity(
            total_asset=total_asset,
            available_cash=available_cash,
            position_value=total_position_mv,
            current_price=pd['current_price'],
            single_stock_cap_pct=single_cap,
            total_cap_pct=total_cap,
        )
        new_max_amount = result['amount']
        new_add_amount = max(0, new_max_amount - pd['mv'])
        new_add_shares = int(new_add_amount / pd['current_price'] / 100) * 100
        new_add_pct = round(new_add_amount / total_asset * 100, 2) if total_asset > 0 else 0

        # ── 旧方式 ──
        old_add_pct = target_pct - pd['mv'] / total_asset
        old_add_amount = total_asset * old_add_pct
        old_add_shares = int(old_add_amount / pd['current_price'] / 100) * 100

        print(f"\n  【{pd['name']}({sym})】 浮盈 {pd['float_pnl_pct']:+.2f}% | {current_tier} → {target_tier}")
        print(f"    当前: {pd['volume']}股 @ {pd['current_price']:.2f} | 市值 {pd['mv']:,.0f} ({pd['mv']/total_asset*100:.1f}%)")
        print(f"    约束: 单票上限 {single_cap}% | 最大总仓 {result['pct']}% ({new_max_amount:,.0f})")
        if result['warnings']:
            for w in result['warnings']:
                print(f"      ⚠️  {w}")
        print(f"    ┌─ 新方式: +{new_add_shares}股 ({new_add_pct}%) = {new_add_amount:,.0f}")
        if new_add_shares < 100:
            print(f"    │  → 不足100股，不会执行")
        else:
            new_total_pct = round((pd['mv'] + new_add_amount) / total_asset * 100, 2)
            print(f"    │  → 加仓后仓位: {new_total_pct}%")
        print(f"    └─ 旧方式: +{old_add_shares}股 ({old_add_pct*100:.1f}%) = {old_add_amount:,.0f}")
        if new_add_shares != old_add_shares:
            diff = new_add_shares - old_add_shares
            print(f"    📊 差异: {diff:+d}股 ({new_add_amount - old_add_amount:+,.0f})")


def _normalize_symbol(sym, xq_list):
    """匹配持仓符号到雪球符号"""
    if sym in xq_list:
        return sym
    for xq in xq_list:
        if sym in xq or xq in sym:
            return xq
    return sym


if __name__ == '__main__':
    main()
