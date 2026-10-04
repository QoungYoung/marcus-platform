# -*- coding: utf-8 -*-
"""bt_fund_asof.py — 回测 as-of 基本面 / 估值取数层(纯数据缓存, 默认不参与任何生产路径)

背景: leader v2 尺子对齐狼大语料时, 语料点名的三块要素此前无数据落库——
      ① 基本面业绩(营收拐点/扭亏/预增预减)  ② 产业链核心中枢/稀缺零件  ③ 估值反转(便宜)
      实测三个上游接口都可达且很便宜(见 docs/wolf-buy-parameter-ledger.md):
        forecast_vip(ann_date=D)   全市场当日业绩预告, 1~4s, 天然带公告日 ⇒ as-of 安全
        fina_indicator(period=Q)   全市场该期财务指标(6000 行, 含多版本修订), 8s
        income(period=Q)           全市场该期利润表(revenue / n_income_attr_p), 12s
        daily_basic(trade_date=D)  全市场当日 pe_ttm/pb/total_mv, 1~8s, 硬上限 5000 行
     产业链结构直接用本地 data/chain_dict_final_<theme>.json(分段 role/label/concepts/kw)。

关键纪律(与全项目一致):
  * as-of 严格: 财务只用 ann_date <= cut 的版本(同一报告期常有多次修订公告, 不能拿最新版);
    估值/预告按公告日或交易日 <= cut。任何穿越未来的取值都是未来函数, 一律禁止。
  * fail-open: 任何缺失/异常一律返回 None 或中性值, 绝不抛错、绝不改变"没有数据"时的行为。
  * 默认关闭: 本模块只做取数与读取; 是否使用由调用方开关决定。
  * 生产零影响: 不写 DB、不碰 paper 表, 只写 data/_bt_fund/ 下的 json 缓存。

用法(手工/回测准备):
  python -u jobs/bt_fund_asof.py fetch --start 20260105 --end 20260120 --tag t2
  python -u jobs/bt_fund_asof.py show  --tag t2 --stocks 603986.SH,600584.SH --asof 20260108
"""
import os, sys, json, time, argparse, datetime as dt

DATA = os.environ.get("DATA_DIR", "data")
ROOT = os.path.join(DATA, "_bt_fund")
# 参考数据（曾用名 / 已退市名单）与"哪条臂"无关 ⇒ 共用一份，不按 tag 分开，
# 否则每换一个臂 tag 就要把 1000+ 只票的曾用名重新拉一遍（promax 还会 503）。
REF_TAG = "_ref"


# ----------------------------------------------------------------------------- relay
def _relay():
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError as _e_sil1:
        print("[silent:bt_fund_asof.py:38] %s: %s" % (type(_e_sil1).__name__, str(_e_sil1)[:110]), flush=True)
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _call(api, fields, tries=3, **kw):
    """中继查询, 带重试; 失败返回 []。"""
    last = None
    for i in range(tries):
        try:
            _f, items = _relay().relay_items(api, fields=fields, **kw)
            return items or []
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    print("[fund] %s %s 失败: %s" % (api, kw, str(last)[:110]), flush=True)
    return []


# ----------------------------------------------------------------------------- paths
_ROOT_FIXED = False


def _fix_root():
    """把 `ROOT` 钉到 `WOLF_FUND_ROOT`（**只在 ROOT 不存在时**）。

    ⚠️ 2026-09-22 实测踩到（用户问「审核前会再请求拉取吗」时查出的真因）：
      回测每个子进程的 `DATA_DIR` 被指到**当天沙箱** ⇒ `ROOT = <sandbox>/_bt_fund`（不存在）
      ⇒ 所有 `load_*` **静默读空**（`_dir()` 还会把空目录建出来，看起来"有目录"）。
      与 2026-09-21 `lowdip_junk_gate` / `universe_clean` 是同一族问题 —— 这次轮到个股资金流。
      生产不受影响（`DATA_DIR` = 真实 data 目录，目录存在 ⇒ 不改）。
    """
    global ROOT, _ROOT_FIXED
    if _ROOT_FIXED:
        return
    _ROOT_FIXED = True
    try:
        env = os.getenv("WOLF_FUND_ROOT")
        # ⚠️ 条件不能用 `not os.path.isdir(ROOT)`：`_dir()` 会把空目录建出来 ⇒ 第二次运行就"看着存在"了
        #    （2026-09-22 实测：沙箱里已经被建出 `<day>/_bt_fund/_ref/moneyflow/`，重定位再也触发不了）。
        #    env 只由回测 pins 设置 ⇒ 生产/单测不设 ⇒ 行为不变。
        if env and os.path.isdir(env) and os.path.abspath(env) != os.path.abspath(ROOT):
            print("[fund] ROOT 重定位 %s → %s（WOLF_FUND_ROOT；DATA_DIR 被指到沙箱）" % (ROOT, env), flush=True)
            ROOT = env
    except Exception as _e_sil2:
        print("[silent:bt_fund_asof.py:86] %s: %s" % (type(_e_sil2).__name__, str(_e_sil2)[:110]), flush=True)


