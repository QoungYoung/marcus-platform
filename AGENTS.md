# AGENTS.md — Marcus AI 交易平台 自定义指令

本文件供 Codex 在本仓库工作时遵循：Windows + PowerShell 执行规则 + 本项目环境约定。

## 1. Windows PowerShell 执行规则（通用，可复制到全局 `C:\Users\fengx\.codex\AGENTS.md`）

- **命令分隔用 `;`**：Windows PowerShell 5.1 不支持 `&&` / `||`，遇到会直接 ParserError。
- **不要用 PowerShell `>` 重定向保存命令输出再读字节**：重定向按 UTF-16 写盘，会破坏字节级内容。需要精确字节/行尾/编码分析时，用 Python `subprocess.run([...], capture_output=True).stdout`，或把输出写临时文件后用 Python 读。
- **管道/重定向后的退出码不可信**：用 `$LASTEXITCODE` 判断真实结果。例：`python -m pytest ... | Select-Object -Last 15` 可能显示 exit 1，实际 pytest 返回 0。
- **不要用内联 `python -c "..."` 传复杂 SQL/引号**：PowerShell 转义会破坏内容。长脚本先写临时文件：`Set-Content -Path "$env:TEMP\s.py" -Encoding UTF8` 再 `python "$env:TEMP\s.py"`。
- **中文输出乱码**：先 `$env:PYTHONIOENCODING='utf-8'`。
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
- 云服务器拉取 git 即部署 dist；后端在云服务器 8000 运行，本地一般不启动后端。

### 数据源（勿再用 Tushare）
- Tushare token 当前不可用（直连/代理均失败），不要再测。
- 真实行情价格用东财 K 线：`push2his.eastmoney.com/api/qt/stock/kline/get`，参数 `klt=101&fqt=1`；secid 规则：SH 代码 5/6 开头 → `1.x`，SZ → `0.x`；close 在返回 `klines` 每行的第 3 位。

### git / 行尾
- 仓库规范行尾 LF；`core.autocrlf=true` 检出会把 LF→CRLF（正常现象）。
- 改 openspec 文档后若 `git add` 出现整文件假 diff（插入/删除行数相同，如 211/211），是行尾不一致：先把工作区该文件 CRLF→LF（Python `b.replace(b"\r\n", b"\n")`）再重新 add。
- staging 后核对 `git diff --cached --stat`，确认只有真实改动再 commit。
- 提交/推送直接到 `main`（延续现有中文 commit message 风格）。
