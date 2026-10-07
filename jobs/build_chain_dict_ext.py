# -*- coding: utf-8 -*-
"""build_chain_dict_ext.py — 补齐产业链字典的覆盖盲区（可复现的规则式构建器）

背景（2026-09-21 用户："PCB补上吧，还有不在产业链的吗"）：
  `data/chain_dict_final_<theme>.json` 是此前 LLM 迭代产出的 10 条链，segment.concepts 只覆盖
  DB 里 **70 / 1068** 个概念 ⇒ 回测窗口宇宙 256 只里 **114 只(45%)** 命中 0，"产业链中枢"一项对它们只能 fail-open。
  盲区有两类：
    (a) **整类缺失**：算力硬件(PCB/光模块/CPO/液冷/数据中心)、计算机软件(信创/AI软)、消费电子/AI端侧、传媒游戏、化工材料
    (b) **已有链概念太窄**：医药/军工/电力/新能源/半导体 的 segment.concepts 漏了该行业的常用环节概念

本脚本做两件事（都只写数据文件，不碰任何策略代码）：
  1. `NEW`   —— 新建缺失主题的链（同一 schema：theme / final_dict.segments / targets / final_metrics / blockers）
  2. `PATCH` —— 给已有链补**环节级**概念（可新增 segment；原文件先备份到 data/_bt_fund/chain_backup/）

纪律：
  * 概念名必须是 **DB `stock_concept_map` 里真实存在**的（不存在的直接丢弃并告警 —— 编一个不存在的名字等于没补）。
  * 只收**环节级**概念；"人工智能/国产芯片/电子/半导体概念"这类大类伞形概念不收（它们不是产业链环节，
    收进来会让所有大票都变成"中枢"）。宽概念（成分股 > 300）默认拒绝，除非显式 allow_broad。
  * 同一概念在同一主题内**只能挂一个环节**（重复挂会把中枢度算重）。
  * 明确标注数据源限制：如"覆铜板/铜箔"在 DB 里没有对应概念 ⇒ 挂进 blockers，不假装覆盖。
  * 幂等：重复运行结果一致；已 patch 过的不会重复追加。

用法：
  python -u jobs/build_chain_dict_ext.py --dry-run     # 只校验概念名与覆盖，不写文件
  python -u jobs/build_chain_dict_ext.py --report      # 统计盲区收敛情况
  python -u jobs/build_chain_dict_ext.py               # 落盘
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import shutil
import sys

DATA = os.environ.get("DATA_DIR", "data")
BACKUP = os.path.join(DATA, "_bt_fund", "chain_backup")
BROAD_N = 300          # 成分股 > 300 视作大类伞形概念
# 例外：成分股虽多但**确实是一个产业链环节**的概念（按语义放行，逐条给出理由）
ALLOW_BROAD = {
    "数据中心": "算力链的 IDC/算力运营环节本身（非伞形，只是热门）",
    "医疗器械概念": "医药链的器械环节本身（DB 另有 医疗器械150 但覆盖更窄）",
    "商业航天": "军工链的宇航/发射总装环节本身",
    "基础化工": "化工链的中游本身",
}

# ─────────────────────────── 规格（概念名必须与 DB 完全一致）───────────────────────────
NEW = {
    "AI算力_算力硬件": {
        "note": "语料 2026-01-05「CPO光模块和PCB板幅度不会像25年那么大，反倒是其他液冷 电源 电网 PCB扩产各种细分」"
                "／2026-08-12「AIDC算力…下半年的主线」⇒ 他把 光模块·CPO·PCB·液冷·电源 当同一条海外链看。",
        "segments": [
            {"role": "upstream", "label": "上游·PCB/覆铜板与高速互连",
             "concepts": ["PCB", "PCB 概念", "铜缆高速连接"],
             "kw": ["PCB", "覆铜板", "铜箔", "铜缆", "高速连接", "线路板", "载板"]},
            {"role": "mid", "label": "中游·光互联(光模块/CPO)",
             "concepts": ["光通信模块", "CPO概念", "CPO 概念"],
             "kw": ["光模块", "CPO", "光器件", "硅光", "800G", "1.6T", "光芯片"]},
            {"role": "mid2", "label": "中游·服务器/AI算力设备",
             "concepts": ["算力概念", "英伟达概念"],
             "kw": ["服务器", "算力", "GPU", "整机", "AI服务器", "英伟达"]},
            {"role": "downstream", "label": "下游·液冷散热/数据中心/算力运营",
             "concepts": ["液冷服务器", "液冷概念", "数据中心", "算力租赁"],
             "kw": ["液冷", "散热", "温控", "数据中心", "IDC", "机柜", "算力租赁", "AIDC"]},
        ],
        "blockers": ["DB 无「覆铜板」「铜箔」「服务器」独立概念 ⇒ 这三层只能用 PCB/算力概念代理，覆盖不完整"],
    },
    "计算机_软件信创": {
        "note": "回测窗口里 64 只盲票带计算机/AI软概念（数据要素 51、信创 49、DeepSeek 46、AIGC 36…），10 条链完全没有这一类。",
        "segments": [
            {"role": "upstream", "label": "上游·基础软件与国产化底座",
             "concepts": ["国产软件", "信创"],
             "kw": ["操作系统", "数据库", "中间件", "信创", "国产化"]},
            {"role": "mid", "label": "中游·云计算/大数据与数据要素",
             "concepts": ["云计算", "数据要素", "大数据"],
             "kw": ["云", "IDC", "数据要素", "大数据", "算力调度"]},
            {"role": "mid2", "label": "中游·AI应用/大模型与智能体",
             "concepts": ["AIGC概念", "AIGC 概念", "AI智能体", "DeepSeek概念", "ChatGPT概念", "元宇宙概念"],
             "kw": ["大模型", "AIGC", "智能体", "AI应用", "多模态", "Agent"]},
            {"role": "downstream", "label": "下游·网络安全/工业互联网与数字经济",
             "concepts": ["网络安全", "工业互联网", "数字经济", "区块链"],
             "kw": ["安全", "工业互联网", "数字经济", "区块链", "数字货币"]},
        ],
        "blockers": ["「操作系统」「数据库」在 DB 无独立概念 ⇒ 上游只能用 国产软件/信创 代理"],
    },
    "消费电子_AI端侧": {
        "note": "语料 2026-01-05「二季度 液冷 电源 pcb细分 下半年 ai端侧」⇒ AI端侧是他列出的独立方向；回测窗口 35 只盲票属此类。",
        "segments": [
            {"role": "upstream", "label": "上游·面板/光学与零部件",
             "concepts": ["光学光电子", "光学元件", "面板", "消费电子零部件及组装", "OLED"],
             "kw": ["面板", "OLED", "光学", "镜头", "结构件", "零部件"]},
            {"role": "mid", "label": "中游·模组与终端部件(折叠屏/耳机/可穿戴)",
             "concepts": ["柔性屏(折叠屏)", "无线耳机", "智能穿戴", "MiniLED", "MicroLED", "混合现实"],
             "kw": ["折叠屏", "TWS", "可穿戴", "MiniLED", "MicroLED", "MR", "模组"]},
            {"role": "downstream", "label": "下游·品牌整机与AI端侧(果链/VR/智能家居)",
             "concepts": ["消费电子", "消费电子概念", "品牌消费电子", "苹果概念", "虚拟现实", "智能家居"],
             "kw": ["苹果", "果链", "VR", "AR", "AI眼镜", "智能家居", "整机"]},
        ],
        "blockers": [],
    },
    "传媒_游戏影视": {
        "note": "回测窗口 29 只盲票属传媒/游戏（10 条链完全没有传媒）。",
        "segments": [
            {"role": "upstream", "label": "上游·内容与IP(出版/动漫)",
             "concepts": ["出版", "大众出版", "教育出版", "影视动漫制作"],
             "kw": ["出版", "IP", "动漫", "内容", "版权"]},
            {"role": "mid", "label": "中游·游戏研发与发行",
             "concepts": ["游戏Ⅲ", "游戏Ⅱ"],
             "kw": ["游戏", "手游", "端游", "发行", "版号"]},
            {"role": "downstream", "label": "下游·院线/广告/文化消费",
             "concepts": ["影视院线", "院线", "广告媒体", "文化用品"],
             "kw": ["院线", "影视", "广告", "短剧", "文化消费"]},
        ],
        "blockers": [],
    },
    "农业_养殖种植": {
        "note": "回测窗口剩 4 只盲票属农业/养殖（立华股份/晓鸣股份/巨星农牧等）。环节划分直接沿用仓库内"
                " `apps/main_line/chain_dict_refine.py` 里既有的农业 v0.6 参考词典（上游 种子/转基因/粮食种植，"
                "中游 养殖/饲料/乳业，下游 农药兽药/生态农业）—— **不是我新编的**，只是把人工审过的口径产品化。",
        "segments": [
            {"role": "upstream", "label": "上游·种子/粮食种植",
             "concepts": ["种子", "转基因", "粮食种植", "粮食概念", "农业种植"],
             "kw": ["种子", "转基因", "粮食", "种植", "土地"]},
            {"role": "mid", "label": "中游·养殖/饲料/乳业",
             "concepts": ["生猪养殖", "肉鸡养殖", "水产养殖", "渔业", "饲料", "乳业",
                          "猪肉概念", "鸡肉概念"],
             "kw": ["养殖", "猪", "鸡", "水产", "饲料", "乳"]},
            {"role": "downstream", "label": "下游·农资/农化",
             "concepts": ["农药兽药", "生态农业"],
             "kw": ["农药", "兽药", "化肥", "农化"]},
        ],
        "blockers": ["「农业」「农林牧渔」成分股 75/114 只属大类，故意不收；乡镇振兴(165)同理。"],
    },
    "化工_材料": {
        "note": "回测窗口 10 只盲票属化工材料（10 条链没有化工）。",
        "segments": [
            {"role": "upstream", "label": "上游·石化/煤化基础原料",
             "concepts": ["化工原料", "化学原料", "煤化工概念"],
             "kw": ["石化", "煤化工", "基础原料", "乙烯", "纯碱"]},
            {"role": "mid", "label": "中游·基础化工与化学制品",
             "concepts": ["基础化工", "化学制品", "其他化学制品"],
             "kw": ["基础化工", "化学制品", "化肥", "农药"]},
            {"role": "downstream", "label": "下游·精细化工与新材料",
             "concepts": ["氟化工概念", "氟化工", "磷化工", "塑料", "改性塑料", "降解塑料",
                          "合成树脂", "电子化学品Ⅲ", "电子化学品Ⅱ", "涂料"],
             "kw": ["氟化工", "磷化工", "塑料", "树脂", "电子化学品", "涂料", "新材料"]},
        ],
        "blockers": ["「基础化工」成分股 491 只属宽口径（环节内部不再分层）"],
    },
}

PATCH = {
    "半导体_芯片": {
        "add_concepts": {
            "上游·半导体材料/设备(含光刻)": ["光刻机(胶)"],
            "中游·芯片设计": ["存储芯片", "第三代半导体", "第四代半导体", "汽车芯片", "IGBT概念"],
            "下游·封装测试": ["先进封装"],
        },
        "why": "原 concepts 只有 半导体材料/设备/光刻胶/光刻机/数字芯片设计/模拟芯片设计/集成电路制造/集成电路封测 8 个；"
               "DB 里另有 存储芯片147只（兆易的命中概念）/先进封装83/第三代半导体121/汽车芯片74/IGBT43/光刻机(胶)99 未收录。",
    },
    "医药": {
        "add_concepts": {
            # 「AI制药（医疗）」78 只 / 「精准医疗」44 只是 AI+医疗交叉概念, DB 里混入大量 IT 票
            # （实测 紫光股份/科大讯飞 都挂着）⇒ **故意不收**：收进来会让 IT 票变成医药链"中枢"。
            "中游·创新药研发制造": ["生物疫苗", "减肥药", "合成生物"],
            "下游·医疗器械(设备/耗材/IVD)": ["医疗器械概念", "体外诊断概念", "精准诊断"],
        },
        "add_segments": [
            {"role": "downstream", "label": "下游·医疗服务与互联网医疗",
             "concepts": ["互联网医疗", "互联医疗", "创新医疗服务", "医美概念"],
             "kw": ["互联网医疗", "医疗服务", "医美", "连锁"]},
        ],
        "why": "22 只盲票带医药概念（医疗器械概念345/互联网医疗100）而原链只收了 CRO/中药Ⅱ/体外诊断/创新药/化学制剂/医疗研发外包；"
               "注：交叉性噪音概念（AI制药（医疗）/精准医疗）故意不收。",
    },
    "军工_航天": {
        "add_concepts": {
            "中游·分系统/军工电子/元器件(含航天电源)": ["军工电子Ⅲ", "卫星导航", "卫星互联网"],
            "下游·整机总装/宇航产品": ["无人机", "商业航天", "航空装备Ⅲ", "航天装备Ⅲ",
                                          "船舶制造", "地面兵装Ⅱ", "通用航空"],
        },
        "why": "13 只盲票带军工概念（无人机230/商业航天324/卫星导航77）而原链只收了 军工电子Ⅱ/航天装备Ⅱ 等细分。",
    },
    "电力_公用": {
        "add_concepts": {
            "发电设备(综合电力设备商)": ["光伏设备", "风电设备", "光伏加工设备"],
        },
        "add_segments": [
            {"role": "mid", "label": "电网/储能配套",
             "concepts": ["智能电网", "电网概念", "电网设备", "充电桩"],
             "kw": ["电网", "特高压", "充电桩", "配网", "储能配套"]},
        ],
        "why": "18 只盲票带电力设备概念（智能电网222/充电桩264）而原链只有发电+运营两类。",
    },
    "资源_周期": {
        "add_concepts": {
            "中游·稀土锂资源冶炼": ["稀土永磁", "小金属概念"],
        },
        "why": "补 稀土永磁86/小金属概念127（原链只收 稀土/锂 两个小概念）。",
    },
    "新能源_电池": {
        "add_concepts": {
            "中游·电芯/电池系统/精密结构件": ["钠离子电池"],
        },
        "why": "补 钠离子电池76。",
    },
}


# ────────────────────────────────── DB / 工具 ──────────────────────────────────
def _pg():
    import psycopg2
    return psycopg2.connect(os.getenv("DATABASE_URL",
                                      "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))


def load_concepts(cur):
    """概念 -> 成分股数"""
    cur.execute("select concept_name, count(*) from stock_concept_map group by 1")
    return {str(a): int(b) for a, b in cur.fetchall()}


def members_of(cur, concepts):
    cur.execute("select distinct ts_code from stock_concept_map where concept_name = any(%s)",
                (list(concepts),))
    return [str(r[0]) for r in cur.fetchall()]


def load_universe():
    p = os.path.join(DATA, "_bt_fund", "t2", "universe.txt")
    if not os.path.exists(p):
        return []
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import bt_fund_asof as F
    return sorted({F._norm(x.strip()) for x in open(p) if x.strip()})


def top_mv_names(cur, codes, n=15):
    """按缓存里最新一份 daily_basic 的 total_mv 取前 n 的股票名（拿不到就按代码序兜底）。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    try:
        import bt_fund_asof as F
        d = os.path.join(DATA, "_bt_fund", "t2", "daily_basic")
        key = max(x[:-5] for x in os.listdir(d) if x.endswith(".json"))
        obj = json.load(open(os.path.join(d, key + ".json"), encoding="utf-8"))
    except Exception:
        obj = {}
    mvs = [(c, (obj.get(c) or {}).get("total_mv") or 0) for c in codes]
    mvs.sort(key=lambda x: -x[1])
    picked = [c for c, _v in mvs[:n]]
    cur.execute("select ts_code, name from stock_pool where ts_code = any(%s)", (picked or ["__none__"],))
    nm = {str(a): str(b) for a, b in cur.fetchall()}
    return [nm.get(c, c) for c in picked]


