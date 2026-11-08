#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mainline_p0_audit.py — P0 分层宇宙审计（只读，不写库）。

检查：
  1. YAML 结构/唯一 id/父子引用/themes 引用；
  2. 每个 source 的代码在 Tushare 是否存在；
  3. 成员数/历史行数（CSI 用 index_weight，SW 用 index_member_all，东财用 dc_member）；
  4. leader/ladder 是否在 stock_basic；
  5. caps 字段是否齐全、上限是否合理（只看项目级 caps，不看总仓位）。

用法：
  python scripts/mainline_p0_audit.py
  python scripts/mainline_p0_audit.py --date 20261023
  python scripts/mainline_p0_audit.py --fast          # 跳过成员/历史检查
  python scripts/mainline_p0_audit.py --json-out .dsh-tmp/mainline_p0_audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import yaml
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.tushare_relay import relay_query  # noqa: E402

CFG = os.path.join(ROOT, "config", "mainline")
CAP_KEYS = [
    "max_weight", "max_names", "max_single_stock", "max_probe", "max_add",
    "cooldown_days", "allow_new", "allow_add", "allow_base", "allow_t",
]


def load(name: str, base: str = CFG) -> Dict[str, Any]:
    path = os.path.join(base, name)
    if not os.path.exists(path):
        path = os.path.join(CFG, name)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def rows(df) -> int:
    try:
        return 0 if df is None else len(df)
    except Exception:
        return 0


