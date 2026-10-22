# -*- coding: utf-8 -*-
"""前复权行情取数（P0-1 ✓ 2026-10-22）—— 给浪型/策略提示任务用的**复权**K线 ✓。

为什么要它（用户 2026-10-22 实测踩坑 ✗）：
  · `512480.SH` 半导体ETF 2026-07-03 做 **1:2 份额折算**（fund_adj 1.9638 → 3.9276）；
  · `588170.SH` 科创半导体设备ETF 2026-07-06 做 **1:3 份额折算**（fund_adj 1.0 → 3.0）；
  · 取**原始价**（不复权）算日涨跌 ⇒ 那两天显示 -50% / -67% 的**假暴跌** ✗
    ⇒ 误触发「跌破 MA20 / 跌破结构位 ⇒ 减仓」✗ —— 这是 P0 级误判 ✗。
  ⇒ 一律用**前复权**价 ✓：`q = raw * factor / factor[-1]`（**末端不缩放** ✓ 最新一日 == 原始价 ✓）。

口径（务必守住 ✓）：
  · ETF → `fund_adj`（列：ts_code / trade_date / adj_factor）；
  · 个股 → `adj_factor`（同名列）；
  · 指数 → **不复权** ✗（指数无拆分/分红 ✓ 调用方继续用 `index_daily` 原样 ✓）。

fail-open ✓（写死的纪律）：复权因子取不到 ⇒ **退回原始价** ✓ + 打 `[adj]` 日志 ✓，**绝不抛异常** ✗
（提示任务不能因为一次取数失败就停摆 ✓；但日志里必须能看出退回了 ✗）。

用法::
    from adj_quote import qfq_bars
    bars = qfq_bars("588170.SH", "20260601", "20261022")    # ETF ⇒ 自动 fund_adj ✓
    bars = qfq_bars("002156.SZ", "20260601", "20261022")    # 个股 ⇒ 自动 adj_factor ✓
    bars[0]   # {"d","o","h","l","c","o_raw","h_raw","l_raw","c_raw","amt"}
"""
import functools

from tushare_relay import relay_query      # core/ 已在 sys.path ✓（各 job 都会加 ✓）

# 代码前缀判据 ✓（只用来选接口口径，不做任何交易含义推断 ✗）
# ★ 判据按**市场**分开 ✓ —— 单看数字前缀会翻车 ✗：`000988.SZ`（华工科技）是**个股** ✓，
#   而 `000001.SH` / `000688.SH` 才是**指数** ✓（沪市个股是 60/68 开头 ✓）。
ETF_PREFIX = ("5", "15", "16", "18")                        # 5 开头=沪/科创 ETF；15/16/18=SZ ETF ✓
INDEX_PREFIX_ANY = ("899", "930", "931", "932", "950")      # 跨市场指数族 ✓（另认 .CSI 后缀 ✓）
INDEX_PREFIX_SH = ("000",)                                  # 上证指数族 ✓
INDEX_PREFIX_SZ = ("399",)                                  # 深证指数族 ✓

_DAILY_API = {"etf": "fund_daily", "stock": "daily", "index": "index_daily"}
_FACTOR_API = {"etf": "fund_adj", "stock": "adj_factor"}              # 指数没有因子 ✓


def kind_of(code):
    """→ 'etf' / 'stock' / 'index' ✓（None 时按代码前缀自动判断 ✓）"""
    c = (code or "").strip().upper()
    num, _sep, mkt = c.partition(".")
    if mkt == "CSI" or num.startswith(INDEX_PREFIX_ANY):
        return "index"
    if mkt == "SH" and num.startswith(INDEX_PREFIX_SH):
        return "index"
    if mkt == "SZ" and num.startswith(INDEX_PREFIX_SZ):
        return "index"
    if num.startswith(ETF_PREFIX):
        return "etf"
    return "stock"


def is_index(code):
    return kind_of(code) == "index"


