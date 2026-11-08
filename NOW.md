# NOW.md — 当前生效状态

> **规矩：每次任务收尾就地覆盖本文件，不追加。上限 200 行。**
> 只写"现在生效的结论"，不写过程。历史流水在 memoir（`memoir_read`），归档在 PROJECT_MEMORY.md（已移出 git）。
>
> 最后更新：2026-11-08（依据：实测 + 当日修复）

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
| 测试 | `.venv/bin/python -m pytest backend/tests -q`（前提：上面的 PG 可连） |

---

## 2. 四条硬约束（走错会红，不是建议）

1. **结论必须落文件。** 任何"先总结 / 先定方案 / 先对齐口径"的产物，必须写进本文件或 `docs/`，不能只留在对话里。对话会被压缩，文件不会。
2. **持久化只走 PG。** 新代码不许把 `data/*.json` 当状态存储；全仓裸 `json.dump` 计数是棘轮（`scripts/lint_persist.py`），**只能降不能升**。
3. **代码只走 git。** 禁 `scp`/`rsync` 传代码到线上；线上只允许 `git pull --ff-only`。
4. **收尾必须跑 `scripts/close_check.py`。** 非 0 退出码就不许说"完成"。

---

## 3. 在途工作（未完成，别当已完成）

- **3 个已修改未提交的跟踪文件**（上一次会话留下的）：
  - `config/mainline/theme_candidate_policy.yaml` —— 新增 `media / data_elements / xinchuang` 三个题材，**同时删掉了原有的 include/exclude 说明注释**（要补回来）
  - `jobs/theme_b_candidates.py` —— 加了 `try: from app.services.theme_universe_store import load_theme_config` / `except Exception: cfg = {}`，然后**静默退回读 `config/mainline/generated/universe_theme.yaml`**
  - `jobs/theme_coarse_screen.py` —— 同一套改法
  - ⚠️ **待决策**：这个"PG 失败就静默读文件"的反模式要留还是改成显式报错（推荐：改成写日志的显式降级，或直接抛）
- 最近提交线：`theme` 候选落 PG（`81ce3226` 一带）+ `缩转放` 盘中监控推送

---

## 4. 下一步候选

1. 决定 §3 的降级策略，把那 3 个文件收尾掉。
2. 全仓 **959 处**硬编码 DSN 端口（`localhost:5432` 599 / `127.0.0.1:5433` 256 / `127.0.0.1:18789` 104）收口到单一配置源。
3. 线上检出落后于 `srv` 时：`git -C /opt/marcus-platform pull self`。

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
