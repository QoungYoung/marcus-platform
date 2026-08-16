# DSH 服务容器构建说明（替代 Pi Server）

本文记录 `docker/Dockerfile.dsh` 的构建方式与踩坑，供服务器重建/维护参考。

## 镜像

- 名称：`marcus-dsh`（tag：`latest` / `spike5`）
- 体积：~1.34 GB（含 node22 + dsh 全家桶 + 编译工具链层）
- 用途：DeepSeek Harness 常驻服务，替代 `marcus-piserver`（Node.js Pi Server）

## 构建命令（在服务器 /opt/marcus-platform 下）

```bash
docker build -f docker/Dockerfile.dsh -t marcus-dsh:latest .
```

## 构建策略（服务器国际出口慢，全走国内源）

| 步骤 | 方式 | 原因 |
|---|---|---|
| 基础镜像 | `node:20-slim`（本地已有） | 避免从 Docker Hub 拉 200MB（服务器出口 ~9KB/s 会卡死） |
| Node 22 | 删 node20 残留后从 npmmirror 下载官方 tar 完整覆盖（30MB，5 秒） | DSH loader 需要 `node:module.stripTypeScriptTypes`（Node ≥22.18）；npm 随之换 22 配套版，避免新旧混用崩溃 |
| apt 工具链 | 阿里云源（HTTP） | node-pty 需要 node-gyp 编译；debian 官方源 16KB/s 会卡死 |
| npm | `ENV npm_config_registry=npmmirror` | 环境变量方式（`npm config set` 在构建环境有差异） |
| dsh CLI | `npm i -g @deepseek-ai/dsh@0.1.0-rc.6 pnpm@9` | 532 包 ~90s（npmmirror） |
| profile | 手工写 `package.json` + `pnpm-workspace.yaml` + patch | 绕开 `dsh plugin` 的 workspace-root 检查（pnpm9）与版本陷阱 |
| agent-teams | 本地 6.1MB tarball 手动解包进 profile node_modules | GitHub 国际出口慢；pnpm add 会因 peerDeps 的 UI 包缺失中断（服务端不需要 UI） |

## 关键踩坑记录

1. **node:20 跑不起 DSH**：`node:module` 无 `stripTypeScriptTypes`（Node 22.18+ API），启动即崩。必须 node ≥22。
2. **node 22 覆盖后 npm 崩溃**：`Class extends value undefined`——node:20 残留的旧 npm 与新 node 混用。根治：先 `rm -rf /usr/local/lib/node_modules /usr/local/bin/npm ...`，再完整解压 node22 tar。
3. **pnpm 11 需要 node22**（`ERR_UNKNOWN_BUILTIN_MODULE`），node20 上用 pnpm9。
4. **agent-teams 的 UI peerDeps 缺失**：pnpm add 中断（`dsh-compact` 不在 registry）。服务端不需要 UI，直接解包 tarball 到 `node_modules/dsh-agent-teams` + 手工加入 `dsh.profile.bundles`。
5. **bash 工具在容器内被沙箱拒绝**（无沙箱后端/审批通道）：服务端场景不需要模型跑任意 bash，bridge 插件的工具走 Node fetch 调 backend，不受影响。如需 bash 需配置 `dsh-bash-sandbox` 的沙箱后端。

## 运行

```bash
docker run -d --name marcus-dsh -p 3001:3001 \
  --network docker_default \
  -e PI_SERVER_PORT=3001 \
  -e DEEPSEEK_API_KEY=... \
  -e MARCUS_API_URL=http://backend:8000/api/v1 \
  marcus-dsh:latest
```

- 加入 `docker_default` 网络才能按服务名访问 backend / piserver（Spike 1.4 验证：backend HTTP 200）。
- 3001 端口当前被现役 `marcus-piserver` 占用，切换前需先停旧服务（任务 7.1 双跑切换）。
- skill：`/app/.agents/skills/marcus-panel-tools` 需挂载或 COPY（Spike 1.3 验证可见可读）。

## 出站代理（可选）

web_search / LLM 的 HTTP 请求走原生 `fetch`，**不读** `HTTP_PROXY`/`HTTPS_PROXY`（Node 22
无 `--use-env-proxy`）。如需走代理，bridge 插件已内置 undici 全局 dispatcher（`EnvHttpProxyAgent`）：

