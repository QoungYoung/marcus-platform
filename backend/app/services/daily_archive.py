# -*- coding: utf-8 -*-
"""daily_archive.py — G2 每日存档（2026-09-12，蓝图 §7 G2）。

**为什么需要它**：生产链里有一批**当日覆盖型**文件（名字里不带日期），每天被新的覆盖：
  `concept_long.json` / `theme_inst_flow.json` / `etf_share_flow.json`（gate 链第 1–3 步）
  `main_line_state.json`（研报 catalyst）/ `stock_confirm_result.json`（选票）
  `strategy_state.json` / `theme_gap_scan.json` / `p2_gate_daily_report.json` / `wave_pivots.json` / `wolf_*.json`
→ 一旦被覆盖，历史**只能靠回放猜**（此前实测回放保真度仅 ~73%，根因就是缺这些历史）。
本模块把「当日全部产物」在收盘后**原样快照**到 `data/_archive/<date>/`，并写 `manifest.json`
（含来源路径、大小、sha256、**缺哪些预期文件**），从此不再产生新的历史缺口。

**口径**
  · 只存**交易日**；非交易日跳过（沿用 wolf_eod.gate）
  · 快照是**只读复制**：不修改任何生产文件，也不写数据库
  · 幂等：重复运行会覆盖同名快照并追加 `manifest_<HHMMSS>.json`，原快照不删
  · 数据库侧只做**只读导出**（legs/triggers/日账本/账户），失败不影响文件快照

开关 `WOLF_DAILY_ARCHIVE`（代码默认 0；2026-09-12 按阶段 0 计划在生产置 1）。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
from typing import Any, Dict, List, Optional

# ── 需要快照的文件（生产 /app/data 实测清单，2026-09-12）──
# 1) 带日期型：按当天日期拼接
PER_DATE = [
    "mainline_gate_{d}.json",
    "heat_v2_{d}.json",
    "trend_confirm_{d}_long.json",
    "wave_state_{d}.json",
    "wave_state_{dash}.json",
    "main_line_state_{d}.json",
]
PER_DATE_GLOB = ["chain_map_{d}*.json"]
# G1 决策对象（19:45 产出 → 19:50 快照）
DECISION = ["decision/{d}.json"]
# 2) 当日覆盖型：名字不带日期，**这是存档的主要价值所在**
SINGLE = [
    "concept_long.json",              # gate 第1步 概念长线
    "theme_inst_flow.json",           # gate 第3步 机构流（旧名 inst_flow）
    "etf_share_flow.json",            # gate 第2步 ETF 份额流（旧名 etf_flow）
    "main_line_state.json",           # 研报 catalyst 状态
    "mainline_confirm_history.json",  # 主题确认历史（累积型）
    "stock_confirm_result.json",      # 选票结果
    "strategy_state.json",
    "theme_gap_scan.json",
    "p2_gate_daily_report.json",
    "wave_pivots.json",
    "latest_hot_sectors.json",
    "plan_library.json",
    "plan_replay_log.json",
]
SINGLE_GLOB = ["wolf_*.json"]         # 我们自己的各机制状态文件（体积小、信息密度高）


def max_payload_bytes() -> int:
    """单条 payload 的落库上限（默认 8MB，可用 WOLF_ARCHIVE_MAX_BYTES 调）。

    实测：`concept_long.json` 约 **2.17MB**（gate 链第 1 步的概念矩阵），是当日最重要的产物之一；
    原先 2MB 的上限把它挡在库外（只留文件），不合适 → 默认放宽到 8MB。
    真超大文件（>上限）仍只做文件快照，并在 manifest 里如实记录 skipped 原因。
    """
    try:
        return int(float(os.getenv("WOLF_ARCHIVE_MAX_BYTES", "") or 8 * 1024 * 1024))
    except (TypeError, ValueError):
        return 8 * 1024 * 1024


def artifact_key(name: str, d8: str) -> Optional[str]:
    """文件名 → 稳定的 artifact_key（去日期/扩展名）。

    mainline_gate_20260911.json → mainline_gate ｜ trend_confirm_20260911_long.json → trend_confirm_long
    wave_state_2026-09-11.json → wave_state ｜ decision/20260911.json → decision
    非 json 返回 None（csv 等只留文件快照）。
    """
    n = os.path.basename(str(name))
    if not n.endswith(".json"):
        return None
    stem = n[:-5]
    dash = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
    for tok in (dash, d8):
        stem = stem.replace(tok, "")
    stem = stem.strip("_-")
    while "__" in stem:
        stem = stem.replace("__", "_")
    stem = stem.strip("_-")
    return stem or "decision"


def sanitize_payload(raw: str) -> Optional[str]:
    """把文件内容规整成 PostgreSQL 能吃的 JSONB。

    生产实测（2026-09-11）：`strategy_state.json` 里的 `a50_futures.change` 是 **NaN**
    （取数失败/除零留下的），Python 的 json 容忍、**PostgreSQL 的 jsonb 拒绝**
    （invalid input syntax for type json）→ 若直接 CAST 会整条失败。
    这里把 NaN/Infinity 统一转成 null；解析失败返回 None（调用方记为 bad_json 跳过）。
    """
    try:
        obj = json.loads(raw, parse_constant=lambda _x: None)
    except Exception:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, allow_nan=False)
    except Exception:
        return None


def should_skip_payload(entry: Dict[str, Any], d8: str) -> Optional[str]:
    """纯函数：该条目是否不进 jsonb。返回 None=应落库，否则返回原因（'not_json'/'too_large'）。"""
    if artifact_key(entry.get("name") or "", d8) is None:
        return "not_json"
    if int(entry.get("size") or 0) > max_payload_bytes():
        return "too_large"
    return None


def ensure_table(db) -> None:
    """防御性建表（正式 schema 在 app/database.py 的迁移里）。"""
    from sqlalchemy import text
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS daily_artifacts (
            trade_date  VARCHAR(8)  NOT NULL,
            artifact_key TEXT       NOT NULL,
            payload     JSONB,
            sha256_16   TEXT,
            src_path    TEXT,
            src_mtime   TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (trade_date, artifact_key)
        )"""))
    db.commit()


