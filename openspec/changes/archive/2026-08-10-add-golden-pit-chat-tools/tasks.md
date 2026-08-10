## 1. 服务端工具实现（servers/pi-server/src/tools.ts）

- [x] 1.1 新增只读工具 `get_golden_pit_status`（调用 `GET /golden-pit/status`，文本输出裁剪为 pit/warning 指数明细 + 窗口 + 三重确认 + 预测 + 宏观摘要，`details` 保留全量）
- [x] 1.2 新增只读工具 `get_golden_pit_history`（参数 `index` 可选默认 all、`days` 可选 1-2000 默认 60）
- [x] 1.3 新增只读工具 `get_golden_pit_dca_status`（调用 `GET /golden-pit/dca/status`）
- [x] 1.4 新增只读工具 `get_golden_pit_dca_logs`（参数 `days` 默认 30、`fund_code` 可选）
- [x] 1.5 新增只读工具 `get_golden_pit_etf_configs`（调用 `GET /golden-pit/etf-configs`）
- [x] 1.6 新增写工具 `update_golden_pit_etf_config`（`PUT /golden-pit/etf-configs/{fund_code}`，参数 `fund_code` 必填 + `enabled`/`strategy`/`daily_amount`/`max_total_amount` 可选，description 注明影响后续自动定投）
- [x] 1.7 将 5 个只读工具加入 `CHAT_TOOLS` 数组，`update_golden_pit_etf_config` 加入 `TRADE_TOOLS` 数组

## 2. 前端工具实现与注册（frontend/src/components/ChatContainer.tsx）

- [x] 2.1 复用 1286-1455 行已有的 4 个 DCA 工具定义，核对 `name`/`parameters` 与 tools.ts 一致
- [x] 2.2 新增 `get_golden_pit_status` 与 `get_golden_pit_history` 两个只读工具（与 tools.ts 同参数同输出格式）
- [x] 2.3 将 5 个只读工具通过 `createTool(...)` 注册进 `chatTools` 数组（1757 行）
- [x] 2.4 不注册写工具 `update_golden_pit_etf_config`（前端无独立 trade 模式，避免聊天可改配置）
- [x] 2.5 将 5 个工具名加入 `COLLAPSIBLE_TOOLS`（1809 行）
- [x] 2.6 将 5 个工具名加入 `TOOL_LABELS` 中文映射（1822 行）

## 3. 系统提示词同步

- [x] 3.1 在 `servers/pi-server/src/index.ts` 的 `CHAT_SYSTEM_PROMPT`（189 行）追加"黄金坑工具使用时机"小节（黄金坑信号/DCA 进度/ETF 配置话题的调用指引）
- [x] 3.2 在 `backend/app/db/prompt_seeds.py` 的 `CHAT_SYSTEM_PROMPT` 种子中同步同一小节

## 4. 验证

- [x] 4.1 服务端类型检查与启动冒烟（`npx tsx src/index.ts`，确认聊天工具计数增加且 `get_golden_pit_*` 出现在工具列表）
- [x] 4.2 前端构建通过（`npm run build` 或等价 tsc/vite 校验）
- [x] 4.3 双端一致性核对：`tools.ts` 与 `ChatContainer.tsx` 中 6 个工具（含写工具仅服务端）的 `name` 与 `parameters` 完全一致
- [ ] 4.4 手动冒烟：聊天询问"现在有黄金坑吗""DCA 定投进度""最近 DCA 买入记录""ETF 定投配置"，确认 AI 调用对应工具并正确渲染折叠结果
