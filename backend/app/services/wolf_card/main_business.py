# -*- coding: utf-8 -*-
"""主营纯度校验：用 Tushare fina_mainbz 的主营构成，剔除“概念沾边但主营无关”的票。

本地 dsh 主营 agent（apps/main_line/theme_member_batch.py）不可用时，用本模块兜底；
二者口径一致：只看主营/产业链归属，概念沾边不算。
"""
from __future__ import annotations
import json, os, sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data", "main_business")
sys.path.insert(0, os.path.join(ROOT, "core"))

KEYWORDS = {
    "AI软": ["软件", "云", "数据", "互联网", "信息", "数字", "智能", "平台", "系统", "网络", "通信", "科技", "saas", "it", "wps", "办公", "订阅", "技术", "服务", "解决方案"],
    "光模块/CPO": ["光", "通信", "模块", "器件", "光纤", "网络"],
    "半导体设备": ["半导体", "芯片", "设备", "材料", "光刻", "电子", "集成电路"],
    "半导体材料": ["半导体", "材料", "光刻", "电子", "化学"],
    "IDC/算力租赁": ["数据", "算力", "云", "互联网", "信息", "网络", "服务器"],
    "AI硬件/PCB/液冷": ["电子", "电路", "pcb", "服务器", "散热", "液冷", "通信"],
    "AI芯片": ["芯片", "半导体", "集成电路", "电子"],
    "人形机器人": ["机器人", "自动化", "伺服", "减速器", "智能", "机械"],
    "工业机器人": ["机器人", "自动化", "智能", "机械"],
    "医药": ["药", "医", "生物", "医疗"],
    "电力/公用": ["电", "能源", "电力", "燃气", "水"],
    "军工/航天": ["军", "航空", "航天", "卫星", "船", "兵器"],
    "资源/周期": ["矿", "金属", "材料", "化工", "黄金", "稀土", "铜", "铝"],
    "金融": ["银行", "证券", "保险", "金融"],
    "消费/内需": ["食品", "白酒", "饮料", "家电", "零售", "旅游", "消费"],
}


def _path(code: str) -> str:
    return os.path.join(DATA, code.replace(".", "_") + ".json")


def _fetch(code: str) -> Dict[str, Any]:
    try:
        from _api_config import get_tushare_pro
        pro = get_tushare_pro()
        df = pro.query("fina_mainbz", ts_code=code, type="P")
        if df is None or len(df) == 0:
            return {"code": code, "items": [], "error": "empty"}
        last = str(df.end_date.max())
        df = df[df.end_date.astype(str) == last]
        items = []
        SUMMARY = {"产品", "行业", "地区", "合计特别调整", "其他业务"}
        for _, r in df.iterrows():
            try:
                sales = float(r.bz_sales or 0)
                if sales != sales:  # NaN
                    sales = 0.0
            except Exception:
                sales = 0.0
            item = str(r.bz_item or "")
            if sales <= 0 or not item or item in SUMMARY:
                continue
            items.append({"item": item, "sales": sales})
        return {"code": code, "end_date": last, "items": items}
    except Exception as e:
        return {"code": code, "items": [], "error": "%s: %s" % (type(e).__name__, str(e)[:100])}


def load(code: str) -> Dict[str, Any]:
    p = _path(code)
    if os.path.exists(p):
        try: return json.load(open(p, encoding="utf-8"))
        except Exception: pass
    d = _fetch(code)
    os.makedirs(DATA, exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return d


def assess(code: str, theme: str) -> Dict[str, Any]:
    """返回 keep/reason/ratio/matched；ratio = 主营相关收入占比。"""
    d = load(code)
    items = d.get("items") or []
    if not items:
        return {"code": code, "keep": None, "reason": "main_business_unknown", "ratio": None, "matched": []}
    kws = KEYWORDS.get(theme) or []
    total = sum(x["sales"] for x in items)
    if total <= 0:
        return {"code": code, "keep": False, "reason": "main_business_zero", "ratio": 0.0, "matched": []}
    matched = []
    hit_sales = 0.0
    for x in items:
        name = x["item"].lower()
        if any(k.lower() in name for k in kws):
            matched.append(x["item"]); hit_sales += x["sales"]
    ratio = hit_sales / total if total else 0.0
    keep = ratio >= 0.3
    return {"code": code, "keep": keep, "ratio": round(ratio, 3),
            "matched": matched[:5], "end_date": d.get("end_date"),
            "reason": "main_business_ok" if keep else "main_business_unrelated"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--theme", required=True)
    a = ap.parse_args()
    print(json.dumps(assess(a.code, a.theme), ensure_ascii=False, indent=1))
