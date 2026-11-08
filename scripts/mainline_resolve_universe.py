#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mainline_resolve_universe.py — P0.5 从 seeds 推导宇宙。

输入：
  config/mainline/seeds.yaml            # 只写主题名/别名/层级意图/是否卫星/父赛道提示
  config/mainline/caps_policy.yaml      # 项目级 caps 策略
  config/mainline/universe_*.yaml       # P0 手写版，仅作 fallback/diff 参照

输出：
  config/mainline/generated/universe_track.yaml
  config/mainline/generated/universe_theme.yaml
  config/mainline/generated/resolution_report.json
  config/mainline/generated/diff_report.md

原则：
  - 不硬编码 CSI/SW/东财代码；按名称/别名匹配候选，按成员/历史/重叠度打分。
  - 父赛道用“主题成员 ∩ 申万 L2 成员”的覆盖率推导，输出多父图。
  - leader/ladder 用成分股权重/市值推导，不手写。
  - caps 用 caps_policy + 成员数推导，不逐项硬编码。
  - 无法推导时回退 P0 参照，并标 fallback_reference。
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.tushare_relay import relay_query  # noqa: E402

CFG = os.path.join(ROOT, "config", "mainline")
GEN = os.path.join(CFG, "generated")


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def norm(s: Any) -> str:
    s = str(s or "")
    s = re.sub(r"[（(].*?[）)]", "", s)
    s = re.sub(r"(全收益|净收益|人民币|美元|港币|中间价|指数|概念|板块|行业|Ⅱ|Ⅲ|II|III)", "", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s)
    return s.upper()


def match_score(name: str, aliases: Iterable[str]) -> float:
    n = norm(name)
    best = 0.0
    for i, a in enumerate(aliases):
        a = norm(a)
        if not a or not n:
            continue
        bonus = 5.0 if i == 0 else (2.0 if i == 1 else 0.0)
        if n == a:
            sc = 100.0
        elif a in n:
            # 名称比别名长：允许少量修饰词，太长则降分
            sc = 90.0 - min(20.0, max(0, len(n) - len(a)) * 2.0)
        elif n in a:
            # 名称只是别名的一部分（如“半导体” vs “半导体设备”）：不够具体，降到阈值下
            sc = 50.0
        else:
            sc = difflib.SequenceMatcher(None, n, a).ratio() * 50.0
        best = max(best, sc + bonus)
    return best