def _dir(kind, tag):
    _fix_root()
    d = os.path.join(ROOT, tag, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _dump(path, obj):
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _f(x):
    try:
        v = float(x)
        return v if v == v else None          # NaN -> None
    except Exception:
        return None


def _norm(code):
    """统一成 6 位代码 + 后缀(与项目其余部分一致: 000001.SZ / 600000.SH)。

    ⚠️ 2026-09-21 实测踩坑: pipeline/回测链路传的是 **SH600895 / SZ002371** 前缀形式,
      而 DB/relay/缓存是 **600895.SH** 后缀形式。不认前缀形式 ⇒ 缓存全部 miss ⇒ 三块静默全 None
      (表现为"加了开关但一个特征都没挂上", 极难发现)。两种形式都必须认。"""
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ") and s[2:].isdigit():     # SH600895 -> 600895.SH
        return s[2:] + "." + s[:2]
    if len(s) == 6 and s.isdigit():
        return s + (".SH" if s[0] in "56" else ".SZ" if s[0] in "03" else ".BJ")
    return s


# ----------------------------------------------------------------------------- fetch
def fetch_daily_basic(day, tag):
    """单日全市场 pe_ttm/pb/total_mv。接口硬上限 5000 行, 超出部分缺失(尾部小票),
    由 fetch_daily_basic_for() 对关注池逐票补齐。"""
    p = os.path.join(_dir("daily_basic", tag), day + ".json")
    if os.path.exists(p):
        return _load(p) or {}
    rows = _call("daily_basic", "ts_code,trade_date,pe_ttm,pb,total_mv,circ_mv",
                 trade_date=day)
    out = {}
    for r in rows:
        try:
            out[_norm(r[0])] = {"pe_ttm": _f(r[2]), "pb": _f(r[3]),
                                "total_mv": _f(r[4]), "circ_mv": _f(r[5])}
        except Exception:
            continue
    _dump(p, out)
    return out


def fetch_fina(period, tag):
    """该报告期全市场财务指标 + 利润表, 保留同期的多个公告版本(按 ann_date 排序)。
    唯一键 ts_code, 值为版本列表 [{ann_date, end_date, or_yoy, netprofit_yoy, revenue, n_income}]。"""
    p = os.path.join(_dir("fina", tag), period + ".json")
    if os.path.exists(p):
        return _load(p) or {}
    fi = _call("fina_indicator", "ts_code,ann_date,end_date,or_yoy,netprofit_yoy,debt_to_assets",
               period=period)
    inc = _call("income", "ts_code,ann_date,end_date,revenue,n_income_attr_p", period=period)
    acc = {}

    def _put(code, ann, end, **kw):
        code = _norm(code)
        if not code or not ann:
            return
        lst = acc.setdefault(code, [])
        for it in lst:
            if it.get("ann_date") == ann and it.get("end_date") == (end or period):
                it.update({k: v for k, v in kw.items() if v is not None})
                return
        d = {"ann_date": str(ann), "end_date": str(end or period)}
        d.update(kw)
        lst.append(d)

    for r in fi:
        try:
            _put(r[0], r[1], r[2], or_yoy=_f(r[3]), netprofit_yoy=_f(r[4]), debt_to_assets=_f(r[5]))
        except Exception:
            continue
    for r in inc:
        try:
            _put(r[0], r[1], r[2], revenue=_f(r[3]), n_income=_f(r[4]))
        except Exception:
            continue
    for code, lst in acc.items():
        lst.sort(key=lambda x: x["ann_date"])
    _dump(p, acc)
    return acc


def fetch_fina_by_code(codes, tag, quiet=False):
    """逐票财务指标(主路径): fina_indicator(ts_code=) + income(ts_code=), 保留全部公告版本。
    实测 datahubco 源不支持按 period 全市场查(报"必填参数 ts_code"), 只有 promax 支持而 promax
    池会 upstream_pool_exhausted; 因此关注池(几百只)以逐票为准, 全市场广度用 income(period=) 兜底。
    存 fina_by_code/<code>.json = [{ann_date, end_date, or_yoy, netprofit_yoy, revenue, n_income, ...}]"""
    d = _dir("fina_by_code", tag)
    got = 0
    for code in codes:
        code = _norm(code)
        if not code:
            continue
        p = os.path.join(d, code + ".json")
        if os.path.exists(p):
            continue
        acc = {}
        fi = _call("fina_indicator", "ts_code,ann_date,end_date,or_yoy,netprofit_yoy,debt_to_assets",
                   ts_code=code)
        for r in fi:
            try:
                if r[0] != code:
                    continue
                k = (str(r[1]), str(r[2]))
                acc.setdefault(k, {"ann_date": str(r[1]), "end_date": str(r[2])})
                acc[k].update(or_yoy=_f(r[3]), netprofit_yoy=_f(r[4]), debt_to_assets=_f(r[5]),
                              src="fina_indicator")
            except Exception:
                continue
        inc = _call("income", "ts_code,ann_date,end_date,revenue,n_income_attr_p", ts_code=code)
        for r in inc:
            try:
                if r[0] != code:
                    continue
                k = (str(r[1]), str(r[2]))
                acc.setdefault(k, {"ann_date": str(r[1]), "end_date": str(r[2])})
                acc[k].update(revenue=_f(r[3]), n_income=_f(r[4]), src="income")
            except Exception:
                continue
        lst = sorted(acc.values(), key=lambda x: (x["ann_date"], x["end_date"]))
        _dump(p, lst)
        got += 1
        if not quiet and got % 25 == 0:
            print("[fund] fina_by_code %d/%d" % (got, len(codes)), flush=True)
    return got


def _yoy_from_income(code, day, tag):
    """fina_indicator 缺失时, 用两期 income 自算营收/净利同比(口径明确: 同期对同期)。"""
    lst = _load(os.path.join(ROOT, tag, "fina_by_code", _norm(code) + ".json")) or []
    by_period = {}
    for v in lst:
        ed = v.get("end_date")
        if not ed or not v.get("ann_date") or v["ann_date"] > day:
            continue
        if ed not in by_period or v["ann_date"] > by_period[ed]["ann_date"]:
            by_period[ed] = dict(v)
    if not by_period:
        return {}
    latest = max(by_period.values(), key=lambda v: v["ann_date"])
    ed = latest.get("end_date") or ""
    prev = (str(int(ed[:4]) - 1) + ed[4:]) if len(ed) == 8 and ed[:4].isdigit() else None
    out = {}
    p = by_period.get(prev) if prev else None
    for k, dst in (("revenue", "or_yoy"), ("n_income", "netprofit_yoy")):
        a, b = latest.get(k), (p or {}).get(k)
        if a is not None and b not in (None, 0):
            out[dst] = round((a - b) / abs(b) * 100.0, 2)
    return out


def fetch_forecast_day(day, tag):
    """当日(公告日)全市场业绩预告。"""
    p = os.path.join(_dir("forecast", tag), day + ".json")
    if os.path.exists(p):
        return _load(p) or {}
    rows = _call("forecast_vip", "ts_code,ann_date,end_date,type,p_change_min,p_change_max,summary",
                 ann_date=day)
    out = {}
    for r in rows:
        try:
            code = _norm(r[0])
            if not code:
                continue
            out[code] = {"ann_date": str(r[1]), "end_date": str(r[2]), "type": (r[3] or "").strip(),
                         "pmin": _f(r[4]), "pmax": _f(r[5]), "summary": (r[6] or "")[:120]}
        except Exception:
            continue
    _dump(p, out)
    return out


def _trading_days(start, end):
    """交易日列表: 优先用本地 index csv(与回测同源), 退化为工作日。"""
    days = []
    try:
        import glob, csv as _csv
        cand = sorted(glob.glob(os.path.join(DATA, "*index*.csv")) + glob.glob(os.path.join(DATA, "index*.csv")))
        for cp in cand[:4]:
            with open(cp, encoding="utf-8", errors="ignore") as f:
                for row in _csv.DictReader(f):
                    d = (row.get("trade_date") or row.get("date") or "").replace("-", "")[:8]
                    if d.isdigit() and start <= d <= end:
                        days.append(d)
            if days:
                break
    except Exception:
        days = []
    if not days:
        d = dt.datetime.strptime(start, "%Y%m%d")
        e = dt.datetime.strptime(end, "%Y%m%d")
        while d <= e:
            if d.weekday() < 5:
                days.append(d.strftime("%Y%m%d"))
            d += dt.timedelta(days=1)
    return sorted(set(days))


def _periods_for(start, end):
    """窗口内需要拉的报告期(含上一期做同比参照)。"""
    out = []
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0 - 1, y1 + 1):
        for md in ("0331", "0630", "0930", "1231"):
            p = "%d%s" % (y, md)
            if p >= "%d0101" % (y0 - 1) and p <= end:
                out.append(p)
    return out[-6:]


