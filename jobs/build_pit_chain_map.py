#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build point-in-time (PIT) chain_map leader/second labels for 2025-01..2026-10.

The production generator (`apps/main_line/chain_map.py`) is a *current-snapshot*
tool: it reads the current `stock_concept_map`, the latest `fina_mainbz` rows and
the latest `daily_basic`, then stamps them with a run date.  Running it for an
old date therefore leaks future membership and future financials.

This builder only uses dated inputs:

  membership : Tushare `dc_member` snapshots (EastMoney concept + industry
               boards), fetched once per weekly label date.  A label date only
               uses its own snapshot (or an earlier cached snapshot when the
               exact date has no data).  Concepts absent from the EM board list
               fall back to the current Shenwan table filtered by
               `in_date <= label_date`; that is PIT-safe but survivorship-biased
               because removed members are not in the current table.
  market cap : Tushare `daily_basic(total_mv)` per date.  Local
               `_bt_full/bars.sqlite` (full market from 2025-12) and
               `market_cap.db` (a ~491-code subset from 2025-01) are fallbacks.
  financials : Tushare `fina_mainbz` by report period plus `income.ann_date`.
               A period is used only if `ann_date < label_date` (strictly before,
               avoiding same-day announcement ambiguity).  `fina_mainbz` has
               semi-annual/annual periods only, so Q1/Q3 are never fabricated.

Outputs (compact CSV/JSON, no database/parquet):
    data/pit/chain_map/labels_weekly.csv
    data/pit/chain_map/coverage_weekly.csv
    data/pit/chain_map/concept_coverage_weekly.csv
    data/pit/chain_map/manifest.json
    data/pit/chain_map/leak_tests.json

Raw network caches live under `.dsh-tmp/pit_chain_map_cache/` and are not part
of the deliverable.

Usage:
    python3 jobs/build_pit_chain_map.py --start 20250110 --end 20261021 --freq weekly
    python3 jobs/build_pit_chain_map.py --dates 20250110,20250627,20260619
    python3 jobs/build_pit_chain_map.py --offline --freq weekly
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import math
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "apps" / "main_line"))

OUT_DIR = ROOT / "data" / "pit" / "chain_map"
CACHE_DIR = ROOT / ".dsh-tmp" / "pit_chain_map_cache"
SEED_PATH = ROOT / "apps" / "main_line" / "chain_map.py"
SW_MEMBERS_PATH = ROOT / "data" / "sw_members_cache.json"
BARS_DB = ROOT / "data" / "_bt_full" / "bars.sqlite"
MARKET_CAP_DB = ROOT / "data" / "market_cap.db"

LEADER_TOP_N = 3
SECOND_TOP_N = 3
POOL_MV_N = 20
POOL_CONCEPT_N = 6
MIN_MV_ROWS = 1000
MIN_SNAPSHOT_QUALITY = 0.50

EM_ALIASES: Dict[str, List[str]] = {
    "黄金": ["黄金概念"],
    "稀土": ["稀土永磁"],
    "锂": ["锂矿概念"],
    "中药Ⅱ": ["中药概念"],
    "体外诊断": ["体外诊断概念"],
    "锂电池": ["锂电池概念"],
    "证券Ⅱ": ["券商概念"],
    "核力发电": ["核能核电"],
    "光伏发电": ["光伏概念"],
    "零售概念": ["新零售"],
    "旅游概念": ["在线旅游"],
    "光刻胶": ["光刻机(胶)"],
    "光刻机": ["光刻机(胶)"],
    "消费电子概念": ["消费电子"],
    "智谱AI": ["智谱AI概念"],
    "液冷概念": ["液冷"],
    "CPO概念": ["CPO"],
    "光通信模块": ["光模块"],
    "汽车一体化压铸": ["一体化压铸"],
    "毫米波概念": ["毫米波雷达"],
    "免税概念": ["免税"],
    "影视概念": ["影视"],
    "算力概念": ["算力"],
    "机器人执行器": ["机器人执行器"],
}

SW_ALIASES: Dict[str, List[str]] = {
    "保险": ["保险Ⅱ"],
    "消费电子概念": ["消费电子"],
    "工程建设": ["基础建设"],
    "汽车整车": ["乘用车"],
    "农药兽药": ["农药"],
    "文娱消费": ["影视院线"],
}


# ---------------------------------------------------------------------------
# IO / dates
# ---------------------------------------------------------------------------
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def json_load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    tmp.replace(path)


