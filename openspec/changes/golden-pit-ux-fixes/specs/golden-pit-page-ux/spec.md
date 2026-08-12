## ADDED Requirements

### Requirement: 科技现状面板满足对比度可读性
The Golden Pit page SHALL render the "牛熊判断 · 科技现状" panel with the page's light-theme tokens so that all text in the panel (summary, stat values, table cells, verdict badge) meets WCAG AA contrast (≥4.5:1 for normal-sized text). The A-share color semantics (bull = red, bear = green) SHALL be preserved.

#### Scenario: 面板文本可读
- **WHEN** the tech-status panel renders summary, stat numbers, and table rows on the light glass panel
- **THEN** every text element has a contrast ratio of at least 4.5:1 against its background

#### Scenario: 牛熊语义色保持
- **WHEN** the verdict is bullish or bearish
- **THEN** the red/green (A股 红涨绿跌) semantics remain unchanged, with only lightness adjusted for contrast

### Requirement: 交互元素键盘焦点可见
Every interactive element in the Golden Pit page SHALL display a visible focus indicator when it receives keyboard focus. The config switch SHALL show its focus state on the visible track (its native checkbox is visually hidden). Icon-only buttons SHALL expose an accessible name via `aria-label`.

#### Scenario: 键盘聚焦按钮
- **WHEN** a user tabs to a button or chip
- **THEN** a visible focus ring appears (e.g. `outline: 2px solid` with offset)

#### Scenario: 键盘聚焦配置开关
- **WHEN** a keyboard user tabs to a config switch
- **THEN** the switch track shows a visible focus ring

#### Scenario: 读屏识别图标按钮
- **WHEN** a screen reader encounters the refresh, settings, fold, close, or header-toggle buttons
- **THEN** it announces a meaningful name (not just "button")

### Requirement: 配置弹窗具备对话框语义
The sector-config modal SHALL behave as a proper dialog: it SHALL carry `role="dialog"` with `aria-modal="true"` and an `aria-labelledby` reference; pressing `Escape` SHALL close it; focus SHALL move into the dialog on open and return to the trigger button on close; the background page SHALL be scroll-locked while it is open; Tab navigation SHALL stay within the dialog.

#### Scenario: Esc 关闭弹窗
- **WHEN** the config modal is open and the user presses Escape
- **THEN** the modal closes and focus returns to the settings button

#### Scenario: 打开时聚焦与滚动锁定
- **WHEN** the modal opens
- **THEN** focus moves to the modal's close button and the background page cannot scroll

#### Scenario: Tab 圈定
- **WHEN** focus is on the last focusable element of the modal and the user presses Tab
- **THEN** focus wraps to the first focusable element inside the modal

### Requirement: 刷新提供反馈并明确失败状态
The refresh action SHALL provide visible feedback while in flight (in-button loading state) and SHALL surface failures instead of silently keeping stale data. On refresh failure, the page SHALL keep the last successful data and its `as_of` timestamp, and SHALL show a dismissible banner stating the data is stale. When status succeeds but history/tech endpoints fail (partial failure), the page SHALL keep the last charts and SHALL state which data is unavailable.

#### Scenario: 刷新按钮 loading
- **WHEN** the user clicks the refresh button while a request is in flight
- **THEN** the button shows a spinner and is disabled until the request settles

#### Scenario: 状态刷新失败
- **WHEN** a refresh's status request fails while previous data exists
- **THEN** the page keeps showing the last successful data and `as_of`, and displays a dismissible "刷新失败，展示上次成功数据" banner

#### Scenario: 部分失败
- **WHEN** a refresh succeeds for status but fails for history or tech-status
- **THEN** the page keeps the previous charts and shows a banner explaining that history/tech data is unavailable

### Requirement: 文本对比度与最小字号
The page's text tokens SHALL meet ≥4.5:1 on the panel surface (`--gp-muted` and `--gp-faint` darkened accordingly), and informational text SHALL not render below 10.5px. White-on-color pairs (status pill, index badge) SHALL meet AA for their rendered size or be re-colored.

#### Scenario: token 对比度达标
- **WHEN** text styled with `--gp-muted` or `--gp-faint` renders on the glass panel
- **THEN** the contrast ratio is at least 4.5:1

#### Scenario: 状态条与徽章可读
- **WHEN** the status pill or an index badge renders white text over its gradient/solid background
- **THEN** the pair meets AA contrast for the rendered font size

### Requirement: 动画尊重减少动效偏好
The canvas particle background SHALL render a single static frame and SHALL NOT start the animation loop when the user prefers reduced motion.

#### Scenario: reduced-motion 静态帧
- **WHEN** the page loads under `prefers-reduced-motion: reduce`
- **THEN** the canvas draws one static frame and no `requestAnimationFrame` loop runs

#### Scenario: 常规环境保持动画
- **WHEN** the user does not prefer reduced motion
- **THEN** the existing particle animation runs as before

### Requirement: 图表阈值参考线可区分于数据曲线
Threshold reference lines (入场/预警/出场) in the greed trend chart SHALL use a dashed stroke so they remain distinguishable from solid index lines even when their colors coincide with `INDEX_COLORS`.

#### Scenario: 撞色仍可区分
- **WHEN** an index line shares a color with a threshold reference line in solo-index view
- **THEN** the dashed threshold line is visually distinct from the solid index line

### Requirement: 页头收起态保留数据新鲜度信息
The collapsed page header SHALL still display the "更新于 {as_of}" timestamp so data freshness is visible without expanding the header.

#### Scenario: 收起后仍见更新时间
- **WHEN** the header is in collapsed state
- **THEN** the as-of timestamp is visible in the header row

### Requirement: 资金流向条形刻度不失真
Capital-flow bars SHALL be normalized to the maximum absolute cumulative pp among visible markets so bar lengths honestly represent relative magnitude.

#### Scenario: 按最大值归一化
- **WHEN** a market has the largest |cumulative_pp| among visible markets
- **THEN** its bar fills 100% and every other bar is proportionally shorter

### Requirement: 过渡动画限定属性
Transitions in the Golden Pit page stylesheet SHALL target only the properties being animated; `transition: all` SHALL NOT be used.

#### Scenario: 样式审查无 transition:all
- **WHEN** the page stylesheet is reviewed
- **THEN** no `transition: all` rule remains and each transition lists explicit properties

### Requirement: 牛熊判断标的列表默认收起
The tech-status panel SHALL render its "标的" table collapsed by default, keeping the verdict, summary, and stats visible. A fold button in the panel header SHALL expand/collapse the table, and SHALL expose an accessible label plus `aria-expanded`.

#### Scenario: 默认收起
- **WHEN** the tech-status panel renders
- **THEN** the "标的" table is collapsed and the fold button reads "展开"

#### Scenario: 展开/收起切换
- **WHEN** the user activates the fold button
- **THEN** the table toggles visibility and the button label/`aria-expanded` update accordingly