```bash
docker run ... \
  -e DSH_PROXY_URL=http://proxy-host:port \   # HTTP CONNECT 代理（可带 user:pass）
  -e NO_PROXY=127.0.0.1,localhost,backend,postgres,frontend \
  marcus-dsh:latest
```

- 设置后进程内所有出站（web_search → OpenCode 网关、聊天 → OpenCode 网关、bridge → backend）
  统一走代理；`NO_PROXY` 务必包含 `backend` 等内网服务名，否则 bridge 调 backend 也会绕代理。
- 日志确认：`docker logs marcus-dsh` 出现 `[Bridge] 出站代理已启用: ...`。
- 不设 `DSH_PROXY_URL` 时行为不变（直连）。

## web_search 端点（当前默认走 OpenCode 网关）

`service.cordis.patch.yml` 把 `web-search-deepseek` 端点指到 OpenCode 网关
（`https://opencode.ai/zen/go/v1`，Anthropic 兼容 `/v1/messages` + `web_search_20250305`
server tool，已在本地实测返回原生 `web_search_tool_result` 结构），key 复用网关
`DEEPSEEK_API_KEY`——与聊天同 key、同通道，无需额外配置。

- 覆盖端点：设 `DEEPSEEK_SEARCH_BASE_URL`（如切回 DeepSeek 官方
  `https://api.deepseek.com/anthropic/v1`，此时 `DEEPSEEK_API_KEY` 需为平台 key）。
- 注意：OpenAI 风格 `/v1/chat/completions` 的 web_search 工具类型网关不支持
  （实测 400：`unknown variant web_search, expected function`），必须走 Anthropic 端点。

## 验证结论（Spike 1.1-1.4）

- ✅ 1.1 容器常驻启动，webserver 监听 0.0.0.0:3001（HTTP 404 = 无业务路由，bridge 插件会加）
- ✅ 1.2 headless 模式真实 LLM 回合成功（"1+1等于2"，deepseek-official → OpenCode 网关）
- ✅ 1.3 marcus-panel-tools skill 可见可读；fs 工具正常
- ✅ 1.4 docker_default 网络互通（backend HTTP 200）

## dsh-t-compaction（做T会话专用上下文压缩）

对 `t-agent-*` 会话（bridge 映射为 `chat:t-agent-{symbol}`）的自动/手动压缩使用
「股票投资信息优先」的结构化摘要指令（标的持仓 / t_conditions / t_triggers / regime /
技术指标 / 决策执行 / 风控状态 / 用户意图 / 下一步）；其余会话保持 DSH 默认摘要。

- **机制**：`BasicCompactionEngine.summarize()` 是官方「唯一可覆盖钩子」（源码注释
  "Override this sole hook for a template or remote summarizer"），所有压缩路径
  （pre-step 压力 / context-overflow / /compact）最终都经 `regionDependencies()` 的
  `this.summarize(...)` 动态分发；插件拿到引擎实例后按会话打补丁，无需重注册
  compaction 服务。见 `t-compaction/lib/index.js` 头部注释。
- **非阻塞设计（重要）**：插件**不再** `inject: ['compaction']`。apply 时探测式取引擎，
  组合无 host 级 compaction（如 web profile）时警告并跳过，**永不阻塞 dsh 启动**。
  做T压缩仅在组合提供 host 级 compaction（如 service profile）时生效。
- **升级 dsh 版本后的风险**：若新版把 service profile 的 host 级 compaction 也移进
  preset 域，做T压缩会静默失效（启动不再报错）。升级后检查日志是否出现
  `[t-compaction] ⚠️ 未找到 compaction 服务`；出现即需把 dsh-t-compaction 行移入
  对应 agent preset 的 compaction 隔离域。Dockerfile 钉死 `@deepseek-ai/dsh@0.1.0-rc.6`，
  不会自动升级。
- **安装（服务端）**：`Dockerfile.dsh` 第 8b 步 COPY 进 profile node_modules，
  `service.cordis.patch.yml` 注册行；本地 GUI 同法放入 `~/.dsh/profiles/node_modules/`
  并在 `web/cordis.patch.yml` 加行，重启生效。
- **验证**：`t-compaction/verify.profile.mjs`（16 项断言：t-agent 走做T指令 /
  普通会话回退默认 / 非引擎实例防御跳过 / 无引擎非阻塞）。运行：把该文件拷到 profile
  根后 `cd <profiles-root> && node verify-t-compaction.mjs`（已实测全过）。
