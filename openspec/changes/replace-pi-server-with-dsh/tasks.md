## 1. Spike：DSH 容器化可行性验证

- [x] 1.1 构建最小 DSH profile（dsh-base + dsh-host-webserver，webserver `host: 0.0.0.0`），验证 Linux 容器内可启动、无 GUI 依赖（✅ spike5 常驻运行，HTTP 404，node22+dsh0.1.0-rc.6）
- [x] 1.2 验证 DeepSeek 模型路由（dsh-llm-pi-ai 配置 DEEPSEEK_API_KEY）在容器内发起一次 agent 回合成功（✅ headless 回合"1+1等于2"；实际用 dsh-llm-deepseek + baseURL=OpenCode 网关）
- [x] 1.3 验证容器内 Linux 工具栈（bash 沙盒）可用，`marcus-panel-tools` skill 装入 profile 后可加载（✅ skill 可见可读；fs 工具正常；bash 需沙箱后端配置——服务端不需要，bridge 走 Node fetch）
- [x] 1.4 验证容器端口暴露与同 compose 网络内其他容器（nginx/backend）可达（✅ docker_default 网络互通，backend HTTP 200）
- [x] 1.5 记录 DSH 镜像构建方式（固定版本安装 vs vendored）与最小 Dockerfile 基线，写入仓库 `docker/`（✅ `docker/Dockerfile.dsh` + `docker/dsh/README.md`）

## 2. dsh-marcus-bridge 插件（HTTP 桥接层）

- [ ] 2.1 插件骨架：`scripts/specs/dsh-marcus-bridge.spec.json` 定义 + `dsh-marcus-bridge` 包（host 半 `lib/index.js`），注册到服务 profile
- [ ] 2.2 实现 `GET /health` 与 `POST /reset`（会话清除）
- [ ] 2.3 实现 `POST /chat`：`session_id → ctx.agents.create/resume` 映射、per-session 锁、`{message, session_id, mode, model, thinking_level}` → `{reply, session_id, mode, elapsed_ms}`
- [ ] 2.4 mode 路由：chat（只读工具）/ trade（含写工具）；`mode=backtest` 返回 400
- [ ] 2.5 Prompt 启动加载：从 Backend `/prompts` 拉取缓存（含回退内置），对齐 `prompt_seeds.py` 真源
- [ ] 2.6 会话持久化验证：容器重启后 `resume` 恢复会话、清理无效历史
- [ ] 2.7 本地 DSH 先跑通 `/chat`（chat/trade 各一次），再容器化联调

## 3. AgentTeams 专家组（reflect 模式重构）

- [ ] 3.1 AgentTeams 流程：`agent_teams_create` + `add_member`（风控审计师/趋势交易员/数据统计师/逆向质疑者/主持人，含模型配置）
- [ ] 3.2 任务依赖图：数据采集 → N×独立分析 → N×交叉评论 → N×反思改进 → 主持人综合（`create_task` + 依赖 + `claim_task`）
- [ ] 3.3 成员直连消息：交叉评论/反思阶段经 `send_message` 送达各成员信箱
- [ ] 3.4 实现 `POST /chat/stream`：SSE 事件（`start` / `expert_message` / `done` / `error`），成员产出即时推送，`X-Accel-Buffering: no`
- [ ] 3.5 `skip_data_collection` / `panel_mode` 参数支持（对齐现有请求体）
- [ ] 3.6 最终报告产出（六段结构 + SIGNAL 行）并持久化到会话文件
- [ ] 3.7 前端 Panel SSE 联调：现有 reflect 交互（加载占位、逐专家气泡、done 收尾）在新桥接下无感知

## 4. 交易写工具注册为 DSH 原生 tool

- [ ] 4.1 从 `tools.ts` 提取写工具清单与参数契约（place_order / cancel_order / calc_position / update_golden_pit_etf_config 等 ≤6 个）
- [ ] 4.2 实现 DSH tool 插件：JSON Schema 参数校验 + `fetch` Backend API（MARCUS_API_URL 环境变量）
- [ ] 4.3 工具名/参数/端点与 `tools.ts` 逐一对齐（对比测试），chat 模式不暴露写工具
- [ ] 4.4 只读工具不重复注册，确认 `marcus-panel-tools` skill 在服务 profile 中可用
- [ ] 4.5 黄金坑写工具 `update_golden_pit_etf_config` 仅 trade 模式可见（spec 对齐）

## 5. 消费方改造（QQ Bot / panel / nginx / compose / 前端）

- [ ] 5.1 `qqbot_service._call_pi_server`：URL 改指 DSH 容器（PI_SERVER_URL 语义不变），请求/响应契约验证
- [ ] 5.2 `panel.py` SSE 代理目标改指 DSH 容器 `/chat/stream`
- [ ] 5.3 nginx.conf `/panel` 与 `/chat/stream` 上游 `piserver:3001` → `dsh:3001`
- [ ] 5.4 docker-compose.yml：新增 `dsh` 服务（Dockerfile + 环境变量），piserver 保留待切换
- [ ] 5.5 前端：reflect 转发目标验证（经 `/panel` 代理则零改动）；ChatContainer 其他逻辑不动
- [ ] 5.6 `backend/app/config.py` PI_SERVER_URL 默认值更新为 DSH 端点

## 6. 回测下架

- [ ] 6.1 `backtest_engine.py`：删除 `_call_pi_server` / `_build_full_prompt` / 回测调度入口及 Pi 相关分支
- [ ] 6.2 移除 `BACKTEST_ONLY_TOOLS` 与工具层 `[BKT:]` 前缀解析、AsyncLocalStorage 回测上下文
- [ ] 6.3 移除 `/reports/{task_id}` 端点与前端 BacktestPage "下载 Pi 报告"入口
- [ ] 6.4 清理回测相关 spec/设计文档引用（标记下架，不删除历史档案）

## 7. 切换与清理

- [ ] 7.1 双跑切换：nginx 上游切到 dsh 服务 → QQ Bot + 前端 reflect 全量验证
- [ ] 7.2 稳定后删除 piserver 服务、`docker/Dockerfile.piserver`、nginx piserver 上游块
- [ ] 7.3 删除 `servers/pi-server/` 全部代码与会话目录
- [ ] 7.4 更新 `marcus.bat`（移除 Pi Server 启动/安装条目）与 README/PROJECT_DOCUMENTATION 架构图
- [ ] 7.5 移除服务端 `@earendil-works/pi-agent-core` / `pi-ai` / `pi-web-ui` 依赖（前端保留）
- [ ] 7.6 全量回归：`python -m pytest backend/tests -q`（既有 141 通过基线）+ 前端 `npm run build`