def validate(spec_segments, cnt, where, allow_broad=tuple(ALLOW_BROAD)):
    """校验概念名存在性/宽窄/重复。返回 (cleaned_segments, problems, notes)"""
    problems, notes = [], []
    seen = {}
    out = []
    for s in spec_segments:
        keep = []
        for c in s.get("concepts") or []:
            if c not in cnt:
                problems.append("%s 概念「%s」在 DB 不存在 ⇒ 丢弃" % (where, c))
                continue
            if cnt[c] > BROAD_N and c not in allow_broad:
                problems.append("%s 概念「%s」成分股 %d > %d（大类伞形）⇒ 丢弃" % (where, c, cnt[c], BROAD_N))
                continue
            if c in seen:
                problems.append("%s 概念「%s」已挂在环节「%s」⇒ 本处丢弃（防中枢度重复计）"
                                % (where, c, seen[c]))
                continue
            seen[c] = s.get("label")
            keep.append(c)
        if keep:
            d = dict(s)
            d["concepts"] = keep
            out.append(d)
        elif s.get("concepts"):
            notes.append("%s 环节「%s」的全部概念被丢弃 ⇒ 该环节不落盘" % (where, s.get("label")))
    return out, problems, notes


def industry_scan(cur, codes):
    """客观杂质体检：链内成员的板块(industry)分布 + 与链主题明显无关的成员。
    与 chain_dict_worker 的 impurity 口径同义（给人工目检），但不据此自动删票。"""
    if not codes:
        return {}, []
    cur.execute("select ts_code, name, industry from stock_pool where ts_code = any(%s)", (list(codes),))
    rows = cur.fetchall()
    dist = collections.Counter((r[2] or "?") for r in rows)
    return dict(dist.most_common()), [(str(r[0]), str(r[1]), str(r[2] or "?")) for r in rows]


