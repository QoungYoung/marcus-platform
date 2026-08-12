## 1. 科技面板浅色化（阻断项）

- [x] 1.1 将 `golden-pit-page.css:1786-1824` 科技面板深色样式重写为浅色 token：summary → `var(--gp-ink-2)`、stat 数字 → `var(--gp-ink)`、stat 卡底 `rgba(255,255,255,.04)` → `rgba(47,124,211,.05)`、表头 → `var(--gp-muted)`、标的名 → `var(--gp-ink)`、as-of → `var(--gp-muted)`、regime 保留紫色强调但文字改 `var(--gp-ink-2)`、`.tech-up/.tech-down` 换加深版红绿
- [x] 1.2 打开页面验证科技面板 summary/数字/表格全部 ≥4.5:1，且牛（红）熊（绿）语义不变

## 2. 键盘可达性与弹窗语义

- [x] 2.1 在 `.golden-pit-page` 作用域补 `:focus-visible` 统一规则（`outline: 2px solid var(--gp-blue); outline-offset: 2px`），覆盖全部按钮/芯片/输入/开关
- [x] 2.2 配置开关增加 `input:focus-visible + .gp-config-switch-track` 焦点环（checkbox 不可见，焦点须呈现在 track）
- [x] 2.3 为 icon-only 按钮补 `aria-label`：页头展开/收起、刷新、设置、宽基/板块折叠、弹窗关闭
- [x] 2.4 配置弹窗补 `role="dialog"`、`aria-modal="true"`、`aria-labelledby`（指向标题 h3）；打开时聚焦关闭按钮，关闭时焦点归还设置按钮
- [x] 2.5 弹窗增加 Esc 关闭、Tab 圈定（弹窗内循环）与打开期间 `body` 滚动锁定（复用 `AnalyticsPage.tsx` 既有 Esc 模式）

## 3. 刷新反馈

- [x] 3.1 新增 `refreshing` 状态：刷新按钮请求期间显示旋转图标并 disabled
- [x] 3.2 status 刷新失败时保留上次成功数据与 `as_of`，并在页头下方显示可关闭的"刷新失败，展示上次成功数据"banner
- [x] 3.3 部分失败语义：status 成功但 history/tech 失败时保留旧图并提示对应数据不可用；移除"error 被设置但 status 存在时被忽略"的静默分支
- [x] 3.4 页头收起态内联显示"更新于 {as_of}"（展开态仍由 subtitle 承担）

## 4. 可读性与图表

- [x] 4.1 更新 `--gp-muted` → `#57718e`（5.05:1）、`--gp-faint` → `#5a7592`（4.78:1），10px 以下语义文本（EN 微标/序号）改用 muted
- [x] 4.2 状态条白字渐变（末端 #5ea3e8）与指数徽章白字（#e5484d/#c98a12）达到 AA：加深底色或改深色文字
- [x] 4.3 贪婪图 3 条阈值参考线（入场/预警/出场）加 `strokeDasharray="4 4"`、`strokeWidth={1.5}`
- [x] 4.4 资金流向条按可见市场最大 |pp| 归一化（`max(8, 100*ppAbs/maxAbs)`），替代 `Math.min(100, ppAbs*10)`
- [x] 4.5 收敛 11 处 `transition: all` 为具体属性（background-color/border-color/color/box-shadow/transform/left）

## 5. 动效与收尾

- [x] 5.1 `useGoldenPitBackground` 检测 `prefers-reduced-motion: reduce`：命中则绘制一帧静态画面后停止，不启动 rAF；resize 时重绘静态帧
- [x] 5.2 （可选，低优）Rajdhani woff2（500/600/700，OFL）自托管到 `frontend/src/assets/fonts/`，`@font-face { font-display: swap }`，移除 CSS `@import`
- [ ] 5.3 验证收尾：`npm run build`（tsc + vite）与 `npm run lint` 通过；手动检查清单——键盘走查全部按钮/开关、弹窗 Esc 与焦点归还、DevTools 模拟 reduced-motion 静态帧、断网模拟刷新失败 banner、1440/640px 两种宽度视觉回归

## 6. 牛熊判断标的列表默认收起

- [x] 6.1 `TechStatusPanel` 新增 `techTableOpen`（默认 `false`，hooks 置于 early-return 之前）；面板头加折叠按钮（复用 `gp-fold-btn`、`aria-expanded`、可读 `aria-label`），"标的"表格仅在展开态渲染；CSS 修正按钮与 as-of 的间距
- [ ] 6.2 浏览器手检：默认收起态下 verdict/summary/stats 仍可见、展开后完整标的列表渲染、按钮焦点环可见
