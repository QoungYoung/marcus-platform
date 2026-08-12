## Why

黄金坑监测页（`frontend/src/pages/GoldenPitPage.tsx` + `golden-pit-page.css`）存在 1 处发布级缺陷与多处 UX 缺口：8-12 新增的"牛熊判断 · 科技现状"面板把深色主题样式（白字/浅灰字）套在浅色玻璃面板上，实测对比度 1.0–2.1:1，整段基本不可读；此外键盘焦点不可见、配置弹窗非对话框、自动刷新失败被静默吞掉、微字低对比、粒子动画无视 `prefers-reduced-motion`。Hallmark 审计（2026-08-12）定为 1 critical · 6 major · 5 minor，本轮全部修复。

## What Changes

- 科技现状面板浅色化：深色 token（`#fff`/`#aab3c5`/`#dfe5ef`、`rgba(255,255,255,…)`）替换为页面既有浅色 token，恢复可读性（≥4.5:1）
- 键盘可达性：全页按钮/芯片补 `:focus-visible` 焦点环；配置开关的隐藏 checkbox 增加可见焦点态；icon-only 按钮（刷新/设置/折叠/关闭/页头）补 `aria-label`
- 配置弹窗对话框化：`role="dialog"` + `aria-modal` + `aria-labelledby`、Esc 关闭、焦点圈定与归还、打开时锁定背景滚动（复用 `AnalyticsPage.tsx` 既有模式）
- 刷新反馈：刷新按钮内 loading 态；失败时页面顶部显示可关闭的失败 banner，并保留"上次成功 as_of"；明确"部分失败"（status 成功但 history 失败）的 UI 语义
- 对比度与字号：`--gp-muted`/`--gp-faint` 加深至 ≥4.5:1；最小可用字号提至 10.5–11px；状态条/徽章白字改达标配色
- 动效降级：画布粒子仅在 `prefers-reduced-motion: reduce` 时静态渲染一帧（不新增 visibilitychange 暂停，不做深色内嵌卡）
- 图表可读性：入场/预警/出场参考线改虚线并采用与 `INDEX_COLORS` 不冲突的独立色组
- 页头收起态保留"更新于 {as_of}" 信息
- 资金流向条由 `ppAbs * 10` 封顶改为按可见最大值归一化
- `transition: all` 收敛为具体属性（11 处）
- Rajdhani 字体自托管，消除 `@import` 渲染阻塞（可延后的低优项）

## Capabilities

### New Capabilities

- `golden-pit-page-ux`: 黄金坑监测页的前端可读性、键盘可达性、反馈机制与动效降级要求

### Modified Capabilities

- 无（本变更为纯前端展示/交互层，不改变任何后端领域需求）

## Impact

- `frontend/src/pages/GoldenPitPage.tsx`：模态框语义、aria-label、刷新反馈、页头 as-of、INDEX_COLORS/参考线、canvas reduced-motion
- `frontend/src/styles/golden-pit-page.css`：科技面板浅色化、focus-visible、对比度 token、字号、transition 收敛、流向条、字体
- `frontend/src/assets/fonts/`（新增，自托管 Rajdhani woff2）
- 无后端/API/数据库变更
