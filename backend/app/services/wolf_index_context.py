# -*- coding: utf-8 -*-
"""wolf_index_context.py — 个股→指数/板块基准与"板块级防御减T"上下文（P2-7 拆分, 2026-09-10）

**拆分来源**: 本模块内容原先被**错拼在** backend/app/services/wolf_t_rules.py 内
（该文件出现 3 处 `coding: utf-8` 标记: 做T规则 / 基准指数 / 板块防御三段粘在一起）。
审计 §5.2 S12 指出"同一文件内两套不相关职责"; 密钥部分已先行外置(commit b26878a), 本次拆出职责。

**职责**: 个股 → 所属基准指数(创业板/科创50/深证成指/上证) / 申万一级行业 → 行业指数；
以及"个股高位滞涨而所属指数/行业未跟"的**板块级防御减T**判定。
数据源: datahubco(指数成分) + promax(申万行业日线)，密钥走环境变量
        DATAHUBCO_API_KEY / PROMAX_API_KEY(见 .env, 已 gitignore)。

**兼容**: wolf_t_rules.py 保留同名的再导出, 既有 `from app.services.wolf_t_rules import ...` 不受影响。
"""
import os

def _norm_ts(sym):
    """symbol → ts_code 归一化。

    兼容三种写法: 交易所前缀(xq: SH600001/SZ300750/BJ430047)、裸 6 位码(600001)、
    已是 ts_code(600001.SH)。
    2026-09-10 修复: 原先 resolve_sw_sector 直接 `s + '.SH' if s.startswith('6') else s + '.SZ'`,
    对 xq 前缀写法会拼出 **垃圾 ts_code**(如 SH600001 → 'SH600001.SZ'), 导致 datahubco 查不到 →
    resolve_sw_sector 返回 None → **defensive_t_reduce_sw(板块级防御减T) 在生产中永不触发**。
    """
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if s.endswith((".SH", ".SZ", ".BJ")):
        return s
    if len(s) > 6 and s[:2] in ("SH", "SZ", "BJ"):
        return s[2:] + "." + s[:2]
    if len(s) == 6 and s.isdigit():
        if s[0] == "6":
            return s + ".SH"
        if s[:2] in ("43", "83", "87", "92"):
            return s + ".BJ"
        return s + ".SZ"
    return s


def benchmark_index(sym):
    """个股→所属基准指数代码: 30/301/300 开头→创业板 399006; 688→科创50 000688;
    00/002/159→深证成指 399001; 否则上证 000001。

    注: 本函数目前**无调用方**(死代码, 2026-09-10 核验); 保留但已按 _norm_ts 归一化入参,
    以免将来被调用时因 xq 前缀(SH/SZ)而恒返回 000001。
    """
    s = _norm_ts(sym).split(".")[0]
    if s.startswith(("30", "301", "300")):
        return "399006"
    if s.startswith("688"):
        return "000688"
    if s.startswith(("00", "002", "159")):
        return "399001"
    return "000001"
def _dh_get(api, **p):
    import requests, urllib3
    urllib3.disable_warnings()
    DH='http://datahubco.com/app-api/openapi/v1/tushare'; K=os.getenv("DATAHUBCO_API_KEY", "")
    r=requests.get(f'{DH}/{api}',params=p,headers={'X-API-Key':K},timeout=40)
    d=r.json(); dd=d.get('data') or {}
    return (dd.get('items') or []), (dd.get('fields') or [])

_MEMBERS_CACHE = {}
def _index_members(index_code):
    if index_code in _MEMBERS_CACHE: return _MEMBERS_CACHE[index_code]
    items, fields = _dh_get('index_weight', index_code=index_code)
    if not items or not fields: return []
    fmap={f:i for i,f in enumerate(fields)}
    maxdt=max(str(it[fmap.get('trade_date',2)]) for it in items)
    mem={str(it[fmap.get('con_code',1)]) for it in items if str(it[fmap.get('trade_date',2)])==maxdt}
    _MEMBERS_CACHE[index_code]=mem
    return mem

def resolve_stock_index(sym, idx_pool=None):
    """通过 index_weight(指数成分权重) 反查个股归属指数. 返回 (指数代码, 权重) 或 (None,None).
    idx_pool 默认覆盖主要宽基: 000001/000016/000300/000905/000852/399001/399006/000688."""
    idx_pool = idx_pool or ['000001.SH','000016.SH','000300.SH','000905.SH','000852.SH','399001.SZ','399006.SZ','000688.SH']
    for idx in idx_pool:
        members=_index_members(idx)
        if sym in members:
            return idx, None
    return None, None