def _num(v):
    """→ float ｜ None（NaN/空/非数字一律 None ✓ 不猜 ✗）"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x          # NaN 自己不等于自己 ✓


def _daily_rows(code, fn, start, end):
    """原始日线 ✓ → [{"d","o","h","l","c","amt"}] 升序"""
    df = relay_query(fn, ts_code=code, start_date=start, end_date=end)
    out = []
    for _, r in (df.iterrows() if df is not None else []):
        c = _num(r.get("close"))
        if not c:
            continue
        o, h, lo = _num(r.get("open")), _num(r.get("high")), _num(r.get("low"))
        out.append({"d": str(r.get("trade_date")),
                    "o": c if o is None else o,
                    "h": c if h is None else h,
                    "l": c if lo is None else lo,
                    "c": c,
                    "amt": _num(r.get("amount")) or 0.0})
    return sorted(out, key=lambda x: x["d"])


def _factors(code, kind, start, end):
    """→ {trade_date: adj_factor} ✓（取不到就有多远算多远 ✓ 空 dict 由调用方兜底 ✓）"""
    api = _FACTOR_API.get(kind)
    if not api:
        return {}
    df = relay_query(api, ts_code=code, start_date=start, end_date=end)
    out = {}
    for _, r in (df.iterrows() if df is not None else []):
        d, f = str(r.get("trade_date")), _num(r.get("adj_factor"))
        if d and f and f > 0:
            out[d] = f
    return out


def _raw_only(bars):
    """fail-open ✓：o/h/l/c == 原始价（o_raw 等相同 ✓）—— 调用方无感 ✓"""
    return tuple({"d": b["d"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                  "o_raw": b["o"], "h_raw": b["h"], "l_raw": b["l"], "c_raw": b["c"],
                  "amt": b["amt"]} for b in bars)


@functools.lru_cache(maxsize=512)      # ★ 按 (code,start,end,kind,fn) 缓存 ✓ relay 有频次限制 ✗
def _qfq_cached(code, start, end, kind, fn):
    try:
        bars = _daily_rows(code, fn, start, end)
    except Exception as e:
        print("[adj] {} 原始K线取数失败（{}）⇒ 返回空 ✗".format(code, str(e)[:100]))
        return ()
    if not bars:
        print("[adj] {} {}~{} 无原始K线 ⇒ 返回空 ✗".format(code, start, end))
        return ()
    if kind not in _FACTOR_API:
        print("[adj] {} 指数口径 ⇒ 不复权 ✓（无拆分/分红 ✓）".format(code))
        return _raw_only(bars)

    try:
        fac = _factors(code, kind, start, end)
    except Exception as e:
        print("[adj] {} 复权因子取数失败（{}）⇒ 退回复权 ✗".format(code, str(e)[:80]))
        fac = {}
    if not fac:
        # 退回复权 ✗ ⇒ 用原始价 ✓（日志必须能看出来 ✗ 绝不静默 ✗）
        print("[adj] {} 退回复权 ✗ 复权因子为空（{} 口径）⇒ 退回原始价 ✓".format(
            code, "fund_adj" if kind == "etf" else "adj_factor"))
        return _raw_only(bars)

    fdates = sorted(fac)

    def _f_at(d):
        """当日缺失 ⇒ 用**最近一个更早**的因子 ✓（不缩放 ✗）；比首日还早 ⇒ 用最早那个 ✓"""
        last = None
        for k in fdates:
            if k <= d:
                last = fac[k]
            else:
                break
        return last if last else fac[fdates[0]]

    f_last = _f_at(bars[-1]["d"]) or 1.0        # ★ 前复权锚在**最新一日** ✓ ⇒ 末端不缩放 ✓
    out = []
    for b in bars:
        k = (_f_at(b["d"]) or f_last) / f_last
        out.append({"d": b["d"],
                    "o": b["o"] * k, "h": b["h"] * k, "l": b["l"] * k, "c": b["c"] * k,
                    "o_raw": b["o"], "h_raw": b["h"], "l_raw": b["l"], "c_raw": b["c"],
                    "amt": b["amt"]})
    return tuple(out)


def qfq_bars(code, start, end, kind=None, fn=None):
    """前复权日线 ✓ → [{"d","o","h","l","c","o_raw","h_raw","l_raw","c_raw","amt"}] 按 trade_date 升序

    参数：
      · code：`588170.SH` / `002156.SZ` ✓
      · kind：`etf` / `stock` / `index` / None ✓（None ⇒ 按前缀自动判断 ✓ 5/15/16/18 开头=ETF ✓）
      · fn：`fund_daily`（ETF 默认 ✓）/ `daily`（个股默认 ✓）/ `index_daily`（指数 ✓），可显式覆盖 ✓
      · o/h/l/c = **前复权到最新一日** ✓（q = raw * factor / factor[-1] ✓ 不做四舍五入 ✓
        这样最后一日 c 与 c_raw **完全相等** ✓）
      · amt（成交额）**不复权** ✓（金额本身不随份额折算变 ✓）

    fail-open ✓：因子取不到 ⇒ o/h/l/c 等于原始价 + 打 `[adj]` 日志 ✓，**不抛异常** ✗。
    返回的是**副本** ✓（缓存里的对象不会被调用方改坏 ✓）。
    """
    code = (code or "").strip().upper()
    k = (kind or kind_of(code)).strip().lower()
    f = fn or _DAILY_API.get(k, "daily")
    if k == "index" and not fn:
        f = "index_daily"
    return [dict(x) for x in _qfq_cached(code, start, end, k, f)]
