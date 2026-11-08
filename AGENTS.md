# AGENTS.md — Marcus AI 交易平台

> **本文件只讲"长期不变的规矩"。当前生效的状态在 [`NOW.md`](./NOW.md) —— 会话开头先读它。**
> 本仓库在两台机器上各有一份检出，规则不同。**先判断平台，只读对应那一节。**

```bash
uname -s   # Linux → 读 §1；MINGW/MSYS/CYGWIN → 读 §2
```

---

## 0. 开工顺序（每次都一样）

1. 读 [`NOW.md`](./NOW.md)（≤200 行，当前定稿：环境真相 / 硬约束 / 在途 / 下一步 / 禁做）。
2. 需要历史细节时再 `memoir_read`（按需全量，别默认全读——有上千条）。
3. **不要**去读 `PROJECT_MEMORY.md`（3.2MB 追加日志，已移出 git，只作归档）。
4. 收尾必须跑 `scripts/close_check.py`，**非 0 不许说"完成"**。

---

## 1. Linux 环境（当前主力）

### 解释器与依赖
- 用仓库内 `.venv/bin/python`（3.12.3）。**不要用系统 `python`。**
- 依赖已装在 `.venv`；新增依赖要同时更新 `requirements*.txt`。

### 搜索
- 本机**没有 `rg`**，用 `grep -rn` / `find` / `git grep`。（旧版本文档要求用 `rg`，在 Linux 上会直接失败。）

### 数据库（重要，2026-11-08 重写）
- `.env` 的 `DATABASE_URL` → `127.0.0.1:18789` → **经隧道到线上 PG**（`marcusrv:127.0.0.1:5432`）。
- 隧道由 `scripts/pg_tunnel.sh` 维持，cron 每 5 分钟自愈。**连不上先跑 `./scripts/pg_tunnel.sh status`，不要写 JSON 兜底。**
- 本机另有两个**独立的**库，别混：`marcus-postgres` 在 `5432`（本地开发库）、`bt_pg` 在 `5433`（回测库）。
- 跑后端测试前必须能连上 `DATABASE_URL`，否则用例会静默跳过。
- 迁移函数 `backend/app/database.py::_apply_paper_account_migration` 幂等，可重复执行。

### git 与部署（2026-11-08 重写）
- **部署真相只有一条链**：本地 `git push srv main` → 线上 `/opt/marcus-platform` 执行 `git pull self`（`self = /opt/git/marcus-platform.git`）。
- GitHub 的 `origin`（`ghfast.top` 代理）是**废弃镜像，落后很多**。**不要把 `origin` 当真相，也不要用它部署。**
- `main` 的 upstream 应为 `srv/main`。若 `.git/config` 又指回 `origin`，先改回来。
- **禁止 `scp`/`rsync`/手工上传代码到线上。** 历史上这么干导致线上工作区变脏，下次 `git pull` 报 *untracked working tree files would be overwritten*，然后只能继续传文件——这是"越传越乱"的死循环。
- 禁止 `git add -A` / `git add .`：先 `git status --porcelain` 逐类看清再提交。
- 行尾规范 LF；`core.autocrlf=true` 检出会把 LF→CRLF（正常）。`git add` 出现整文件假 diff（增删行数相同）时，先把该文件 CRLF→LF 再 add。
- staging 后核对 `git diff --cached --stat`，确认只有真实改动再 commit。中文 commit message，直接提交 `main`。

### 进程与容器
- 线上容器：`marcus-frontend` / `marcus-dsh` / `marcus-backend` / `marcus-worker` / `marcus-postgres`。
- 重启惯例：改 `backend/app|jobs` → 重启 `marcus-worker`；改 `backend/api|models` → 重启 `marcus-backend`；改 `apps/main_line` 脚本 → `/app/apps` bind mount 直接生效。
- **必须同时有 API 和 Worker**，否则 API 显示 worker 离线、调度不工作。
  - API：`cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`（只做 HTTP）
  - Worker：`cd backend && python -m app.worker_main`（APScheduler + 止损/加仓/候选池监控 + QQ Bot）
- 控制通道在 PG：`worker_status`（worker 5s 发布快照，API 只读）、`worker_commands`（API 写命令，worker 轮询）。
- `/api/v1/scheduler/*` 状态类读快照，控制类写命令；执行历史从 `logs/scheduler_*.jsonl` 直读。
- 拆进程原因：19 个调度任务 + 监控线程与 HTTP 同进程共享 GIL，接口偶发 3-20s 卡顿。

### 长任务限制
- 经 ssh 启动的容器后台进程约 **213s 被回收**（非 OOM）→ 长拉取必须拆小批次 + 断点续跑；`docker exec -d` 也不能豁免。
- worker 内存 512MB 上限，大任务临时 `docker update --memory 1024m`，跑完还原。

---

## 2. Windows 环境（另一份检出，例如 `F:\pythonProject\AITrade\marcus-platform`）

