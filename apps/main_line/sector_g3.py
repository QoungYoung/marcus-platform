# -*- coding: utf-8 -*-
import json, os, sys, math
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_mainline as fm

DATA = os.environ.get("DATA_DIR", "/app/data")

# 收敛判据(板块级 G3, 全自动·无 ETF 硬编码, 2026-09-07):
#   持仓→stock_concept_map 概念→fusion THEME_CONCEPTS 主题；板块代理=主题成员概念(concept_hist 521 概念全量)
#   c1 波动率: 主题概念 close 日收益 近20日std < 0.8×近60日std
#   c2 资金活跃度: 成员概念 |net_amount| 5日均 ≤ 0.8×60日中位
#   两条件持续(>=MIN_DAYS 日) 视为"主力洗盘收敛期" → 该主题持仓不自动 T出
STD_RATIO = float(os.getenv("G3_STD_RATIO", "0.8"))
NET_RATIO = float(os.getenv("G3_NET_RATIO", "0.8"))
W20, W60 = 20, 60
MIN_CONCEPTS = 3

_HIST = None
def _hist():
    global _HIST
    if _HIST is None:
        _HIST = fm.load("concept_hist.json")
    return _HIST

def theme_concepts(theme):
    """theme → 在 concept_hist 中命中的概念 dicts(按名字)"""
    names = set(fm.THEME_CONCEPTS.get(theme, []))
    return [a for a in _hist().values() if a.get("name") in names]

def concept_frame(theme):
    """主题成员概念 → DataFrame(日期 x 列: 合成 close 指数/收益、|net| 均值)"""
    hist = _hist()
    codes = [c for c, a in hist.items() if a.get("name") in set(fm.THEME_CONCEPTS.get(theme, []))]
    if len(codes) < MIN_CONCEPTS:
        return None, codes
    cl = {}
    for c in codes:
        a = hist[c]
        try:
            cl[c] = pd.Series(dict(zip(a["dates"], a["close"])), dtype=float)
        except Exception:
            pass
    if not cl:
        return None, codes
    px = pd.DataFrame(cl).sort_index().ffill()
    ret = px.pct_change().mean(axis=1, skipna=True)   # 主题等权收益
    net = pd.DataFrame({c: pd.Series(dict(zip(hist[c]["dates"], hist[c]["net_amount"])), dtype=float)
                        for c in cl}).sort_index().abs().mean(axis=1, skipna=True)
    df = pd.concat([ret.rename("ret"), net.rename("net")], axis=1)
    return df, codes

def theme_converged(theme, as_of=None):
    """主题洗盘收敛判定(相对自身常态) → dict"""
    try:
        df, codes = concept_frame(theme)
        if df is None or len(df) < W60 + 5:
            return {"theme": theme, "converged": False, "reason": "no_data", "concepts": len(codes or [])}
        s = df.dropna()
        if as_of is not None:
            s = s[s.index <= as_of]
        if len(s) < W60 + 5:
            return {"theme": theme, "converged": False, "reason": "short", "concepts": len(codes or [])}
        std20 = s["ret"].iloc[-W20:].std(); std60 = s["ret"].iloc[-W60:].std()
        n5 = s["net"].iloc[-5:].mean(); n60 = s["net"].iloc[-W60:].median()
        std_ratio = float(std20 / std60) if std60 and std60 > 0 else 9.0
        net_ratio = float(n5 / n60) if n60 and n60 > 0 else 9.0
        conv = std_ratio < STD_RATIO and net_ratio <= NET_RATIO
        return {"theme": theme, "converged": conv, "std_ratio": round(std_ratio, 3),
                "net_ratio": round(net_ratio, 3), "concepts": len(codes or []),
                "as_of": str(s.index[-1])[:10], "reason": "" if conv else "not_converged"}
    except Exception as ex:
        return {"theme": theme, "converged": False, "reason": str(ex)[:80], "concepts": 0}

_THEME_KEYWORDS = {
    "半导体/芯片": ["半导体", "芯片", "集成电路", "光刻", "科创芯片"],
    "AI/算力/科技": ["人工智能", "AI", "算力", "数字经济", "软件", "通信", "云计算", "科创50", "大数据"],
    "医药": ["创新药", "医药", "生物", "医疗", "CXO", "CRO"],
    "军工/航天": ["军工", "航天", "航空", "无人机", "卫星"],
    "新能源/电池": ["新能源", "电池", "光伏", "锂电", "储能"],
    "消费/内需": ["消费", "白酒", "食品", "零售", "旅游", "免税"],
    "金融": ["证券", "银行", "保险", "非银"],
    "资源/周期": ["有色", "煤炭", "钢铁", "石油", "资源"],
    "稳增长/基建": ["基建", "建材", "建筑", "电力"],
}

def symbol_themes(symbol):
    """持仓/ETF → 命中主题。股票走 stock_concept_map 概念 ∩ THEME_CONCEPTS;
    ETF/无概念行 → 名称关键词 → 主题(粗粒度主题级映射, 非标的硬编码表)。"""
    import psycopg2
    s = str(symbol).strip().upper()
    code6, ex = "", ""
    if s.endswith((".SH", ".SZ")):
        code6, ex = s[:6], s[-2:]
    elif s[:2] in ("SH", "SZ"):
        code6, ex = s[2:], s[:2]
    else:
        code6 = s
        ex = "SH" if s.startswith("6") else "SZ"
    ts = code6 + "." + ex
    out = []
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
        cur.execute("SELECT concept_name FROM stock_concept_map WHERE ts_code=%s", (ts,))
        cnames = {r[0] for r in cur.fetchall()}
        cur.close(); conn.close()
        for theme, names in fm.THEME_CONCEPTS.items():
            if theme == "银行": continue
            if cnames & set(names):
                out.append(theme)
    except Exception:
        pass
    if out:
        return out
    # ETF/无概念行: 名称关键词匹配
    name = ""
    try:
        from app.services.t_data_sources import fetch_tencent_quote
        qsym = ex.lower() + code6          # 腾讯符号: sh588170
        q = (fetch_tencent_quote([qsym]) or {}).get(qsym) or {}
        name = str(q.get("name") or "")
    except Exception:
        name = ""
    for theme, kws in _THEME_KEYWORDS.items():
        if any(k.lower() in name.lower() for k in kws):
            out.append(theme)
    return out

def build_state():
    """全部主题收敛 → 供盘前 sector_g3_state.json 与 TMonitor no_t_gate"""
    st = {}
    for theme in fm.MAIN_THEMES:
        if theme == "银行": continue
        st[theme] = theme_converged(theme)
    return st

if __name__ == "__main__":
    import pprint
    pprint.pprint(build_state())
