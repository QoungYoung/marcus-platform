# AGENTS.md — Marcus AI 交易平台 自定义指令

本文件供 Codex 在本仓库工作时遵循：Windows + PowerShell 执行规则 + 本项目环境约定。

## 1. Windows PowerShell 执行规则（通用，可复制到全局 `C:\Users\fengx\.codex\AGENTS.md`）

- **命令分隔用 `;`**：Windows PowerShell 5.1 不支持 `&&` / `||`，遇到会直接 ParserError。
- **不要用 PowerShell `>` 重定向保存命令输出再读字节**：重定向按 UTF-16 写盘，会破坏字节级内容。需要精确字节/行尾/编码分析时，用 Python `subprocess.run([...], capture_output=True).stdout`，或把输出写临时文件后用 Python 读。
- **管道/重定向后的退出码不可信**：用 `$LASTEXITCODE` 判断真实结果。例：`python -m pytest ... | Select-Object -Last 15` 可能显示 exit 1，实际 pytest 返回 0。
- **不要用内联 `python -c "..."` 传复杂 SQL/引号**：PowerShell 转义会破坏内容。长脚本先写临时文件：`Set-Content -Path "$env:TEMP\s.py" -Encoding UTF8` 再 `python "$env:TEMP\s.py"`。
- **复杂命令一律「写文件、跑文件」，绝不内联传参**（最高优先级规则，适用于 `node -e` / `python -c` / `bash -c` / 任何含引号、`$`、反引号、中文或长逻辑的命令）：先用 write 工具（或 `Set-Content`）把脚本写到临时文件（如 `$env:TEMP\x.mjs` 或仓库 `.dsh-tmp/`），再执行 `node "$env:TEMP\x.mjs"`。PowerShell 5.1 引号规则与 bash/node/python 不同，内联 `-e`/`-c` 经过多层转义必然偶发损坏。
- **数据传递走文件，不走命令行参数**：脚本需要的输入用 JSON 文件（如 `--from-spec spec.json`），脚本内部用 `process.argv`/`sys.argv` 读文件路径，不要在 `-e` 里拼字符串。
- **PowerShell 字符串转义备忘**：双引号插值（`` `$ `` 转义 `$`、`` `n `` 换行）、单引号全字面；想产生**字面** `\n`（如 JSON 转义串）直接写 `\n` 即可（PS 不解析 `\n`）。跨层传参前先 `Write-Output` 预览最终字符串。
- **看文件真实内容**：优先用 read 工具，或 `[System.IO.File]::ReadAllText(path, [System.Text.Encoding]::UTF8)`。PowerShell 5.1 的 `Get-Content`/控制台默认 ANSI，中文显示乱码**不代表文件损坏**（文件可能是好的 UTF-8）。
- **中文输出乱码**：先 `$env:PYTHONIOENCODING='utf-8'`；node 侧 console 输出默认 UTF-8，乱码多为 PS 控制台显示问题。
- **本机可用 Git Bash（备用 shell）**：`C:\Program Files\Git\bin\bash.exe`（不在 PATH，用完整路径从 pwsh 调用）。需要 POSIX 语义的一行命令（管道、`sed`/`awk`、单引号直觉）时用：`& 'C:\Program Files\Git\bin\bash.exe' -lc '...'`。复杂逻辑仍按「写文件、跑文件」处理；WSL 未装发行版不可用；cmd.exe 引号规则更糟，不用。
- **搜索用 `rg`**（`rg` 搜内容、`rg --files` 列文件），不要用 `grep`/`findstr`。
- **Windows 递归删除/移动**：先解析并确认目标绝对路径在工作区或明确目录内，全程用 PowerShell 原生 cmdlet（`Remove-Item`/`Move-Item -LiteralPath`），不跨 shell 拼接路径传参。
- **后台进程**：`Start-Process` 加 `-WindowStyle Hidden`（除非用户明确要看界面）。

## 2. 本项目环境（仓库专用）

### Python / 依赖
- 解释器：`C:\veighna_studio\python.exe`（Python 3.13.8），依赖在 `C:\veighna_studio\Lib\site-packages`；命令行直接用 `python`。

### 数据库（重要）
- 共享 PostgreSQL：**本地 == 云服务器同一库**，改动立即影响线上！
- DSN：`postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading`
- 跑后端测试必须设 `$env:DATABASE_URL`（上面的 DSN），否则连错库/用例自动跳过。
- 模拟盘多账户：`paper_accounts`（注册表）+ `paper_account_info`（账本）；`golden_pit` 初始资金 25 万，`stock` 为股票任务账户；迁移函数 `backend/app/database.py::_apply_paper_account_migration` 幂等，可重复执行。

### 测试
- 多账户/黄金坑落盘模块：`backend/tests/test_multi_account_paper_infra.py` + `backend/tests/test_golden_pit_paper_execution.py`（28 用例）。
- 全量：`python -m pytest backend/tests -q`（141 passed，1 个既有无关失败可忽略）。
- 示例：`$env:DATABASE_URL='postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading'; $env:PYTHONIOENCODING='utf-8'; python -m pytest backend/tests/test_multi_account_paper_infra.py -q`
- `core/qq_notifier.py` 顶层 import 会强制覆盖 `DATABASE_URL`（qqbot_service 顶层导入 + core 模块双重 `_load_env()`）；集成测试需在 `setUpModule` 预导入两个模块名规避——新增相关测试保持同样写法。

