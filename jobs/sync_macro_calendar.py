# -*- coding: utf-8 -*-
"""sync_macro_calendar.py — 宏观事件日历（FXMacroData）→ data/macro_calendar.json

为什么做它：波浪理论给不了"度"，我们四轮实验（E1~E1d）证明价格结构也判不出度 ⇒
  需要**外生时钟**（用户 2026-10-16 拍板：「接入 FXMacroData，这样就能在 CPI 发布和加息会议之前减仓规避」✓）。

接口（实测 2026-10-16）：
  · 基址 https://api.fxmacrodata.com/v1 ✓；认证 X-API-Key（**USD 免 key** ✓，每天 ≤100 次；非 USD 需 key ✗）
  · GET /calendar/{currency} ⇒ {"currency","timezone","data_quality","data":[{announcement_datetime(epoch),
      announcement_datetime_utc, announcement_datetime_local, release(如 inflation/policy_rate), calendar_event_id}]} ✓
  · 日历**免 key** 即可（15 分钟延迟无所谓 —— 我们要的是**事前**知道日期 ✓）

输出：data/macro_calendar.json = {src, fetched_at, events:[{ts, utc, local_date, release, id, tier, risk}]}
  tier/risk 由下方 RISK 映射决定（只标我们关心的类：通胀/利率/就业/GDP ✓，其余 tier=0 仍保留以便事后分析 ✓）
"""
import json, os, sys, time, urllib.request

BASE = "https://api.fxmacrodata.com/v1"
DATA = os.environ.get("DATA_DIR", "data")

# release slug → (tier, 中文名)；tier 越高越重要（0 = 不关心，仅留档）
RISK = {
    "inflation": (3, "CPI 通胀"),
    "core_inflation": (3, "核心 CPI"),
    "policy_rate": (3, "政策利率/FOMC"),
    "interest_rate_decision": (3, "利率决议"),
    "non_farm_payrolls": (3, "非农就业"),
    "unemployment_rate": (2, "失业率"),
    "gdp": (2, "GDP"),
    "pce": (2, "PCE 物价"),
    "retail_sales": (2, "零售销售"),
    "ism_manufacturing": (1, "ISM 制造业"),
    "ism_services": (1, "ISM 服务业"),
    "cpi": (3, "CPI"),
}


def fetch(path, key=""):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    if key:
        req.add_header("X-API-Key", key)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


CN_STATS_URL = "https://www.stats.gov.cn/sj/fbrc/bnxxfb/"