def universe_hits(cur, concepts, uni):
    if not concepts or not uni:
        return 0
    cur.execute("select distinct ts_code from stock_concept_map where concept_name = any(%s) and ts_code = any(%s)",
                (list(concepts), list(uni)))
    return len(cur.fetchall())


# ────────────────────────────────── 构建 ──────────────────────────────────
def build_new(theme, spec, cur, cnt, uni, dry):
    segs, problems, notes = validate(spec["segments"], cnt, "new:%s" % theme,
                                     allow_broad=tuple(ALLOW_BROAD))
    if not segs:
        print("  !! %s 无有效环节，跳过" % theme)
        return None
    allc = [c for s in segs for c in s["concepts"]]
    mem = members_of(cur, allc)
    fdict = {"theme": theme, "segments": segs}
    doc = {
        "theme": theme,
        "final_dict": fdict,
        "targets": top_mv_names(cur, mem),
        "impurity": [],
        "rounds": [{"n": 1, "dict": fdict, "note": "规则式构建（jobs/build_chain_dict_ext.py），非 LLM 迭代"}],
        "final_metrics": {
            "target_hit": universe_hits(cur, allc, uni), "target_total": len(uni),
            "n_concepts": len(allc), "n_members": len(mem),
            "segments": [{"label": s["label"], "leading": [], "verified_all": [],
                          "n_concepts": len(s["concepts"]),
                          "n_members": len(members_of(cur, s["concepts"]))} for s in segs],
            "builder": "rule-based/2026-09-21",
        },
        "blockers": "；".join((spec.get("blockers") or []) + notes),
        "note": spec.get("note", ""),
    }
    dist, rows = industry_scan(cur, mem)
    doc["final_metrics"]["industry_dist"] = dist
    doc["impurity"] = [{"ts_code": c, "name": n, "industry": i} for c, n, i in rows]
    print("  + %-18s 环节 %d 概念 %2d 成分股 %4d | 回测宇宙命中 %d/%d"
          % (theme, len(segs), len(allc), len(mem), doc["final_metrics"]["target_hit"], len(uni)))
    print("      行业分布: %s" % dict(list(dist.items())[:6]))
    for p in problems:
        print("      ⚠ " + p)
    if not dry:
        p = os.path.join(DATA, "chain_dict_final_%s.json" % theme)
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("      → 写出 %s" % p)
    return doc


