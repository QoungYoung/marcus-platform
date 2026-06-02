#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双均线交叉策略
经典的趋势跟踪策略
"""

from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager
)


class DualMaStrategy(CtaTemplate):
    """
    双均线交叉策略
    
    策略逻辑：
    - 快线上穿慢线：金叉，开多
    - 快线下穿慢线：死叉，开空/平多
    
    参数：
    - fast_window: 快线周期
    - slow_window: 慢线周期
    - fixed_size: 每次交易数量
    """
    
    author = "VN.PY Skill"
    
    # 策略参数
    fast_window = 10
    slow_window = 30
    fixed_size = 10
    
    # 策略变量
    fast_ma0 = 0
    fast_ma1 = 0
    slow_ma0 = 0
    slow_ma1 = 0
    
    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        """初始化"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # K 线合成器
        self.bg = BarGenerator(self.on_bar)
        
        # 数据容器
        self.am = ArrayManager(size=100)
    
    def on_init(self):
        """策略初始化"""
        self.write_log("策略初始化")
        self.load_bar(10)
    
    def on_start(self):
        """策略启动"""
        self.write_log("策略启动")
        self.put_event()
    
    def on_stop(self):
        """策略停止"""
        self.write_log("策略停止")
        self.put_event()
    
    def on_tick(self, tick: TickData):
        """Tick 数据推送"""
        self.bg.update_tick(tick)
    
    def on_bar(self, bar: BarData):
        """K 线数据推送"""
        # 撤销未成交订单
        self.cancel_all()
        
        # 更新 K 线
        self.am.update_bar(bar)
        
        # 数据不足则返回
        if not self.am.inited:
            return
        
        # 计算均线
        fast_ma = self.am.sma(self.fast_window, array=True)
        slow_ma = self.am.sma(self.slow_window, array=True)
        
        self.fast_ma0 = fast_ma[-1]
        self.fast_ma1 = fast_ma[-2]
        self.slow_ma0 = slow_ma[-1]
        self.slow_ma1 = slow_ma[-2]
        
        # 交易逻辑
        if not self.pos:
            # 无持仓，金叉开多
            if self.fast_ma0 > self.slow_ma0 and self.fast_ma1 <= self.slow_ma1:
                self.buy(bar.close_price, self.fixed_size)
                self.write_log(f"金叉开多：快线{self.fast_ma0:.2f} > 慢线{self.slow_ma0:.2f}")
        
        elif self.pos > 0:
            # 持有多单，死叉平多
            if self.fast_ma0 < self.slow_ma0 and self.fast_ma1 >= self.slow_ma1:
                self.sell(bar.close_price, abs(self.pos))
                self.write_log(f"死叉平多：快线{self.fast_ma0:.2f} < 慢线{self.slow_ma0:.2f}")
        
        elif self.pos < 0:
            # 持有空单，金叉平空
            if self.fast_ma0 > self.slow_ma0 and self.fast_ma1 <= self.slow_ma1:
                self.cover(bar.close_price, abs(self.pos))
                self.write_log(f"金叉平空：快线{self.fast_ma0:.2f} > 慢线{self.slow_ma0:.2f}")
        
        # 更新图形
        self.put_event()
    
    def on_order(self, order: OrderData):
        """订单推送"""
        pass
    
    def on_trade(self, trade: TradeData):
        """成交推送"""
        msg = f"成交：{trade.direction} {trade.volume}手 @ {trade.price:.2f}"
        self.write_log(msg)
        self.put_event()
    
    def on_stop_order(self, stop_order: StopOrder):
        """停止单推送"""
        pass