def fetch(start, end, tag, daily=True, fina=True, forecast=True, quiet=False):
    days = _trading_days(start, end)
    todo = []
    if daily:
        todo += [("daily_basic", d) for d in days]
    if forecast:
        todo += [("forecast", d) for d in days]
    if fina:
        todo += [("fina", p) for p in _periods_for(start, end)]
    for kind, key in todo:
        p = os.path.join(ROOT, tag, kind, key + ".json")
        if os.path.exists(p):
            continue
        t0 = time.time()
        if kind == "daily_basic":
            n = len(fetch_daily_basic(key, tag))
        elif kind == "forecast":
            n = len(fetch_forecast_day(key, tag))
        else:
            n = len(fetch_fina(key, tag))
        if not quiet:
            print("[fund] %-11s %s rows=%-5d %.1fs" % (kind, key, n, time.time() - t0), flush=True)
    return True


def fetch_namechange(codes, tag, quiet=False):
    """逐票曾用名历史(tushare namechange) ⇒ **as-of 名称**可精确重建, 用来判 ST/*ST/退 而**不穿越未来**
    (用"今天的名字"去过滤 1 月的票 = 轻度未来函数: 一只票可能 6 月才戴帽)。
    存 namechange/<code>.json = [{name, start_date, end_date, ann_date, reason}]
    ⚠️ 落在 **REF_TAG** 下（参考数据不分臂）。"""
    d = _dir("namechange", REF_TAG)
    got = 0
    for code in codes:
        code = _norm(code)
        if not code:
            continue
        p = os.path.join(d, code + ".json")
        if os.path.exists(p):
            continue
        rows = _call("namechange", "ts_code,name,start_date,end_date,ann_date,change_reason", ts_code=code)
        lst = []
        for r in rows:
            try:
                if r[0] != code:
                    continue
                lst.append({"name": (r[1] or "").strip(), "start_date": str(r[2] or ""),
                            "end_date": str(r[3]) if r[3] else None,
                            "ann_date": str(r[4]) if r[4] else None,
                            "reason": (r[5] or "").strip()})
            except Exception:
                continue
        lst.sort(key=lambda x: x["start_date"])
        _dump(p, lst)
        got += 1
        if not quiet and got % 25 == 0:
            print("[fund] namechange %d/%d" % (got, len(codes)), flush=True)
    return got


