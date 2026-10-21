# -*- coding: utf-8 -*-
"""东财概念级子主题：把 13 个大主题拆成概念粒度。

数据：stock_pool.db / stock_concept_map。
"""
from __future__ import annotations
import os, sqlite3
from typing import Dict, Set

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
POOL = os.path.join(ROOT, ".dsh-tmp", "buyside", "prod_data", "stock_pool.db")

# subtheme -> (parent, 东财概念名精确列表)
SUBTEMES = {
    "AI软": ("AI/算力/科技", [
        "AI应用", "AI智能体", "AIGC概念", "AIGC 概念", "软件开发", "垂直应用软件",
        "多模态AI", "AI语料", "智谱AI概念", "横向通用软件", "国产软件",
        "互联网服务", "云计算", "数据安全", "信创",
    ]),
    "光模块/CPO": ("AI/算力/科技", ["光通信模块", "CPO概念", "CPO 概念", "光纤概念"]),
    "半导体设备": ("半导体/芯片", ["半导体设备", "光刻机", "光刻机(胶)"]),
    "半导体材料": ("半导体/芯片", ["半导体材料", "光刻胶"]),
    "IDC/算力租赁": ("AI/算力/科技", ["数据中心", "算力概念", "算力租赁", "东数西算"]),
    "AI硬件/PCB/液冷": ("AI/算力/科技", ["PCB", "PCB 概念", "液冷概念", "服务器", "铜缆高速连接"]),
    "AI芯片": ("AI/算力/科技", ["AI芯片", "AI 芯片", "国产芯片", "存储芯片"]),
    "芯片设计": ("半导体/芯片", ["数字芯片设计", "模拟芯片设计", "芯片设计"]),
    "存储芯片": ("半导体/芯片", ["存储芯片"]),
    "封测": ("半导体/芯片", ["封测", "先进封装"]),
    "第三代半导体": ("半导体/芯片", ["第三代半导体", "第四代半导体"]),
    "人形机器人": ("机器人/智能制造", ["人形机器人", "机器人执行器", "减速器"]),
    "工业机器人": ("机器人/智能制造", ["机器人概念", "机器人", "机器视觉"]),
}
_C = {}


def _concept_map():
    if "c2s" not in _C:
        c2s = {}
        try:
            con = sqlite3.connect(POOL)
            for code, concept in con.execute("select ts_code,concept_name from stock_concept_map"):
                c2s.setdefault(str(concept), set()).add(str(code))
        except Exception:
            pass
        _C["c2s"] = c2s
    return _C["c2s"]


AI_SOFT_INDUSTRIES = {"互联网", "软件服务", "IT设备", "通信服务", "计算机", "电信运营"}


def _industries():
    if "ind" not in _C:
        out = {}
        try:
            con = sqlite3.connect(POOL)
            for code, ind in con.execute("select ts_code,industry from stock_pool"):
                out[str(code)] = str(ind)
        except Exception:
            pass
        _C["ind"] = out
    return _C["ind"]


def stocks(subtheme: str) -> Set[str]:
    parent_kw = SUBTEMES.get(subtheme)
    if not parent_kw:
        return set()
    c2s = _concept_map()
    out = set()
    for c in parent_kw[1]:
        out |= c2s.get(c, set())
    if subtheme == "AI软":
        ind = _industries()
        out = {c for c in out if ind.get(c) in AI_SOFT_INDUSTRIES}
        # 排除 ST/*ST
        try:
            con = sqlite3.connect(POOL)
            bad = {str(c) for c, name in con.execute("select ts_code,name from stock_pool")
                   if "ST" in str(name).upper()}
            out = out - bad
        except Exception:
            pass
    return out


def parent(subtheme: str):
    x = SUBTEMES.get(subtheme)
    return x[0] if x else None


def list_subthemes(parent_theme: str = None):
    if parent_theme:
        return [k for k, v in SUBTEMES.items() if v[0] == parent_theme]
    return list(SUBTEMES)


if __name__ == "__main__":
    for k in list_subthemes():
        print(k, parent(k), len(stocks(k)))
