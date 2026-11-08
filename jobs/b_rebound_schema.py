# -*- coding: utf-8 -*-
"""b_rebound_schema.py — B反「段位 / 锚点」两张表的**唯一** DDL（幂等）。

为什么需要：这两张表 2026-10-23 是用 `.dsh-tmp/brebound/{wave_state,anchors}.sql` **手工**建在
**生产**库上的，代码里没有 DDL ⇒ 换一个库（本地 dev、重建卷、新环境）就
`relation "b_rebound_wave_state" does not exist`。2026-10-30 14:50 本地调度即此：
`[warn] 浪型 PG 读取失败，退回文件` 被渲染进通知的「错误」栏（任务其实 success，走的是
`config/b_rebound_anchors.json` 兜底）⇒ 读写两侧各自 `ensure_schema()` 一次即可自愈 ✓。

列口径 = 生产实际列（含后来手工 alter 补的 `level_key` / `level`）。
"""
from __future__ import annotations

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__)))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402

import os

DEFAULT_DSN = _dsn_src.main()

DDL = (
    """create table if not exists b_rebound_wave_state (
  id              bigserial primary key,
  as_of           varchar(8) not null unique,
  level           text,
  completed       text,
  running         text,
  next_watch      text,
  rejudge_trigger text,
  evidence        text,
  source          varchar(16),
  created_at      timestamptz default now()
)""",
    "alter table b_rebound_wave_state add column if not exists level_key text",
    """create table if not exists b_rebound_anchors (
  id            bigserial primary key,
  instrument    varchar(16) not null default '000001.SH',
  session_date  varchar(8)  not null,
  price         numeric     not null,
  kind          varchar(8)  not null,
  label         varchar(32),
  role          varchar(16),
  status        varchar(12) not null default 'candidate',
  evidence      text,
  confirmed_at  timestamptz,
  superseded_by bigint,
  created_by    varchar(24),
  created_at    timestamptz default now(),
  unique(instrument, session_date, price, kind)
)""",
    "alter table b_rebound_anchors add column if not exists level text",
    "create index if not exists idx_bra_asof "
    "on b_rebound_anchors(instrument, status, confirmed_at)",
    # 盘中浪型反哺（观察级）：**不写生产 wave_state** —— wave_store 默认 PG 优先，
    # 写进去会直接进 p2_entry_gate / t_monitor / wolf_pick 的交易门禁 ✗
    # B反 三条件（他 2026-11-04 13:44）日表：14:50 job 写，接口/卡片读
    """create table if not exists b_rebound_daily (
  session_date  varchar(8) not null unique,
  bottom_state  text,
  cond_feet     boolean default false,
  cond_volume   boolean default false,
  cond_ma5      boolean default false,
  cond_count    int default 0,
  intraday_turn text,
  volume_phase  text,
  ma5           numeric,
  right_side_streak int,
  right_side_ready  boolean default false,
  verdict       text,
  created_at    timestamptz default now()
)""",
    "alter table b_rebound_daily add column if not exists position_pct numeric",
    "alter table b_rebound_daily add column if not exists position_reason text",
    "alter table b_rebound_daily add column if not exists position_used numeric",
    "alter table b_rebound_daily add column if not exists position_delta numeric",
    "alter table b_rebound_daily add column if not exists episode_days int",
    "alter table b_rebound_daily add column if not exists position_source varchar(24)",
    """create table if not exists b_rebound_wave_intraday (
  id          bigserial primary key,
  session_date varchar(8) not null unique,
  as_of_time   varchar(8),
  rung_today   varchar(16),
  rung_index   int,
  prev_rung    int,
  reset        boolean default false,
  reason       text,
  differs      boolean default false,
  created_at   timestamptz default now()
)""",
)


def dsn() -> str:
    return os.getenv("DATABASE_URL") or DEFAULT_DSN


def ensure_schema(conn=None, dsn_str: str | None = None) -> None:
    """建表 / 补列（幂等）。传 conn 复用调用方连接，否则自己连（用完关）。"""
    import psycopg2

    own = conn is None
    if own:
        conn = psycopg2.connect(dsn_str or dsn(), connect_timeout=8)
    try:
        with conn.cursor() as cur:
            for sql in DDL:
                cur.execute(sql)
        conn.commit()
    finally:
        if own:
            conn.close()
