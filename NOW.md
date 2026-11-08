# NOW.md — 当前生效状态

> **规矩：每次任务收尾就地覆盖本文件，不追加。上限 200 行。**
> 只写"现在生效的结论"，不写过程。历史流水在 memoir（`memoir_read`），归档在 PROJECT_MEMORY.md（已移出 git）。
>
> 最后更新：2026-11-08（依据：实测 + 当日修复；工程基建已落地，见 §2 与 git log）

---

## 0. 一句话

这是一个 A 股量化/交易平台：本地 DSH 会话写代码 → push 到服务器 git → 线上容器跑。**所有持久化走线上 PG，所有代码走 git，没有第二条路。**

---

## 1. 环境真相（2026-11-08 实测，不是猜的）

| 项 | 事实 |
|---|---|
| 工作目录 | `/home/fengx/marcus-platform`（Linux）。Windows 机器是**另一份检出**，平台规则见 `AGENTS.md` 第 1 节 |
| Python | `.venv/bin/python` → 3.12.3（不要用系统 python） |
| **PG（重要）** | `.env` 的 `DATABASE_URL` → `127.0.0.1:18789` → **经隧道到线上** `marcusrv:127.0.0.1:5432`（`wave_state` 445 行 = 线上库） |
| 另外两个库 | 本地独立容器 `marcus-postgres` 在 `5432`（439 行，**不是线上**）、`bt_pg` 在 `5433`（回测库）。**三个库互不相同，别混** |
| 隧道 | `scripts/pg_tunnel.sh {start\|status\|stop\|restart}`；cron 每 5 分钟自愈 + 开机拉起；日志 `~/.dsh/pg-tunnel.log` |
| 线上主机 | `81.70.44.68`，容器 `marcus-frontend / marcus-dsh / marcus-backend / marcus-worker / marcus-postgres` |
| **部署链路** | 本地 `git push srv main` → 线上 `/opt/marcus-platform` 执行 `git pull self`（`self=/opt/git/marcus-platform.git`）。**这是唯一部署路径** |
| GitHub `origin` | `ghfast.top` 代理，**已落后 114 个提交，是废弃镜像**。别拿它当真相，也别靠它部署 |
| SSH | `~/.ssh/config` 已配 `ControlMaster/ControlPersist`（2026-11-08），**裸 `ssh marcusrv` 直接可用**，不要再加 `-4 -o IPQoS` |
| 数据目录 | `data/` 被 gitignore，**每个环境一份，不是同步状态**。不要用本地 `data/` 判断线上 |
| 搜索 | 本机**没有 rg**，用 `grep` / `find` / `git grep` |
| DSN 唯一来源 | `core/dsn.py`：`dsn.main()` / `dsn.backtest()`（进程环境 > `.env` > 报错） |
| 测试 | `.venv/bin/python -m pytest backend/tests -q`（前提：上面的 PG 可连） |

---

## 2. 四条硬约束（走错会红，不是建议）

1. **结论必须落文件。** 任何"先总结 / 先定方案 / 先对齐口径"的产物，必须写进本文件或 `docs/`，不能只留在对话里。对话会被压缩，文件不会。
2. **持久化只走 PG。** 新代码不许把 `data/*.json` 当状态存储；全仓裸 `json.dump` 计数是棘轮（`scripts/lint_persist.py`），**只能降不能升**。
3. **连接串只从 `core/dsn.py` 取。** 不许在代码里写 `postgresql://...` 字面量；
   `scripts/lint_dsn.py` 是棘轮（基线 2 处，在 `apps/main_line/db_dsn.py` 的多候选探活里，有意保留）。
4. **代码只走 git。** 禁 `scp`/`rsync` 传代码到线上；线上只允许 `git pull --ff-only`。
5. **收尾必须跑 `scripts/close_check.py`。** 非 0 退出码就不许说"完成"。

---

## 3. 在途工作（未完成，别当已完成）

- **无。** 2026-11-08 已把上一次会话遗留的 3 个文件收尾并提交（`46beb6eb`）：
  `theme_candidate_policy.yaml`（补回注释）+ `theme_b_candidates.py` /
  `theme_coarse_screen.py`（PG 优先读题材配置，降级改成打 stderr 警告，不再静默）。
- 当前工作区干净，`HEAD == srv/main`。**线上检出仍落后 4 个提交**，属于"待部署"而非"未提交"。
- 最近提交线：`theme` 候选落 PG（`81ce3226` 一带）+ `缩转放` 盘中监控推送 +
  `chore(agent)` 工程基建修复（`2584ee97`）+ 223 个历史文件入库（`a8650636`）。

---

## 4. 下一步候选

1. **DSN 收口：已完成（2026-11-08）。** 163 处硬编码 → 只剩 2 处（`apps/main_line/db_dsn.py`
   的多候选探活清单，有意保留）。唯一来源是 `core/dsn.py`：`dsn.main()` / `dsn.backtest()`，
   优先级 = 进程环境 > 仓库 `.env` > 明确报错。`backend/app/config.py` 的 `DATABASE_URL`
   改成必填——缺了直接启动失败，而不是静默连 5432。
   ⚠️ 教训：这个改动返工了三次（解析器被误改、`__future__` 被压、自举级数算错），
   批量改连接串必须先问"这个文件是不是自己就是解析器"。
2. **浪型读侧已收口（2026-11-08）**：5 个作业（`shadow_daily` / `event_alert` /
   `entry_timing` / `event_briefing_agent` / `scripts/position_rules`）改为
   `from core.wave import current`（PG）；`data/wave_state.json` 自 2026-11-04 起就是
   冻结导出物，**不要再读它**（`close_check` 第 6 项会拦）。
   ⚠️ 回滚杠杆变更：`WOLF_WAVE_STORE=file` 现在只会读回**冻结档**，真回滚请用 `git revert`。
3. 把 `435 处` 裸 `json.dump` 逐步迁到 PG；每迁完一批就
   `.venv/bin/python scripts/lint_persist.py --update-baseline` 把基线压下去。
4. 让 `scripts/close_check.py` 进 CI / pre-push hook（现在只能靠自觉调用）。
5. 部署方式固定：本地 `git push srv` → 线上 `git pull --ff-only self`。

---

## 5. 禁做清单

- ❌ `scp` / `rsync` / 手工上传任何代码到线上
- ❌ 新增 `data/*.json` 当状态存储（临时中间产物也不行，用 `.dsh-tmp/`）
- ❌ `git add -A` / `git add .`（先逐类看清未跟踪文件再提交）
- ❌ 把 `.env` 里的口令/API key 写进任何被 git 跟踪的文件
- ❌ 用 `data/` 目录内容判断线上状态（它每环境一份）

---

## 6. 怎么更新

任务收尾时**覆盖**本文件：改 §0 一句话、§3 在途、§4 下一步；§1 环境事实有变才改。
不要在这里追加日记——那是 memoir 的活。
