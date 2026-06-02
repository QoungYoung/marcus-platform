#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 图形界面启动脚本
需要 X11 环境支持
"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    """启动 VN.PY 图形界面"""
    print("=" * 60)
    print("VN.PY 图形界面启动程序")
    print("=" * 60)
    
    try:
        from vnpy.event import EventEngine
        from vnpy.trader.engine import MainEngine
        from vnpy.trader.ui import create_qapp, MainWindow
        
        # 可选插件
        from vnpy_paperaccount import PaperAccountApp
        from vnpy_ctastrategy import CtaStrategyApp
        from vnpy_backtester import BacktesterApp
        
        print("✓ 所有模块加载成功")
        
    except ImportError as e:
        print(f"✗ 模块导入失败：{e}")
        print("\n请确保已安装必要组件:")
        print("  pip install vnpy vnpy-paperaccount vnpy-ctastrategy vnpy-backtester")
        print("\n如果不需要 GUI，可运行无界面版本:")
        print("  python3 paper_demo.py")
        sys.exit(1)
    
    # 创建 Qt 应用
    print("\n正在启动图形界面...")
    qapp = create_qapp()
    
    # 创建事件引擎
    event_engine = EventEngine()
    print("✓ 事件引擎已初始化")
    
    # 创建主引擎
    main_engine = MainEngine(event_engine)
    print("✓ 主引擎已初始化")
    
    # 添加应用
    main_engine.add_app(PaperAccountApp)
    print("✓ 模拟账户应用已加载")
    
    main_engine.add_app(CtaStrategyApp)
    print("✓ CTA 策略应用已加载")
    
    main_engine.add_app(BacktesterApp)
    print("✓ 回测应用已加载")
    
    # 创建主窗口
    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()
    
    print("\n" + "=" * 60)
    print("VN.PY 图形界面已启动")
    print("=" * 60)
    print("\n使用说明:")
    print("  1. 在左侧选择应用（模拟账户/CTA 策略/回测）")
    print("  2. 配置参数后点击启动")
    print("  3. 查看日志和成交记录")
    print("\n按 Ctrl+C 或关闭窗口退出")
    print("=" * 60)
    
    # 运行应用
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