def gz_load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def gz_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def norm_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().replace("-", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return None


def to_float(value: Any) -> Optional[float]:
    if value in (None, "", "nan", "None"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# seed / calendar / SW
# ---------------------------------------------------------------------------
# PIT-only 扩展宇宙：不改生产 chain_map.SEED，避免影响线上 15 主题。
EXTRA_SEED: Dict[str, List[Dict[str, Any]]] = {
    "光伏": [{"role": "seg1", "label": "光伏·全链", "concepts": ["光伏概念", "光伏设备", "光伏电池组件", "光伏辅材"],
              "kw": ["硅料", "硅片", "电池片", "组件", "逆变器", "光伏"]}],
    "储能": [{"role": "seg1", "label": "储能·全链", "concepts": ["储能概念", "熔盐储能"],
              "kw": ["储能", "电化学储能", "储能系统", "PCS"]}],
    "火电": [{"role": "seg1", "label": "火电", "concepts": ["火电"], "kw": ["火电", "燃煤发电", "火电机组"]}],
    "水电": [{"role": "seg1", "label": "水电", "concepts": ["水电"], "kw": ["水电", "水力发电", "水电站"]}],
    "航空装备": [{"role": "seg1", "label": "航空装备", "concepts": ["航空装备Ⅱ", "航空装备Ⅲ", "航天航空"],
                "kw": ["航空装备", "军机", "航空发动机", "航空零部件"]}],
}


def load_seed() -> Dict[str, List[Dict[str, Any]]]:
    spec = importlib.util.spec_from_file_location("_pit_chain_map_seed", SEED_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % SEED_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seed = dict(mod.SEED)
    seed.update(EXTRA_SEED)
    return seed


def seed_concepts(seed: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    out: List[str] = []
    for segs in seed.values():
        for seg in segs:
            for c in seg.get("concepts", []):
                if c not in out:
                    out.append(c)
    return out


def fetch_trade_calendar(start: str, end: str, offline: bool = False) -> List[str]:
    if not offline:
        try:
            from tushare_relay import relay_items

            fields, items = relay_items(
                "trade_cal", exchange="SSE", start_date=start, end_date=end,
                is_open="1", limit=5000,
            )
            dates = sorted({
                norm_date(dict(zip(fields, r)).get("cal_date"))
                for r in items
                if str(dict(zip(fields, r)).get("is_open")) == "1"
            } - {None})
            if dates:
                return dates
        except Exception as exc:
            print("[warn] trade_cal failed: %s" % str(exc)[:180], flush=True)

    dates: Set[str] = set()
    try:
        con = sqlite3.connect("file:%s?mode=ro" % BARS_DB, uri=True)
        for (d,) in con.execute(
            "select distinct trade_date from bars where trade_date between ? and ?",
            (start, end),
        ):
            d = norm_date(d)
            if d:
                dates.add(d)
        con.close()
    except Exception:
        pass
    for p in sorted((ROOT / "data").glob("chain_map_[0-9]*.json")):
        d = norm_date(p.stem.split("_")[-1])
        if d and start <= d <= end:
            dates.add(d)
    return sorted(dates)


def weekly_dates(dates: Sequence[str]) -> List[str]:
    by_week: Dict[Tuple[int, int], str] = {}
    for d in dates:
        key = datetime.strptime(d, "%Y%m%d").isocalendar()[:2]
        by_week[key] = max(by_week.get(key, ""), d)
    return sorted(by_week.values())


def monthly_dates(dates: Sequence[str]) -> List[str]:
    by_month: Dict[str, str] = {}
    for d in dates:
        by_month[d[:6]] = max(by_month.get(d[:6], ""), d)
    return sorted(by_month.values())


def load_sw_members(path: Path = SW_MEMBERS_PATH) -> Dict[str, List[Dict[str, Any]]]:
    raw = json_load(path, [])
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen: Set[Tuple[str, str]] = set()
    for row in raw:
        code = row.get("ts_code")
        if not code:
            continue
        in_date = norm_date(row.get("in_date"))
        out_date = norm_date(row.get("out_date"))
        for level in ("l1_name", "l2_name", "l3_name"):
            name = row.get(level)
            if not name:
                continue
            key = (name, code)
            if key in seen:
                continue
            seen.add(key)
            out[name].append({
                "ts_code": code,
                "name": row.get("name") or "",
                "in_date": in_date,
                "out_date": out_date,
                "level": level,
            })
    return dict(out)


def sw_codes_at(name: str, date8: str, sw: Dict[str, List[Dict[str, Any]]]) -> Set[str]:
    codes: Set[str] = set()
    for row in sw.get(name, []):
        in_date = row.get("in_date")
        out_date = row.get("out_date")
        if not in_date or in_date > date8:
            continue
        if out_date and out_date <= date8:
            continue
        codes.add(row["ts_code"])
    return codes


# ---------------------------------------------------------------------------
# membership snapshots
# ---------------------------------------------------------------------------
def membership_cache_path(date8: str) -> Path:
    return CACHE_DIR / "membership" / ("%s.json.gz" % date8)


def fetch_membership_snapshot(
    date8: str,
    needed_names: Set[str],
    offline: bool = False,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    cache = membership_cache_path(date8)
    if cache.exists() and not force:
        payload = gz_load(cache)
        if payload and "quality" not in payload:
            boards = payload.get("boards") or {}
            nonempty = sum(1 for b in boards.values() if b.get("members"))
            payload["n_boards"] = len(boards)
            payload["n_nonempty_boards"] = nonempty
            payload["quality"] = round(nonempty / max(1, len(boards)), 4)
        return payload
    if offline:
        return None

    from tushare_relay import relay_items

    try:
        fields, items = relay_items("dc_index", trade_date=date8, limit=5000)
    except Exception as exc:
        print("[warn] dc_index %s failed: %s" % (date8, str(exc)[:180]), flush=True)
        return None

    boards: Dict[str, Dict[str, Any]] = {}
    name_to_codes: Dict[str, List[str]] = defaultdict(list)
    for row in items:
        d = dict(zip(fields, row))
        code, name = d.get("ts_code"), d.get("name")
        if not code or not name:
            continue
        boards[code] = {"name": name, "type": d.get("idx_type") or ""}
        name_to_codes[name].append(code)

    keep_codes: Set[str] = set()
    for name in needed_names:
        keep_codes.update(name_to_codes.get(name, []))
    if not keep_codes:
        return None

    members: Dict[str, List[List[str]]] = defaultdict(list)
    seen: Set[Tuple[str, str]] = set()
    total_rows = 0
    member_trade_date = None
    complete = True
    for offset in range(0, 200000, 5000):
        try:
            mf, mit = relay_items("dc_member", trade_date=date8, limit=5000, offset=offset)
        except Exception as exc:
            print("[warn] dc_member %s offset %d failed: %s"
                  % (date8, offset, str(exc)[:180]), flush=True)
            complete = False
            break
        if not mit:
            break
        total_rows += len(mit)
        for row in mit:
            d = dict(zip(mf, row))
            code, con_code = d.get("ts_code"), d.get("con_code")
            if not code or not con_code:
                continue
            if member_trade_date is None:
                member_trade_date = norm_date(d.get("trade_date"))
            if code not in keep_codes:
                continue
            key = (code, con_code)
            if key in seen:
                continue
            seen.add(key)
            members[code].append([con_code, d.get("name") or ""])
        if len(mit) < 5000:
            break

    if total_rows == 0:
        return None

    kept_boards = {
        code: {
            "name": boards[code]["name"],
            "type": boards[code]["type"],
            "members": members.get(code, []),
        }
        for code in keep_codes
        if code in boards
    }
    nonempty = sum(1 for b in kept_boards.values() if b["members"])
    payload = {
        "date": date8,
        "member_trade_date": member_trade_date,
        "fetched_at": now(),
        "n_rows_total": total_rows,
        "complete": complete,
        "n_boards": len(kept_boards),
        "n_nonempty_boards": nonempty,
        "quality": round(nonempty / max(1, len(kept_boards)), 4),
        "boards": kept_boards,
    }
    gz_dump(cache, payload)
    return payload


# ---------------------------------------------------------------------------
# market cap
# ---------------------------------------------------------------------------
def mv_cache_path(date8: str) -> Path:
    return CACHE_DIR / "mv" / ("%s.json" % date8)


def local_bars_mv(date8: str) -> Dict[str, float]:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % BARS_DB, uri=True)
        rows = con.execute(
            "select ts_code,total_mv from bars where trade_date=? "
            "and total_mv is not null and total_mv!='' and total_mv>0",
            (date8,),
        ).fetchall()
        con.close()
    except Exception:
        return {}
    out = {}
    for code, mv in rows:
        f = to_float(mv)
        if f and f > 0:
            out[str(code)] = f
    return out


def local_market_cap_mv(date8: str) -> Dict[str, float]:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % MARKET_CAP_DB, uri=True)
        rows = con.execute(
            "select ts_code,total_mv from daily_basic where trade_date=? "
            "and total_mv is not null and total_mv>0",
            (date8,),
        ).fetchall()
        con.close()
    except Exception:
        return {}
    return {str(code): float(mv) for code, mv in rows if to_float(mv)}


def fetch_mv(date8: str, offline: bool = False, force: bool = False) -> Dict[str, Any]:
    cache = mv_cache_path(date8)
    if cache.exists() and not force:
        payload = json_load(cache, {})
        if payload and (offline or len(payload.get("mv") or {}) >= MIN_MV_ROWS):
            return payload
    if offline and cache.exists() and not force:
        return json_load(cache, {}) or {"date": date8, "mv": {}, "source": "cache_missing"}

    if offline:
        mv = local_bars_mv(date8)
        source = "local_bars"
        if len(mv) < MIN_MV_ROWS:
            mv2 = local_market_cap_mv(date8)
            if len(mv2) > len(mv):
                mv, source = mv2, "local_market_cap_subset"
        return {"date": date8, "mv": mv, "source": source, "fetched_at": now()}

    from tushare_relay import relay_items

    mv: Dict[str, float] = {}
    try:
        for offset in range(0, 30000, 5000):
            fields, items = relay_items(
                "daily_basic", trade_date=date8, fields="ts_code,total_mv",
                limit=5000, offset=offset,
            )
            if not items:
                break
            for row in items:
                d = dict(zip(fields, row))
                code, val = d.get("ts_code"), to_float(d.get("total_mv"))
                if code and val and val > 0:
                    mv[str(code)] = val
            if len(items) < 5000:
                break
    except Exception as exc:
        print("[warn] daily_basic %s failed: %s" % (date8, str(exc)[:180]), flush=True)
        mv = {}

    source = "tushare_daily_basic"
    if len(mv) < MIN_MV_ROWS:
        local = local_bars_mv(date8)
        if len(local) > len(mv):
            mv, source = local, "local_bars_fallback"
        else:
            local2 = local_market_cap_mv(date8)
            if len(local2) > len(mv):
                mv, source = local2, "local_market_cap_subset_fallback"

    payload = {"date": date8, "mv": mv, "source": source, "fetched_at": now()}
    if len(mv) >= MIN_MV_ROWS:
        json_dump(cache, payload)
    return payload


# ---------------------------------------------------------------------------
# financials
# ---------------------------------------------------------------------------
def fina_cache_path(ts_code: str) -> Path:
    return CACHE_DIR / "fina" / ("%s.json" % ts_code.replace(".", "_"))


def fetch_fina(ts_code: str, offline: bool = False, force: bool = False) -> Dict[str, Any]:
    cache = fina_cache_path(ts_code)
    if cache.exists() and not force:
        return json_load(cache, {})
    if offline:
        return {"ts_code": ts_code, "periods": {}, "error": "offline_cache_miss"}

    from tushare_relay import relay_items

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    error = ""
    try:
        fields, items = relay_items(
            "fina_mainbz", ts_code=ts_code, start_date="20240101",
            end_date="20261031", limit=500,
        )
        for row in items:
            d = dict(zip(fields, row))
            end = norm_date(d.get("end_date"))
            sales = to_float(d.get("bz_sales"))
            if not end or sales is None or sales <= 0:
                continue
            grouped[end].append({
                "bz": str(d.get("bz_item") or "")[:80],
                "bz_code": str(d.get("bz_code") or ""),
                "sales": sales,
            })
    except Exception as exc:
        error = "fina_mainbz:" + str(exc)[:160]

    ann_map: Dict[str, str] = {}
    try:
        fields, items = relay_items(
            "income", ts_code=ts_code, start_date="20240101", end_date="20261031",
            fields="ts_code,ann_date,f_ann_date,end_date", limit=200,
        )
        for row in items:
            d = dict(zip(fields, row))
            end = norm_date(d.get("end_date"))
            if not end:
                continue
            for key in ("ann_date", "f_ann_date"):
                ann = norm_date(d.get(key))
                if ann and (end not in ann_map or ann < ann_map[end]):
                    ann_map[end] = ann
    except Exception as exc:
        if not error:
            error = "income:" + str(exc)[:160]

    excluded = {"行业", "产品", "地区", "合计", "合计特别调整"}
    periods: Dict[str, Dict[str, Any]] = {}
    for end, rows in grouped.items():
        product = [r for r in rows if r["bz_code"].upper() == "P" and r["bz"] not in excluded]
        if not product:
            product = [
                r for r in rows
                if r["bz"] not in excluded and r["bz_code"].upper() not in ("I", "D")
            ]
        product.sort(key=lambda r: -r["sales"])
        periods[end] = {
            "end_date": end,
            "ann_date": ann_map.get(end),
            "items": product[:20],
        }

    payload = {"ts_code": ts_code, "periods": periods, "error": error, "fetched_at": now()}
    if periods or not error:
        json_dump(cache, payload)
    return payload


def select_asof_period(
    fina: Dict[str, Any],
    date8: str,
    allow_same_day_ann: bool = False,
) -> Optional[Dict[str, Any]]:
    best = None
    for period in (fina or {}).get("periods", {}).values():
        ann = norm_date(period.get("ann_date"))
        if not ann:
            continue
        if not (ann <= date8 if allow_same_day_ann else ann < date8):
            continue
        if not period.get("items"):
            continue
        if best is None or period["end_date"] > best["end_date"]:
            best = period
    return best


def fina_hits(period: Optional[Dict[str, Any]], keywords: Sequence[str]) -> List[str]:
    if not period:
        return []
    tops = [str(x.get("bz") or "") for x in (period.get("items") or [])[:6]]
    hits: List[str] = []
    for text in tops:
        low = text.lower()
        if any(kw and kw.lower() in low for kw in keywords):
            hits.append(text)
    return hits[:3]


# ---------------------------------------------------------------------------
# as-of concept resolution / pool
# ---------------------------------------------------------------------------
def resolve_concepts(
    date8: str,
    concepts: Sequence[str],
    snapshot: Optional[Dict[str, Any]],
    sw: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    boards = (snapshot or {}).get("boards", {})
    asof = (snapshot or {}).get("date")
    name_to_codes: Dict[str, List[str]] = defaultdict(list)
    for code, info in boards.items():
        name_to_codes[info.get("name", "")].append(code)

    out: Dict[str, Dict[str, Any]] = {}
    for c in concepts:
        codes: Set[str] = set()
        source, match, detail, concept_asof = "missing", "none", "", None

        em_codes = name_to_codes.get(c, [])
        for code in em_codes:
            for row in boards.get(code, {}).get("members", []):
                codes.add(row[0])
        if codes:
            source, match, concept_asof = "dc_member", "em_exact", asof
            detail = ",".join(sorted(em_codes))
        else:
            sw_codes = sw_codes_at(c, date8, sw)
            if sw_codes:
                codes = sw_codes
                source, match, concept_asof = "sw_current_in_date", "sw_exact", date8
                detail = "in_date<=" + date8

        if not codes:
            for alias in EM_ALIASES.get(c, []):
                alias_codes = name_to_codes.get(alias, [])
                for code in alias_codes:
                    for row in boards.get(code, {}).get("members", []):
                        codes.add(row[0])
                if codes:
                    source, match, concept_asof = "dc_member", "em_alias", asof
                    detail = "%s -> %s" % (c, alias)
                    break

        if not codes:
            for alias in SW_ALIASES.get(c, []):
                sw_codes = sw_codes_at(alias, date8, sw)
                if sw_codes:
                    codes = sw_codes
                    source, match, concept_asof = "sw_current_in_date", "sw_alias", date8
                    detail = "%s -> %s" % (c, alias)
                    break

        out[c] = {
            "codes": codes,
            "source": source,
            "match": match,
            "asof": concept_asof,
            "detail": detail,
            "n": len(codes),
        }
    return out


def build_pool(concept_members: Dict[str, Dict[str, Any]], mv: Dict[str, float]) -> List[str]:
    big: Set[str] = set()
    for info in concept_members.values():
        big.update(code for code in info["codes"] if mv.get(code, 0) > 0)
    mv_sorted = sorted(big, key=lambda c: (-mv.get(c, 0.0), c))[:POOL_MV_N]
    extras: List[str] = []
    for info in concept_members.values():
        for code in sorted(
            (c for c in info["codes"] if mv.get(c, 0) > 0),
            key=lambda c: (-mv.get(c, 0.0), c),
        )[:POOL_CONCEPT_N]:
            if code not in extras:
                extras.append(code)
    pool = list(dict.fromkeys(mv_sorted + extras))
    pool.sort(key=lambda c: (-mv.get(c, 0.0), c))
    return pool


def compute_date_pools(
    date8: str,
    seed: Dict[str, List[Dict[str, Any]]],
    snapshot: Optional[Dict[str, Any]],
    sw: Dict[str, List[Dict[str, Any]]],
    mv: Dict[str, float],
) -> Dict[str, Any]:
    concepts = seed_concepts(seed)
    resolved = resolve_concepts(date8, concepts, snapshot, sw)
    names: Dict[str, str] = {}
    for info in (snapshot or {}).get("boards", {}).values():
        for code, name in info.get("members", []):
            names.setdefault(code, name)

    kw_by_label = {
        (seg.get("label"), seg.get("role")): list(seg.get("kw", []))
        for segs in seed.values() for seg in segs
    }
    segments: Dict[str, Any] = {}
    for theme, segs in seed.items():
        for seg in segs:
            cm = {c: resolved[c] for c in seg.get("concepts", []) if c in resolved}
            pool = build_pool(cm, mv)
            key = theme + "|" + str(seg.get("label") or "")
            segments[key] = {
                "theme": theme,
                "role": seg.get("role"),
                "label": seg.get("label"),
                "concepts": list(seg.get("concepts", [])),
                "concept_members": cm,
                "pool": pool,
                "kw": kw_by_label.get((seg.get("label"), seg.get("role")), []),
            }
    return {"date": date8, "concepts": resolved, "segments": segments, "names": names}


def compute_labels(
    date8: str,
    pools: Dict[str, Any],
    fina_by_code: Dict[str, Dict[str, Any]],
    mv: Dict[str, float],
    allow_same_day_ann: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    labels: List[Dict[str, Any]] = []
    coverage: List[Dict[str, Any]] = []
    names = pools.get("names") or {}

    for key, seg in pools["segments"].items():
        cm = seg["concept_members"]
        missing = [c for c in seg["concepts"] if not cm.get(c, {}).get("codes")]
        verified: List[Dict[str, Any]] = []
        for code in seg["pool"]:
            period = select_asof_period(fina_by_code.get(code) or {}, date8, allow_same_day_ann)
            hits = fina_hits(period, seg.get("kw", []))
            if not hits:
                continue
            srcs, asofs = set(), []
            for info in cm.values():
                if code in info["codes"]:
                    srcs.add(info["source"])
                    if info.get("asof"):
                        asofs.append(info["asof"])
            verified.append({
                "date": date8,
                "theme": seg["theme"],
                "segment_role": seg.get("role") or "",
                "segment_label": seg.get("label") or "",
                "ts_code": code,
                "name": names.get(code, ""),
                "total_mv_wan": round(mv.get(code, 0.0), 2),
                "membership_sources": ";".join(sorted(srcs)),
                "membership_asof": max(asofs) if asofs else "",
                "fina_period": period.get("end_date") if period else "",
                "fina_ann_date": period.get("ann_date") if period else "",
                "fina_hits": ";".join(hits),
                "source": "rule_mv",
                "method": "pit_rules_mv_fina",
            })

        verified.sort(key=lambda r: (-r["total_mv_wan"], r["ts_code"]))
        top = verified[: LEADER_TOP_N + SECOND_TOP_N]
        for i, row in enumerate(top, start=1):
            row["rank"] = i
            row["bucket"] = "leader" if i <= LEADER_TOP_N else "second"
            row["is_leader"] = 1 if i <= LEADER_TOP_N else 0
            row["is_second"] = 1 if i > LEADER_TOP_N else 0
            labels.append(row)

        coverage.append({
            "date": date8,
            "theme": seg["theme"],
            "segment_role": seg.get("role") or "",
            "segment_label": seg.get("label") or "",
            "n_concepts": len(seg["concepts"]),
            "n_concepts_with_membership": len(seg["concepts"]) - len(missing),
            "n_concepts_missing": len(missing),
            "missing_concepts": ";".join(missing),
            "n_pool": len(seg["pool"]),
            "n_verified": len(verified),
            "n_leaders": sum(1 for r in top if r["bucket"] == "leader"),
            "n_seconds": sum(1 for r in top if r["bucket"] == "second"),
            "fina_periods_used": ";".join(sorted({str(r["fina_period"]) for r in top if r.get("fina_period")})),
            "membership_sources_used": ";".join(sorted({s for r in top for s in str(r.get("membership_sources") or "").split(";") if s})),
        })
    return labels, coverage


# ---------------------------------------------------------------------------
# leak validation
# ---------------------------------------------------------------------------
def run_leak_tests(
    labels: Sequence[Dict[str, Any]],
    concept_rows: Sequence[Dict[str, Any]],
    pools: Dict[str, Any],
    fina_by_code: Dict[str, Dict[str, Any]],
    allow_same_day_ann: bool = False,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    bad = []
    for row in labels:
        d = row["date"]
        if row.get("membership_asof") and row["membership_asof"] > d:
            bad.append({"date": d, "ts_code": row["ts_code"], "field": "membership_asof"})
        ann = row.get("fina_ann_date")
        if ann:
            usable = ann <= d if allow_same_day_ann else ann < d
            if not usable:
                bad.append({"date": d, "ts_code": row["ts_code"], "field": "fina_ann_date"})
    checks.append({"name": "label_asof_fields_not_after_date", "passed": not bad,
                   "n_violations": len(bad), "sample": bad[:20]})

    pool_bad = []
    for row in labels:
        dp = pools.get(row["date"])
        seg = (dp or {}).get("segments", {}).get(row["theme"] + "|" + row["segment_label"])
        if not seg or row["ts_code"] not in seg["pool"]:
            pool_bad.append({"date": row["date"], "ts_code": row["ts_code"],
                             "segment": row["segment_label"]})
    checks.append({"name": "labels_come_from_date_pool", "passed": not pool_bad,
                   "n_violations": len(pool_bad), "sample": pool_bad[:20]})

    concept_bad = [
        {"date": r["date"], "concept": r["concept"], "asof": r["asof"]}
        for r in concept_rows if r.get("asof") and r["asof"] > r["date"]
    ]
    checks.append({"name": "concept_asof_not_after_date", "passed": not concept_bad,
                   "n_violations": len(concept_bad), "sample": concept_bad[:20]})

    fina_bad = []
    for row in labels:
        period = ((fina_by_code.get(row["ts_code"]) or {}).get("periods") or {}).get(
            str(row.get("fina_period")))
        if not period:
            continue
        ann = period.get("ann_date")
        if not ann:
            fina_bad.append({"date": row["date"], "ts_code": row["ts_code"], "field": "missing_ann"})
        elif ann > row["date"] or (ann == row["date"] and not allow_same_day_ann):
            fina_bad.append({"date": row["date"], "ts_code": row["ts_code"], "ann": ann})
    checks.append({"name": "fina_period_announced_before_date", "passed": not fina_bad,
                   "n_violations": len(fina_bad), "sample": fina_bad[:20]})

    return {
        "generated_at": now(),
        "passed": all(c["passed"] for c in checks),
        "n_labels": len(labels),
        "checks": checks,
        "policy": {
            "membership_asof": "<= label date",
            "fina_ann_date": "< label date" if not allow_same_day_ann else "<= label date",
            "mv_date": "== label date (fallback source recorded in manifest)",
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20250101")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--freq", choices=["weekly", "monthly", "daily"], default="weekly")
    parser.add_argument("--dates", default="")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--reuse-cache", action="store_true", default=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-same-day-ann", action="store_true")
    parser.add_argument("--limit-dates", type=int, default=0)
    parser.add_argument("--fina-workers", type=int, default=4)
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    today = datetime.now().strftime("%Y%m%d")
    if args.end > today:
        args.end = today

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    seed = load_seed()
    concepts = seed_concepts(seed)
    needed = set(concepts)
    for vals in EM_ALIASES.values():
        needed.update(vals)
    for vals in SW_ALIASES.values():
        needed.update(vals)

    if args.dates:
        dates = sorted({d for d in (norm_date(x) for x in args.dates.split(",")) if d})
    else:
        cal = fetch_trade_calendar(args.start, args.end, offline=args.offline)
        dates = weekly_dates(cal) if args.freq == "weekly" else (
            monthly_dates(cal) if args.freq == "monthly" else cal)
    if args.limit_dates:
        dates = dates[: args.limit_dates]
    if not dates:
        print("[error] no snapshot dates", flush=True)
        return 2

    print("[pit] dates=%d %s..%s freq=%s" % (len(dates), dates[0], dates[-1], args.freq), flush=True)

    sw = load_sw_members()
    mv_by_date: Dict[str, Dict[str, float]] = {}
    mv_sources: Dict[str, str] = {}
    snapshots: Dict[str, Optional[Dict[str, Any]]] = {}
    pools: Dict[str, Any] = {}
    blockers: List[Dict[str, Any]] = []
    concept_rows: List[Dict[str, Any]] = []
    last_snapshot: Optional[Dict[str, Any]] = None

    for idx, d in enumerate(dates, start=1):
        t0 = time.time()
        snap = fetch_membership_snapshot(d, needed, offline=args.offline, force=args.force)
        if snap is None:
            if last_snapshot is not None:
                snap = dict(last_snapshot)
                snap["_forward_filled_from"] = last_snapshot.get("date")
                blockers.append({"kind": "membership_forward_fill", "date": d,
                                 "using": last_snapshot.get("date"),
                                 "note": "no exact dc_member snapshot; prior snapshot used as-of"})
            else:
                blockers.append({"kind": "membership_missing", "date": d,
                                 "note": "no dc_member snapshot; date skipped"})
                print("[pit] %d/%d %s membership MISSING (skip)" % (idx, len(dates), d), flush=True)
                continue
        else:
            last_snapshot = snap
        snapshots[d] = snap

        mv_payload = fetch_mv(d, offline=args.offline, force=args.force)
        mv = mv_payload.get("mv") or {}
        mv_by_date[d] = mv
        mv_sources[d] = mv_payload.get("source", "")
        if len(mv) < MIN_MV_ROWS:
            blockers.append({"kind": "mv_coverage_low", "date": d, "n_codes": len(mv),
                             "source": mv_sources[d]})

        dp = compute_date_pools(d, seed, snap, sw, mv)
        pools[d] = dp
        for c in concepts:
            info = dp["concepts"].get(c) or {}
            concept_rows.append({
                "date": d, "concept": c,
                "source": info.get("source", "missing"),
                "match": info.get("match", "none"),
                "asof": info.get("asof") or "",
                "n_members": info.get("n", 0),
                "detail": info.get("detail", ""),
            })
        q = snap.get("quality")
        if q is not None and q < MIN_SNAPSHOT_QUALITY:
            blockers.append({"kind": "membership_snapshot_sparse", "date": d,
                             "quality": q, "n_boards": snap.get("n_boards"),
                             "n_nonempty": snap.get("n_nonempty_boards")})
        print("[pit] %d/%d %s membership=%s q=%s mv=%d(%s) %.1fs"
              % (idx, len(dates), d, snap.get("date"), q, len(mv), mv_sources[d],
                 time.time() - t0), flush=True)

    codes: Set[str] = set()
    for dp in pools.values():
        for seg in dp["segments"].values():
            codes.update(seg["pool"])
    print("[pit] unique candidate stocks=%d; fetching financials..." % len(codes), flush=True)

    fina_by_code: Dict[str, Dict[str, Any]] = {}
    missing_fina: List[str] = []
    ordered = sorted(codes)
    workers = max(1, int(args.fina_workers or 1))
    if workers == 1:
        for i, code in enumerate(ordered, start=1):
            payload = fetch_fina(code, args.offline, args.force)
            fina_by_code[code] = payload
            if payload.get("error") and not payload.get("periods"):
                missing_fina.append(code)
            if i % 100 == 0:
                print("[pit] fina %d/%d" % (i, len(ordered)), flush=True)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {ex.submit(fetch_fina, code, args.offline, args.force): code
                       for code in ordered}
            for i, fut in enumerate(as_completed(fut_map), start=1):
                code = fut_map[fut]
                try:
                    payload = fut.result()
                except Exception as exc:
                    payload = {"ts_code": code, "periods": {},
                               "error": "thread:" + str(exc)[:160]}
                fina_by_code[code] = payload
                if payload.get("error") and not payload.get("periods"):
                    missing_fina.append(code)
                if i % 200 == 0:
                    print("[pit] fina %d/%d" % (i, len(ordered)), flush=True)

    labels: List[Dict[str, Any]] = []
    coverage: List[Dict[str, Any]] = []
    for d in sorted(pools):
        lrows, crows = compute_labels(
            d, pools[d], fina_by_code, mv_by_date[d],
            allow_same_day_ann=args.allow_same_day_ann,
        )
        labels.extend(lrows)
        coverage.extend(crows)

    labels.sort(key=lambda r: (r["date"], r["theme"], r["segment_label"], r["rank"]))
    coverage.sort(key=lambda r: (r["date"], r["theme"], r["segment_label"]))
    concept_rows.sort(key=lambda r: (r["date"], r["concept"]))

    label_fields = [
        "date", "theme", "segment_role", "segment_label", "rank", "bucket",
        "is_leader", "is_second", "ts_code", "name", "total_mv_wan",
        "source", "method", "membership_sources", "membership_asof",
        "fina_period", "fina_ann_date", "fina_hits",
    ]
    coverage_fields = [
        "date", "theme", "segment_role", "segment_label", "n_concepts",
        "n_concepts_with_membership", "n_concepts_missing", "missing_concepts",
        "n_pool", "n_verified", "n_leaders", "n_seconds",
        "fina_periods_used", "membership_sources_used",
    ]
    concept_fields = ["date", "concept", "source", "match", "asof", "n_members", "detail"]

    labels_path = out_dir / ("labels_%s.csv" % args.freq)
    coverage_path = out_dir / ("coverage_%s.csv" % args.freq)
    concepts_path = out_dir / ("concept_coverage_%s.csv" % args.freq)
    write_csv(labels_path, labels, label_fields)
    write_csv(coverage_path, coverage, coverage_fields)
    write_csv(concepts_path, concept_rows, concept_fields)

    leak = run_leak_tests(labels, concept_rows, pools, fina_by_code,
                          allow_same_day_ann=args.allow_same_day_ann)

    by_period: Dict[str, Dict[str, int]] = {}
    for r in labels:
        p = str(r["date"])[:6]
        by_period.setdefault(p, {"labels": 0, "leaders": 0, "seconds": 0})
        by_period[p]["labels"] += 1
        by_period[p]["leaders"] += int(r.get("is_leader") or 0)
        by_period[p]["seconds"] += int(r.get("is_second") or 0)

    manifest = {
        "generated_at": now(),
        "builder": "jobs/build_pit_chain_map.py",
        "date_range_requested": [args.start, args.end],
        "snapshot_frequency": args.freq,
        "snapshot_dates": sorted(pools),
        "n_snapshot_dates": len(pools),
        "n_themes": len(seed),
        "n_segments": sum(len(v) for v in seed.values()),
        "n_seed_concepts": len(concepts),
        "rows": {"labels": len(labels), "coverage": len(coverage),
                 "concept_coverage": len(concept_rows)},
        "n_unique_candidate_stocks": len(codes),
        "n_stocks_with_fina": sum(1 for p in fina_by_code.values() if p.get("periods")),
        "mv_sources": dict(Counter(mv_sources.values())),
        "membership_snapshot_dates": {d: (s or {}).get("date") for d, s in snapshots.items()},
        "membership_quality": {d: (s or {}).get("quality") for d, s in snapshots.items()},
        "labels_by_month": by_period,
        "blockers": blockers,
        "missing_fina_count": len(missing_fina),
        "missing_fina_sample": missing_fina[:50],
        "leak_tests": leak,
        "policy": {
            "no_future_membership": True,
            "no_future_financials": not args.allow_same_day_ann,
            "no_ai_layer": True,
            "daily_exact_membership_claimed": False,
        },
        "notes": [
            "Rule layer only (rule_mv). Cached AI verdicts have no as-of metadata and are not used.",
            "fina_mainbz has semi-annual/annual periods only; Q1/Q3 are intentionally absent.",
            "SW fallback filters current members by in_date <= date; removed members are absent.",
            "Membership snapshots are weekly; a date never borrows a later snapshot.",
        ],
    }
    json_dump(out_dir / "manifest.json", manifest)
    json_dump(out_dir / "leak_tests.json", leak)

    print("[pit] wrote %s (%d rows)" % (labels_path, len(labels)), flush=True)
    print("[pit] wrote %s (%d rows)" % (coverage_path, len(coverage)), flush=True)
    print("[pit] wrote %s (%d rows)" % (concepts_path, len(concept_rows)), flush=True)
    print("[pit] wrote %s" % (out_dir / "manifest.json"), flush=True)
    print("[pit] leak tests passed=%s" % leak.get("passed"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
