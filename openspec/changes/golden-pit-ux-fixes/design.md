## Context

黄金坑监测页是浅色 Blue Archive 风仪表盘（`.hallmark/log.json` 记录 studied-DNA，accent #2f7cd3，玻璃拟态面板）。2026-08-12 合入的 `404dfeb` 新增"牛熊判断 · 科技现状"面板时，把一套深色主题样式（白字/浅灰字、半透明白底）独立写在 `golden-pit-page.css:1786-1824`，渲染在 `rgba(255,255,255,.92)` 的浅色面板上，实测对比度 1.0–2.1:1（`#fff` 1.0、`#dfe5ef` 1.27、`#aab3c5` 2.11），整段不可读。同页另有键盘焦点不可见、配置弹窗非对话框、刷新失败静默、微字低对比、粒子动画无降级等 UX 缺口（Hallmark audit：1 critical · 6 major · 5 minor）。

约束：无前端测试基建（仅 `tsc && vite build` + eslint），所有改动需手动视觉验证；本变更为纯前端展示/交互层，不改任何后端 API。

## Goals / Non-Goals

**Goals:**
- 科技现状面板恢复 ≥4.5:1 可读性，风格与全页浅色玻璃面板一致
- 全页键盘可达：`focus-visible` 焦点可见、icon-only 按钮有可访问名、配置弹窗满足对话框语义
- 刷新有反馈：按钮内 loading、失败 banner、保留"上次成功 as_of"、明确部分失败语义
- 对比度 token 提升至 AA（正常文本 ≥4.5:1），最小可用字号 ≥10.5px
- 动效降级：画布粒子在 `prefers-reduced-motion: reduce` 时静态渲染一帧
- 图表阈值参考线可区分于指数曲线；资金流向条刻度不失真；`transition: all` 收敛

**Non-Goals:**
- 不做深色内嵌卡 / 暗色模式（用户已否决）
- 不为 canvas 新增 `visibilitychange` 暂停或节流（用户已明确只做 reduced-motion）
- 不改 `#2f7cd3` accent（log.json 是历史记录，不修改）
- 不引入 toast 基建、不重构图表组件、不新增前端测试框架

## Decisions

### D1 科技面板浅色化（替代深色内嵌卡）
- 将 `golden-pit-page.css:1786-1824` 的深色 token 全部替换为页面既有浅色 token：`.gp-tech-summary` `#aab3c5` → `var(--gp-ink-2)`；`.gp-tech-stat b` `#fff` → `var(--gp-ink)`；`.gp-tech-stat` 底色 `rgba(255,255,255,.04)` → `rgba(47,124,211,.05)`，边框 → `var(--gp-line-soft)`；`.gp-tech-name` `#dfe5ef` → `var(--gp-ink)`；表头 `#8a93a6` → `var(--gp-muted)`；`.gp-tech-asof`/`span` → `var(--gp-muted)`；`.gp-tech-regime` 保留紫色强调但文字改 `var(--gp-ink-2)`；`.tech-up`/`.tech-down` 换为页面已有红绿 token 的加深版本
- 理由：样式段是今天独立新增、无历史包袱；深色卡会割裂全页并引入第二套 token。A股语义（牛红熊绿）保持不变

### D2 对比度 token 提升
- `--gp-muted`: `#6b86a3` (3.77:1) → `#57718e` (5.05:1)
- `--gp-faint`: `#93a9c0` (2.42:1) → `#5a7592` (4.78:1)，并规定 `gp-faint` 不再用于 10px 以下正文语义（EN 微标、序号改 muted）
- 状态条白字渐变末端 `#5ea3e8` (2.66:1) 与徽章白字 `#e5484d` (3.91:1)：10-11px 白字不达标 → 加深底色（渐变终点 ≥ `#3f7dc7`，或白字改 `--gp-ink` 深字）；实现时用 devtools 复核
- 理由：保持层级（muted 略深于 faint），两档都过 AA，且为小字号留余量