class Audit:
    def __init__(self, date8: str, fast: bool = False, cfg_dir: str = CFG):
        self.date8 = date8
        self.fast = fast
        self.cfg = cfg_dir
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.rows: List[Dict[str, Any]] = []
        self._cache: Dict[str, Any] = {}
        self.dc_api_ok = True

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def cached(self, key: str, fn):
        if key not in self._cache:
            try:
                self._cache[key] = fn()
            except Exception as e:
                self._cache[key] = e
        v = self._cache[key]
        if isinstance(v, Exception):
            raise v
        return v

    # ── maps ──
    def maps(self):
        def _load():
            sw2 = relay_query("index_classify", level="L2")
            sw3 = relay_query("index_classify", level="L3")
            csi = relay_query("index_basic", market="CSI", limit=10000)
            try:
                dc = relay_query("dc_index", trade_date=self.date8, limit=5000)
            except Exception as e:
                self.dc_api_ok = False
                self.warn(f"dc_index 不可用，改用 concept_hist 校验代码：{str(e)[:100]}")
                j = json.load(open(os.path.join(ROOT, "data", "concept_hist.json"), encoding="utf-8"))
                dc = pd.DataFrame([{"ts_code": k, "name": (v or {}).get("name", "")} for k, v in j.items()])
            sb = relay_query("stock_basic", list_status="L", limit=6000)
            return sw2, sw3, csi, dc, sb
        sw2, sw3, csi, dc, sb = self.cached("maps", _load)
        self.sw2 = {str(r.index_code): str(r.industry_name) for r in sw2.itertuples()}
        self.sw3 = {str(r.index_code): str(r.industry_name) for r in sw3.itertuples()}
        self.csi = {str(r.ts_code): str(r.name) for r in csi.itertuples()}
        self.dc = {str(r.ts_code): str(r.name) for r in dc.itertuples()}
        self.sb = {str(r.ts_code): str(r.name) for r in sb.itertuples()}

    def check_stock(self, code: str, label: str, owner: str) -> None:
        if not code:
            self.err(f"{owner}: {label} 为空")
            return
        if code not in self.sb:
            self.err(f"{owner}: {label} {code} 不在 stock_basic")

    def check_caps(self, caps: Dict[str, Any], owner: str, kind: str) -> None:
        if not isinstance(caps, dict):
            self.err(f"{owner}: 缺 caps")
            return
        for k in CAP_KEYS:
            if k not in caps:
                self.err(f"{owner}: caps 缺 {k}")
        try:
            mw = float(caps.get("max_weight", 0))
            mn = int(caps.get("max_names", 0))
            ms = float(caps.get("max_single_stock", 0))
            mp = float(caps.get("max_probe", 0))
            if not (0 < mw <= 1):
                self.err(f"{owner}: max_weight 越界 {mw}")
            if mn < 1:
                self.err(f"{owner}: max_names 越界 {mn}")
            if ms <= 0 or ms > mw + 1e-9:
                self.err(f"{owner}: max_single_stock 越界 {ms} > {mw}")
            if mp <= 0 or mp > ms + 1e-9:
                self.err(f"{owner}: max_probe 越界 {mp} > {ms}")
        except Exception as e:
            self.err(f"{owner}: caps 数值解析失败 {e}")
        if kind == "satellite":
            if float(caps.get("max_weight", 1)) > 0.10 + 1e-9:
                self.err(f"{owner}: 卫星题材 max_weight 应 <= 0.10")
            if bool(caps.get("allow_base")):
                self.err(f"{owner}: 卫星题材 allow_base 应为 false")

    def check_source(self, src: Dict[str, Any], owner: str) -> Tuple[str, int, int]:
        typ = str(src.get("type") or "")
        code = str(src.get("code") or "")
        name = str(src.get("name") or "")
        if typ not in {"csi", "sw_l2", "sw_l3", "eastmoney"}:
            self.err(f"{owner}: source type 非法 {typ}")
            return typ, 0, 0
        if not code:
            self.err(f"{owner}: source code 为空")
            return typ, 0, 0
        members = 0
        hrows = 0
        try:
            if typ == "csi":
                if code not in self.csi:
                    self.err(f"{owner}: CSI {code} 不在 index_basic")
                if not self.fast:
                    for wd in (self.date8, "20260930", "20260630", "20260331"):
                        try:
                            w = relay_query("index_weight", index_code=code, trade_date=wd)
                            members = rows(w)
                            if members:
                                break
                        except Exception as e:
                            self.warn(f"{owner}: CSI {code} index_weight({wd}) 失败 {str(e)[:80]}")
                    try:
                        d = relay_query("index_daily", ts_code=code, start_date="20260101", end_date=self.date8)
                        hrows = rows(d)
                    except Exception as e:
                        self.warn(f"{owner}: CSI {code} index_daily 失败 {str(e)[:80]}")
            elif typ in {"sw_l2", "sw_l3"}:
                mp = self.sw2 if typ == "sw_l2" else self.sw3
                allm = self.cached("index_member_all", lambda: relay_query("index_member_all", limit=10000))
                col = "l2_code" if typ == "sw_l2" else "l3_code"
                in_member = code in set(allm[col].astype(str))
                if code not in mp and not in_member:
                    self.err(f"{owner}: {typ} {code} 不在 index_classify / index_member_all")
                if not self.fast:
                    try:
                        d = relay_query("sw_daily", ts_code=code, start_date="20260101", end_date=self.date8)
                        hrows = rows(d)
                        if hrows == 0:
                            self.warn(f"{owner}: {typ} {code} sw_daily 0 行（SW2014/SW2021 代码差异，回测用成分股聚合）")
                    except Exception as e:
                        self.warn(f"{owner}: {typ} {code} sw_daily 失败 {str(e)[:80]}")
                    try:
                        members = int((allm[col].astype(str) == code).sum())
                        if members == 0:
                            self.warn(f"{owner}: {typ} {code} 成员 0（检查 SW 版本/代码）")
                    except Exception as e:
                        self.warn(f"{owner}: {typ} {code} index_member_all 失败 {str(e)[:80]}")
            else:  # eastmoney
                if code not in self.dc:
                    if self.dc_api_ok:
                        self.err(f"{owner}: Eastmoney {code} 不在 dc_index({self.date8})")
                    else:
                        self.warn(f"{owner}: Eastmoney {code} 无法在线校验（dc_index 不可用）")
                if not self.fast:
                    try:
                        d = relay_query("dc_daily", ts_code=code, start_date="20260101", end_date=self.date8)
                        hrows = rows(d)
                    except Exception as e:
                        self.warn(f"{owner}: Eastmoney {code} dc_daily 失败 {str(e)[:80]}")
                    try:
                        m = relay_query("dc_member", ts_code=code, trade_date=self.date8, limit=5000)
                        members = rows(m)
                    except Exception as e:
                        self.warn(f"{owner}: Eastmoney {code} dc_member 失败 {str(e)[:80]}")
        except Exception as e:
            self.err(f"{owner}: source {typ}/{code} 检查异常 {str(e)[:120]}")
        return typ, members, hrows

    def check_item(self, item: Dict[str, Any], layer: str, kind: str = "track") -> None:
        iid = str(item.get("id") or "")
        name = str(item.get("name") or "")
        owner = f"{layer}/{iid}({name})"
        if not iid or not name:
            self.err(f"{layer}: 缺 id/name")
            return
        caps = item.get("caps") or {}
        self.check_caps(caps, owner, kind if kind == "satellite" else "normal")
        leader = item.get("leader") or {}
        self.check_stock(str(leader.get("code") or ""), "leader", owner)
        for i, x in enumerate(item.get("ladder") or []):
            self.check_stock(str((x or {}).get("code") or ""), f"ladder[{i}]", owner)
        sources = item.get("sources") or []
        if not sources:
            self.err(f"{owner}: 缺 sources")
        total_members = 0
        total_hrows = 0
        for src in sources:
            typ, m, h = self.check_source(src or {}, owner)
            total_members = max(total_members, m)
            total_hrows = max(total_hrows, h)
        if total_hrows and total_hrows < 60:
            self.warn(f"{owner}: 历史仅 {total_hrows} 行（<60），回测需注意")
        if total_members and total_members < 10:
            self.warn(f"{owner}: 成员仅 {total_members} 只（<10）")
        self.rows.append({
            "layer": layer, "id": iid, "name": name, "kind": kind,
            "sources": ";".join(f"{s.get('type')}:{s.get('code')}" for s in sources),
            "members": total_members, "history_rows": total_hrows,
            "leader": leader.get("code"), "max_weight": caps.get("max_weight"),
            "status": item.get("status", ""),
        })

    # ── main ──
    def run(self) -> None:
        self.maps()
        ml = load("universe_mainline.yaml", self.cfg)
        tr = load("universe_track.yaml", self.cfg)
        th = load("universe_theme.yaml", self.cfg)

        mainlines = ml.get("mainlines") or []
        tracks = (tr.get("tracks") or []) + (tr.get("satellite_tracks") or [])
        themes = (th.get("themes") or []) + (th.get("satellite_themes") or [])

        ml_ids = set()
        for x in mainlines:
            i = str(x.get("id") or "")
            if i in ml_ids:
                self.err(f"mainline id 重复 {i}")
            ml_ids.add(i)
            self.check_caps(x.get("caps") or {}, f"mainline/{i}", "mainline")
        tr_ids = {str(x.get("id")) for x in tracks}
        th_ids = {str(x.get("id")) for x in themes}
        if len(tr_ids) != len(tracks):
            self.err("track id 有重复")
        if len(th_ids) != len(themes):
            self.err("theme id 有重复")

        for x in tracks:
            kind = "satellite" if str(x.get("status")) == "satellite" else "track"
            self.check_item(x, "L1", kind)
            pm = x.get("parent_mainline")
            if pm and str(pm) not in ml_ids:
                self.err(f"track/{x.get('id')}: parent_mainline {pm} 不存在")
            for t in x.get("themes") or []:
                if str(t) not in th_ids:
                    self.err(f"track/{x.get('id')}: theme {t} 不存在")

        for x in themes:
            kind = "satellite" if str(x.get("status")) == "satellite" else "theme"
            self.check_item(x, "L2", kind)
            pt = x.get("parent_track")
            if pt and str(pt) not in tr_ids:
                self.err(f"theme/{x.get('id')}: parent_track {pt} 不存在")
            # theme caps 不得大于父 track
            parent = next((t for t in tracks if str(t.get("id")) == str(pt)), None)
            if parent:
                pc = parent.get("caps") or {}
                tc = x.get("caps") or {}
                if float(tc.get("max_weight", 0)) > float(pc.get("max_weight", 1)) + 1e-9:
                    self.err(f"theme/{x.get('id')}: max_weight > parent track")
                if int(tc.get("max_names", 0)) > int(pc.get("max_names", 99)):
                    self.warn(f"theme/{x.get('id')}: max_names > parent track（允许但请确认）")

    def report(self) -> Dict[str, Any]:
        return {
            "date": self.date8,
            "errors": self.errors,
            "warnings": self.warnings,
            "rows": self.rows,
            "ok": not self.errors,
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261023")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--dir", default=CFG)
    a = ap.parse_args()
    au = Audit(a.date, a.fast, a.dir)
    au.run()
    rep = au.report()
    # table
    print("layer | id | name | sources | members | history | leader | max_w | status")
    print("---|---|---|---|---:|---:|---|---:|---")
    for r in rep["rows"]:
        print("%s | %s | %s | %s | %s | %s | %s | %s | %s" % (
            r["layer"], r["id"], r["name"], r["sources"], r["members"],
            r["history_rows"], r["leader"], r["max_weight"], r["status"]))
    if rep["warnings"]:
        print("\nWARNINGS:")
        for w in rep["warnings"]:
            print("  ⚠️", w)
    if rep["errors"]:
        print("\nERRORS:")
        for e in rep["errors"]:
            print("  ❌", e)
    print("\n%s: %d errors, %d warnings, %d items" % (
        "OK" if rep["ok"] else "FAIL", len(rep["errors"]), len(rep["warnings"]), len(rep["rows"])))
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    if not rep["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