> 这一节**只在 Windows 上生效**。旧版本文档把它放在最前面，导致 Linux 会话读到一堆 `C:\` 路径和 `$LASTEXITCODE`，整份指令的可信度被带崩——所以现在隔离到本节。

### 解释器
- `C:\veighna_studio\python.exe`（Python 3.13.8），依赖在 `C:\veighna_studio\Lib\site-packages`，命令行直接用 `python`。

### PowerShell 5.1 规则
- 命令分隔用 `;`（不支持 `&&` / `||`，会 ParserError）。
- 不要用 `>` 重定向保存输出再读字节：按 UTF-16 写盘，破坏字节级内容。要精确字节/行尾/编码，用 Python `subprocess.run([...], capture_output=True).stdout`。
- 管道/重定向后的退出码不可信，用 `$LASTEXITCODE` 判断。
- **复杂命令一律"写文件、跑文件"**（最高优先级）：`python -c` / `node -e` / `bash -c` 中含引号、`$`、反引号、中文或长逻辑的，先写到 `$env:TEMP\x.py` 或仓库 `.dsh-tmp/`，再执行文件。
- 数据传递走文件不走命令行参数（`--from-spec spec.json`）。
- 字符串转义备忘：双引号插值（`` `$ `` 转义 `$`、`` `n `` 换行），单引号全字面；要字面 `\n` 直接写 `\n`。
- 看文件真实内容用 read 工具或 `[System.IO.File]::ReadAllText(path, [System.Text.Encoding]::UTF8)`；控制台 ANSI 中文乱码**不代表文件损坏**。
- 中文输出乱码先设 `$env:PYTHONIOENCODING='utf-8'`。
- 备用 shell：`C:\Program Files\Git\bin\bash.exe`（不在 PATH，用完整路径）；WSL 未装发行版不可用；cmd.exe 不用。
- 递归删除/移动：先解析并确认绝对路径在工作区内，全程 PowerShell 原生 cmdlet（`Remove-Item -LiteralPath`）。
- 后台进程：`Start-Process -WindowStyle Hidden`（除非用户要看界面）。

---

## 3. 数据源（两端共用）

- Tushare（2026-09-13 起）：统一走 `core/tushare_relay.py` 中继 —— **datahubco**（`DATAHUBCO_API_KEY`，基础接口快）优先、**promax**（`PROMAX_API_KEY`，聚合接口）兜底；`get_tushare_pro()` 返回与 `tushare DataApi` 兼容的客户端，自动路由 + 退避重试 + 分页 + 降级。
- 旧 `TUSHARE_API_URL=https://ts.gyzcloud.top/api` 的 token 已失效（401），仅 `TUSHARE_SOURCE=legacy` 兜底。批量取数控制频次（datahubco 单请求 ≤5000 行；promax 上游限流）。
- 实时行情：Tushare 不提供，用东财 `push2his.eastmoney.com/api/qt/stock/kline/get`（`klt=101&fqt=1`）。secid 规则：SH 代码 5/6 开头 → `1.x`，SZ → `0.x`；close 在 `klines` 每行第 3 位。
- ETF 池基础信息刷新：`POST /api/v1/etf/sync-tushare`（`backend/app/services/etf_pool_sync.py`，Tushare `etf_basic` 全量 upsert，保留东财快照）。

---

## 4. 持久化规范（2026-11-08 新增，硬约束）

- **状态只走 PG。** 新增代码禁止把 `data/*.json` 当状态存储（临时中间产物放 `.dsh-tmp/`）。
- 全仓裸 `json.dump` 计数是**棘轮**：`scripts/lint_persist.py` 记录基线，**只能降不能升**。迁移掉一处就 `--update-baseline` 把数字压下来。
- 反模式（已发现，别再写）：`try: <PG> except Exception: <静默读本地文件>`。降级必须留日志，或者干脆抛错。

---

## 5. 禁做清单

- ❌ `scp` / `rsync` / 手工上传代码到线上
- ❌ 新增 `data/*.json` 当状态存储
- ❌ `git add -A` / `git add .`
- ❌ 把 `.env` 里的口令 / API key 写进被 git 跟踪的文件
- ❌ 用本地 `data/` 判断线上状态（每环境一份）
- ❌ 不经 §1 的隧道就断言"PG 连不上"（先跑 `scripts/pg_tunnel.sh status`）

---

## 6. OpenSpec × DeepSeek Harness

- openspec CLI 已支持 `deepseekharness` 工具目标（DSH 直接读项目 `.agents/skills/` 下的 openspec-* 技能）。
- 补丁由 `scripts/patch-openspec-deepseekharness.mjs` 打入全局安装；npm 升级/重装 openspec 后需重跑（`--check` 查状态，`--revert` 撤销）。
- 本仓库 `.agents/skills/.openspec-target = deepseekharness`，技能内以 `$openspec-<skill>` 引用兄弟技能。
- 重新生成技能：`openspec init --tools deepseekharness`（extend 模式，幂等）。

## 7. Hallmark 设计技能

- 已安装到 `.agents/skills/hallmark/`（DSH 自动加载，输入框 `@hallmark` 可提及）。
- 更新：`npx skills add nutlope/hallmark`，或从 https://github.com/nutlope/hallmark 拷贝 `skills/hallmark/`。
- 用法：默认（新建 UI）、`hallmark audit <target>`（评分不改）、`hallmark redesign <target>`、`hallmark study <截图|URL>`。

## 8. 插件固化（动态 Cordis 插件持久化）

- 动态插件（`cordis_define` / `cordis_run`）默认**临时**：进程重启失效。流程固定「定义 → 运行 → 固化」，除非用户明确要临时。
- 固化：`node scripts/persist-cordis-plugin.mjs --from-spec scripts/specs/<id>.spec.json`（spec = `cordis_define` 入参：id/name/purpose/host/client）；`--check` 查状态，`--remove` 卸载。
- 落盘：`~/.dsh/profiles/node_modules/dsh-<id>/`（`package.json` + `lib/index.js` 宿主 + `lib/client.js` 浏览器端）+ `~/.dsh/profiles/web/cordis.patch.yml` 注册行；**重启 Harness 后生效**。
- 已固化：`openspec-mention`（spec：`scripts/specs/openspec-mention.spec.json`）。
- 改已固化插件：直接改 `~/.dsh/profiles/node_modules/dsh-<id>/lib/*.js` 后重启；或改 spec 重跑脚本覆盖。