def build_patch(theme, spec, cur, cnt, uni, dry):
    p = os.path.join(DATA, "chain_dict_final_%s.json" % theme)
    if not os.path.exists(p):
        print("  !! 找不到 %s，跳过" % p)
        return None
    doc = json.load(open(p, encoding="utf-8"))
    segs = ((doc.get("final_dict") or {}).get("segments")) or []
    by_label = {s.get("label"): s for s in segs}
    added = 0
    problems, notes = [], []
    for label, cs in (spec.get("add_concepts") or {}).items():
        if label not in by_label:
            problems.append("patch:%s 环节「%s」不存在于原链 ⇒ 丢弃" % (theme, label))
            continue
        tgt = by_label[label]
        for c in cs:
            if c not in cnt:
                problems.append("patch:%s 概念「%s」在 DB 不存在 ⇒ 丢弃" % (theme, c))
                continue
            if cnt[c] > BROAD_N and c not in ALLOW_BROAD:
                problems.append("patch:%s 概念「%s」成分股 %d > %d（伞形）⇒ 丢弃" % (theme, c, cnt[c], BROAD_N))
                continue
            if c in (tgt.get("concepts") or []):
                continue
            tgt.setdefault("concepts", []).append(c)
            added += 1
    for s in (spec.get("add_segments") or []):
        if s.get("label") in by_label:
            continue
        keep, pr, no = validate([s], cnt, "patch:%s" % theme)
        problems += pr
        notes += no
        if keep:
            segs.append(keep[0])
            added += 1
    if added:
        if not dry:
            os.makedirs(BACKUP, exist_ok=True)
            shutil.copy2(p, os.path.join(BACKUP, os.path.basename(p)))
        doc.setdefault("patch_history", []).append(
            {"at": "2026-09-21", "by": "build_chain_dict_ext.py", "why": spec.get("why", ""),
             "added": added})
        doc["blockers"] = ((doc.get("blockers") or "") + "；" if doc.get("blockers") else "") + \
                          "已按 2026-09-21 补齐：" + spec.get("why", "")
        allc = [c for s in segs for c in (s.get("concepts") or [])]
        doc["final_metrics"] = dict(doc.get("final_metrics") or {})
        doc["final_metrics"].update({"n_concepts": len(allc),
                                     "target_hit": universe_hits(cur, allc, uni),
                                     "target_total": len(uni)})
        if not dry:
            json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  ~ %-18s 新增概念/环节 %d | 现共 %d 概念 | 回测宇宙命中 %s"
          % (theme, added, len([c for s in segs for c in (s.get('concepts') or [])]),
             doc.get("final_metrics", {}).get("target_hit")))
    for x in problems:
        print("      ⚠ " + x)
    return doc


