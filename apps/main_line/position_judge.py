# -*- coding: utf-8 -*-
"""position_judge.py — 每周调度: position_class(高低位) → low_logic_agent(低位看逻辑) → stock_confirm_judge(个股确认比例, 三层联动之三)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class
import low_logic_agent
import stock_confirm_judge
if __name__=="__main__":
    print("=== position_class ===")
    position_class.main()
    print("=== low_logic_agent ===")
    low_logic_agent.main()
    print("=== stock_confirm_judge ===")
    stock_confirm_judge.main()