def defensive_t_reduce_index(sym, idx_code, sym_hi_now, sym_hi_prev, idx_hi_now, idx_hi_prev, wave_op='t_only'):
    """防御减T(按个股所属指数): 该股归属指数 近高/创新高(>=前5日高*0.998) 且 该股未跟(<阶段高*0.998) → 防御."""
    if wave_op not in ('t_only','side','defense','exit'): return False
    idx_near_high = idx_hi_now is not None and idx_hi_prev and idx_hi_now >= idx_hi_prev*0.998
    sym_lag = sym_hi_now is not None and sym_hi_prev and sym_hi_now < sym_hi_prev*0.998
    return bool(idx_near_high and sym_lag)


_SW_MEMBER_CACHE = {}
def resolve_sw_sector(sym):
    """个股→申万一级行业指数代码(801150.SI 医药生物/801080.SI 电子等). 用 datahubco index_member_all(ts_code) 反查 l1_code.
    泛化: 非科技也适用(药明→医药生物, 华林→非银金融). 缓存避免反复请求."""
    s=str(sym)
    if s in _SW_MEMBER_CACHE: return _SW_MEMBER_CACHE[s]
    ts = _norm_ts(s)   # 2026-09-10 修复: 原先对 xq 前缀(SH600001)会拼出垃圾 ts_code → 查不到 → 机制永不触发
    items, fields = _dh_get('index_member_all', ts_code=ts)
    if not items or not fields:
        _SW_MEMBER_CACHE[s]=None; return None
    fmap={f:i for i,f in enumerate(fields)}
    l1 = items[0][fmap['l1_code']] if 'l1_code' in fmap else None
    _SW_MEMBER_CACHE[s]=l1
    return l1


_SW_DAILY_CACHE = {}
_SW_DAILY_TS = {}
def _sw_daily_high(idx_code):
    """申万行业指数日线 high 序列 {YYYYMMDD: high}. promax sw_daily, 5分钟缓存."""
    import time
    now=time.time()
    if idx_code in _SW_DAILY_CACHE and now-_SW_DAILY_TS.get(idx_code,0)<300:
        return _SW_DAILY_CACHE[idx_code]
    import requests, urllib3
    urllib3.disable_warnings()
    PM='https://pcd.mobcvb.cn/tushare/pro'; PK=os.getenv("PROMAX_API_KEY", "")
    try:
        from datetime import datetime
        end=datetime.now().strftime('%Y%m%d')
        r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx_code,'start_date':'20250101','end_date':end},headers={'X-API-Key':PK},verify=False,timeout=30)
        dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
        fmap={f[i]:i for i in range(len(f))} if f else {}
        out={}
        if 'trade_date' in fmap and 'high' in fmap:
            for x in it:
                td=x[fmap['trade_date']]; hi=x[fmap['high']]
                if td is None or hi is None: continue   # 跳过宽窗口下 high=None 行
                out[str(td).replace('-','')]=float(hi)
        _SW_DAILY_CACHE[idx_code]=out; _SW_DAILY_TS[idx_code]=now
        return out
    except Exception:
        return _SW_DAILY_CACHE.get(idx_code) or {}


def defensive_t_reduce_sw(sym, sym_hi_now, sym_hi_prev, wave_op='t_only', idx_near_th=0.998, sym_lag_th=0.98):
    """防御减T(个股申万行业级, 泛化非科技): 该股所属申万一级行业指数 近高/创新高(>=前5日高*idx_near_th)
    且 该股未跟(低<阶段高*sym_lag_th) → 防御减T. 返回 (ok, reason).
    v15收紧: 默认 idx_near_th=0.998(行业须近高), sym_lag_th=0.98(个股须回落>2%) 以减少误报(08-24/09-03式)."""
    if wave_op not in ('t_only','side','defense','exit'):
        return False, 'wave allow build'
    idx = resolve_sw_sector(sym)
    if not idx: return False, 'no_sw_sector'
    d = _sw_daily_high(idx)
    if not d: return False, 'no_sw_daily'
    keys = sorted(d)
    idx_now = d.get(keys[-1]) if keys else None
    idx_prev = max([d[k] for k in keys[:-1]][-5:]) if len(keys)>1 else None
    sym_lag = sym_hi_now is not None and sym_hi_prev and sym_hi_now < sym_hi_prev*sym_lag_th
    idx_near = idx_now is not None and idx_prev and idx_now >= idx_prev*idx_near_th
    ok = bool(idx_near and sym_lag)
    ip = ((idx_now/idx_prev-1)*100 if (idx_now and idx_prev) else 0)
    sp = ((sym_hi_now/sym_hi_prev-1)*100 if (sym_hi_now and sym_hi_prev) else 0)
    return ok, '行业%s 近高=%.3f/%.3f(%+.2f%%) 个股未跟=%.3f/%.3f(%+.2f%%)' % (idx, idx_now or 0, idx_prev or 0, ip, sym_hi_now or 0, sym_hi_prev or 0, sp)