def report(cur, cnt, uni):
    """盲区收敛统计：链字典覆盖的概念数 / 回测宇宙里仍命中的票数。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import bt_fund_asof as F
    F._CHAIN_CACHE.clear()
    seg = F.concept_segments()
    cov = set(seg)
    print("\n=== 报告 ===")
    print("链字典概念 %d / DB %d（%.0f%%）| 链数 %d"
          % (len(cov), len(cnt), 100.0 * len(cov) / max(len(cnt), 1),
             len(glob.glob(os.path.join(DATA, "chain_dict_final_*.json")))))
    if not uni:
        return
    cur.execute("select ts_code, concept_name from stock_concept_map where ts_code = any(%s)", (uni,))
    m = collections.defaultdict(set)
    for c, n in cur.fetchall():
        m[str(c)].add(str(n))
    blind = [c for c in uni if not (m.get(c, set()) & cov)]
    print("回测窗口宇宙 %d 只：链字典命中 %d，盲 %d（%.0f%%）"
          % (len(uni), len(uni) - len(blind), len(blind), 100.0 * len(blind) / max(len(uni), 1)))
    if blind:
        print("仍盲（前 20）：%s" % blind[:20])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    conn = _pg()
    cur = conn.cursor()
    cnt = load_concepts(cur)
    uni = load_universe()
    print("DB 概念 %d | 回测宇宙 %d 只 | 模式=%s"
          % (len(cnt), len(uni), "dry-run" if a.dry_run else "write"))
    print("\n== 新建链 ==")
    for th, spec in NEW.items():
        if a.only and a.only not in th:
            continue
        build_new(th, spec, cur, cnt, uni, a.dry_run)
    print("\n== 补已有链 ==")
    for th, spec in PATCH.items():
        if a.only and a.only not in th:
            continue
        build_patch(th, spec, cur, cnt, uni, a.dry_run)
    if a.report or a.dry_run:
        report(cur, cnt, uni)
    conn.close()


if __name__ == "__main__":
    main()
