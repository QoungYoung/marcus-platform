# -*- coding: utf-8 -*-
"""build_pit_theme_members.py — PIT（point-in-time）主题成员历史构建器。

背景
====
core P1 需要“某个历史日期上，某主题由哪些股票构成”。生产里长期用的是
`stock_pool.db::stock_concept_map` 的**当期快照**；把它套到历史日期上 = 未来函数。
本脚本只输出**有日期证据**的成员，并把“当期快照”单独落一个显式 `pit=false` 的文件，
绝不混进 as-of 结果。

数据源与口径
============
1. `data/concept_hist.json`（真 PIT，稀疏）
   - 每个概念每天一行，`leader` 字段来自 Tushare `moneyflow_ind_dc(...).buy_sm_amount_stock`
     = 当天该概念里“小单买入金额最大”的股票。
   - 一只股票能出现在某概念当天的 leader 里，就**证明它当天属于该概念**（成分股）。
   - 这是“每天每概念 1 只”的稀疏证据，不是完整成分表；`leader` 在生成端做过前值填充，
     所以本脚本标记 `fresh` / `ffill_repeat`，并给 `source_date`（该 leader 首次出现的日期）。
   - 覆盖：2025-08-20 ~ 2026-08-31（250 个交易日，521 个概念）。
2. `data/chain_map_YYYYMMDD.json`（半 PIT，日期化产物）
   - 文件自带 `date`、`mv_date`、`themes[].segments[].candidates / leading_verified /
     verified_all / ai_promoted`。
   - 但这些成员是构建时用 `stock_pool.db::stock_concept_map` 现查的；如果文件日期早于
     本地 DB 快照时间，就会把“未来才有的成分”带回历史。
   - 因此默认 `--snapshot-policy strict`：只使用日期 >= DB 文件 mtime（2026-09-16）的
     chain_map；更早的日期一律拒收并在 manifest 里列出。
3. `data/stock_pool.db`（当期快照，非 PIT）
   - 只用于：股票名 -> ts_code 映射、当前快照文件（显式 `pit=false`）。
   - 不参与任何 as-of 成员断言。

主题映射
========
- 父主题：`apps/main_line/fusion_mainline.py::THEME_CONCEPTS`（14 个，精确概念名）。
- 子主题：`backend/app/services/wolf_card/subthemes.py::SUBTEMES`（49 个，含父主题）。
- 只用精确名 + 少量“同一概念的写法变体”别名（空格、Ⅱ/Ⅲ、`AI 芯片` 等）；不做关键词扩散。

输出（默认 `data/pit/theme_members/`）
=====================================
- `observations.csv`            每条 = 某日/某主题/某股票/某概念的一条证据
- `coverage.csv`                主题 × 日期的成员数（含 0，显式暴露缺口）
- `theme_summary.csv`           主题级首末日期、成员数分布、概念覆盖
- `current_snapshot_not_pit.csv` 当期快照（`pit=false`，禁止用于历史回测）
- `unmatched_leaders.csv`       leader 名字无法映射到当前股票池的清单
- `manifest.json`               输入哈希、口径、覆盖、已知缺口/偏差、校验
- `validation.json`             leak tests 明细

用法
====
    python jobs/build_pit_theme_members.py
    python jobs/build_pit_theme_members.py --snapshot-policy content   # 放宽 chain_map 门槛（有风险）
    python jobs/build_pit_theme_members.py --asof 20250820 --theme 半导体/芯片
    python jobs/build_pit_theme_members.py --out-dir /tmp/pit_theme_members
"""
from __future__ import annotations

import argparse
import ast
import csv
import glob
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_OUT_DIR = os.path.join(DEFAULT_DATA_DIR, "pit", "theme_members")
BUILDER_VERSION = "1.0"

LEVEL_PARENT = "parent"
LEVEL_SUBTHEME = "subtheme"
OBS_FRESH = "fresh"
OBS_FFILL = "ffill_repeat"
OBS_DATED = "dated_artifact"
SOURCE_CONCEPT_HIST = "concept_hist.leader"

OBS_FIELDS = [
    "as_of_date", "theme", "theme_level", "parent_theme", "ts_code", "concept_name",
    "source", "source_date", "observation_type", "quality", "notes",
]
COV_FIELDS = [
    "as_of_date", "theme", "theme_level", "parent_theme", "n_members", "n_fresh",
    "n_carried", "n_concepts", "n_sources", "status",
]
SUMMARY_FIELDS = [
    "theme", "theme_level", "parent_theme", "requested_concepts", "matched_concepts",
    "missing_concepts", "first_date", "last_date", "n_dates_with_members",
    "n_dates_fresh", "n_unique_stocks", "min_members", "median_members",
    "max_members", "sources",
]
SNAPSHOT_FIELDS = [
    "theme", "theme_level", "parent_theme", "ts_code", "concepts",
    "snapshot_effective", "pit", "note",
]
UNMATCHED_FIELDS = [
    "leader_name", "count", "normalized", "reason", "example_date", "example_concept",
]

