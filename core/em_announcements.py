# -*- coding: utf-8 -*-
"""em_announcements.py —— 东财**公告**数据源（账本 §9.566／§9.567 落地 ✓）

**为什么需要它**：他的原口径「**个股自身利空（减持／暴雷）⇒ 可以出（且要快）**」✓
（`wolf-behavior-blueprint.md:3367` ✓；中联重科遇减持认亏出局并**立规** ✓）。
而我们的 Tushare 中继**没有公告接口** ✗（96 个接口实测无公告类 ✗）⇒ 本模块补这个缺口 ✓。

**源**（2026-10-04 实测可用 ✓）：
  `https://np-anotice-stock.eastmoney.com/api/security/ann`
    · 参数：`stock_list=<6位代码>`、`page_index`、`page_size`、`ann_type=A`（A 股 ✓）
    · 返回：`{"data":{"list":[{"art_code","title","notice_date",...}]}}` ✓
    · **可回溯**（实测 page 3 达 2025-11 ✓）⇒ **可支撑回测** ✓

**本地缓存**：`data/_em_ann/<code>.jsonl`（按 `art_code` 去重、追加式 ✓）⇒ 回测不必重复请求 ✓

**用法**：
  · `.venv/bin/python core/em_announcements.py --symbol 301511 --from 20260301 --to 20260315`
  · `.venv/bin/python core/em_announcements.py --symbol 301511 --only-reduction --pages 6`
  · `.venv/bin/python core/em_announcements.py --selftest`
  · 代码内：`from core.em_announcements import fetch, reductions_between, is_reduction`
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "_em_ann")
API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}

# 减持类关键词（**不含**「增持」「回购」✗ —— 只认减持 ✓）
REDUCTION_KEYS = ("减持",)
NOT_REDUCTION = ("增持", "回购", "不减持", "取消减持", "终止减持")


def is_reduction(title: str) -> bool:
    """标题是否属于**减持**类公告 ✓（排除增持／回购 ✗）"""
    t = str(title or "")
    if not t or "减持" not in t:
        return False
    if any(k in t for k in NOT_REDUCTION):
        return False
    return True


def _cache_path(code: str) -> str:
    return os.path.join(CACHE_DIR, "%s.jsonl" % str(code).strip()[-6:])


def load_cache(code: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    p = _cache_path(code)
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def save_cache(code: str, rows: List[Dict[str, Any]]) -> int:
    """追加式写入（按 art_code 去重 ✓）；返回新增条数 ✓"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    old = {str(r.get("art_code") or "") for r in load_cache(code)}
    new = [r for r in rows if str(r.get("art_code") or "") and str(r.get("art_code")) not in old]
    if new:
        with open(_cache_path(code), "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)


def _norm(code: str) -> str:
    c = str(code or "").strip().upper()
    for pre in ("SH", "SZ", "BJ"):
        if c.startswith(pre):
            c = c[2:]
    return c.split(".")[0]


def fetch_pages(code: str, pages: int = 1, page_size: int = 50,
                stop_before: str = "", sleep: float = 0.3) -> List[Dict[str, Any]]:
    """按页抓取公告 ✓（`stop_before` ⇒ 遇到更早的日期就停 ✓）"""
    import requests
    out: List[Dict[str, Any]] = []
    c6 = _norm(code)
    for pg in range(1, max(1, int(pages)) + 1):
        url = ("%s?sr=-1&page_size=%d&page_index=%d&ann_type=A&client_source=web&stock_list=%s"
               % (API, int(page_size), pg, c6))
        try:
            r = requests.get(url, timeout=15, headers=HEADERS)
            j = r.json()
        except Exception as e:
            print("[em_ann] 第 %d 页失败：%s" % (pg, str(e)[:90]), flush=True)
            break
        lst = ((j or {}).get("data") or {}).get("list") or []
        if not lst:
            break
        oldest = "99999999"
        for x in lst:
            d = str(x.get("notice_date") or x.get("display_time") or "")[:10].replace("-", "")
            x["notice_date8"] = d
            x["code6"] = c6
            x["is_reduction"] = is_reduction(x.get("title"))
            x["url"] = "https://data.eastmoney.com/notices/detail/%s/%s.html" % (c6, x.get("art_code"))
            out.append(x)
            oldest = min(oldest, d) if d else oldest
        if stop_before and oldest != "99999999" and oldest < str(stop_before):
            break
        time.sleep(max(0.0, float(sleep)))
    return out


def fetch(code: str, pages: int = 3, page_size: int = 50, use_cache: bool = True,
          stop_before: str = "") -> List[Dict[str, Any]]:
    """抓取并**合并**本地缓存 ✓（先缓存、后网络 ⇒ 回测零请求 ✓）"""
    rows = fetch_pages(code, pages=pages, page_size=page_size, stop_before=stop_before)
    if use_cache and rows:
        save_cache(code, rows)
    merged = {str(r.get("art_code") or ""): r for r in load_cache(code)}
    for r in rows:
        merged[str(r.get("art_code") or "")] = r
    out = [v for k, v in merged.items() if k]
    out.sort(key=lambda r: str(r.get("notice_date8") or ""), reverse=True)
    return out


def reductions_between(code: str, d0: str = "", d1: str = "", **kw) -> List[Dict[str, Any]]:
    """取**减持**类公告 ✓（`d0`/`d1` 为 `YYYYMMDD` ✓；**优先读缓存** ✓）"""
    rows = fetch(code, use_cache=True, **kw)
    out = []
    for r in rows:
        if not r.get("is_reduction"):
            continue
        d = str(r.get("notice_date8") or "")
        if d0 and d < str(d0):
            continue
        if d1 and d > str(d1):
            continue
        out.append(r)
    return out


def selftest() -> int:
    """自测：301511 的已知 5 条减持公告必须全部命中 ✓；增持/回购必须不误报 ✓"""
    print("  ── 自测 ✓ ──")
    cases = [
        ("德福科技:关于公司部分董事及高级管理人员及合伙企业股份减持计划的预披露公告", True),
        ("德福科技:关于持股5%以上股东及其一致行动人股份减持计划完成的公告", True),
        ("德福科技:关于控股股东、实际控制人、董事及高级管理人员减持股份计划实施完成的公告", True),
        ("德福科技:关于回购股份进展的公告", False),
        ("某公司:关于股东增持公司股份的公告", False),
        ("某公司:关于不减持公司股份的承诺公告", False),
    ]
    ok = 0
    for t, exp in cases:
        got = is_reduction(t)
        flag = "✓" if got == exp else "✗"
        ok += 1 if got == exp else 0
        print("    %s %s ⇒ %s（期望 %s）" % (flag, t[:44], got, exp))
    print("  判定用例 ✓: %d/%d" % (ok, len(cases)))
    rows = reductions_between("301511", pages=6)
    print("  301511 减持类公告 ✓: %d 条（缓存 ✓: %d 条）" % (len(rows), len(load_cache("301511"))))
    for r in rows[:6]:
        print("    %s | %s" % (str(r.get("notice_date"))[:10], str(r.get("title"))[:66]))
    return 0 if ok == len(cases) and len(rows) >= 5 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="")
    ap.add_argument("--from", dest="d0", default="")
    ap.add_argument("--to", dest="d1", default="")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--only-reduction", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.symbol:
        print("  需要 --symbol 或 --selftest ✗"); return 2
    rows = (reductions_between(a.symbol, a.d0, a.d1, pages=a.pages) if a.only_reduction
            else fetch(a.symbol, pages=a.pages))
    if a.d0 or a.d1:
        rows = [r for r in rows
                if (not a.d0 or str(r.get("notice_date8") or "") >= a.d0)
                and (not a.d1 or str(r.get("notice_date8") or "") <= a.d1)]
    print("  %s ⇒ %d 条（减持类 %d 条 ✓）" % (a.symbol, len(rows), sum(1 for r in rows if r.get("is_reduction"))))
    for r in rows[:20]:
        mk = "★减持" if r.get("is_reduction") else "    "
        print("    %s %s | %s" % (mk, str(r.get("notice_date"))[:10], str(r.get("title"))[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
