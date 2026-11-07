# -*- coding: utf-8 -*-
"""theme_dedup.py — 题材成员重叠去重（纯函数，供 discovery/resolver/agent 复用）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

@dataclass
class ThemeDedup:
    themes: List[Dict[str, Any]]
    relations: List[Dict[str, Any]]
    owned: Dict[str, str]
    def eligible(self, theme_id: str) -> Set[str]:
        for t in self.themes:
            if t["id"] == theme_id:
                return set(t.get("eligible") or [])
        return set()


def _ov(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def dedup(themes: List[Dict[str, Any]], members: Dict[str, Set[str]],
          child_overlap: float = 0.50, child_ratio: float = 0.70,
          merge_overlap: float = 0.60) -> ThemeDedup:
    """按成员重叠给题材关系，并把共享股票分给更具体的题材。

    themes: [{"id","name",...}]
    members: {theme_id: set(codes)}
    """
    ids = [t["id"] for t in themes]
    rel: List[Dict[str, Any]] = []
    parent: Dict[str, str] = {}
    merged: Dict[str, str] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ma, mb = members.get(a, set()), members.get(b, set())
            o = _ov(ma, mb)
            j = len(ma & mb) / len(ma | mb) if (ma | mb) else 0.0
            if o >= merge_overlap:
                rel.append({"a": a, "b": b, "relation": "merge", "overlap": round(o, 3), "jaccard": round(j, 3)})
                # 合并到成员更多的题材
                keep, drop = (a, b) if len(ma) >= len(mb) else (b, a)
                merged[drop] = keep
            elif o >= child_overlap and min(len(ma), len(mb)) / max(len(ma), len(mb)) <= child_ratio:
                p, c = (a, b) if len(ma) > len(mb) else (b, a)
                rel.append({"a": a, "b": b, "relation": "child", "overlap": round(o, 3), "jaccard": round(j, 3), "parent": p, "child": c})
                parent[c] = p
            else:
                rel.append({"a": a, "b": b, "relation": "sibling", "overlap": round(o, 3), "jaccard": round(j, 3)})
    # 每个股票归到“更具体”的题材：child 归 parent；sibling 归成员更少的题材
    owned: Dict[str, str] = {}
    for t in themes:
        tid = t["id"]
        if tid in merged:
            t["merged_into"] = merged[tid]
        if tid in parent:
            t["child_of"] = parent[tid]
        t["eligible"] = sorted(members.get(tid, set()))
    base = [t for t in themes if t["id"] not in merged and t["id"] not in parent]
    for t in base:
        for c in sorted(members.get(t["id"], set())):
            owned.setdefault(c, t["id"])
    # sibling 共享：较小题材优先
    for t in sorted(base, key=lambda x: len(members.get(x["id"], set()))):
        elig = set(members.get(t["id"], set()))
        for c in list(elig):
            if owned.get(c, t["id"]) != t["id"]:
                elig.discard(c)
        t["eligible"] = sorted(elig)
    # child/merged 不单独给 eligible
    for t in themes:
        if t["id"] in merged or t["id"] in parent:
            t["eligible"] = []
    return ThemeDedup(themes=themes, relations=rel, owned=owned)
