import { TObject, TOptional, TNumber } from "@sinclair/typebox";
import type { AgentTool, Static } from "@earendil-works/pi-agent-core";

const MARCUS_API_DEFAULT = "http://localhost:8000";

export function createPortfolioTools(config: { apiUrl?: string } = {}): AgentTool[] {
	const baseUrl = config.apiUrl || MARCUS_API_DEFAULT;

	const getPortfolioTool: AgentTool = {
		name: "get_portfolio",
		description: "获取当前持仓详情，包括仓位、成本、市值、盈亏",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/portfolio/positions`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取持仓失败: ${response.status}` }],
					details: { error: response.statusText },
					isError: true,
				};
			}
			const data = await response.json();
			return {
				content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
				details: data,
			};
		},
	};

	const getAccountTool: AgentTool = {
		name: "get_account",
		description: "获取账户总览，包括总资产、现金、市值、总盈亏",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/portfolio`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取账户信息失败: ${response.status}` }],
					details: { error: response.statusText },
					isError: true,
				};
			}
			const data = await response.json();
			return {
				content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
				details: data,
			};
		},
	};

	const getTodayProfitLossTool: AgentTool = {
		name: "get_today_profit_loss",
		description: "获取今日盈亏统计",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/portfolio?type=today`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取今日盈亏失败: ${response.status}` }],
					details: { error: response.statusText },
					isError: true,
				};
			}
			const data = await response.json();
			return {
				content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
				details: data,
			};
		},
	};

	const getProfitLossHistoryTool: AgentTool = {
		name: "get_profit_loss_history",
		description: "获取历史盈亏记录",
		parameters: TObject({
			days: TOptional(TNumber({ description: "天数，默认 30" })),
		}),
		execute: async (toolCallId: string, params: Static<typeof getProfitLossHistoryTool.parameters>) => {
			const days = params.days || 30;
			const response = await fetch(`${baseUrl}/api/v1/portfolio/history?days=${days}`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取历史盈亏失败: ${response.status}` }],
					details: { error: response.statusText },
					isError: true,
				};
			}
			const data = await response.json();
			return {
				content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
				details: data,
			};
		},
	};

	return [getPortfolioTool, getAccountTool, getTodayProfitLossTool, getProfitLossHistoryTool];
}