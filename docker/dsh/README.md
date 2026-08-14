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

## 验证结论（Spike 1.1-1.4）

- ✅ 1.1 容器常驻启动，webserver 监听 0.0.0.0:3001（HTTP 404 = 无业务路由，bridge 插件会加）
- ✅ 1.2 headless 模式真实 LLM 回合成功（"1+1等于2"，deepseek-official → OpenCode 网关）
- ✅ 1.3 marcus-panel-tools skill 可见可读；fs 工具正常
- ✅ 1.4 docker_default 网络互通（backend HTTP 200）