def rows(df) -> int:
    try:
        return 0 if df is None else len(df)
    except Exception:
        return 0


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class Resolver:
    def __init__(self, date8: str, max_candidates: int = 3, only: Optional[Set[str]] = None):
        self.date8 = date8
        self.max_candidates = max_candidates
        self.only = only
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.report: Dict[str, Any] = {"date": date8, "tracks": {}, "themes": {}, "warnings": [], "errors": []}
        self._cache: Dict[str, Any] = {}
        self.weights_cache: Dict[str, Tuple[Set[str], Dict[str, float]]] = {}

    # ── cache ──
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

    # ── registries ──
    def load(self) -> None:
        self.seeds = load_yaml(os.path.join(CFG, "seeds.yaml"))
        self.policy = load_yaml(os.path.join(CFG, "caps_policy.yaml"))
        self.ref_track = load_yaml(os.path.join(CFG, "universe_track.yaml"))
        self.ref_theme = load_yaml(os.path.join(CFG, "universe_theme.yaml"))
        self.ref_tracks = {str(x.get("id")): x for x in (self.ref_track.get("tracks") or []) + (self.ref_track.get("satellite_tracks") or [])}
        self.ref_themes = {str(x.get("id")): x for x in (self.ref_theme.get("themes") or []) + (self.ref_theme.get("satellite_themes") or [])}

        sw2 = self.cached("sw2", lambda: relay_query("index_classify", level="L2"))
        sw3 = self.cached("sw3", lambda: relay_query("index_classify", level="L3"))
        csi = self.cached("csi", lambda: relay_query("index_basic", market="CSI", limit=10000))
        dc = self.cached("dc", lambda: relay_query("dc_index", trade_date=self.date8, limit=5000))
        ths = self.cached("ths", lambda: relay_query("ths_index", limit=5000))
        members = self.cached("members", lambda: relay_query("index_member_all", limit=10000))
        sb = self.cached("sb", lambda: relay_query("stock_basic", list_status="L", limit=6000))
        db = self.cached("db", lambda: relay_query("daily_basic", trade_date=self.date8, limit=6000))

        self.sw2 = {str(r.index_code): str(r.industry_name) for r in sw2.itertuples()}
        for r in members[["l2_code", "l2_name"]].drop_duplicates().itertuples():
            self.sw2.setdefault(str(r.l2_code), str(r.l2_name))
        self.sw3 = {str(r.index_code): str(r.industry_name) for r in sw3.itertuples()}
        # SW2021 registry: index_classify + index_member_all 的 l3_name/code
        for r in members[["l3_code", "l3_name"]].drop_duplicates().itertuples():
            self.sw3.setdefault(str(r.l3_code), str(r.l3_name))
        self.csi = {}
        self.csi_category = {}
        for r in csi.itertuples():
            name = str(r.name)
            if str(r.category) not in {"主题指数", "行业指数"}:
                continue
            if re.search(r"(全收益|净收益|USD|HKD|CNY|SGD|人民币|美元|港币|\(全\)|全\)|中间价)", name):
                continue
            self.csi[str(r.ts_code)] = name
            self.csi_category[str(r.ts_code)] = str(r.category)
        self.dc = {str(r.ts_code): str(r.name) for r in dc.itertuples()}
        self.ths = {str(r.ts_code): str(r.name) for r in ths.itertuples()} if ths is not None else {}
        self.member_all = members
        self.stock_name = {str(r.ts_code): str(r.name) for r in sb.itertuples()}
        self.sb = set(self.stock_name)
        self.rs20, self.amt = self.load_rs_amount()
        self.mv = {}
        for r in db.itertuples():
            try:
                self.mv[str(r.ts_code)] = float(r.circ_mv or 0)
            except Exception:
                self.mv[str(r.ts_code)] = 0.0
        self.l2_members: Dict[str, Set[str]] = defaultdict(set)
        for r in members.itertuples():
            if str(r.l2_code) and str(r.ts_code):
                self.l2_members[str(r.l2_code)].add(str(r.ts_code))
        self.l3_members: Dict[str, Set[str]] = defaultdict(set)
        for r in members.itertuples():
            if str(r.l3_code) and str(r.ts_code):
                self.l3_members[str(r.l3_code)].add(str(r.ts_code))

    # ── candidate pools ──
    def candidates_track(self, aliases: List[str]) -> List[Dict[str, Any]]:
        out = []
        for code, name in self.sw2.items():
            sc = match_score(name, aliases)
            if sc >= 75:
                out.append({"type": "sw_l2", "code": code, "name": name, "name_score": sc})
        out.sort(key=lambda x: -x["name_score"])
        return out[: self.max_candidates + 2]

    def candidates_theme(self, aliases: List[str], level_hint: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for typ, mapping in (("csi", self.csi), ("sw_l3", self.sw3), ("eastmoney", self.dc)):
            for code, name in mapping.items():
                sc = match_score(name, aliases)
                if sc >= 75:
                    out.append({"type": typ, "code": code, "name": name, "name_score": sc,
                                "category": self.csi_category.get(code, "")})
        # 同类型按名称分取前 N；再全局排序
        by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for x in out:
            by_type[x["type"]].append(x)
        picked: List[Dict[str, Any]] = []
        for typ in ("csi", "sw_l3", "eastmoney"):
            arr = sorted(by_type.get(typ, []), key=lambda x: -x["name_score"])[: self.max_candidates]
            picked.extend(arr)
        return picked

    # ── members / history ──
    def members_csi(self, code: str) -> Tuple[Set[str], Dict[str, float]]:
        if code in self.weights_cache:
            return self.weights_cache[code]
        for wd in (self.date8, "20260930", "20260630", "20260331"):
            try:
                w = relay_query("index_weight", index_code=code, trade_date=wd)
                if rows(w):
                    mem = set(str(x) for x in w["con_code"])
                    wt = {str(r.con_code): float(r.weight or 0) for r in w.itertuples()}
                    self.weights_cache[code] = (mem, wt)
                    return mem, wt
            except Exception:
                continue
        self.weights_cache[code] = (set(), {})
        return set(), {}

    def members_dc(self, code: str) -> Set[str]:
        try:
            m = relay_query("dc_member", ts_code=code, trade_date=self.date8, limit=5000)
            return set(str(x) for x in m["con_code"]) if rows(m) else set()
        except Exception:
            return set()

    def members_ths(self, code: str) -> Set[str]:
        try:
            m = relay_query("ths_member", ts_code=code)
            col = "con_code" if "con_code" in m.columns else ("code" if "code" in m.columns else None)
            return set(str(x) for x in m[col]) if rows(m) and col else set()
        except Exception:
            return set()

    def members_of(self, src: Dict[str, Any]) -> Tuple[Set[str], Dict[str, float]]:
        typ, code = src["type"], src["code"]
        if typ == "csi":
            return self.members_csi(code)
        if typ == "sw_l3":
            return set(self.l3_members.get(code, set())), {}
        if typ == "sw_l2":
            return set(self.l2_members.get(code, set())), {}
        if typ == "eastmoney":
            return self.members_dc(code), {}
        if typ == "ths":
            return self.members_ths(code), {}
        return set(), {}

    def history_rows(self, typ: str, code: str) -> int:
        try:
            if typ == "csi":
                d = relay_query("index_daily", ts_code=code, start_date="20260101", end_date=self.date8)
            elif typ in {"sw_l2", "sw_l3"}:
                d = relay_query("sw_daily", ts_code=code, start_date="20260101", end_date=self.date8)
            elif typ == "eastmoney":
                d = relay_query("dc_daily", ts_code=code, start_date="20260101", end_date=self.date8)
            elif typ == "ths":
                d = relay_query("ths_daily", ts_code=code, start_date="20260101", end_date=self.date8)
            else:
                return 0
            return rows(d)
        except Exception:
            return 0

    # ── parents ──
    def parents_of(self, members: Set[str], track_by_l2: Dict[str, str]) -> List[Dict[str, Any]]:
        if not members:
            return []
        out = []
        for l2, lm in self.l2_members.items():
            inter = len(members & lm)
            if inter <= 0:
                continue
            cov = inter / max(1, len(members))
            if cov < 0.05:
                continue
            track = track_by_l2.get(l2)
            if not track:
                continue
            out.append({"track": track, "overlap": round(cov, 3), "sw_l2": l2, "sw_l2_name": self.sw2.get(l2, "")})
        out.sort(key=lambda x: -x["overlap"])
        return out[:3]

    # ── RS/amount ──
    def load_rs_amount(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        try:
            cal = relay_query("trade_cal", exchange="SSE", start_date="20260901", end_date=self.date8, is_open="1")
            dates = sorted(str(x) for x in cal["cal_date"])
            if self.date8 not in dates:
                dates.append(self.date8)
            idx = dates.index(self.date8)
            d20 = dates[max(0, idx - 20)]
            now = self.cached("daily_now", lambda: relay_query("daily", trade_date=self.date8))
            prev = self.cached("daily_prev", lambda: relay_query("daily", trade_date=d20))
            close_now = {str(r.ts_code): float(r.close or 0) for r in now.itertuples()}
            close_prev = {str(r.ts_code): float(r.close or 0) for r in prev.itertuples()}
            amt = {str(r.ts_code): float(r.amount or 0) for r in now.itertuples()}
            rs = {}
            for c, p in close_prev.items():
                if p > 0 and c in close_now and close_now[c] > 0:
                    rs[c] = close_now[c] / p - 1.0
            return rs, amt
        except Exception as e:
            self.warnings.append(f"RS/amount 加载失败，leader 退回市值/权重：{str(e)[:120]}")
            return {}, {}

    @staticmethod
    def pct_rank(vals: List[float], v: float) -> float:
        if not vals:
            return 0.5
        less = sum(1 for x in vals if x < v)
        eq = sum(1 for x in vals if abs(x - v) < 1e-12)
        return (less + 0.5 * eq) / len(vals)

    # ── leader/ladder ──
    def leader_ladder(self, members: Set[str], weights: Dict[str, float], allowed: Optional[Set[str]] = None) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
        def valid(c):
            n = self.stock_name.get(c, "")
            return c in self.sb and "ST" not in n.upper() and "退" not in n

        cand = [c for c in members if valid(c)]
        if allowed:
            filtered = [c for c in cand if c in allowed]
            if len(filtered) >= 5:
                cand = filtered
        if not cand:
            return {}, []
        rs_vals = [self.rs20.get(c, 0.0) for c in cand]
        mv_vals = [self.mv.get(c, 0.0) for c in cand]
        amt_vals = [self.amt.get(c, 0.0) for c in cand]
        max_w = max([weights.get(c, 0.0) for c in cand] or [0.0])
        max_mv = max(mv_vals or [0.0])
        score = {}
        for c in cand:
            w_norm = (weights.get(c, 0.0) / max_w) if (weights and max_w > 0) else (self.mv.get(c, 0.0) / max_mv if max_mv > 0 else 0.0)
            rs_pct = self.pct_rank(rs_vals, self.rs20.get(c, 0.0))
            mv_pct = self.pct_rank(mv_vals, self.mv.get(c, 0.0))
            amt_pct = self.pct_rank(amt_vals, self.amt.get(c, 0.0))
            score[c] = 0.30 * w_norm + 0.35 * rs_pct + 0.20 * mv_pct + 0.15 * amt_pct
        ranked = sorted(cand, key=lambda c: -score.get(c, 0.0))
        leader = {"code": ranked[0], "name": self.stock_name.get(ranked[0], "")}
        ladder = [{"code": c, "name": self.stock_name.get(c, "")} for c in ranked[1:6]]
        return leader, ladder

    # ── caps ──
    def caps(self, kind: str, members_n: int, satellite: bool) -> Dict[str, Any]:
        p = self.policy
        if satellite:
            base = p["satellite_theme"] if kind == "theme" else p["satellite_track"]
            mw = float(base["max_weight"])
            mn = int(base["max_names"])
            ms = min(float(p["single_stock_cap"]), mw)
            mp = min(float(p["probe_cap"]), ms * 0.4)
            return {
                "max_weight": round(mw, 2), "max_names": mn,
                "max_single_stock": round(ms, 2), "max_probe": round(mp, 2),
                "max_add": round(mw / 2, 2), "cooldown_days": int(p["satellite_cooldown_days"]),
                "allow_new": True, "allow_add": False, "allow_base": False, "allow_t": False,
            }
        pol = p[kind]
        base = float(pol["base_max_weight"])
        lo, hi = [float(x) for x in pol["max_weight_range"]]
        if members_n >= int(pol["large_members"]):
            base += float(pol["liquidity_bonus"])
        elif members_n <= int(pol["small_members"]):
            base -= float(pol["liquidity_cut"])
        mw = round(clip(base, lo, hi), 2)
        mn = int(pol["max_names"])
        ms = round(min(float(p["single_stock_cap"]), mw), 2)
        mp = round(min(float(p["probe_cap"]), ms * 0.4), 2)
        return {
            "max_weight": mw, "max_names": mn,
            "max_single_stock": ms, "max_probe": mp,
            "max_add": round(min(float(p["add_cap"]), mw), 2),
            "cooldown_days": int(p["cooldown_days"]),
            "allow_new": True, "allow_add": True, "allow_base": True, "allow_t": True,
        }

    # ── purity / leader source ──
    @staticmethod
    def source_purity(x: Dict[str, Any]) -> float:
        base = {"csi": 1.0, "sw_l3": 1.0, "eastmoney": 0.6, "ths": 0.4}.get(x.get("type"), 0.4)
        n = int(x.get("members") or 0)
        size = 1.0 if 15 <= n <= 100 else (0.7 if 100 < n <= 200 else 0.4)
        return base * size

    # ── score candidate ──
    def score_candidate(self, c: Dict[str, Any], members: Set[str], hist: int,
                        parents: List[Dict[str, Any]], level_hint: str) -> float:
        member_score = min(1.0, len(members) / 60.0)
        hist_score = min(1.0, hist / 120.0)
        overlap_score = parents[0]["overlap"] if parents else 0.0
        type_w = {"csi": 1.0, "sw_l3": 0.95, "eastmoney": 0.85, "ths": 0.6}.get(c["type"], 0.5)
        lv = 0.5
        cat = c.get("category", "")
        if level_hint == "industry":
            if c["type"] == "sw_l3":
                lv = 1.0
            elif c["type"] == "csi":
                lv = 1.0 if cat == "行业指数" else 0.7
            else:
                lv = 0.4
        elif level_hint == "theme":
            if c["type"] == "csi":
                lv = 1.0 if cat == "主题指数" else 0.85
            elif c["type"] == "eastmoney":
                lv = 0.9
            else:
                lv = 0.8
        elif level_hint == "route":
            lv = 1.0 if c["type"] == "eastmoney" else (0.7 if c["type"] == "csi" else 0.5)
        purity = self.source_purity(c)
        return round(
            0.25 * member_score + 0.15 * purity + 0.20 * hist_score + 0.20 * overlap_score
            + 0.10 * type_w + 0.05 * lv + 0.05 * min(1.0, c["name_score"] / 100.0),
            4,
        )

    # ── resolve tracks ──
    def resolve_tracks(self) -> Dict[str, Any]:
        tracks, satellite = [], []
        track_by_l2: Dict[str, str] = {}
        seeds = self.seeds.get("track_seeds") or []
        for s in seeds:
            cands = self.candidates_track(s.get("aliases") or [s["name"]])
            best = None
            members: Set[str] = set()
            for c in cands:
                m = set(self.l2_members.get(c["code"], set()))
                if m:
                    best, members = c, m
                    track_by_l2[c["code"]] = s["id"]
                    break
            if best is None and cands:
                best = cands[0]
                members = set(self.l2_members.get(best["code"], set()))
                track_by_l2[best["code"]] = s["id"]
            ref = self.ref_tracks.get(s["id"], {})
            fallback = False
            if not best or not members:
                # fallback P0
                src = (ref.get("sources") or [{}])[0]
                if src.get("code"):
                    best = {"type": src.get("type", "sw_l2"), "code": src["code"], "name": src.get("name", s["name"]), "name_score": 0}
                    members = set(self.l2_members.get(best["code"], set()))
                    fallback = True
            if best:
                track_by_l2[best["code"]] = s["id"]
            derived_leader, derived_ladder = self.leader_ladder(members, {})
            leader = ref.get("leader") or derived_leader
            ladder = ref.get("ladder") or derived_ladder
            leader_method = "reference_pending_p1" if ref.get("leader") else "derived_weight_rs_mv_amount"
            item = {
                "id": s["id"], "name": s["name"], "status": "satellite" if s.get("sleeve") == "satellite" else "active",
                "parent_mainline": s.get("parent_mainline"),
                "sw_l2": best["code"] if best and best["type"] == "sw_l2" else ref.get("sw_l2"),
                "benchmark": best["code"] if best else ref.get("benchmark"),
                "themes": [t["id"] for t in (self.seeds.get("theme_seeds") or []) if (t.get("parent_hint") == s["id"] or t.get("parent_track") == s["id"])],
                "leader": leader or ref.get("leader") or {},
                "ladder": ladder or ref.get("ladder") or [],
                "sources": [{"type": best["type"], "code": best["code"], "name": best["name"], "role": "primary"}] if best else (ref.get("sources") or []),
                "caps": self.caps("track", len(members), s.get("sleeve") == "satellite"),
                "data_quality": {"membership_pit": (best or {}).get("type") in {"sw_l2", "sw_l3", "csi"}, "history_start": "2021-01-01", "stale_days": 1},
                "resolution": {"seed": s["id"], "candidates": cands[:5], "selected": best, "members": len(members),
                                "fallback_reference": fallback, "leader_method": leader_method,
                                "derived_leader": derived_leader, "derived_ladder": derived_ladder},
                "notes": ref.get("notes", ""),
            }
            (satellite if item["status"] == "satellite" else tracks).append(item)
        # 第二遍：track 成员为 0 时用子 theme 成员并集
        theme_seeds = {t["id"]: t for t in (self.seeds.get("theme_seeds") or [])}
        for item in tracks + satellite:
            if item["resolution"]["members"] == 0:
                child = [theme_seeds.get(t) for t in item["themes"]]
                union = set()
                for c in child:
                    if not c:
                        continue
                    cands = self.candidates_theme(c.get("aliases") or [c["name"]], c.get("level_hint", "theme"))
                    for cand in cands:
                        mem, _ = self.members_of(cand)
                        union |= mem
                        if len(union) >= 10:
                            break
                if union:
                    item["resolution"]["members"] = len(union)
                    item["leader"], item["ladder"] = self.leader_ladder(union, {})
                    item["caps"] = self.caps("track", len(union), item["status"] == "satellite")
        track_caps = {x["id"]: x.get("caps", {}) for x in tracks + satellite}
        return {"tracks": tracks, "satellite_tracks": satellite, "track_by_l2": track_by_l2, "track_caps": track_caps}

    # ── resolve themes ──
    def resolve_themes(self, track_by_l2: Dict[str, str], track_caps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        themes, satellite = [], []
        for s in self.seeds.get("theme_seeds") or []:
            if self.only and s["id"] not in self.only:
                continue
            is_satellite = s.get("sleeve") == "satellite"
            cands = self.candidates_theme(s.get("aliases") or [s["name"]], s.get("level_hint", "theme"))
            scored = []
            for c in cands:
                mem, wt = self.members_of(c)
                parents = [] if is_satellite else self.parents_of(mem, track_by_l2)
                hist = self.history_rows(c["type"], c["code"]) if mem else 0
                score = self.score_candidate(c, mem, hist, parents, s.get("level_hint", "theme"))
                scored.append({**c, "members": len(mem), "history": hist, "parents": parents,
                               "score": score, "_mem": mem, "_wt": wt})
            scored.sort(key=lambda x: -x["score"])
            selected = [x for x in scored if x["members"] > 0 and x["score"] >= 0.55 and x.get("name_score", 0) >= 75][:2]
            ref = self.ref_themes.get(s["id"], {})
            fallback = False
            if not selected:
                # fallback P0 sources
                for src in (ref.get("sources") or []):
                    mem, wt = self.members_of(src)
                    if mem:
                        selected = [{**src, "members": len(mem), "history": 0, "parents": [], "score": 0, "_mem": mem, "_wt": wt}]
                        fallback = True
                        break
            # parents
            all_mem = set().union(*[x["_mem"] for x in selected]) if selected else set()
            weights: Dict[str, float] = {}
            for x in selected:
                weights.update(x.get("_wt") or {})
            if is_satellite:
                parents = [{"track": s.get("parent_track"), "overlap": 1.0}]
            else:
                parents = self.parents_of(all_mem, track_by_l2)
                hint = s.get("parent_hint")
                if hint:
                    hit = next((p for p in parents if p.get("track") == hint), None)
                    if hit:
                        parents = [hit] + [p for p in parents if p.get("track") != hint]
                    else:
                        parents = [{"track": hint, "overlap": 0.0, "hint": True}] + parents
                    parents = parents[:3]
            leader_src = max(selected, key=self.source_purity) if selected else None
            leader_mem = leader_src["_mem"] if leader_src else set()
            leader_wt = leader_src.get("_wt") or {} if leader_src else {}
            allowed: Set[str] = set()
            for p in parents[:3]:
                if p.get("sw_l2"):
                    allowed |= set(self.l2_members.get(p["sw_l2"], set()))
            leader, ladder = self.leader_ladder(leader_mem, leader_wt, allowed if allowed else None)
            if not leader:
                leader, ladder = self.leader_ladder(all_mem, weights)
            derived_leader, derived_ladder = leader, ladder
            if ref.get("leader"):
                leader, ladder = ref.get("leader"), ref.get("ladder") or []
                leader_method = "reference_pending_p1"
            else:
                leader_method = "derived_weight_rs_mv_amount"
            if not leader:
                leader = {}
                ladder = []
                fallback = True
            sources = [{"type": x["type"], "code": x["code"], "name": x["name"], "role": "primary" if i == 0 else "secondary",
                        "members": x["members"], "history_rows": x["history"], "score": x["score"],
                        "membership_pit": x["type"] in {"csi", "sw_l3"}} for i, x in enumerate(selected)]
            if not sources:
                sources = ref.get("sources") or []
            item = {
                "id": s["id"], "name": s["name"],
                "status": "satellite" if is_satellite else "active",
                "parent_track": (s.get("parent_hint") or (parents[0]["track"] if parents else s.get("parent_track"))),
                "parents": parents,
                "sleeve": "satellite" if is_satellite else "theme",
                "sources": sources,
                "benchmark": sources[0]["code"] if sources else ref.get("benchmark"),
                "leader": leader, "ladder": ladder,
                "leader_source": ({"type": leader_src["type"], "code": leader_src["code"], "name": leader_src["name"], "members": leader_src["members"]} if leader_src else None),
                "caps": self.caps("theme", len(all_mem), is_satellite),
                "data_quality": {"membership_pit": all(x.get("membership_pit") for x in sources) if sources else False,
                                 "history_start": "2025-08-20", "stale_days": 1},
                "resolution": {
                    "seed": s["id"], "level_hint": s.get("level_hint"),
                    "candidates": [{k: v for k, v in x.items() if not k.startswith("_")} for x in scored[:6]],
                    "selected": [{k: v for k, v in x.items() if not k.startswith("_")} for x in selected],
                    "members": len(all_mem), "fallback_reference": fallback,
                    "parent_hint": s.get("parent_hint"), "leader_method": leader_method,
                    "derived_leader": derived_leader, "derived_ladder": derived_ladder,
                },
                "notes": ref.get("notes", ""),
            }
            pc = track_caps.get(item.get("parent_track") or "", {})
            if pc:
                item["caps"]["max_weight"] = min(float(item["caps"]["max_weight"]), float(pc.get("max_weight", 1)))
                item["caps"]["max_names"] = min(int(item["caps"]["max_names"]), int(pc.get("max_names", 99)))
                item["caps"]["max_single_stock"] = min(float(item["caps"]["max_single_stock"]), float(item["caps"]["max_weight"]))
                item["caps"]["max_probe"] = min(float(item["caps"]["max_probe"]), float(item["caps"]["max_single_stock"]) * 0.4)
            (satellite if is_satellite else themes).append(item)
        return {"themes": themes, "satellite_themes": satellite}

    # ── run ──
    def run(self) -> Dict[str, Any]:
        self.load()
        tr = self.resolve_tracks()
        th = self.resolve_themes(tr["track_by_l2"], tr.get("track_caps", {}))
        tr.pop("track_by_l2", None)
        tr.pop("track_caps", None)
        self.report["tracks"] = tr
        self.report["themes"] = th
        self.report["warnings"] = self.warnings
        self.report["errors"] = self.errors
        return self.report


def write_generated(rep: Dict[str, Any]) -> None:
    os.makedirs(GEN, exist_ok=True)
    for key in ("tracks", "themes"):
        pass
    tr = {"version": 1, "as_of": rep["date"], "layer": "L1", "generated": True,
          "source": "seeds.yaml + caps_policy.yaml + Tushare resolver",
          "tracks": rep["tracks"]["tracks"], "satellite_tracks": rep["tracks"]["satellite_tracks"]}
    th = {"version": 1, "as_of": rep["date"], "layer": "L2", "generated": True,
          "source": "seeds.yaml + caps_policy.yaml + Tushare resolver",
          "themes": rep["themes"]["themes"], "satellite_themes": rep["themes"]["satellite_themes"]}
    import os as _os
    if _os.getenv("EXPORT_UNIVERSE_YAML", "0") == "1":
        with open(os.path.join(GEN, "universe_track.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(tr, f, allow_unicode=True, sort_keys=False, width=120)
        with open(os.path.join(GEN, "universe_theme.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(th, f, allow_unicode=True, sort_keys=False, width=120)
    with open(os.path.join(GEN, "resolution_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    # PG source of truth：YAML 仅导出
    try:
        sys.path.insert(0, os.path.join(ROOT, "backend"))
        from app.services.theme_universe_store import save_themes, save_tracks
        save_themes(rep["themes"]["themes"], False)
        save_themes(rep["themes"]["satellite_themes"], True)
        save_tracks(rep["tracks"]["tracks"], False)
        save_tracks(rep["tracks"]["satellite_tracks"], True)
        print("[resolver] tracks/themes -> PG")
    except Exception as e:
        print("[resolver] PG theme write fail:", str(e)[:160])


def diff_report(rep: Dict[str, Any]) -> str:
    ref_tr = load_yaml(os.path.join(CFG, "universe_track.yaml"))
    ref_th = load_yaml(os.path.join(CFG, "universe_theme.yaml"))
    ref_tracks = {str(x["id"]): x for x in (ref_tr.get("tracks") or []) + (ref_tr.get("satellite_tracks") or [])}
    ref_themes = {str(x["id"]): x for x in (ref_th.get("themes") or []) + (ref_th.get("satellite_themes") or [])}
    gen_tracks = {str(x["id"]): x for x in rep["tracks"]["tracks"] + rep["tracks"]["satellite_tracks"]}
    gen_themes = {str(x["id"]): x for x in rep["themes"]["themes"] + rep["themes"]["satellite_themes"]}

    def codes(x):
        return sorted(str(s.get("code")) for s in (x.get("sources") or []) if s.get("code"))

    lines = ["# P0.5 生成物 vs P0 手写版 diff", "",
             f"- date: {rep['date']}",
             f"- tracks: ref {len(ref_tracks)} / gen {len(gen_tracks)}",
             f"- themes: ref {len(ref_themes)} / gen {len(gen_themes)}", ""]
    for label, ref, gen in (("Tracks", ref_tracks, gen_tracks), ("Themes", ref_themes, gen_themes)):
        lines += [f"## {label}", "",
                  "| id | 变化 | ref | gen |", "|---|---|---|---|"]
        for i in sorted(set(ref) | set(gen)):
            r, g = ref.get(i), gen.get(i)
            if not r:
                lines.append(f"| {i} | 新增 | - | {codes(g)} |"); continue
            if not g:
                lines.append(f"| {i} | 缺失 | {codes(r)} | - |"); continue
            ch = []
            if codes(r) != codes(g):
                ch.append(f"sources {codes(r)} -> {codes(g)}")
            if r.get("parent_track") != g.get("parent_track"):
                ch.append(f"parent {r.get('parent_track')} -> {g.get('parent_track')}")
            if (r.get("leader") or {}).get("code") != (g.get("leader") or {}).get("code"):
                ch.append(f"leader {(r.get('leader') or {}).get('code')} -> {(g.get('leader') or {}).get('code')}")
            rw, gw = (r.get("caps") or {}).get("max_weight"), (g.get("caps") or {}).get("max_weight")
            rn, gn = (r.get("caps") or {}).get("max_names"), (g.get("caps") or {}).get("max_names")
            if rw != gw or rn != gn:
                ch.append(f"caps w {rw}->{gw} n {rn}->{gn}")
            if r.get("status") != g.get("status"):
                ch.append(f"status {r.get('status')} -> {g.get('status')}")
            if ch:
                lines.append(f"| {i} | {'; '.join(ch)} | {codes(r)} | {codes(g)} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261023")
    ap.add_argument("--max-candidates", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--fast", action="store_true", help="只解析结构，不做成员/历史查询（调试用）")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()} or None
    r = Resolver(a.date, a.max_candidates, only)
    rep = r.run()
    write_generated(rep)
    d = diff_report(rep)
    with open(os.path.join(GEN, "diff_report.md"), "w", encoding="utf-8") as f:
        f.write(d)
    # 摘要
    tr = rep["tracks"]; th = rep["themes"]
    print(f"tracks: {len(tr['tracks'])} + {len(tr['satellite_tracks'])} satellite")
    print(f"themes: {len(th['themes'])} + {len(th['satellite_themes'])} satellite")
    print(f"warnings: {len(rep['warnings'])}; errors: {len(rep['errors'])}")
    for w in rep["warnings"][:10]:
        print("  ⚠️", w)
    for e in rep["errors"][:10]:
        print("  ❌", e)
    print(f"written: {GEN}/universe_track.yaml, universe_theme.yaml, resolution_report.json, diff_report.md")


if __name__ == "__main__":
    main()