# 同一概念的“写法变体”别名。只收录明确同义/仅写法差异的项，不做语义扩散。
CONCEPT_ALIASES: Dict[str, str] = {
    "AI 芯片": "AI芯片",
    "AIGC 概念": "AIGC概念",
    "CPO 概念": "CPO概念",
    "PCB 概念": "PCB",
    "光刻机": "光刻机(胶)",
    "锂电池概念": "锂电池",
    "储能概念": "储能",
    "医疗器械": "医疗器械概念",
    "黄金": "黄金概念",
    "稀土": "稀土永磁",
    "白酒Ⅱ": "白酒",
    "白酒Ⅲ": "白酒",
    "白酒概念": "白酒",
    "中药Ⅱ": "中药概念",
    "中药Ⅲ": "中药概念",
    "游戏Ⅱ": "网络游戏",
    "游戏Ⅲ": "网络游戏",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


def norm_text(s: Any) -> str:
    """NFKC + 去空白 + 大写；用于概念名/股票名的宽松比较。"""
    return re.sub(r"[\s\u3000]+", "", unicodedata.normalize("NFKC", str(s or ""))).strip().upper()


def norm_name(s: Any) -> str:
    """股票简称归一：全角转半角、去空白、去 ST/XD/XR/DR/N/C 前缀、去 -U/-W 等后缀。"""
    x = norm_text(s)
    # 只在前缀后紧跟中文时剥离，避免误伤 "C..." 之类英文名。
    x = re.sub(r"^(?:\*ST|ST|XD|XR|DR|N|C)(?=[\u4e00-\u9fff])", "", x)
    x = re.sub(r"^(?:\*ST|ST|XD|XR|DR|N|C)(?=[\u4e00-\u9fff])", "", x)
    x = re.sub(r"-(?:U|W|UW|D|N)$", "", x)
    return x


def valid_ts(ts: Any) -> bool:
    return bool(re.match(r"^\d{6}\.(SH|SZ|BJ)$", str(ts or "")))


def parse_date8(v: Any) -> Optional[str]:
    """把 2026-08-03 / 2026-08-03T08:00:03 / 20260803 统一成 YYYYMMDD。"""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    if not m:
        return None
    return m.group(1) + m.group(2) + m.group(3)


def day_diff(a: str, b: str) -> int:
    """a - b，单位自然日；a/b 为 YYYYMMDD。"""
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return (da - db).days


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_files(paths: Sequence[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(os.path.basename(p).encode("utf-8"))
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def write_csv(path: str, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 主题映射
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ThemeSpec:
    name: str
    level: str
    parent: str
    requested: Tuple[str, ...]
    matched: Tuple[str, ...]
    missing: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme": self.name,
            "theme_level": self.level,
            "parent_theme": self.parent,
            "requested_concepts": list(self.requested),
            "matched_concepts": list(self.matched),
            "missing_concepts": list(self.missing),
        }


def load_subthemes(path: str) -> Dict[str, Tuple[str, List[str]]]:
    spec = importlib.util.spec_from_file_location("_pit_theme_subthemes", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load subthemes: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = getattr(mod, "SUBTEMES", None)
    if not isinstance(data, dict):
        raise RuntimeError("SUBTEMES not found in %s" % path)
    out: Dict[str, Tuple[str, List[str]]] = {}
    for k, v in data.items():
        parent, concepts = v
        out[str(k)] = (str(parent), [str(x) for x in concepts])
    return out


def extract_dict_assignment(path: str, name: str) -> Optional[Dict[str, Any]]:
    """用 AST 取模块级 dict 字面量，避免 import 生产模块的副作用。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    val = ast.literal_eval(node.value)
                    if isinstance(val, dict):
                        return val
    except Exception as exc:  # noqa: BLE001 - 映射读取失败时调用方回退
        log("[warn] extract %s from %s failed: %s: %s" % (name, path, type(exc).__name__, exc))
    return None


def resolve_concept_names(
    requested: Sequence[str],
    available: Set[str],
    aliases: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[str], List[Tuple[str, str]]]:
    """把配置里的概念名解析到 concept_hist 的真实概念名。

    顺序：精确 -> 别名 -> NFKC/去空白归一。找不到就进 missing，绝不猜。
    """
    aliases = aliases or CONCEPT_ALIASES
    matched: List[str] = []
    missing: List[str] = []
    alias_used: List[Tuple[str, str]] = []
    by_norm: Dict[str, List[str]] = defaultdict(list)
    for x in available:
        by_norm[norm_text(x)].append(x)

    def add(x: str) -> None:
        if x not in matched:
            matched.append(x)

    for w in requested:
        if w in available:
            add(w)
            continue
        if w in aliases and aliases[w] in available:
            add(aliases[w])
            alias_used.append((w, aliases[w]))
            continue
        hits = by_norm.get(norm_text(w)) or []
        if len(hits) == 1:
            add(hits[0])
            if hits[0] != w:
                alias_used.append((w, hits[0]))
        elif len(hits) > 1:
            for h in sorted(hits):
                add(h)
            alias_used.append((w, "|".join(sorted(hits))))
        else:
            missing.append(w)
    return sorted(matched), sorted(set(missing)), alias_used


def build_theme_specs(
    hist_names: Set[str],
    parent_concepts: Dict[str, Sequence[str]],
    subthemes: Dict[str, Tuple[str, List[str]]],
) -> Tuple[List[ThemeSpec], Dict[str, Any]]:
    specs: List[ThemeSpec] = []
    alias_rows: List[Dict[str, Any]] = []

    for theme, wanted in sorted(parent_concepts.items()):
        wanted_l = sorted({str(x) for x in wanted})
        matched, missing, alias_used = resolve_concept_names(wanted_l, hist_names)
        specs.append(ThemeSpec(
            name=str(theme), level=LEVEL_PARENT, parent=str(theme),
            requested=tuple(wanted_l), matched=tuple(matched), missing=tuple(missing),
        ))
        for a, b in alias_used:
            alias_rows.append({"theme": str(theme), "level": LEVEL_PARENT, "requested": a, "resolved": b})
        if missing:
            alias_rows.append({"theme": str(theme), "level": LEVEL_PARENT, "missing": missing})

    for sub, (parent, wanted) in sorted(subthemes.items()):
        wanted_l = sorted({str(x) for x in wanted})
        matched, missing, alias_used = resolve_concept_names(wanted_l, hist_names)
        specs.append(ThemeSpec(
            name=str(sub), level=LEVEL_SUBTHEME, parent=str(parent),
            requested=tuple(wanted_l), matched=tuple(matched), missing=tuple(missing),
        ))
        for a, b in alias_used:
            alias_rows.append({"theme": str(sub), "level": LEVEL_SUBTHEME, "requested": a, "resolved": b})
        if missing:
            alias_rows.append({"theme": str(sub), "level": LEVEL_SUBTHEME, "missing": missing})

    return specs, {"aliases": alias_rows}


def load_parent_concepts(repo_root: str, subthemes: Dict[str, Tuple[str, List[str]]]) -> Tuple[Dict[str, List[str]], str]:
    fusion_path = os.path.join(repo_root, "apps", "main_line", "fusion_mainline.py")
    data = extract_dict_assignment(fusion_path, "THEME_CONCEPTS")
    if data:
        out = {str(k): [str(x) for x in v] for k, v in data.items()}
        return out, fusion_path
    # 回退：只按子主题的父主题并集，覆盖会变窄，但口径可解释。
    out: Dict[str, List[str]] = defaultdict(list)
    for _sub, (parent, concepts) in subthemes.items():
        out[parent].extend(concepts)
    return {k: sorted(set(v)) for k, v in out.items()}, "fallback:subthemes.parent"


# ---------------------------------------------------------------------------
# 股票名 -> ts_code
# ---------------------------------------------------------------------------
def load_name_map(db_path: str, sw_path: Optional[str] = None) -> Tuple[Dict[str, str], Dict[str, List[str]], Dict[str, Any]]:
    """返回 (唯一映射, 歧义映射, 统计)。只使用当前股票池/申万缓存里的名字。"""
    pairs: List[Tuple[str, str, str]] = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            for ts, name in con.execute("select ts_code, name from stock_pool"):
                if ts and name:
                    pairs.append((str(ts), str(name), "stock_pool"))
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        log("[warn] stock_pool name map unavailable: %s: %s" % (type(exc).__name__, exc))

    if sw_path and os.path.exists(sw_path):
        try:
            for row in load_json(sw_path):
                ts = row.get("ts_code")
                nm = row.get("name")
                if ts and nm:
                    pairs.append((str(ts), str(nm), "sw_members_cache"))
        except Exception as exc:  # noqa: BLE001
            log("[warn] sw_members_cache unavailable: %s: %s" % (type(exc).__name__, exc))

    by_key: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for ts, name, src in pairs:
        if not valid_ts(ts):
            continue
        keys = {norm_name(name), norm_text(name), str(name).strip()}
        keys = {k for k in keys if k}
        for k in keys:
            by_key[k][ts].add(src)

    name_map: Dict[str, str] = {}
    ambiguous: Dict[str, List[str]] = {}
    for key, tss in by_key.items():
        if len(tss) == 1:
            name_map[key] = next(iter(tss))
        else:
            # 同名多票：优先 stock_pool，仍不唯一则判定歧义并跳过。
            pool_only = [ts for ts, srcs in tss.items() if "stock_pool" in srcs]
            if len(pool_only) == 1:
                name_map[key] = pool_only[0]
            else:
                ambiguous[key] = sorted(tss)

    stats = {
        "source_rows": len(pairs),
        "unique_keys": len(name_map),
        "ambiguous_keys": len(ambiguous),
        "ambiguous_examples": {k: v for k, v in sorted(ambiguous.items())[:30]},
    }
    return name_map, ambiguous, stats


def map_leader_name(name: Any, name_map: Dict[str, str], ambiguous: Dict[str, List[str]]) -> Tuple[Optional[str], str]:
    raw = str(name or "").strip()
    if not raw or raw == "-" or raw.upper() in {"NAN", "NONE"}:
        return None, "empty_or_placeholder"
    for key in (norm_name(raw), norm_text(raw), raw):
        if key in name_map:
            return name_map[key], ""
        if key in ambiguous:
            return None, "ambiguous:" + ",".join(ambiguous[key][:4])
    return None, "not_in_current_stock_pool"


# ---------------------------------------------------------------------------
# concept_hist -> 观测
# ---------------------------------------------------------------------------
def iter_leader_runs(dates: Sequence[str], leaders: Sequence[Any]) -> Iterable[Tuple[str, str, bool, str, int]]:
    """逐日产出 (date, leader, fresh, run_start, run_len)。

    concept_hist 的 leader 在生成端做过 ffill；同一名字连续出现时，只有第一天是
    “当天新观测”，后续标记为 ffill_repeat，并把 source_date 指回 run_start。
    """
    prev: Optional[str] = None
    run_start = ""
    run_len = 0
    for d, lead in zip(dates, leaders):
        cur = str(lead or "").strip()
        if cur != prev:
            prev = cur
            run_start = str(d)
            run_len = 1
            fresh = True
        else:
            run_len += 1
            fresh = False
        yield str(d), cur, fresh, run_start, run_len


def _quality(fresh: bool, run_len: int, coverage_ratio: float) -> str:
    if fresh:
        q = "high"
    elif run_len <= 5:
        q = "medium"
    else:
        q = "low"
    if coverage_ratio < 0.5 and q == "high":
        q = "medium"
    if coverage_ratio < 0.25:
        q = "low"
    return q


def build_concept_hist_rows(
    hist: Dict[str, Dict[str, Any]],
    specs: Sequence[ThemeSpec],
    name_map: Dict[str, str],
    ambiguous: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Counter]:
    byname: Dict[str, Dict[str, Any]] = {}
    for code, a in hist.items():
        if not isinstance(a, dict):
            continue
        nm = str(a.get("name") or "")
        if nm:
            byname[nm] = dict(a, _code=str(code))

    rows: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    unmatched: Dict[str, Dict[str, Any]] = {}
    matched_counter: Counter = Counter()

    for spec in specs:
        for cname in spec.matched:
            a = byname.get(cname)
            if not a:
                continue
            matched_counter[spec.name] += 1
            dates = [str(x) for x in (a.get("dates") or [])]
            leaders = list(a.get("leader") or [])
            n_dates = len(dates)
            cov_days = a.get("coverage")
            if cov_days is None:
                cov_days = sum(1 for x in (a.get("close") or []) if x is not None)
            try:
                cov_days = int(cov_days)
            except Exception:  # noqa: BLE001
                cov_days = 0
            cov_ratio = (cov_days / n_dates) if n_dates else 0.0
            code = str(a.get("_code") or "")

            for d, leader, fresh, run_start, run_len in iter_leader_runs(dates, leaders):
                ts, reason = map_leader_name(leader, name_map, ambiguous)
                if not ts:
                    if leader and leader not in {"-"}:
                        rec = unmatched.setdefault(leader, {
                            "count": 0, "reason": reason, "first_date": d, "first_concept": cname,
                        })
                        rec["count"] += 1
                    continue
                key = (d, spec.name, ts, cname, spec.level)
                if key in rows:
                    continue
                q = _quality(fresh, run_len, cov_ratio)
                rows[key] = {
                    "as_of_date": d,
                    "theme": spec.name,
                    "theme_level": spec.level,
                    "parent_theme": spec.parent,
                    "ts_code": ts,
                    "concept_name": cname,
                    "source": SOURCE_CONCEPT_HIST,
                    "source_date": run_start,
                    "observation_type": OBS_FRESH if fresh else OBS_FFILL,
                    "quality": q,
                    "notes": "concept_coverage=%d/%d;leader_run=%d;concept_code=%s"
                             % (cov_days, n_dates, run_len, code),
                }

    out = sorted(rows.values(), key=lambda r: (
        r["as_of_date"], r["theme"], r["theme_level"], r["ts_code"], r["concept_name"],
    ))
    return out, unmatched, matched_counter


# ---------------------------------------------------------------------------
# chain_map 日期化产物 -> 观测（仅 parent 级）
# ---------------------------------------------------------------------------
def load_chain_maps(data_dir: str) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    for p in sorted(glob.glob(os.path.join(data_dir, "chain_map_2*.json"))):
        base = os.path.basename(p)
        if base.endswith("_v2_cmp.json") or "ai_verdicts" in base:
            continue
        try:
            obj = load_json(p)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict) and isinstance(obj.get("themes"), list):
            out.append((p, obj))
    return out


def build_chain_map_rows(
    chain_maps: Sequence[Tuple[str, Dict[str, Any]]],
    specs: Sequence[ThemeSpec],
    gate_date: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    parent_specs = {s.name: s for s in specs if s.level == LEVEL_PARENT}
    sub_specs = {s.name: s for s in specs if s.level == LEVEL_SUBTHEME}
    rows: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    used_dates: List[str] = []
    excluded: List[Dict[str, str]] = []
    unknown_themes: Counter = Counter()

    for path, obj in chain_maps:
        date = parse_date8(obj.get("date"))
        base = os.path.basename(path)
        if not date:
            excluded.append({"date": str(obj.get("date") or ""), "file": base, "reason": "no_date"})
            continue
        if date < gate_date:
            excluded.append({
                "date": date, "file": base,
                "reason": "date_lt_snapshot_gate(%s)" % gate_date,
            })
            continue
        used_dates.append(date)
        for th in obj.get("themes") or []:
            if not isinstance(th, dict):
                continue
            theme = str(th.get("theme") or "")
            # chain_map 的 theme 可能是父主题名，也可能是子主题名（实测有独立的“银行”）。
            # 子主题成员同时属于其父主题，所以两者都落；父主题行与既有“金融”行按 key 合并。
            targets: List[ThemeSpec] = []
            if theme in parent_specs:
                targets.append(parent_specs[theme])
            if theme in sub_specs:
                sub = sub_specs[theme]
                if sub not in targets:
                    targets.append(sub)
                if sub.parent in parent_specs and parent_specs[sub.parent] not in targets:
                    targets.append(parent_specs[sub.parent])
            if not targets:
                unknown_themes[theme] += 1
                continue
            for seg in th.get("segments") or []:
                if not isinstance(seg, dict):
                    continue
                segment_concepts = "|".join(str(x) for x in (seg.get("concepts") or []))
                # chain_map 的 candidates/verified 只说明“在该环节里”，不说明属于环节内哪个概念；
                # 用 segment:<label> 作证据 scope，避免把整段概念列表冒充个股概念。
                scope = "segment:" + (str(seg.get("label") or "") or segment_concepts or "?")
                for field in ("candidates", "leading_verified", "verified_all", "ai_promoted"):
                    for item in seg.get(field) or []:
                        if isinstance(item, str):
                            ts = item
                        elif isinstance(item, dict):
                            ts = item.get("ts") or item.get("ts_code")
                        else:
                            continue
                        ts = str(ts or "")
                        if not valid_ts(ts):
                            continue
                        for spec in targets:
                            key = (date, spec.name, ts)
                            rec = rows.setdefault(key, {
                                "as_of_date": date,
                                "theme": spec.name,
                                "theme_level": spec.level,
                                "parent_theme": spec.parent,
                                "ts_code": ts,
                                "concepts": set(),
                                "segment_concepts": set(),
                                "sources": set(),
                                "fields": set(),
                                "artifact": base,
                                "mv_date": obj.get("mv_date"),
                            })
                            rec["concepts"].add(scope)
                            if segment_concepts:
                                rec["segment_concepts"].add(segment_concepts)
                            rec["sources"].add("chain_map." + field)
                            rec["fields"].add(field)

    out: List[Dict[str, Any]] = []
    for rec in rows.values():
        out.append({
            "as_of_date": rec["as_of_date"],
            "theme": rec["theme"],
            "theme_level": rec["theme_level"],
            "parent_theme": rec["parent_theme"],
            "ts_code": rec["ts_code"],
            "concept_name": "|".join(sorted(rec["concepts"])),
            "source": "|".join(sorted(rec["sources"])),
            "source_date": rec["as_of_date"],
            "observation_type": OBS_DATED,
            "quality": "medium",
            "notes": "artifact=%s;mv_date=%s;fields=%s;segment_concepts=%s"
                     % (rec["artifact"], rec.get("mv_date"), "|".join(sorted(rec["fields"])),
                        " || ".join(sorted(rec["segment_concepts"]))),
        })
    out.sort(key=lambda r: (r["as_of_date"], r["theme"], r["ts_code"]))
    meta = {
        "used_dates": sorted(set(used_dates)),
        "excluded": excluded,
        "unknown_themes": dict(sorted(unknown_themes.items())),
    }
    return out, meta


# ---------------------------------------------------------------------------
# 覆盖 / 汇总
# ---------------------------------------------------------------------------
def build_coverage(
    rows: Sequence[Dict[str, Any]],
    all_dates: Sequence[str],
    specs: Sequence[ThemeSpec],
) -> List[Dict[str, Any]]:
    by_td: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_td[(r["theme"], r["as_of_date"])].append(r)

    out: List[Dict[str, Any]] = []
    for spec in specs:
        for d in all_dates:
            rs = by_td.get((spec.name, d), [])
            members = {r["ts_code"] for r in rs}
            fresh = {r["ts_code"] for r in rs if r["observation_type"] != OBS_FFILL}
            concepts = {r["concept_name"] for r in rs if r.get("concept_name")}
            sources = {r["source"] for r in rs if r.get("source")}
            if not members:
                status = "none"
            elif fresh:
                status = "observed"
            else:
                status = "carried_only"
            out.append({
                "as_of_date": d,
                "theme": spec.name,
                "theme_level": spec.level,
                "parent_theme": spec.parent,
                "n_members": len(members),
                "n_fresh": len(fresh),
                "n_carried": len(members - fresh),
                "n_concepts": len(concepts),
                "n_sources": len(sources),
                "status": status,
            })
    out.sort(key=lambda r: (r["as_of_date"], r["theme_level"], r["theme"]))
    return out


def build_theme_summary(
    rows: Sequence[Dict[str, Any]],
    coverage: Sequence[Dict[str, Any]],
    specs: Sequence[ThemeSpec],
) -> List[Dict[str, Any]]:
    obs_by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        obs_by_theme[r["theme"]].append(r)
    cov_by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in coverage:
        cov_by_theme[c["theme"]].append(c)

    out: List[Dict[str, Any]] = []
    for spec in specs:
        cov = cov_by_theme.get(spec.name, [])
        with_members = [c for c in cov if int(c["n_members"]) > 0]
        with_fresh = [c for c in cov if int(c["n_fresh"]) > 0]
        vals = [int(c["n_members"]) for c in with_members]
        obs = obs_by_theme.get(spec.name, [])
        sources = sorted({s for r in obs for s in str(r["source"]).split("|") if s})
        out.append({
            "theme": spec.name,
            "theme_level": spec.level,
            "parent_theme": spec.parent,
            "requested_concepts": "|".join(spec.requested),
            "matched_concepts": "|".join(spec.matched),
            "missing_concepts": "|".join(sorted(spec.missing)),
            "first_date": with_members[0]["as_of_date"] if with_members else "",
            "last_date": with_members[-1]["as_of_date"] if with_members else "",
            "n_dates_with_members": len(with_members),
            "n_dates_fresh": len(with_fresh),
            "n_unique_stocks": len({r["ts_code"] for r in obs}),
            "min_members": min(vals) if vals else 0,
            "median_members": round(float(statistics.median(vals)), 1) if vals else 0.0,
            "max_members": max(vals) if vals else 0,
            "sources": "|".join(sources),
        })
    out.sort(key=lambda r: (r["theme_level"], r["theme"]))
    return out


# ---------------------------------------------------------------------------
# 当期快照（显式非 PIT）
# ---------------------------------------------------------------------------
def build_current_snapshot(
    db_path: str,
    specs: Sequence[ThemeSpec],
    snapshot_effective: str,
) -> List[Dict[str, Any]]:
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        stock_set = {str(r[0]) for r in con.execute("select ts_code from stock_pool")}
        c2s: Dict[str, Set[str]] = defaultdict(set)
        db_concepts: Set[str] = set()
        for ts, cname in con.execute("select ts_code, concept_name from stock_concept_map"):
            if ts and cname:
                c2s[str(cname)].add(str(ts))
                db_concepts.add(str(cname))
    finally:
        con.close()

    by_norm: Dict[str, List[str]] = defaultdict(list)
    for c in db_concepts:
        by_norm[norm_text(c)].append(c)

    out: List[Dict[str, Any]] = []
    for spec in specs:
        wanted = set(spec.requested)
        resolved: Set[str] = set()
        for w in wanted:
            if w in db_concepts:
                resolved.add(w)
            elif w in CONCEPT_ALIASES and CONCEPT_ALIASES[w] in db_concepts:
                resolved.add(CONCEPT_ALIASES[w])
            else:
                hits = by_norm.get(norm_text(w)) or []
                if len(hits) == 1:
                    resolved.add(hits[0])
        ts_concepts: Dict[str, Set[str]] = defaultdict(set)
        for c in resolved:
            for ts in c2s.get(c, set()):
                if ts in stock_set:
                    ts_concepts[ts].add(c)
        for ts in sorted(ts_concepts):
            out.append({
                "theme": spec.name,
                "theme_level": spec.level,
                "parent_theme": spec.parent,
                "ts_code": ts,
                "concepts": "|".join(sorted(ts_concepts[ts])),
                "snapshot_effective": snapshot_effective,
                "pit": "false",
                "note": "current stock_concept_map snapshot only; NOT point-in-time; do not use for historical as-of",
            })
    out.sort(key=lambda r: (r["theme_level"], r["theme"], r["ts_code"]))
    return out


# ---------------------------------------------------------------------------
# DB 元信息 / 输入
# ---------------------------------------------------------------------------
def read_db_meta(db_path: str) -> Dict[str, Any]:
    updated: List[str] = []
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        tables = {str(r[0]) for r in con.execute(
            "select name from sqlite_master where type='table'"
        )}
        for table in ("stock_pool", "sectors", "concept_sectors"):
            if table not in tables:
                continue
            try:
                row = con.execute('select max(updated_at) from "%s"' % table).fetchone()
            except Exception:  # noqa: BLE001
                continue
            d = parse_date8(row[0]) if row and row[0] else None
            if d:
                updated.append(d)
        counts = {}
        if "stock_pool" in tables:
            counts["stock_pool_rows"] = int(con.execute("select count(*) from stock_pool").fetchone()[0])
        if "stock_concept_map" in tables:
            counts["stock_concept_map_rows"] = int(con.execute("select count(*) from stock_concept_map").fetchone()[0])
            counts["concept_count"] = int(con.execute(
                "select count(distinct concept_name) from stock_concept_map"
            ).fetchone()[0])
    finally:
        con.close()

    # 用**本地时区**：DB 文件 mtime 是本地交易/同步时间；用 UTC 会把 2026-09-16 07:52
    # 变成 2026-09-15 23:52，导致严格门槛少一天（实测踩到）。
    file_date = datetime.fromtimestamp(os.path.getmtime(db_path)).strftime("%Y%m%d")
    content_date = max(updated) if updated else file_date
    return {
        "path": db_path,
        "file_date": file_date,
        "content_date": content_date,
        "max_updated_at": max(updated) if updated else None,
        "updated_at_dates": sorted(set(updated)),
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _check(checks: Dict[str, Any], name: str, ok: bool, detail: Any = "") -> None:
    checks[name] = {"passed": bool(ok), "detail": detail}


def validate_outputs(
    out_dir: str,
    specs: Sequence[ThemeSpec],
    gate_date: str,
    snapshot_policy: str,
    expected: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    obs = read_csv(os.path.join(out_dir, "observations.csv"))
    cov = read_csv(os.path.join(out_dir, "coverage.csv"))
    snap = read_csv(os.path.join(out_dir, "current_snapshot_not_pit.csv"))
    checks: Dict[str, Any] = {}
    errors: List[str] = []

    def fail(msg: str) -> None:
        errors.append(msg)

    _check(checks, "observations_nonempty", len(obs) > 0, "%d rows" % len(obs))
    if not obs:
        fail("observations.csv is empty")

    bad_dates = [r for r in obs if not re.match(r"^\d{8}$", r.get("as_of_date", ""))]
    _check(checks, "as_of_date_format", not bad_dates, "%d bad" % len(bad_dates))
    bad_src_dates = [r for r in obs if not re.match(r"^\d{8}$", r.get("source_date", ""))]
    _check(checks, "source_date_format", not bad_src_dates, "%d bad" % len(bad_src_dates))

    future = [r for r in obs if r.get("source_date", "") > r.get("as_of_date", "")]
    _check(checks, "no_future_source_date", not future,
           "%d rows with source_date > as_of_date" % len(future))
    for r in future[:10]:
        fail("future source: %s %s %s" % (r.get("as_of_date"), r.get("theme"), r.get("source_date")))

    gate_bad = [
        r for r in obs
        if str(r.get("source", "")).startswith("chain_map.")
        and r.get("as_of_date", "") < gate_date
    ]
    _check(checks, "chain_map_gate_%s" % snapshot_policy, not gate_bad,
           "%d chain_map rows before %s" % (len(gate_bad), gate_date))

    bad_ts = [r for r in obs + snap if not valid_ts(r.get("ts_code", ""))]
    _check(checks, "ts_code_format", not bad_ts, "%d bad" % len(bad_ts))

    known_themes = {s.name for s in specs}
    bad_theme = [r for r in obs + snap if r.get("theme") not in known_themes]
    _check(checks, "theme_known", not bad_theme, "%d bad" % len(bad_theme))

    spec_by_name = {s.name: s for s in specs}
    bad_level = [
        r for r in obs + snap
        if r.get("theme_level") not in (LEVEL_PARENT, LEVEL_SUBTHEME)
    ]
    _check(checks, "theme_level_known", not bad_level, "%d bad" % len(bad_level))

    bad_parent = [
        r for r in obs + snap
        if r.get("theme") in spec_by_name
        and r.get("parent_theme") != spec_by_name[r["theme"]].parent
    ]
    _check(checks, "parent_theme_consistent", not bad_parent, "%d bad" % len(bad_parent))

    snap_in_pit = [r for r in obs if str(r.get("source", "")).startswith("current_snapshot")]
    _check(checks, "no_current_snapshot_in_pit", not snap_in_pit, "%d rows" % len(snap_in_pit))

    snap_pit = [r for r in snap if str(r.get("pit", "")).lower() != "false"]
    _check(checks, "current_snapshot_marked_not_pit", not snap_pit, "%d rows" % len(snap_pit))

    dup_key = Counter(
        (r.get("as_of_date"), r.get("theme"), r.get("ts_code"), r.get("concept_name"))
        for r in obs
    )
    dups = [k for k, n in dup_key.items() if n > 1]
    _check(checks, "no_duplicate_observation_keys", not dups, "%d duplicate keys" % len(dups))

    # coverage 与 observations 必须对得上
    by_td: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    by_td_c: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for r in obs:
        by_td[(r.get("theme", ""), r.get("as_of_date", ""))].add(r.get("ts_code", ""))
        if r.get("concept_name"):
            by_td_c[(r.get("theme", ""), r.get("as_of_date", ""))].add(r.get("concept_name", ""))
    cov_bad = []
    for c in cov:
        key = (c.get("theme", ""), c.get("as_of_date", ""))
        want = len(by_td.get(key, set()))
        want_c = len(by_td_c.get(key, set()))
        if int(c.get("n_members", -1)) != want or int(c.get("n_concepts", -1)) != want_c:
            cov_bad.append((key, c.get("n_members"), want, c.get("n_concepts"), want_c))
    _check(checks, "coverage_matches_observations", not cov_bad,
           "%d mismatches" % len(cov_bad))
    for x in cov_bad[:10]:
        fail("coverage mismatch: %s" % (x,))

    if expected:
        _check(checks, "row_counts", all(
            len({"observations": obs, "coverage": cov, "current_snapshot": snap}.get(k, [])) == v
            for k, v in expected.items()
        ), expected)

    passed = all(c["passed"] for c in checks.values())
    return {"passed": passed, "checks": checks, "errors": errors}


# ---------------------------------------------------------------------------
# as-of 查询（供 core P1 使用）
# ---------------------------------------------------------------------------
def asof_members(
    observations: Sequence[Dict[str, Any]],
    date8: str,
    theme: Optional[str] = None,
    max_staleness_days: int = 0,
) -> Dict[str, Any]:
    """取 `date8` 当天/之前最近的成员证据。

    只允许 `source_date <= date8`；`max_staleness_days` 限制“最近证据”的年龄
    （自然日）。默认 0 = 只用当天证据；>0 是显式的“成员延续”假设。
    """
    date8 = str(date8)
    selected: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in observations:
        if theme and r.get("theme") != theme:
            continue
        sd = r.get("source_date", "")
        ad = r.get("as_of_date", "")
        if not sd or sd > date8 or ad > date8:
            continue
        if max_staleness_days is not None and max_staleness_days >= 0:
            if day_diff(date8, sd) > max_staleness_days:
                continue
        ts = r.get("ts_code", "")
        if not ts:
            continue
        th = r.get("theme", "")
        key = (th, ts)
        cur = selected.get(key)
        if cur is None:
            selected[key] = {
                "theme": th,
                "ts_code": ts,
                "concepts": set([r.get("concept_name", "")]) if r.get("concept_name") else set(),
                "source_date": sd,
                "observation_type": r.get("observation_type", ""),
                "quality": r.get("quality", ""),
            }
        else:
            if r.get("concept_name"):
                cur["concepts"].add(r.get("concept_name", ""))
            if sd > cur["source_date"]:
                cur["source_date"] = sd
                cur["observation_type"] = r.get("observation_type", "")
                cur["quality"] = r.get("quality", "")

    members = []
    for key in sorted(selected):
        rec = selected[key]
        members.append({
            "theme": rec["theme"],
            "ts_code": rec["ts_code"],
            "concepts": sorted(x for x in rec["concepts"] if x),
            "source_date": rec["source_date"],
            "age_days": day_diff(date8, rec["source_date"]),
            "observation_type": rec["observation_type"],
            "quality": rec["quality"],
        })
    return {
        "as_of_date": date8,
        "theme": theme,
        "max_staleness_days": max_staleness_days,
        "n_members": len(members),
        "members": members,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build(
    data_dir: str = DEFAULT_DATA_DIR,
    out_dir: str = DEFAULT_OUT_DIR,
    repo_root: str = ROOT,
    snapshot_policy: str = "strict",
    include_current_snapshot: bool = True,
    quiet: bool = False,
) -> Dict[str, Any]:
    concept_hist_path = os.path.join(data_dir, "concept_hist.json")
    db_path = os.path.join(data_dir, "stock_pool.db")
    sw_path = os.path.join(data_dir, "sw_members_cache.json")
    subthemes_path = os.path.join(repo_root, "backend", "app", "services", "wolf_card", "subthemes.py")

    for p in (concept_hist_path, db_path, subthemes_path):
        if not os.path.exists(p):
            raise FileNotFoundError(p)

    log("[1/6] load inputs", quiet)
    hist = load_json(concept_hist_path)
    if not isinstance(hist, dict):
        raise ValueError("concept_hist.json must be a dict")
    hist_names = {str(a.get("name")) for a in hist.values() if isinstance(a, dict) and a.get("name")}
    subthemes = load_subthemes(subthemes_path)
    parent_concepts, parent_source = load_parent_concepts(repo_root, subthemes)
    specs, map_meta = build_theme_specs(hist_names, parent_concepts, subthemes)

    db_meta = read_db_meta(db_path)
    content_date = db_meta["content_date"]
    file_date = db_meta["file_date"]
    gate_date = max(content_date, file_date) if snapshot_policy == "strict" else content_date

    name_map, ambiguous, name_meta = load_name_map(db_path, sw_path)

    log("[2/6] concept_hist observations (true daily evidence, sparse)", quiet)
    hist_rows, unmatched, _matched_counter = build_concept_hist_rows(hist, specs, name_map, ambiguous)

    log("[3/6] chain_map dated artifacts (gate=%s, policy=%s)" % (gate_date, snapshot_policy), quiet)
    chain_maps = load_chain_maps(data_dir)
    chain_rows, chain_meta = build_chain_map_rows(chain_maps, specs, gate_date)

    rows = sorted(hist_rows + chain_rows, key=lambda r: (
        r["as_of_date"], r["theme"], r["theme_level"], r["ts_code"], r["concept_name"], r["source"],
    ))
    all_dates = sorted({r["as_of_date"] for r in rows})
    # 覆盖要包含“没有成员”的日期：concept_hist 全日期 + 已用 chain_map 日期。
    hist_dates = sorted({str(d) for a in hist.values() if isinstance(a, dict) for d in (a.get("dates") or [])})
    all_dates = sorted(set(hist_dates) | set(chain_meta["used_dates"]) | set(all_dates))

    coverage = build_coverage(rows, all_dates, specs)
    summary = build_theme_summary(rows, coverage, specs)

    snap_rows: List[Dict[str, Any]] = []
    if include_current_snapshot:
        log("[4/6] current snapshot (explicitly not PIT)", quiet)
        snap_rows = build_current_snapshot(db_path, specs, content_date)

    log("[5/6] write outputs", quiet)
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "observations.csv"), rows, OBS_FIELDS)
    write_csv(os.path.join(out_dir, "coverage.csv"), coverage, COV_FIELDS)
    write_csv(os.path.join(out_dir, "theme_summary.csv"), summary, SUMMARY_FIELDS)
    write_csv(os.path.join(out_dir, "current_snapshot_not_pit.csv"), snap_rows, SNAPSHOT_FIELDS)

    unmatched_rows = []
    for name, rec in sorted(unmatched.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        unmatched_rows.append({
            "leader_name": name,
            "count": rec["count"],
            "normalized": norm_name(name),
            "reason": rec["reason"],
            "example_date": rec["first_date"],
            "example_concept": rec["first_concept"],
        })
    write_csv(os.path.join(out_dir, "unmatched_leaders.csv"), unmatched_rows, UNMATCHED_FIELDS)

    expected = {
        "observations": len(rows),
        "coverage": len(coverage),
        "current_snapshot": len(snap_rows),
    }
    log("[6/6] validate", quiet)
    validation = validate_outputs(out_dir, specs, gate_date, snapshot_policy, expected)

    parent_specs = [s for s in specs if s.level == LEVEL_PARENT]
    sub_specs = [s for s in specs if s.level == LEVEL_SUBTHEME]
    covered_sub = [
        s.name for s in sub_specs
        if any(c["theme"] == s.name and int(c["n_members"]) > 0 for c in coverage)
    ]
    uncovered_sub = [s.name for s in sub_specs if s.name not in covered_sub]
    parent_with_data = [s.name for s in parent_specs if any(
        c["theme"] == s.name and int(c["n_members"]) > 0 for c in coverage
    )]

    inputs = {
        "concept_hist": {
            "path": os.path.relpath(concept_hist_path, repo_root),
            "sha256": sha256_file(concept_hist_path),
            "concepts": len(hist),
            "names": len(hist_names),
            "dates_min": min(hist_dates) if hist_dates else "",
            "dates_max": max(hist_dates) if hist_dates else "",
            "n_dates": len(hist_dates),
        },
        "stock_pool_db": {
            "path": os.path.relpath(db_path, repo_root),
            "sha256": sha256_file(db_path),
            **{k: v for k, v in db_meta.items() if k != "path"},
        },
        "theme_mapping": {
            "parent_source": os.path.relpath(parent_source, repo_root) if os.path.isabs(parent_source) else parent_source,
            "subthemes_source": os.path.relpath(subthemes_path, repo_root),
            "subthemes_sha256": sha256_file(subthemes_path),
        },
        "chain_map": {
            "n_discovered": len(chain_maps),
            "used_dates": chain_meta["used_dates"],
            "excluded": chain_meta["excluded"],
            "unknown_themes": chain_meta["unknown_themes"],
            "sha256_used": hash_files([
                p for p, o in chain_maps
                if parse_date8(o.get("date")) and parse_date8(o.get("date")) >= gate_date
            ]) if any(
                parse_date8(o.get("date")) and parse_date8(o.get("date")) >= gate_date
                for _, o in chain_maps
            ) else "",
        },
        "sw_members_cache": {
            "path": os.path.relpath(sw_path, repo_root),
            "sha256": sha256_file(sw_path) if os.path.exists(sw_path) else "",
        },
        "name_map": name_meta,
    }

    manifest = {
        "schema": "pit.theme_members/v1",
        "builder": {
            "path": "jobs/build_pit_theme_members.py",
            "version": BUILDER_VERSION,
            "snapshot_policy": snapshot_policy,
        },
        "scope": {
            "theme_levels": [LEVEL_PARENT, LEVEL_SUBTHEME],
            "as_of_basis": "evidence date; source_date <= as_of_date; no future membership",
        },
        "inputs": inputs,
        "snapshot": {
            "content_date": content_date,
            "file_date": file_date,
            "gate_date": gate_date,
            "meaning": "content_date=max(updated_at) in stock_pool.db; file_date=mtime; strict gate uses max(both)",
        },
        "theme_mappings": {
            "parent_themes": [s.to_dict() for s in parent_specs],
            "subthemes": [s.to_dict() for s in sub_specs],
            "aliases": map_meta["aliases"],
        },
        "coverage": {
            "dates": {
                "min": all_dates[0] if all_dates else "",
                "max": all_dates[-1] if all_dates else "",
                "n": len(all_dates),
            },
            "parent_themes": {
                "n": len(parent_specs),
                "with_data": parent_with_data,
                "without_data": [s.name for s in parent_specs if s.name not in parent_with_data],
            },
            "subthemes": {
                "n": len(sub_specs),
                "with_data": covered_sub,
                "without_data": uncovered_sub,
            },
            "rows": {
                "observations": len(rows),
                "coverage": len(coverage),
                "current_snapshot": len(snap_rows),
                "unmatched_leaders": len(unmatched_rows),
            },
            "unique_stocks": len({r["ts_code"] for r in rows}),
            "observation_types": dict(sorted(Counter(r["observation_type"] for r in rows).items())),
            "sources": dict(sorted(Counter(
                s for r in rows for s in str(r["source"]).split("|") if s
            ).items())),
        },
        "outputs": {
            "observations.csv": len(rows),
            "coverage.csv": len(coverage),
            "theme_summary.csv": len(summary),
            "current_snapshot_not_pit.csv": len(snap_rows),
            "unmatched_leaders.csv": len(unmatched_rows),
        },
        "validation": validation,
        "known_gaps": [
            "No complete point-in-time concept membership table exists locally.",
            "concept_hist.json starts 2025-08-20; 2025-01-01..2025-08-19 has no PIT membership evidence.",
            "concept_hist.json records only 1 leader per concept per day (buy_sm_amount_stock), not the full member list.",
            "concept_hist leader is forward-filled by the producer; ffill_repeat rows are marked and are not fresh observations.",
            "Subthemes with no matching concept in concept_hist have zero PIT coverage: %s" % (uncovered_sub,),
            "leader names not in the current stock pool (delisted/renamed) are dropped: %d names" % len(unmatched_rows),
            "chain_map dates before %s are rejected because they predate the local stock_concept_map snapshot." % gate_date,
            "PostgreSQL daily_artifacts and data/_bt_pg were not reachable/readable in this environment; not used.",
        ],
        "known_biases": [
            "Leader selection is small-order-buy leadership, not a random or market-cap sample; it over-represents retail/small-order active names.",
            "Coverage is sparse: typically 3-18 known parent-theme members per day (vs 500-1700 current snapshot members).",
            "Membership persistence is not asserted for ffill_repeat rows; consumers should filter observation_type=fresh for strict evidence.",
            "Name mapping uses the current stock pool; delisted/renamed stocks are missing (survivorship bias).",
            "chain_map membership is segment top-candidates/verified names, not a full member list; selection is biased to larger/verified names.",
            "Theme taxonomy is the current 14 parent + 49 subtheme map; historical concept renames/splits are not reconstructed.",
            "stock_concept_map snapshot has no per-row valid_from/valid_to; its content date is inferred from max(updated_at) and file mtime.",
        ],
    }
    write_json(os.path.join(out_dir, "manifest.json"), manifest)
    write_json(os.path.join(out_dir, "validation.json"), validation)

    log(
        "[done] rows=%d themes(parent=%d, sub=%d, sub_with_data=%d) dates=%s..%s validation=%s"
        % (
            len(rows), len(parent_specs), len(sub_specs), len(covered_sub),
            all_dates[0] if all_dates else "", all_dates[-1] if all_dates else "",
            "PASS" if validation["passed"] else "FAIL",
        ),
        quiet,
    )
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build PIT theme membership history")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--repo-root", default=ROOT)
    ap.add_argument("--snapshot-policy", choices=("strict", "content"), default="strict",
                    help="strict=only chain_map dates >= max(content,mtime); content=only >= content date (riskier)")
    ap.add_argument("--skip-current-snapshot", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--asof", default=None, help="query existing observations.csv for this YYYYMMDD")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--max-staleness-days", type=int, default=0,
                    help="carry-forward window in natural days; 0=strict same-source-date only")
    args = ap.parse_args(argv)

    if args.asof:
        path = os.path.join(args.out_dir, "observations.csv")
        if not os.path.exists(path):
            print("missing %s; run build first" % path, file=sys.stderr)
            return 2
        obs = read_csv(path)
        print(json.dumps(asof_members(
            obs, args.asof, args.theme, args.max_staleness_days,
        ), ensure_ascii=False, indent=1))
        return 0

    manifest = build(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        repo_root=args.repo_root,
        snapshot_policy=args.snapshot_policy,
        include_current_snapshot=not args.skip_current_snapshot,
        quiet=args.quiet,
    )
    return 0 if manifest["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