### D3 键盘焦点可见性
- 在 `.golden-pit-page` 作用域补 `:focus-visible` 规则：统一 `outline: 2px solid var(--gp-blue); outline-offset: 2px`，覆盖所有按钮/芯片/输入/开关
- 配置开关：`input:focus-visible + .gp-config-switch-track` 加焦点环（checkbox 本身 `opacity:0` 不可见，焦点必须呈现在 track 上）
- icon-only 按钮（页头展开、刷新、设置、两个折叠、弹窗关闭）补 `aria-label`（与现有 `title` 文案一致）
- 理由：同仓库 `monitor-log-page.css:46`、`industry-leaderboard.css:759` 已有 `:focus-visible` 先例；按钮保留原生 outline 不足以形成一致体验

### D4 配置弹窗对话框化
- 复用 `AnalyticsPage.tsx:97` 既有的 Esc 关闭 + 点击外部关闭模式，补齐：`role="dialog"` + `aria-modal="true"` + `aria-labelledby`（指向标题 h3）；打开时聚焦关闭按钮；关闭时焦点归还触发按钮；弹窗打开期间 `body { overflow: hidden }` 锁滚动；最小 Tab 圈定（Tab 在弹窗内循环）
- 理由：`<dialog>` 原生元素与现有 Portal + 自定义遮罩结构差异大、迁移成本高；增量补齐语义最稳

### D5 刷新反馈与部分失败语义
- 新增 `refreshing` 状态：刷新按钮点击后显示旋转 `RefreshCw`（CSS 旋转类）并 disabled，结束恢复
- 新增 `refreshError` + 可关闭 banner（渲染在 `.gp-header` 下方、status 存在时）：status 失败 → "刷新失败，展示上次成功数据（更新于 {as_of}）"；status 成功但 history/tech 失败 → "数据部分更新失败：历史/技术面数据不可用" 且保留旧图
- 移除"`error` 被设置但 `!status` 才渲染"的静默分支；首屏失败仍走现有错误页
- 理由：监控页信任依赖数据新鲜度，静默失败会展示"看似新鲜"的旧数据

### D6 画布粒子 reduced-motion
- `useGoldenPitBackground` 开头检测 `matchMedia('(prefers-reduced-motion: reduce)')`：命中则画一帧静态画面（time=0）后 return，不启动 rAF；resize 时重画静态帧
- 理由：用户已明确"只尊重 reduced-motion"；静态帧保留装饰完整性

### D7 图表阈值参考线
- 3 条 `ReferenceLine`（入场 `#e5484d` / 预警 `#c98a12` / 出场 `#2f7cd3`）加 `strokeDasharray="4 4"`、`strokeWidth={1.5}`
- 不改 `INDEX_COLORS`：实线指数 vs 虚线阈值足以区分，侵入最小
- 理由：改色板会牵连 3 处图表 + 图例色，风险收益不划算

### D8 其余小项
- 页头收起态内联显示"更新于 {as_of}"（展开态由 subtitle 承担，避免重复）
- 资金流向条 `width = max(8, 100 * ppAbs / maxAbs)` 按可见市场最大值归一化（替代 `Math.min(100, ppAbs*10)`）
- `transition: all` 11 处 → 只过渡实际属性（`background-color, border-color, color, box-shadow, transform, left`）
- Rajdhani 自托管（woff2 500/600/700，OFL 许可）到 `frontend/src/assets/fonts/`，`@font-face { font-display: swap }`，移除 CSS `@import`（低优可选任务）
- accent 漂移：不改代码，`#2f7cd3` 为现行系统色；`log.json` 保持历史记录

## Risks / Trade-offs

- [token 加深改变全页观感] → 本变更内手动过一遍全页视觉，确认层次仍在；faint 仍浅于 muted
- [模态框 Tab 圈定与 Portal 冲突] → 圈定逻辑限定在 `gp-config-modal` 内查询可聚焦元素；实现后键盘走一遍
- [banner 文案误导"数据新鲜"] → 文案必须带"上次成功 as_of"；banner 不自动消失，用户显式关闭
- [reduced-motion 静态帧在低 DPI 上显脏] → 静态帧沿用粒子/光晕第 0 帧参数，视觉与动画态一致
- [自托管字体下载失败/体积] → Rajdhani 三字重 woff2 约 30-60KB，可回退到现有系统栈（`@font-face` 失败不影响布局，font-display: swap）
- [无前端测试，回归靠手测] → 改动集中在两个文件；合入前 `npm run build` + 页面手动检查清单