def fetch_cn_stats_schedule():
    """抓**统计局年度发布日程表**，解析「居民消费价格指数月度报告」的每月发布日期 ✓。

    为什么要它（2026-10-16 实测教训 ✗）：中国 CPI 的发布日**不规则**（近 12 个月是 9/11/9/10/11/10/9/9/9/**14**/9/9 日 ⇒
    2026 年 9 月数据实际 **10-14** 发布），而我们原来的规则写死「每月 9 日」⇒ 偏了 5 天 ✗；
    美元事件（CPI/FOMC）能用 FXMacroData 日历 ✓，中国数据需 key ✗ ⇒ 用官方日程补齐 ✓。

    返回 {"202610": 14, ...}（键 = %Y%m，值 = 日 ✓，时间固定 09:30 ✓）；抓不到 ⇒ 返回 {}（fail-open ✓ 上层回落到规则 ✓）。
    """
    import re, urllib.request
    try:
        req = urllib.request.Request(CN_STATS_URL, headers={"User-Agent": "Mozilla/5.0"})
        t = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return {}
    t = re.sub(r"<script.*?</script>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    i = t.find("居民消费价格指数月度报告")
    if i < 0:
        return {}
    seg = t[i:i + 600]
    days = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*/\s*[\u4e00-\u9fa5]", seg)][:12]
    if len(days) < 12:
        return {}
    import datetime as dt
    y = dt.date.today().year
    out = {}
    for m in range(1, 13):
        # 表里的 12 个日期是"当月发布上上月数据"的**发布日期**；跨年时 1 月的日期属上一年 12 月数据 ⇒ 键用发布日期 ✓
        out["%04d%02d" % (y, m)] = days[m - 1]
    return out


def load_policy_calendar(months_ahead=18):
    """把 config/policy_calendar.yaml 的 rules 展开成具体日期 + 合并 explicit（src=manual-policy ✓）。

    为什么需要它：FXMacroData 只有**数据发布**日历 ✓；政治局会议/两会/中央经济工作会议这类**会议**不在里面 ✗，
    中国数据（CNY）还需 key ✗ ⇒ 用规则生成名义日期补齐 ✓（真实日期确定后写进 explicit ✓）。
    """
    import datetime as dt
    p = os.path.join("config", "policy_calendar.yaml")
    if not os.path.exists(p):
        return []
    import yaml
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    out, today = [], dt.date.today()
    end = today + dt.timedelta(days=31 * months_ahead)
    sched = fetch_cn_stats_schedule()          # 统计局官方日程（CPI 实际发布日 ✓ 不规则）
    if sched:
        print("  已解析统计局官方日程：%d 个月（例：2026-10 → %s 日 ✓）" % (len(sched), sched.get("202610", "?")))
    for r in d.get("rules") or []:
        months = r.get("months") or ([r["month"]] if r.get("month") else list(range(1, 13)))
        y, m = today.year, today.month
        for _ in range(months_ahead + 1):
            if m in months:
                # ★ CPI 类优先用**官方日程**（按发布日期的主键 %Y%m 查 ✓）；其它条目仍用规则 ✓
                dom = None
                if r.get("release") == "cny_cpi" and sched.get("%04d%02d" % (y, m)):
                    dom = sched["%04d%02d" % (y, m)]
                dom = dom or r.get("day_of_month") or 1
                try:
                    day = dt.date(y, m, int(dom))
                except Exception:
                    day = None
                if day and today <= day <= end:
                    out.append({"date": day.strftime("%Y%m%d"), "name": r.get("name") or r.get("release"),
                                "release": r.get("release") or "", "tier": int(r.get("tier") or 0),
                                "note": r.get("note") or ""})
            m += 1
            if m > 12:
                m, y = 1, y + 1
    for e in d.get("explicit") or []:
        out.append({"date": (e.get("date") or "").replace("-", ""), "name": e.get("name") or e.get("release"),
                    "release": e.get("release") or "", "tier": int(e.get("tier") or 0), "note": e.get("note") or ""})
    out.sort(key=lambda x: x["date"])
    return out


def main():
    key = (os.environ.get("FXMACRODATA_API_KEY") or "").strip()
    os.makedirs(DATA, exist_ok=True)
    events, meta = [], {}
    for ccy in ("usd",) + (("cny",) if key else ()):
        try:
            d = fetch("/calendar/%s" % ccy, key)
        except Exception as e:
            print("  %-4s 拉取失败 %s %s" % (ccy, type(e).__name__, str(e)[:90]))
            continue
        rows = d.get("data") or []
        meta[ccy] = {"timezone": d.get("timezone"), "data_quality": d.get("data_quality"), "n": len(rows)}
        for r in rows:
            rel = str(r.get("release") or "")
            tier, cn = RISK.get(rel, (0, rel))
            events.append({
                "ts": int(r.get("announcement_datetime") or 0),
                "utc": r.get("announcement_datetime_utc") or "",
                "local": r.get("announcement_datetime_local") or "",
                "ccy": ccy,
                "release": rel,
                "name_cn": cn if tier else "",
                "tier": tier,
                "id": r.get("calendar_event_id") or "",
            })
        print("  %-4s n=%-4d tz=%s 质量=%s" % (ccy, len(rows), d.get("timezone"), d.get("data_quality")))
    if not key:
        print("  ⚠️ 未设 FXMACRODATA_API_KEY ⇒ 只同步了 USD（免 key ✓）；中国事件(CNY)与预测值需 key ✗")
    # 合并政策/会议日历（规则展开 ✓，src=manual-policy ✓）—— 时间取当日 09:30（北京时间，A 股开盘前 ✓）
    pol = load_policy_calendar()
    for r in pol:
        try:
            import datetime as dt
            ts = int(dt.datetime.strptime(r["date"], "%Y%m%d").replace(hour=9, minute=30).timestamp())
        except Exception:
            continue
        events.append({"ts": ts, "utc": "", "local": r["date"][:4] + "-" + r["date"][4:6] + "-" + r["date"][6:8] + "T09:30:00",
                       "ccy": "cn", "release": r["release"], "name_cn": r["name"], "tier": r["tier"],
                       "id": "cn_policy_" + r["release"] + "_" + r["date"], "src": "manual-policy", "note": r["note"]})
    print("  合并政策/会议日历 %d 条（src=manual-policy ✓）" % len(pol))
    events.sort(key=lambda x: x["ts"])
    p = os.path.join(DATA, "macro_calendar.json")
    json.dump({"src": "fxmacrodata:/v1/calendar", "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "meta": meta, "events": events}, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(p + ".tmp", p)
    hi = [e for e in events if e["tier"] >= 3]
    print("写入 %s：事件 %d 条（其中 tier≥3 的 %d 条，最近 5 条：）" % (p, len(events), len(hi)))
    for e in hi[:5]:
        print("   %s ｜ %s ｜ %s" % (e["local"][:19], e["name_cn"] or e["release"], e["ccy"]))


if __name__ == "__main__":
    main()