def fetch_delisted(tag):
    """全市场**已退市**名单(一次调用, 6s)。as-of 语义: delist_date <= day 才算已退市。"""
    p = os.path.join(ROOT, REF_TAG, "delisted.json")
    if os.path.exists(p):
        return _load(p) or {}
    rows = _call("stock_basic", "ts_code,name,list_status,delist_date,list_date", list_status="D")
    out = {}
    for r in rows:
        try:
            out[_norm(r[0])] = {"name": (r[1] or "").strip(),
                                "delist_date": str(r[3]) if r[3] else None}
        except Exception:
            continue
    _dump(p, out)
    return out


def name_as_of(code, day, tag):
    """as-of 名称: 取 start_date <= day 的最后一段(namechange 里就是当时真实简称)。
    无缓存 ⇒ None(由调用方决定回退策略)。"""
    p = os.path.join(ROOT, REF_TAG, "namechange", _norm(code) + ".json")
    lst = _load(p) if os.path.exists(p) else None
    if not lst:
        return None
    best = None
    for it in lst:
        sd = it.get("start_date") or ""
        if sd and sd <= day:
            if best is None or sd >= (best.get("start_date") or ""):
                best = it
    return best.get("name") if best else None


def delisted_by(code, day, tag):
    """该票在 day 之前是否**已经退市**(delist_date <= day); 无数据 ⇒ False(fail-open)。"""
    d = _load(os.path.join(ROOT, REF_TAG, "delisted.json"))
    if not d:
        return False
    it = (d or {}).get(_norm(code))
    if not it:
        return False
    dd = it.get("delist_date")
    return bool(dd and dd <= day)


# ----------------------------------------------------------------------------- read (as-of)
def _prev_cache(kind, tag, day):
    """<= day 的最近一份缓存文件名(不含后缀)。"""
    d = os.path.join(ROOT, tag, kind)
    try:
        cand = [x[:-5] for x in os.listdir(d) if x.endswith(".json") and x[:-5] <= day]
    except Exception:
        return None
    return max(cand) if cand else None


def load_val(code, day, tag):
    """as-of 估值: {pe_ttm, pb, total_mv, circ_mv, trade_date}"""
    k = _prev_cache("daily_basic", tag, day)
    if not k:
        return None
    obj = _load(os.path.join(ROOT, tag, "daily_basic", k + ".json")) or {}
    v = obj.get(_norm(code))
    if not v:
        return None
    v = dict(v); v["trade_date"] = k
    return v


_FINA_FILE = {}


def fina_versions(code, tag, end_date):
    """按期的全市场财务文件（**进程内缓存**：文件 6000+ 行，逐票 parse 会把回放拖死）。"""
    key = (tag, end_date)
    if key not in _FINA_FILE:
        _FINA_FILE[key] = _load(os.path.join(ROOT, tag, "fina", end_date + ".json")) or {}
    return _FINA_FILE[key].get(_norm(code)) or []