def upsert_artifacts(d8: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把当天结论类产物 upsert 进 `daily_artifacts`（双写的"库"那一半）。失败不抛。"""
    out: Dict[str, Any] = {"ok": True, "upserted": 0, "skipped": [], "errors": []}
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception as e:
        return {"ok": False, "upserted": 0, "skipped": [], "errors": ["import:%s" % type(e).__name__]}
    db = None
    try:
        db = SessionLocal()
        ensure_table(db)
        for e in entries:
            key = artifact_key(e["name"], d8)
            why = should_skip_payload(e, d8)
            if why:
                out["skipped"].append({"name": e["name"], "why": why, "size": e.get("size")})
                continue
            try:
                with open(e["src"], encoding="utf-8") as f:
                    payload = sanitize_payload(f.read())
                if payload is None:
                    out["skipped"].append({"name": e["name"], "why": "bad_json"})
                    continue
                # 每条一个 SAVEPOINT：单条失败只回滚它自己（此前整体 rollback 曾把前面 15 条一起丢掉）
                save = db.begin_nested()
                db.execute(text("""
                    INSERT INTO daily_artifacts (trade_date, artifact_key, payload, sha256_16, src_path, src_mtime)
                    VALUES (:d, :k, CAST(:p AS jsonb), :h, :sp, to_timestamp(:m))
                    ON CONFLICT (trade_date, artifact_key) DO UPDATE
                      SET payload = EXCLUDED.payload, sha256_16 = EXCLUDED.sha256_16,
                          src_path = EXCLUDED.src_path, src_mtime = EXCLUDED.src_mtime,
                          created_at = now()
                """), {"d": d8, "k": key, "p": payload, "h": e.get("sha256_16"),
                       "sp": e.get("src"), "m": e.get("mtime") or 0})
                save.commit()
                out["upserted"] += 1
            except Exception as ex:
                out["errors"].append("%s:%s" % (e["name"], str(ex)[:70]))
                try:
                    save.rollback()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
        db.commit()
    except Exception as e:
        out["ok"] = False
        out["errors"].append("session:%s" % str(e)[:80])
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def load_artifacts(d8: str, keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """从库里读回当天产物 {key: payload}（供后续度量/复盘只读使用）。"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception:
        return {}
    db = None
    try:
        db = SessionLocal()
        sql = "SELECT artifact_key, payload FROM daily_artifacts WHERE trade_date = :d"
        if keys:
            sql += " AND artifact_key = ANY(:ks)"
        rows = db.execute(text(sql), {"d": d8, "ks": keys or []}).mappings().all()
        return {r["artifact_key"]: r["payload"] for r in rows}
    except Exception:
        return {}
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def enabled() -> bool:
    return os.getenv("WOLF_DAILY_ARCHIVE", "0").strip().lower() not in ("0", "false", "no", "")


def data_dir() -> str:
    return os.environ.get("DATA_DIR", "/app/data")


def archive_dir(d8: str) -> str:
    return os.path.join(data_dir(), "_archive", str(d8))


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def expected_files(d8: str) -> List[str]:
    """当天**预期存在**的文件绝对路径（用于 manifest 里报告缺失）。"""
    D = data_dir()
    out: List[str] = []
    for tpl in PER_DATE:
        out.append(os.path.join(D, tpl.format(d=d8, dash="%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8]))))
    for g in PER_DATE_GLOB:
        out.extend(sorted(glob.glob(os.path.join(D, g.format(d=d8)))))
    for tpl in DECISION:
        out.append(os.path.join(D, tpl.format(d=d8)))
    for name in SINGLE:
        out.append(os.path.join(D, name))
    for g in SINGLE_GLOB:
        out.extend(sorted(glob.glob(os.path.join(D, g))))
    # 去重保序
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def snapshot_files(d8: str, dest: Optional[str] = None) -> Dict[str, Any]:
    """只读复制当天产物 → `_archive/<d8>/`，返回 {copied, missing, entries}。"""
    dest = dest or archive_dir(d8)
    os.makedirs(dest, exist_ok=True)
    entries, missing = [], []
    for src in expected_files(d8):
        if not os.path.isfile(src):
            missing.append(os.path.basename(src))
            continue
        name = os.path.basename(src)
        try:
            shutil.copy2(src, os.path.join(dest, name))
            entries.append({"name": name, "src": src, "size": os.path.getsize(src),
                            "sha256_16": _sha256(src), "mtime": int(os.path.getmtime(src))})
        except Exception as e:
            missing.append("%s(复制失败:%s)" % (name, type(e).__name__))
    return {"copied": len(entries), "missing": missing, "entries": entries, "dir": dest}


# ── 数据库只读导出（失败不影响文件快照）──
DB_EXPORTS = [
    ("t_conditions", "select * from t_conditions order by id"),
    ("t_triggers_today", "select * from t_triggers where created_at::date = :d order by id"),
    ("t_daily_state_today", "select * from t_daily_state where trade_date = :d"),
    ("paper_account_info", "select * from paper_account_info"),
    ("paper_positions", "select * from paper_positions"),
]


def db_snapshot(d8: str, dest: Optional[str] = None) -> Dict[str, Any]:
    """把 legs/triggers/日账本/账户持仓**只读**导出为 csv（失败返回 {ok:False}，不抛）。"""
    dest = dest or archive_dir(d8)
    d_dash = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
    out: Dict[str, Any] = {"ok": True, "files": [], "errors": []}
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception as e:
        return {"ok": False, "files": [], "errors": ["import:%s" % type(e).__name__]}
    db = None
    try:
        db = SessionLocal()
        for name, sql in DB_EXPORTS:
            try:
                rows = db.execute(text(sql), {"d": d_dash}).mappings().all()
                p = os.path.join(dest, "db_%s.csv" % name)
                with open(p, "w", encoding="utf-8") as f:
                    if rows:
                        cols = list(rows[0].keys())
                        f.write(",".join(cols) + "\n")
                        for r in rows:
                            f.write(",".join('"%s"' % str(r[c]).replace('"', "'") for c in cols) + "\n")
                    else:
                        f.write("(empty)\n")
                out["files"].append({"name": "db_%s.csv" % name, "rows": len(rows)})
            except Exception as e:
                out["errors"].append("%s:%s" % (name, str(e)[:80]))
    except Exception as e:
        out["ok"] = False
        out["errors"].append("session:%s" % str(e)[:80])
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def run(d8: Optional[str] = None, save: bool = True, skip_db: bool = False) -> Dict[str, Any]:
    """当天存档：文件快照 + DB 只读导出 + manifest。"""
    import datetime as _dt
    d8 = d8 or _dt.date.today().strftime("%Y%m%d")
    if not save:
        return {"ok": True, "dry_run": True, "expected": len(expected_files(d8))}
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    snap = snapshot_files(d8)
    dbbits = {"ok": True, "files": [], "errors": ["skipped"]} if skip_db else db_snapshot(d8)
    # **双写**：结论类产物 upsert 进 daily_artifacts（库为主）+ 保留文件快照（兜底）
    art = {"ok": True, "upserted": 0, "skipped": [], "errors": ["skipped"]} if skip_db else \
        upsert_artifacts(d8, snap["entries"])
    dbres = {"files": dbbits.get("files"), "errors": dbbits.get("errors"), "artifacts": art}
    manifest = {
        "date": d8, "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "dir": snap["dir"], "copied": snap["copied"], "missing": snap["missing"],
        "entries": snap["entries"], "db": dbres,
        "note": ("含当日覆盖型文件的快照（concept_long/theme_inst_flow/etf_share_flow/main_line_state/"
                 "stock_confirm_result 等），这类文件不带日期、每天被覆盖；"
                 "同时双写进 PostgreSQL 表 daily_artifacts（trade_date, artifact_key）"),
    }
    try:
        with open(os.path.join(snap["dir"], "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        ts = _dt.datetime.now().strftime("%H%M%S")
        with open(os.path.join(snap["dir"], "manifest_%s.json" % ts), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
    except Exception as e:
        manifest["manifest_error"] = str(e)[:80]
    print("[archive] %s 快照 %d 文件 → %s｜落库 %d 条（跳过 %d）｜缺失 %d"
          % (d8, snap["copied"], snap["dir"], art.get("upserted", 0), len(art.get("skipped") or []),
             len(snap["missing"])))
    if art.get("errors"):
        print("[archive]   落库错误: %s" % art["errors"][:2])
    if snap["missing"]:
        print("[archive]   缺失: " + ", ".join(snap["missing"][:12]))
    return {"ok": True, **{k: manifest[k] for k in ("date", "dir", "copied", "missing")}, "manifest": manifest}


def load(d8: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(archive_dir(d8), "manifest.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def available_dates() -> List[str]:
    root = os.path.join(data_dir(), "_archive")
    try:
        return sorted([d for d in os.listdir(root) if d.isdigit() and len(d) == 8])
    except Exception:
        return []
