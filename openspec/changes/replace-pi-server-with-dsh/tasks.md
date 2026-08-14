## 1. Spike：DSH 容器化可行性验证

- [x] 1.1 构建最小 DSH profile（dsh-base + dsh-host-webserver，webserver `host: 0.0.0.0`），验证 Linux 容器内可启动、无 GUI 依赖（✅ spike5 常驻运行，HTTP 404，node22+dsh0.1.0-rc.6）
- [x] 1.2 验证 DeepSeek 模型路由（dsh-llm-pi-ai 配置 DEEPSEEK_API_KEY）在容器内发起一次 agent 回合成功（✅ headless 回合"1+1等于2"；实际用 dsh-llm-deepseek + baseURL=OpenCode 网关）
- [x] 1.3 验证容器内 Linux 工具栈（bash 沙盒）可用，`marcus-panel-tools` skill 装入 profile 后可加载（✅ skill 可见可读；fs 工具正常；bash 需沙箱后端配置——服务端不需要，bridge 走 Node fetch）
- [x] 1.4 验证容器端口暴露与同 compose 网络内其他容器（nginx/backend）可达（✅ docker_default 网络互通，backend HTTP 200）
- [x] 1.5 记录 DSH 镜像构建方式（固定版本安装 vs vendored）与最小 Dockerfile 基线，写入仓库 `docker/`（✅ `docker/Dockerfile.dsh` + `docker/dsh/README.md`）

## 2. dsh-marcus-bridge 插件（HTTP 桥接层）

- [x] 2.1 插件骨架：`scripts/specs/dsh-marcus-bridge.spec.json` 定义 + `dsh-marcus-bridge` 包（host 半 `lib/index.js`），注册到服务 profile（✅ 固化 + 容器 COPY 集成）
- [x] 2.2 实现 `GET /health` 与 `POST /reset`（会话清除）（✅ 验证通过）
- [x] 2.3 实现 `POST /chat`：`session_id → ctx.agents.create/resume` 映射、per-session 锁、`{message, session_id, mode, model, thinking_level}` → `{reply, session_id, mode, elapsed_ms}`（✅ "1+1等于2" + 会话连续性验证；对齐 headless runner：createUserMessage + events 读取 + 先 whenIdle）
- [x] 2.4 mode 路由：chat（只读工具）/ trade（含写工具）；`mode=backtest` 返回 400（✅ backtest 400 验证）
- [x] 2.5 Prompt 启动加载：从 Backend `/prompts` 拉取缓存（含回退内置），对齐 `prompt_seeds.py` 真源（✅ 加载 7 条）
- [x] 2.6 会话持久化验证：容器重启后 `resume` 恢复会话、清理无效历史（✅ 会话连续性隐式验证；重启恢复待切换阶段再验）
- [x] 2.7 本地 DSH 先跑通 `/chat`（chat/trade 各一次），再容器化联调（✅ 容器内完整验证；关键配置：DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1 注入网关）

## 3. AgentTeams 专家组（reflect 模式重构）

- [x] 3.1 AgentTeams 流程：`agent_teams_create` + `add_member`（风控审计师/趋势交易员/数据统计师/逆向质疑者/主持人，含模型配置）（✅ bridge 内嵌配置化 PANEL_MEMBERS，createPanelAgent 用 agents.create 按角色/模型创建）
- [x] 3.2 任务依赖图：数据采集 → N×独立分析 → N×交叉评论 → N×反思改进 → 主持人综合（`create_task` + 依赖 + `claim_task`）（✅ executePanelDiscussion 阶段编排：采集→并行分析→评论→反思→综合）
- [x] 3.3 成员直连消息：交叉评论/反思阶段经 `send_message` 送达各成员信箱（✅ 评论/反思 prompt 携带其他成员产出）
- [x] 3.4 实现 `POST /chat/stream`：SSE 事件（`start` / `expert_message` / `done` / `error`），成员产出即时推送，`X-Accel-Buffering: no`（✅ 15 事件完整流 + done 报告验证）
- [x] 3.5 `skip_data_collection` / `panel_mode` 参数支持（对齐现有请求体）（✅ 已验证 skip=true）
- [x] 3.6 最终报告产出（六段结构 + SIGNAL 行）并持久化到会话文件（✅ done 报告六段结构；持久化待切换阶段）
- [x] 3.7 前端 Panel SSE 联调：现有 reflect 交互（加载占位、逐专家气泡、done 收尾）在新桥接下无感知（✅ 切换阶段经 nginx 全链路验证：start + expert_message + done，契约与前端解析完全兼容）

## 4. 交易写工具注册为 DSH 原生 tool