def load_fina(code, day, tag, max_quarters=6):
    """as-of 财务: 取 ann_date <= day 的最新一期(逐季回退), 返回该期的合并字段。
    合并两个来源: ① fina_by_code/<code>.json(逐票, 权威) ② fina/<period>.json(全市场 income 兜底)。
    or_yoy / netprofit_yoy 若接口未给, 用两期 income 自算补齐。"""
    code = _norm(code)
    cands = []
    for v in (_load(os.path.join(ROOT, tag, "fina_by_code", code + ".json")) or []):
        if v.get("ann_date") and v["ann_date"] <= day:
            cands.append(dict(v))
    y = int(day[:4])
    periods = []
    for py in (y, y - 1):
        for md in ("1231", "0930", "0630", "0331"):
            p = "%d%s" % (py, md)
            if p <= day:
                periods.append(p)
    periods = sorted(set(periods), reverse=True)[:max_quarters]
    for p in periods:
        for v in fina_versions(code, tag, p):
            if v.get("ann_date") and v["ann_date"] <= day:
                c = dict(v); c["period"] = p
                cands.append(c)
    if not cands:
        return None
    latest = max(cands, key=lambda v: (v["ann_date"], v.get("period") or v.get("end_date") or ""))
    if latest.get("or_yoy") is None or latest.get("netprofit_yoy") is None:
        yy = _yoy_from_income(code, day, tag)
        for k, v in yy.items():
            latest.setdefault(k, v)
            if latest.get(k) is None:
                latest[k] = v
    return latest


_FC_INDEX = {}


def forecast_index(tag):
    """倒排索引 {code: [(ann_date, rec), ...]}（一次读完所有 forecast 日文件，进程内缓存）。

    ⚠️ 2026-09-21 性能修复：原实现每只票都从缓存目录**倒着逐个 parse 日文件**（最多 400 个），
      而每天的 JSON 有上百条预告 ⇒ 单票最坏 400 次 JSON parse。实测把逐日回放拖到 ~60s/天。
      改成一次性倒排后，取一条预告 = 一次字典查找 + 一次二分。
    """
    if tag in _FC_INDEX:
        return _FC_INDEX[tag]
    idx = {}
    d = os.path.join(ROOT, tag, "forecast")
    try:
        files = sorted(x for x in os.listdir(d) if x.endswith(".json"))
    except Exception:
        files = []
    for fn in files:
        obj = _load(os.path.join(d, fn)) or {}
        for code, rec in obj.items():
            ann = rec.get("ann_date") or fn[:-5]
            idx.setdefault(code, []).append((ann, rec))
    for code in idx:
        idx[code].sort(key=lambda x: x[0])
    _FC_INDEX[tag] = idx
    return idx


def load_forecast(code, day, tag, lookback=400):
    """as-of 业绩预告: ann_date <= day 的最新一条。"""
    lst = forecast_index(tag).get(_norm(code)) or []
    best = None
    for ann, rec in lst:
        if ann <= day:
            best = rec
        else:
            break
    return best


# ----------------------------------------------------------------------------- state
BAD_FORECAST = {"首亏", "续亏"}
GOOD_FORECAST = {"预增", "扭亏", "略增"}


def earnings_state(code, day, tag):
    """业绩状态(狼大"核心 3 要点"之一: 要看基本面 / 营收拐点·扭亏)。
    返回 {ok, flag, score_hint, rev_yoy, np_yoy, ftype, ann_date, src, note}
      flag: good(预增/扭亏/营收正增) | bad(首亏/续亏/大幅预减) | flat | unknown
    """
    out = {"ok": False, "flag": "unknown", "score_hint": 0.0, "rev_yoy": None, "np_yoy": None,
           "ftype": None, "ann_date": None, "src": None, "note": ""}
    fin = load_fina(code, day, tag)
    if fin:
        out["ok"] = True
        out["src"] = "fina"
        out["ann_date"] = fin.get("ann_date")
        out["rev_yoy"] = fin.get("or_yoy")
        out["np_yoy"] = fin.get("netprofit_yoy")
        out["note"] = "period=%s" % fin.get("period")
    fc = load_forecast(code, day, tag)
    if fc:
        out["ok"] = True
        out["ftype"] = fc.get("type")
        if not out["ann_date"] or (fc.get("ann_date") or "") > out["ann_date"]:
            out["ann_date"] = fc.get("ann_date")
        out["src"] = ("fina+forecast" if fin else "forecast")
        pmax = fc.get("pmax")
        if fc.get("type") in BAD_FORECAST or (fc.get("type") in ("预减", "略减") and (pmax or 0) < -30):
            out["flag"] = "bad"; out["score_hint"] = -1.0
            return out
        if fc.get("type") in GOOD_FORECAST and (pmax or 0) > 0:
            out["flag"] = "good"; out["score_hint"] = 1.0
            return out
    if fin:
        ry, ny = out["rev_yoy"], out["np_yoy"]
        if ry is not None and ny is not None:
            if ny > 50 or (ry > 20 and ny > 0):
                out["flag"] = "good"; out["score_hint"] = 1.0
            elif ny < -30 or (ny < 0 and ry < -10):
                out["flag"] = "bad"; out["score_hint"] = -1.0
            else:
                out["flag"] = "flat"
    return out


