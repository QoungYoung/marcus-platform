import type { ExecutionEnv } from "@earendil-works/pi-agent-core";
import { loadSkills, type Skill } from "@earendil-works/pi-agent-core";
import type { TradingSkill } from "../types.js";

/**
 * Load trading skills from skills directory
 */
export async function loadTradingSkills(
	env: ExecutionEnv,
	skillsDir: string,
): Promise<{ skills: TradingSkill[]; diagnostics: any[] }> {
	const result = await loadSkills(env, skillsDir);

	return {
		skills: result.skills.map((skill) => ({
			...skill,
			category: determineSkillCategory(skill.name),
		})),
		diagnostics: result.diagnostics,
	};
}

/**
 * Determine skill category from skill name
 */
function determineSkillCategory(name: string): "analysis" | "execution" | "strategy" {
	if (name.includes("trade") || name.includes("execute")) {
		return "execution";
	}
	if (name.includes("strategy") || name.includes("backtest")) {
		return "strategy";
	}
	return "analysis";
}

/**
 * Create default trading skills inline (when no skill files available)
 */
export function createDefaultSkills(): TradingSkill[] {
	return [
		{
			name: "market-analysis",
			description: "分析市场走势、板块热点、指数表现",
			content: `你是股票市场分析师。当用户要求分析市场时：

1. 首先调用 get_market_indices 获取主要指数数据
2. 调用 get_sector_performance 获取板块表现
3. 调用 get_hot_stocks 获取热门股票
4. 综合以上数据，结合宏观消息面，给出市场分析

分析要点：
- 指数涨跌情况及趋势判断
- 板块轮动情况，热点板块分析
- 资金流向分析
- 短期技术面和中期趋势判断
- 风险提示`,
			filePath: "built-in:market-analysis",
			category: "analysis",
		},
		{
			name: "stock-research",
			description: "研究个股基本面和技术面，给出投资建议",
			content: `你是股票研究员。当用户要求研究股票时：

1. 调用 get_quote 获取股票实时行情
2. 获取公司基本面信息（可以通过 get_news 相关新闻）
3. 分析技术形态和趋势
4. 结合市场情绪给出投资建议

研究要点：
- 基本面：估值、业绩、行业地位
- 技术面：趋势、支撑阻力、形态
- 消息面：近期新闻、公告
- 风险因素：系统性风险、行业风险
- 操作建议：买入/持有/卖出区间`,
			filePath: "built-in:stock-research",
			category: "analysis",
		},
		{
			name: "trading-execute",
			description: "执行股票交易、管理订单",
			content: `你是交易执行专家。当用户要求下单时：

1. 确认交易意图：买入还是卖出
2. 检查持仓情况：可用数量是否足够
3. 检查账户资金是否充足
4. 执行交易 execute_trade
5. 回报交易结果

重要原则：
- 必须确认交易方向和数量
- 买入时检查可用资金
- 卖出时检查持仓可用数量
- Paper trading 不涉及真实资金
- 每次交易都要回报完整结果`,
			filePath: "built-in:trading-execute",
			category: "execution",
		},
		{
			name: "portfolio-review",
			description: "审视投资组合，评估持仓状况和风险",
			content: `你是投资组合管理专家。当用户要求审视组合时：

1. 调用 get_portfolio 获取当前持仓
2. 调用 get_account 获取账户总览
3. 调用 get_today_profit_loss 获取今日盈亏
4. 分析持仓结构和风险分布

分析要点：
- 仓位分布：是否过于集中
- 盈亏状况：总体和个股
- 风险暴露：行业集中度、风格暴露
- 调仓建议：是否需要优化
- 风险控制建议`,
			filePath: "built-in:portfolio-review",
			category: "strategy",
		},
		{
			name: "news-sentiment",
			description: "分析财经新闻和市场情绪",
			content: `你是财经新闻分析专家。当用户要求分析新闻时：

1. 调用 get_news 获取最新新闻
2. 调用 get_sentiment 获取市场情绪
3. 分析新闻对市场的潜在影响

分析要点：
- 重大新闻的事件性质（正面/负面/中性）
- 对相关板块和个股的影响
- 市场情绪变化
- 资金流向预判
- 投资决策参考`,
			filePath: "built-in:news-sentiment",
			category: "analysis",
		},
	];
}