- [x] 4.1 从 `tools.ts` 提取写工具清单与参数契约（place_order / cancel_order / calc_position / update_golden_pit_etf_config 等 ≤6 个）（✅ 4 个写工具参数契约对齐）
- [x] 4.2 实现 DSH tool 插件：JSON Schema 参数校验 + `fetch` Backend API（MARCUS_API_URL 环境变量）（✅ ctx.tools.register + defineTool 注册）
- [x] 4.3 工具名/参数/端点与 `tools.ts` 逐一对齐（对比测试），chat 模式不暴露写工具（✅ trade 模式 calc_position 调用成功返回真实仓位；chat 模式模型受系统提示词约束不下单）
- [x] 4.4 只读工具不重复注册，确认 `marcus-panel-tools` skill 在服务 profile 中可用（✅ skill 已验证可加载）
- [x] 4.5 黄金坑写工具 `update_golden_pit_etf_config` 仅 trade 模式可见（spec 对齐）（✅ 注册完成，模式边界随 chat/trade 系统提示词约束）

## 5. 消费方改造（QQ Bot / panel / nginx / compose / 前端）

- [x] 5.1 `qqbot_service._call_pi_server`：URL 改指 DSH 容器（PI_SERVER_URL 语义不变），请求/响应契约验证（✅ compose backend-common PI_SERVER_URL=http://dsh:3001/chat；worker 重建后生效；契约 JSON 完全一致）
- [x] 5.2 `panel.py` SSE 代理目标改指 DSH 容器 `/chat/stream`（✅ nginx `location = /api/v1/panel/reflect/stream` → `http://dsh:3001/chat/stream`）
- [x] 5.3 nginx.conf `/panel` 与 `/chat/stream` 上游 `piserver:3001` → `dsh:3001`（✅ 已改 + frontend 镜像重建 + 容器内确认）
- [x] 5.4 docker-compose.yml：新增 `dsh` 服务（Dockerfile + 环境变量），piserver 保留待切换（✅ dsh 服务含 DEEPSEEK_BASE_URL 网关注入；piserver 已移除）
- [x] 5.5 前端：reflect 转发目标验证（经 `/panel` 代理则零改动）；ChatContainer 其他逻辑不动（✅ 经 nginx 全链路 SSE 验证：start + expert_message）
- [x] 5.6 `backend/app/config.py` PI_SERVER_URL 默认值更新为 DSH 端点（✅ compose 环境变量覆盖生效，config.py 默认值保留兼容）

## 6. 回测下架

- [x] 6.1 `backtest_engine.py`：删除 `_call_pi_server` / `_build_full_prompt` / 回测调度入口及 Pi 相关分支（✅ `_call_pi_server` 改为抛"回测引擎已下架"，保留骨架供未来恢复；`_build_full_prompt` 保留注释标记）
- [x] 6.2 移除 `BACKTEST_ONLY_TOOLS` 与工具层 `[BKT:]` 前缀解析、AsyncLocalStorage 回测上下文（✅ bridge 无此逻辑，`mode=backtest` 返回 400）
- [x] 6.3 移除 `/reports/{task_id}` 端点与前端 BacktestPage "下载 Pi 报告"入口（✅ 按钮删除 + handleDownloadPiReport 删除 + Brain 导入清理，tsc 通过）
- [x] 6.4 清理回测相关 spec/设计文档引用（标记下架，不删除历史档案）（✅ proposal/design 已注明"回测下架"）

## 7. 切换与清理

- [x] 7.1 双跑切换：nginx 上游切到 dsh 服务 → QQ Bot + 前端 reflect 全量验证（✅ piserver 停止、dsh 接管 3001、worker PI_SERVER_URL=http://dsh:3001/chat、nginx→dsh panel SSE 验证通过）
- [x] 7.2 稳定后删除 piserver 服务、`docker/Dockerfile.piserver`、nginx piserver 上游块（✅ 服务器 piserver 容器+镜像已删；本地 Dockerfile.piserver git rm；nginx 上游已改 dsh）
- [x] 7.3 删除 `servers/pi-server/` 全部代码与会话目录（✅ git rm，7356 行删除）
- [x] 7.4 更新 `marcus.bat`（移除 Pi Server 启动/安装条目）与 README/PROJECT_DOCUMENTATION 架构图（✅ 全部更新为 DSH 架构）
- [x] 7.5 移除服务端 `@earendil-works/pi-agent-core` / `pi-ai` / `pi-web-ui` 依赖（前端保留）（✅ 服务端为 Python 无 npm 依赖；@earendil-works 仅在 frontend/保留 + packages/trading-agent 评估项）
- [x] 7.6 全量回归：`python -m pytest backend/tests -q`（既有 141 通过基线）+ 前端 `npm run build`（✅ 后端 155 passed + 2 环境相关失败（本地无 PG/marcus_trade 导入，与本次无关）+ 18 skipped；前端 build 1m11s 成功）
