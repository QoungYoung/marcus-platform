## Context

前端 GoldenPitPage、PortfolioPage、ChatContainer 中存在大量与后端 `CHINA_INDICES`、`STATUS_MAP` 等配置重复的硬编码值。当后端新增指数或调整阈值时，前端不会自动同步，产生数据不一致。

## Goals / Non-Goals

**Goals:**
- 状态颜色、策略名标签、阈值描述由后端统一管理，前端通过 API 获取
- Chart 参考线不再硬编码固定数值，改用后端下发的每指数阈值或通用标注
- PortfolioPage 恐慌判断使用每指数的 `pit_greed`/`entry_greed`

**Non-Goals:**
- 不改变后端计算逻辑（只新增展示配置接口）
- 不改动 AI 工具描述中的硬编码（ChatContainer 工具描述文字长度受限，仅更新关键列表）
- 不涉及 PortfolioPage 的风险阈值（仓位>80、回撤>15 等纯 UI 决策）

## Decisions

### 1. 新增 `/golden-pit/display-config` 端点

返回前端展示所需的静态元数据，包括 `status_colors`、`strategy_labels`、`status_labels`。

**理由**: 单一端点承载所有展示配置，前端启动时调用一次即可缓存。避免在多个接口中重复嵌入。

**替代方案**: 在每个 `/status` 响应中嵌入 → 增加响应体积，且元数据在每个请求中重复传输。

### 2. DCA 策略名翻译放在后端

新增 `_strategy_label()` 函数，与 `_strategy_weights()` 并排放置，覆盖所有 9 种策略。

```python
STRATEGY_LABELS = {
    "uniform_3": "3日等权", "uniform_5": "5日等权", "uniform_7": "7日等权",
    "uniform_10": "10日等权", "uniform_15": "15日等权",
    "front_loaded": "前重后轻", "back_loaded": "前轻后重",
    "triangle": "三角加权", "lump_entry": "一次性建仓",
}
```

### 3. PortfolioPage 贪婪判断改为每指数动态

当前 `PortfolioPage.tsx` 用 `panicLine = 0.35` 和 `safeCeil = 0.50` 的固定值。改为从 `/status` 接口的 `indices[n].pit_greed` 和 `indices[n].entry_greed` 读取，对每个指数独立计算状态。

**注意**: PortfolioPage 显示的是组合级别概览，取所有持仓指数的加权平均 `pit_greed`/`entry_greed` 作为参考范围。

### 4. Chart 参考线改为通用标注

GoldenPitPage 的贪心趋势图同时显示多个指数（各有不同的 pit_greed），无法用单一参考线。改为：
- 保留 y=0.35 和 y=0.40 的虚线作为视觉参考
- 标签改为 "参考线 (0.35)" / "参考线 (0.40)"，不再标注"黄金坑线"/"预警线"
- 或者：移除固定参考线，改为每个指数各自的水平线（但多线混合会很乱，推荐保留并改标注）

### 5. ChatContainer 工具描述更新

`update_golden_pit_etf_config` 工具的描述中硬编码了 6 个基金代码。改为运行时从 `CHINA_INDICES` 动态生成，或至少在 `get_golden_pit_etf_configs_tool` 中提示"从 /etf-configs 接口获取完整列表"。

## Risks / Trade-offs

- [Risk] 新增 API 端点增加一次请求 → 前端缓存 displayConfig，仅在页面首次加载时请求
- [Risk] PortfolioPage 加权平均阈值可能不精确 → 取最低/最保守的阈值作为参考线
