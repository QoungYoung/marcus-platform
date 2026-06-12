---
name: minimax-token-plan-fix
overview: 将 pi-server 中 MiniMax API 调用从按量计费模式切换到 Token Plan 模式：修正 baseUrl、provider 和 API Key 配置
todos:
  - id: fix-provider-baseurl
    content: 修改 servers/pi-server/src/index.ts：buildMinimaxM3Model() provider 改为 minimax-cn + baseUrl 改为 api.minimaxi.com/anthropic；data_analyst member provider 改为 minimax-cn
    status: completed
  - id: rebuild-deploy
    content: 重建部署并提醒用户将 .env 中 MINIMAX_API_KEY 替换为 Token Plan 订阅 Key
    status: completed
    dependencies:
      - fix-provider-baseurl
---

## 需求概述

用户已购买 MiniMax Token Plan（订阅计划），需将现有代码从按量计费国际站 API 切换到 Token Plan 中国站 API。

## 核心改动

1. `buildMinimaxM3Model()` 手工模型定义中的 `provider` 从 `"minimax"` 改为 `"minimax-cn"`，`baseUrl` 从 `https://api.minimax.io/anthropic` 改为 `https://api.minimaxi.com/anthropic`
2. `data_analyst` 专家成员的 `provider` 从 `'minimax'` 改为 `'minimax-cn'`
3. `.env` 中 `MINIMAX_API_KEY` 需用户替换为 Token Plan 订阅 Key

## 技术分析

### 当前问题

- `buildMinimaxM3Model()` 第 853 行：`provider: "minimax"` → 使用的是 pi-ai 内置的 minimax 国际站模型，baseUrl 为 `https://api.minimax.io/anthropic`
- `data_analyst` 第 900 行：`provider: 'minimax'` → 同样走国际站
- Token Plan 要求 baseUrl 为 `https://api.minimaxi.com/anthropic`，国际站域名会导致认证失败

### 解决方案

pi-ai 包已内置 `minimax-cn` provider（中国站），其 `MiniMax-M2.7` 模型的 baseUrl 为 `https://api.minimaxi.com/anthropic`。只需将 provider 切换为 `minimax-cn` 即可自动使用正确的 baseUrl。

对于 `MiniMax-M3`（pi-ai 未内置注册的自定义模型），需同步修改手工定义中的 provider 和 baseUrl。

### 兼容性确认

- `getApiKey` 逻辑第 1000 行已兼容 `minimax-cn`：`if (provider === 'minimax' || provider === 'minimax-cn') return MINIMAX_API_KEY;`
- `apiKey` 字段仍为 `MINIMAX_API_KEY`，用户自行替换为订阅 Key 即可
- pi-ai 的 `env-api-keys.js` 自动将 `minimax-cn` provider 映射到 `MINIMAX_CN_API_KEY` 环境变量，但 `getApiKey` 回调优先于环境变量，因此无需额外配置

## 实施计划

### 改动文件

1. **servers/pi-server/src/index.ts** — 改 2 行
2. **.env** — 用户手动替换 Key（无需代码改动）

### 具体步骤

| 步骤 | 文件 | 行号 | 改动 |
| --- | --- | --- | --- |
| 1 | `index.ts` | 853 | `provider: "minimax"` → `provider: "minimax-cn"` |
| 2 | `index.ts` | 854 | `baseUrl: "https://api.minimax.io/anthropic"` → `baseUrl: "https://api.minimaxi.com/anthropic"` |
| 3 | `index.ts` | 900 | `provider: 'minimax' as const` → `provider: 'minimax-cn' as const` |
| 4 | `.env` | 9 | 提示用户将 `MINIMAX_API_KEY` 值替换为 Token Plan 订阅 Key |


### 验证方式

重建后查看启动日志，应显示：

```
- 数据统计师: minimax-cn/MiniMax-M2.7
- 逆向质疑者: minimax-cn/MiniMax-M3
```