# -*- coding: utf-8 -*-
"""东财概念级子主题：把 13 个大主题拆成概念粒度。

数据：stock_pool.db / stock_concept_map。
"""
from __future__ import annotations
import os, sqlite3
from typing import Dict, Set

_HERE = os.path.dirname(os.path.abspath(__file__))
# 本地：<repo>/backend/app/services/wolf_card；生产：/app/app/services/wolf_card
_CANDS = [
    os.path.join(_HERE, "..", "..", "..", "..", ".dsh-tmp", "buyside", "prod_data", "stock_pool.db"),
    os.path.join(_HERE, "..", "..", "..", "..", "data", "stock_pool.db"),
    os.path.join(_HERE, "..", "..", "..", "..", "..", "data", "stock_pool.db"),
    "/app/data/stock_pool.db",
]
POOL = next((os.path.normpath(p) for p in _CANDS if os.path.exists(os.path.normpath(p))), os.path.normpath(_CANDS[0]))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

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
    # 非科技主题也拆概念：风向标按子主题独立刷新
    "创新药": ("医药", ["创新药"]),
    "中药": ("医药", ["中药Ⅱ", "中药Ⅲ", "中药概念"]),
    "医疗器械": ("医药", ["医疗器械", "医疗器械概念", "医疗设备", "医疗耗材"]),
    "CXO": ("医药", ["医疗研发外包"]),
    "火电": ("电力/公用", ["火电"]),
    "水电": ("电力/公用", ["水电"]),
    "核电": ("电力/公用", ["核能核电"]),
    "绿电": ("电力/公用", ["绿色电力"]),
    "电网设备": ("电力/公用", ["电网设备"]),
    "航空装备": ("军工/航天", ["航天航空", "航空装备"]),
    "航天装备": ("军工/航天", ["航天装备Ⅱ", "航天装备Ⅲ"]),
    "卫星互联网": ("军工/航天", ["卫星互联网"]),
    "船舶": ("军工/航天", ["船舶制造"]),
    "军工电子": ("军工/航天", ["军工电子Ⅱ", "军工电子Ⅲ"]),
    "锂电池": ("新能源/电池", ["锂电池", "锂电池概念", "锂"]),
    "光伏": ("新能源/电池", ["光伏概念", "光伏设备", "光伏电池组件", "光伏辅材"]),
    "风电": ("新能源/电池", ["风电设备", "风电整机", "风电零部件"]),
    "储能": ("新能源/电池", ["储能概念", "熔盐储能"]),
    "汽车整车": ("汽车/智驾", ["汽车整车"]),
    "汽车零部件": ("汽车/智驾", ["汽车零部件", "其他汽车零部件", "汽车一体化压铸"]),
    "汽车电子": ("汽车/智驾", ["汽车电子电气系统", "汽车热管理"]),
    "黄金": ("资源/周期", ["黄金", "黄金概念"]),
    "稀土": ("资源/周期", ["稀土", "稀土永磁"]),
    "煤炭": ("资源/周期", ["煤炭", "煤炭开采", "煤炭概念"]),
    "银行": ("金融", ["银行", "银行Ⅱ", "国有大型银行Ⅲ", "股份制银行Ⅲ"]),
    "证券": ("金融", ["证券Ⅱ", "证券Ⅲ"]),
    "保险": ("金融", ["保险", "保险Ⅱ", "保险Ⅲ"]),
    "白酒": ("消费/内需", ["白酒", "白酒Ⅱ", "白酒Ⅲ", "白酒概念"]),
    "食品饮料": ("消费/内需", ["食品饮料", "休闲食品", "食品加工"]),
    "家电": ("消费/内需", ["家电", "白色家电", "黑色家电", "小家电"]),
    "养殖": ("农业", ["养殖业", "生猪养殖", "肉鸡养殖", "水产养殖"]),
    "种植业": ("农业", ["种植业", "农产品"]),
    "基建": ("稳增长/基建", ["基建概念", "基建市政工程", "铁路基建"]),
    "地产": ("稳增长/基建", ["房地产", "房地产开发", "房地产服务"]),
    "游戏": ("传媒/游戏", ["游戏Ⅱ", "游戏Ⅲ", "网络游戏", "短剧互动游戏"]),
    "影视": ("传媒/游戏", ["影视概念", "影视院线", "影视动漫制作"]),
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