def val_state(code, day, tag, pb_hi=12.0):
    """估值状态(狼大"估值便宜/估值反转"; 排位靠后, 只做轻微加减分, 不做硬门)。
    返回 {ok, pe_ttm, pb, total_mv, flag, score_hint}
      pe_ttm<=0 视为亏损(不给便宜加分也不扣分), pb 过高视为贵。
    """
    v = load_val(code, day, tag)
    if not v:
        return {"ok": False, "pe_ttm": None, "pb": None, "total_mv": None,
                "flag": "unknown", "score_hint": 0.0}
    pe, pb, mv = v.get("pe_ttm"), v.get("pb"), v.get("total_mv")
    flag, hint = "flat", 0.0
    if pe is not None and 0 < pe <= 25:
        flag, hint = "cheap", 0.5
    elif pe is not None and pe > 120:
        flag, hint = "rich", -0.5
    if pb is not None and pb_hi and pb > pb_hi:
        flag, hint = ("rich", min(hint, 0) - 0.5 if hint <= 0 else -0.5)
    return {"ok": True, "pe_ttm": pe, "pb": pb, "total_mv": mv, "flag": flag, "score_hint": hint}


# ----------------------------------------------------------------------------- chain
_CHAIN_CACHE = {}
_STOCK_CONCEPT = {}


def concept_segments(theme_kw=None):
    """概念名 -> [(theme, role, label)]。来自本地 data/chain_dict_final_<theme>.json 的
    segments(role/label/concepts/kw) —— 这是货真价实的"产业链环节"分层(半导体链实测:
    上游材料设备 / 中游芯片设计 / 中游晶圆制造 / 下游封装测试)。覆盖 10 大主题 70 个概念。"""
    import glob
    key = "seg|" + (theme_kw or "*")
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key]
    out = {}
    for p in sorted(glob.glob(os.path.join(DATA, "chain_dict_final_*.json"))):
        theme = os.path.basename(p)[len("chain_dict_final_"):-len(".json")]
        if theme_kw and theme_kw not in theme:
            continue
        d = _load(p) or {}
        for s in ((d.get("final_dict") or {}).get("segments")) or []:
            for c in (s.get("concepts") or []):
                out.setdefault(c, []).append((theme, s.get("role") or "", s.get("label") or ""))
    _CHAIN_CACHE[key] = out
    return out


def stock_concepts(codes=None):
    """票 -> 概念集合(DB stock_concept_map, 全市场)。DB 不可用时返回 {} 由调用方 fail-open。"""
    if codes is None and _STOCK_CONCEPT:
        return _STOCK_CONCEPT
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        if codes:
            cur.execute("select ts_code, concept_name from stock_concept_map where ts_code = any(%s)",
                        (list(codes),))
        else:
            cur.execute("select ts_code, concept_name from stock_concept_map")
        for c, n in cur.fetchall():
            _STOCK_CONCEPT.setdefault(_norm(c), set()).add(str(n))
        cur.close(); conn.close()
    except Exception as _e_sil3:
        print("[silent:bt_fund_asof.py:649] %s: %s" % (type(_e_sil3).__name__, str(_e_sil3)[:110]), flush=True)
    return _STOCK_CONCEPT


_SEG_WIDTH = {}