### 前端
- Vite + React：`cd frontend; npm run build`（tsc + vite build），产物 `frontend/dist`（提交进 git）。
- 云服务器拉取 git 即部署 dist；本地一般不启动后端。

### 进程拆分（API / Worker）
- **必须同时启动两个进程**，否则 API 显示 worker 离线、调度/监控不工作：
  - API：`cd backend; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`（只做 HTTP）
  - Worker：`cd backend; python -m app.worker_main`（跑 APScheduler 任务 + 止损/加仓/候选池/长期池监控 + QQ Bot）
- 控制通道在 PostgreSQL：`worker_status`（worker 每 5s 发布状态快照，API 只读）、`worker_commands`（API 写命令，worker 轮询执行）；表由 `database.py::_apply_worker_control_migration` 幂等创建。
- `/api/v1/scheduler/*` 状态类接口读快照、控制类接口（trigger/enable/disable/start/stop/监控启停）写命令；调度执行历史从 `logs/scheduler_*.jsonl` 直读（`scheduler_service.read_executions_from_disk`）。
- 改造原因：19 个调度任务（自动交易最长 8 分钟、周度反思 12 分钟等）+ 监控线程与 HTTP 同进程共享 GIL，导致接口偶发 3-20s 卡顿；拆分后重活全部在 worker 进程。

### 数据源
- Tushare：除实时行情外均可使用（日线、历史分钟线 `etf_mins` 等）；调用时注意控制请求频次（如 `time.sleep(0.6)`）避免限流。
- 实时行情：Tushare 不提供实时报价，用东财接口取实时价（如 `push2his.eastmoney.com/api/qt/stock/kline/get`，参数 `klt=101&fqt=1`）；secid 规则：SH 代码 5/6 开头 → `1.x`，SZ → `0.x`；close 在返回 `klines` 每行的第 3 位。
- ETF 池基础信息刷新：`POST /api/v1/etf/sync-tushare`（`backend/app/services/etf_pool_sync.py`，走 Tushare `etf_basic` 全量 upsert，保留东财行情快照）。

### git / 行尾
- 仓库规范行尾 LF；`core.autocrlf=true` 检出会把 LF→CRLF（正常现象）。
- 改 openspec 文档后若 `git add` 出现整文件假 diff（插入/删除行数相同，如 211/211），是行尾不一致：先把工作区该文件 CRLF→LF（Python `b.replace(b"\r\n", b"\n")`）再重新 add。
- staging 后核对 `git diff --cached --stat`，确认只有真实改动再 commit。
- 提交/推送直接到 `main`（延续现有中文 commit message 风格）。

### OpenSpec × DeepSeek Harness
- openspec CLI 已支持 `deepseekharness` 工具目标（DeepSeek Harness 直接读取项目 `.agents/skills/` 下的 openspec-* 技能）。
- 补丁由 `scripts/patch-openspec-deepseekharness.mjs` 打入全局安装（`@fission-ai/openspec` 的 `AI_TOOLS` 注册 + `$openspec-*` 技能引用风格）；npm 升级/重装 openspec 后需重跑：`node scripts/patch-openspec-deepseekharness.mjs`（`--check` 查状态，`--revert` 撤销）。
- 本仓库 `.agents/skills/.openspec-target = deepseekharness`，标识该共享技能根归属 DeepSeek Harness；技能内以 `$openspec-<skill>`（shell 风格）引用兄弟技能。
- 重新生成技能：`openspec init --tools deepseekharness`（extend 模式，幂等）。

### Hallmark 设计技能
- 已安装到 `.agents/skills/hallmark/`（DSH 自动加载，输入框 `@hallmark` 可直接提及）。
- 更新/重装：`npx skills add nutlope/hallmark`，或从 https://github.com/nutlope/hallmark 拷贝 `skills/hallmark/`（SKILL.md + references/）。
- 用法：默认（新建 UI）、`hallmark audit <target>`（评分不改动）、`hallmark redesign <target>`（重设计）、`hallmark study <截图|URL>`（提取设计 DNA）。

### 插件固化（动态 Cordis 插件持久化）
- 动态插件（`cordis_define` / `cordis_run`）默认**临时**：进程重启后失效。安装流程固定为「定义 → 运行 → 固化」三步，除非用户明确要求临时。
- 固化：`node scripts/persist-cordis-plugin.mjs --from-spec scripts/specs/<id>.spec.json`（spec = cordis_define 的入参：id/name/purpose/host/client）；`--check` 查状态，`--remove` 卸载。
- 固化落盘：`~/.dsh/profiles/node_modules/dsh-<id>/`（package.json + `lib/index.js` 宿主 + `lib/client.js` 浏览器端）+ `~/.dsh/profiles/web/cordis.patch.yml` 注册行；**重启 Harness 后生效**（浏览器端无需再审批）。
- 已固化：`openspec-mention`（spec：`scripts/specs/openspec-mention.spec.json`）——输入框 `@` 提及 openspec 技能 + 模型约定提示。
- 改已固化插件的代码：直接改 `~/.dsh/profiles/node_modules/dsh-<id>/lib/*.js` 后重启；或改 spec 重跑脚本覆盖。
