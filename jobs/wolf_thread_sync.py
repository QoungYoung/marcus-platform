# -*- coding: utf-8 -*-
"""抓狼大帖子（楼主 + 回复）→ data/wolf_posts.json（2026-10-21 ✓）。

背景：用户「NGA 插件里有访问 key ✓ 读他每天发言和回复，等他帖子什么时候没了再说」✓
  ⇒ 用同一套 Cookie（`ngaPassportUid` / `ngaPassportCid` ✓ 从环境变量 `NGA_UID` / `NGA_CID` 读 ✓）
     抓最后几页 ✓ 只保留**楼主发言**（`OP_UID`）与该页**回复**摘要 ✓。
  · 产出 `data/wolf_posts.json`（**gitignore ✓ 不进仓库**）供 `wave_advice_agent` 当**参考** ✓；
  · **纪律仍然是 `config/wave_advice_policy.json`（我们自己的 ✓）** —— 他断更/帖子没了 ⇒ 本任务自动失效 ✓
    不阻塞任何东西 ✓（用户 2026-10-21：「等他帖子什么时候没了再说」✓）。

用法::
    python jobs/wolf_thread_sync.py --dry            # 只打印今天抓到什么 ✓ 不写文件
    python jobs/wolf_thread_sync.py                  # 写 data/wolf_posts.json ✓
    python jobs/wolf_thread_sync.py --pages 2        # 抓最后 2 页（默认 2 页 = 40 层 ✓）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

TID = "47288722"
OP_UID = "150058"          # 楼主 -阿狼-
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or "data"
OUT = os.path.join(DATA_DIR, "wolf_posts.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def cookie():
    """环境变量优先 ✓，其次读 `data/nga_cookie.env`（KEY=VALUE ✓ 宿主可写 ✓ 免重建容器 ✗）。

    为什么加文件回退（2026-10-21 实测）：`docker restart` **不会**重新读 compose 的 env_file ✗，
    新加的 NGA_UID/NGA_CID 在容器里看不到 ✗；而 `data/` 是 bind-mount ✓ ⇒ 写个文件就能被读到 ✓，
    且 600 权限 ✓ 不进仓库 ✓（.gitignore 的 data/*.json 之外，本文件也用 *.env 忽略 ✓）。
    """
    uid = (os.environ.get("NGA_UID") or "").strip()
    cid = (os.environ.get("NGA_CID") or "").strip()
    if not (uid and cid):
        try:
            for line in open(os.path.join(DATA_DIR, "nga_cookie.env"), encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "NGA_UID":
                    uid = v.strip()
                elif k.strip() == "NGA_CID":
                    cid = v.strip()
        except Exception:
            pass
    return uid, cid


def fetch(page, op_only=True):
    """op_only=True ⇒ **只看楼主视图**（authorid）；页码超出会自动**钳到末页** ✓（实测 page=70 与 66 同 ✓）"""
    uid, cid = cookie()
    if not (uid and cid):
        raise RuntimeError("缺 NGA_UID / NGA_CID ✗（插件配置或 .env ✓）")
    url = "https://bbs.nga.cn/read.php?tid=%s%s&page=%d" % (
        TID, ("&authorid=%s" % OP_UID) if op_only else "", page)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://bbs.nga.cn/",
        "Cookie": "ngaPassportUid=%s; ngaPassportCid=%s" % (uid, cid)})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("gb18030", "ignore")


def total_pages(html):
    m = re.search(r"共\s*(\d+)\s*页", html) or re.search(r"totalPage\s*=\s*(\d+)", html)
    return int(m.group(1)) if m else None


def parse(html, all_op=False):
    """→ [{'lou','uid','author','date','text','is_op'}...]（按 NGA 实际结构 ✓ 2026-10-21 校准）

    结构（实测 ✓）：
      ... title='reply time'>2026-10-20 18:08</span> ... id='postcontent<lou>' class='postcontent ubbcode'>正文</span>
      ... id='posterinfo<lou>' ... uid=<作者uid> ...
    """
    out = []
    parts = re.split(r"id='postdate(\d+)'", html)
    for k in range(1, len(parts) - 1, 2):
        lou, blk = parts[k], parts[k + 1]
        date = re.search(r"title='reply time'>([0-9:\- ]{10,20})</span>", blk)
        body = re.search(r"id='postcontent" + lou + r"'[^>]*>(.*?)</span>", blk, re.S)
        text = ""
        if body:
            text = re.sub(r"\[quote\].*?\[/quote\]", "", body.group(1), flags=re.S)
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\n{2,}", "\n", text).strip()[:900]
        pinfo = re.search(r"id='posterinfo" + lou + r"'(.{0,800})", blk, re.S)
        uid = re.search(r"uid=(\d+)", pinfo.group(1)) if pinfo else None
        author = re.search(r"class='author'[^>]*>([^<]{1,30})<", (pinfo.group(1) if pinfo else blk)) \
            or re.search(r"uid=\d+[^>]*>([^<]{1,30})<", (pinfo.group(1) if pinfo else ""))
        out.append({"lou": int(lou),
                    "uid": (uid.group(1) if uid else ""),
                    "author": (author.group(1).strip() if author else ""),
                    "date": (date.group(1).strip() if date else ""),
                    "text": text,
                    "is_op": bool(all_op or (uid and uid.group(1) == OP_UID))})   # 只看楼主视图 ⇒ 全是他的 ✓
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    # ★ 只看楼主视图 + 超大页码（服务端自动钳到末页 ✓ 实测 ✓）⇒ 一次就拿到他最新发言 ✓
    try:
        pages = [999] if args.pages <= 1 else [999]
        h = fetch(999, op_only=True)
        floors = parse(h, all_op=True)
        print("只看楼主视图 ✓ 抓到 %d 层（他最新发言 ✓）" % len(floors))
        if args.pages > 1:
            # 再补前几页（用末层的"楼主序号"回推 ✓ 20 条/页 ✓）
            last_no = max((f["lou"] for f in floors), default=0)
            for p in range(max(1, last_no // 20), max(1, last_no // 20) + args.pages - 1):
                try:
                    floors.extend(parse(fetch(p, op_only=True), all_op=True))
                except Exception as e:
                    print("  page %d 失败: %s" % (p, str(e)[:60]))
                time.sleep(0.4)
    except Exception as e:
        print("抓取失败（%s）⇒ 跳过 ✓（他帖子没了也不影响任何东西 ✓）" % str(e)[:120])
        return 0
    op = [f for f in floors if f["is_op"]]
    day = sorted({(f["date"] or "")[:10] for f in op})[-1:] or [""]
    print("抓到 %d 层 ✓ 其中楼主 %d 层 ✓ 最新日期 %s" % (len(floors), len(op), day[0] or "-"))
    for f in op[-5:]:
        print("  %s ｜ %s | %s" % (f["date"], f["author"], f["text"][:90].replace("\n", " ")))
    if not op and not floors:
        print("没抓到内容 ⇒ 不写文件 ✓")
        return 0
    if args.dry:
        print("（--dry ✓ 不写文件）")
        return 0
    try:
        old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"floors": []}
    except Exception:
        old = {"floors": []}
    # ★ 去重键：pid 优先 ✓；解析器不产 pid 时用 `lou`（只看楼主视图的楼号 = 他的发言序号 ✓ 稳定 ✓）
    def _key(f):
        return f.get("pid") or ("lou:%s" % f.get("lou"))

    bypid = {_key(f): f for f in (old.get("floors") or [])}
    for f in floors:
        bypid[_key(f)] = f
    merged = sorted(bypid.values(), key=lambda x: x.get("lou") or 0)
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"tid": TID, "updated": time.strftime("%Y%m%d"), "op_uid": OP_UID,
               "total": len(merged), "floors": merged[-400:]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("已写 %s（累计 %d 层，保留最近 400 ✓）" % (OUT, len(merged)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
