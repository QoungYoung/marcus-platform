#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
布林带通道策略
基于布林带上下轨进行突破交易
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


class BollChannelStrategy(CtaTemplate):
    """
    布林带通道策略
    
    策略逻辑：
    - 价格突破上轨：开多
    - 价格跌破下轨：开空
    - 价格回到中轨：平仓
    
    参数：
    - boll_window: 布林带周期
    - boll_dev: 布林带标准差倍数
    - fixed_size: 每次交易数量
    """
    
    author = "VN.PY Skill"
    
    # 策略参数
    boll_window = 20
    boll_dev = 2
    fixed_size = 10
    
    # 策略变量
    boll_up = 0
    boll_down = 0
    boll_mid = 0
    
    parameters = ["boll_window", "boll_dev", "fixed_size"]
    variables = ["boll_up", "boll_down", "boll_mid"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        """初始化"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # K 线合成器（1 分钟合成）
        self.bg = BarGenerator(self.on_bar)
        
        # 数据容器
        self.am = ArrayManager(size=100)
    
    def on_init(self):
        """策略初始化"""
        self.write_log("策略初始化")
        self.load_bar(10)  # 加载 10 天数据
    
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
        
        # 计算布林带
        self.boll_up, self.boll_down, self.boll_mid = self.am.boll(
            self.boll_window, self.boll_dev
        )
        
        # 交易逻辑
        if not self.pos:
            # 无持仓，开仓信号
            if bar.close_price > self.boll_up:
                # 突破上轨，开多
                self.buy(bar.close_price, self.fixed_size)
                self.write_log(f"突破上轨开多：{bar.close_price:.2f} > {self.boll_up:.2f}")
            elif bar.close_price < self.boll_down:
                # 跌破下轨，开空
                self.short(bar.close_price, self.fixed_size)
                self.write_log(f"跌破下轨开空：{bar.close_price:.2f} < {self.boll_down:.2f}")
        
        elif self.pos > 0:
            # 持有多单，平仓信号
            if bar.close_price < self.boll_mid:
                # 回到中轨，平多
                self.sell(bar.close_price, abs(self.pos))
                self.write_log(f"回到中轨平多：{bar.close_price:.2f} < {self.boll_mid:.2f}")
        
        elif self.pos < 0:
            # 持有空单，平仓信号
            if bar.close_price > self.boll_mid:
                # 回到中轨，平空
                self.cover(bar.close_price, abs(self.pos))
                self.write_log(f"回到中轨平空：{bar.close_price:.2f} > {self.boll_mid:.2f}")
        
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