def segment_widths():
    """环节宽度 = 该 (theme, label) 下所有概念的成分股并集大小。
    用途：语料「产业链核心中枢或**稀缺零件**」——环节越窄 ⇒ 越接近"稀缺零件"。
    这是**参数无关**的稀缺度代理（回测里再用分位，不设固定阈值）。"""
    if _SEG_WIDTH:
        return _SEG_WIDTH
    # 磁盘缓存：回测里布腿器**每天一个新进程**，不缓存的话每天都要重算 ~190 个环节的并集
    import glob as _glob
    cache_p = os.path.join(ROOT, "_seg_width.json")
    sig = str(len(_glob.glob(os.path.join(DATA, "chain_dict_final_*.json"))))
    try:
        c = _load(cache_p)
        if c and c.get("sig") == sig and c.get("data"):
            _SEG_WIDTH.update({tuple(k.split("\x1f")): v for k, v in c["data"].items()})
            return _SEG_WIDTH
    except Exception as _e_sil4:
        print("[silent:bt_fund_asof.py:672] %s: %s" % (type(_e_sil4).__name__, str(_e_sil4)[:110]), flush=True)
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        for key in sorted({(t, l) for lst in concept_segments().values() for t, _r, l in lst}):
            cons = [c for c, lst in concept_segments().items() if any((x[0], x[2]) == key for x in lst)]
            try:
                cur.execute("select count(distinct ts_code) from stock_concept_map where concept_name = any(%s)",
                            (cons,))
                _SEG_WIDTH[key] = {"n": int(cur.fetchone()[0]), "concepts": cons}
            except Exception:
                _SEG_WIDTH[key] = {"n": 9999, "concepts": cons}
        cur.close(); conn.close()
        try:
            _dump(cache_p, {"sig": sig, "data": {"\x1f".join(k): v for k, v in _SEG_WIDTH.items()}})
        except Exception as _e_sil5:
            print("[silent:bt_fund_asof.py:690] %s: %s" % (type(_e_sil5).__name__, str(_e_sil5)[:110]), flush=True)
    except Exception as _e_sil6:
        print("[silent:bt_fund_asof.py:692] %s: %s" % (type(_e_sil6).__name__, str(_e_sil6)[:110]), flush=True)
    return _SEG_WIDTH


def chain_state(code=None, name=None, theme_kw=None):
    """产业链中枢代理: 该票的概念命中的链环节数/跨链数(命中越多越靠中枢)。
    返回 {ok, hits, roles, labels, themes, flag, score_hint, cover}
      cover=False 表示该票的概念完全不在链字典覆盖的 10 大主题内(数据盲区, 中性处理)。
    注意: 这是"环节中心度"代理; 语料里的"稀缺零件/不可替代性"是产品级判断, 本项目无数据源,
          因此本函数不外推该含义。"""
    seg = concept_segments(theme_kw)
    cons = stock_concepts().get(_norm(code)) if code else None
    hits = []
    if cons:
        for c in cons:
            for h in seg.get(c, []):
                hits.append({"concept": c, "theme": h[0], "role": h[1], "label": h[2]})
    if not hits and name:
        for p in sorted(__import__("glob").glob(os.path.join(DATA, "chain_dict_final_*.json"))):
            d = _load(p) or {}
            for t in (d.get("targets") or []):
                nm = (t.get("name") if isinstance(t, dict) else str(t)) or ""
                if nm and nm == name:
                    hits.append({"concept": "", "theme": os.path.basename(p)[16:-5],
                                 "role": "", "label": "targets名单"})
    if not hits:
        return {"ok": False, "hits": 0, "roles": [], "labels": [], "themes": [],
                "flag": "unknown", "score_hint": 0.0, "cover": False}
    themes = sorted({h["theme"] for h in hits})
    labels = sorted({h["label"] for h in hits if h["label"]})
    roles = sorted({h["role"] for h in hits if h["role"]})
    n = len(set(labels))
    flag = "core" if n >= 2 else "mid"
    hint = 0.6 if n >= 2 else 0.3
    # 环节宽度（越窄越接近"稀缺零件"）；取命中环节里**最窄**的一个
    w = None
    try:
        sw = segment_widths()
        cand = [sw.get((h["theme"], h["label"]), {}).get("n") for h in hits if h["label"]]
        cand = [x for x in cand if x]
        w = min(cand) if cand else None
    except Exception:
        w = None
    return {"ok": True, "hits": len(hits), "roles": roles, "labels": labels, "themes": themes,
            "flag": flag, "score_hint": hint, "cover": True, "width": w}


# ----------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "show"])
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--tag", default="t2")
    ap.add_argument("--stocks", default="")
    ap.add_argument("--asof", default="")
    ap.add_argument("--no-daily", action="store_true")
    ap.add_argument("--no-fina", action="store_true")
    ap.add_argument("--no-forecast", action="store_true")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.start, a.end, a.tag, daily=not a.no_daily, fina=not a.no_fina,
              forecast=not a.no_forecast)
        return
    day = a.asof or dt.datetime.now().strftime("%Y%m%d")
    for s in [x.strip() for x in a.stocks.split(",") if x.strip()]:
        print("== %s @%s" % (s, day))
        print("   earnings:", json.dumps(earnings_state(s, day, a.tag), ensure_ascii=False))
        print("   value   :", json.dumps(val_state(s, day, a.tag), ensure_ascii=False))
        print("   chain   :", json.dumps(chain_state(code=s), ensure_ascii=False))


if __name__ == "__main__":
    main()


