# -*- coding: utf-8 -*-
"""NGA 研究帖读取（app_api 带 Cookie，供交易 agent 作为研究情报源）。

走 POST bbs.nga.cn/app_api.php?__lib=post&__act=list（同工具同款真实抓取），
返回干净 JSON，直接解析，勿用 read.php?tid=X&lite=js（会截成前导逗号残片）。
仅作研究参考，非交易信号——落地仍须经系统打分/纪律过滤。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List

_NGA_API = "https://bbs.nga.cn/app_api.php?__lib=post&__act=list"
_NGA_UID = os.environ.get("NGA_PASSPORT_UID", "42306667")
_NGA_CID = os.environ.get("NGA_PASSPORT_CID", "Z8h37gm99e5h4cps4d5fs1fgh774t2df8v0npev3")


def _cookie() -> str:
    return f"ngaPassportUid={_NGA_UID}; ngaPassportCid={_NGA_CID}"


def read_nga_post(tid: str, page: int = 1, max_floors: int = 40) -> Dict[str, Any]:
    """读 NGA 帖（app_api + Cookie），返回 {ok, title, floors, count}。"""
    data = urllib.parse.urlencode({"page": page, "tid": tid}).encode("utf-8")
    req = urllib.request.Request(
        _NGA_API, data=data, method="POST",
        headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Referer": "https://bbs.nga.cn/",
            "Cookie": _cookie(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    d = json.loads(raw)
    if d.get("code") != 0:
        return {"ok": False, "code": d.get("code"), "msg": d.get("msg"), "floors": [], "count": 0}
    res = d.get("result") or []
    floors: List[Dict[str, Any]] = []
    for r in res[:max_floors]:
        if not isinstance(r, dict):
            continue
        content = re.sub(r"<[^>]+>", " ", r.get("content") or "")
        content = re.sub(r"[img[^]]*]", "[图]", content)
        content = re.sub(r"[url[^]]*]", "[链接]", content)
        content = re.sub(r"[quote[^]]*]", "[引用]", content)
        content = re.sub(r"[/[^]]*]", "", content)
        content = re.sub(r"s+", " ", content).strip()
        au = r.get("author")
        floors.append({
            "lou": r.get("lou"),
            "postdate": r.get("postdate") or "",
            "subject": (r.get("subject") or "").strip(),
            "author": au.get("username", "") if isinstance(au, dict) else "",
            "content": content[:1500],
        })
    return {"ok": True, "code": 0, "title": d.get("tsubject", ""), "floors": floors, "count": len(floors)}


if __name__ == "__main__":
    import sys
    print(json.dumps(read_nga_post("47458281", max_floors=3), ensure_ascii=False, indent=2, default=str)[:1500])