# ─────────────────────── 个股逐日资金流（moneyflow）───────────────────────
# 2026-09-22（用户问「net_stop 为什么为 null」）：
#   `stock_confirm_judge` 判级时 **写死 net=None** ⇒ S1 的「抛压减弱」从未参与；
#   而 as-of 层此前**根本没有 moneyflow 这个 api**（诚实地回 n/a，不是静默取未来值）。
#   数据实测可拉：`moneyflow(ts_code=)` → 该票全历史逐日 net_mf_amount（603986 有 2,230 条）；
#   **按日全市场会被截断在 5,000 条**（0409 候选池 263 只里缺 22 只）⇒ 必须**按票拉**。
#   缓存落 `_ref/moneyflow/<6位码>.json`（参考数据不分臂）。

def fetch_moneyflow_by_code(codes, tag=None, quiet=False, keep_from="20241001"):
    """逐票拉 moneyflow 落缓存（幂等：已有文件跳过）。返回新增条数。"""
    d = _dir("moneyflow", tag or REF_TAG)
    got = 0
    for code in codes or []:
        c = _norm(code)
        if not c:
            continue
        p = os.path.join(d, c.split(".")[0] + ".json")
        if os.path.exists(p):
            continue
        try:
            rows = _call("moneyflow", "ts_code,trade_date,net_mf_amount", ts_code=c) or []
        except Exception:
            continue
        series = {}
        for r in rows:
            try:
                dd, vv = str(r[1]), r[2]
                if dd >= str(keep_from) and vv is not None:
                    series[dd] = float(vv)
            except Exception:
                continue
        _dump(p, {"ts_code": c, "src": "tushare.moneyflow", "n": len(series), "net_mf_amount": series})
        got += 1
        if not quiet and got % 100 == 0:
            print("[fund] moneyflow 预拉 %d 只" % got, flush=True)
    return got


def load_moneyflow(code, tag=None):
    """{date8: net_mf_amount}；无缓存/取不到 → None（调用方 fail-open）。"""
    c = _norm(code)
    if not c:
        return None
    p = os.path.join(_dir("moneyflow", tag or REF_TAG), c.split(".")[0] + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return None
    d = obj.get("net_mf_amount") if isinstance(obj, dict) else None
    return d if isinstance(d, dict) and d else None


# ── 缺缓存时的**按需补拉**（2026-09-22 用户："预拉不到资金流，审核前会再请求拉取吗"）──
#   默认关（`WOLF_MF_FETCH_ON_MISS=0`）：回放主循环里不引入网络，结果完全由缓存决定（确定性）。
#   打开后：miss ⇒ 当场调一次 relay 拉该票、**写回缓存**、再读 ⇒ 同一票同一天只会拉一次（负缓存兜住失败）。
#   为什么要这个兜底：候选池每天可能出现预拉并集之外的票（概念成分变动），没有它那些票会静默退回旧口径。
#   安全：①每进程上限 `WOLF_MF_FETCH_MAX`（默认 60，防止某天 300 个 miss 拖死回放）；
#        ②失败的票进负缓存，不再重试；③回放里 relay 调用走 shim，**返回行按 trade_date ≤ as-of 过滤**
#        （本函数再按 upto 过滤一次，双保险 ⇒ 不会引未来数据）。
_MISS: set = set()
_FETCHED = {"n": 0}


def _allow_fetch(explicit=None) -> bool:
    on = explicit if explicit is not None else \
        (str(os.getenv("WOLF_MF_FETCH_ON_MISS", "0")).strip().lower() in ("1", "true", "yes", "on"))
    if not on:
        return False
    try:
        cap = int(float(os.getenv("WOLF_MF_FETCH_MAX", "60") or 60))
    except Exception:
        cap = 60
    return _FETCHED["n"] < cap


def net_series(code, upto=None, tag=None, min_len=15, allow_fetch=None):
    """该票 **≤ upto** 的逐日净流入（升序 pandas.Series，索引=交易日）；不足 min_len → None。

    ⚠️ `confirm_chain` 只用 `net.values`（不看索引），但**必须按日期升序**，否则 `net_stop` 会算反。
    缺缓存时若 `allow_fetch`/`WOLF_MF_FETCH_ON_MISS` 打开 ⇒ **当场补拉一次并写回缓存**。
    """
    d = load_moneyflow(code, tag)
    if not d:
        c = _norm(code)
        if c and c not in _MISS and _allow_fetch(allow_fetch):
            _FETCHED["n"] += 1
            try:
                got = fetch_moneyflow_by_code([c], tag=tag, quiet=True)
            except Exception:
                got = 0
            if got:
                d = load_moneyflow(code, tag)
                if d:
                    print("[fund] moneyflow 按需补拉成功 %s（%d 日）" % (c, len(d)), flush=True)
            if not d:
                _MISS.add(c)
        if not d:
            return None
    u = str(upto or "").replace("-", "")[:8]
    ks = sorted(k for k in d if (not u or str(k) <= u))
    if len(ks) < int(min_len or 1):
        return None
    try:
        import pandas as pd
        return pd.Series([float(d[k]) for k in ks], index=pd.to_datetime(ks))
    except Exception:
        return None
