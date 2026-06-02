import { TObject, TString, TNumber, TLiteral } from "@sinclair/typebox";
import type { AgentTool, Static } from "@earendil-works/pi-agent-core";

const MARCUS_API_DEFAULT = "http://localhost:8000";

export interface TradeToolsConfig {
	apiUrl?: string;
	timeout?: number;
}

export function createTradeTools(config: TradeToolsConfig = {}): AgentTool[] {
	const baseUrl = config.apiUrl || MARCUS_API_DEFAULT;

	const executeTradeTool: AgentTool = {
		name: "execute_trade",
		description: "执行股票买入或卖出交易（paper trading 模拟）",
		parameters: TObject({
			symbol: TString({ description: "股票代码，如 000001" }),
			direction: TLiteral(["buy", "sell"], { description: "交易方向，买入或卖出" }),
			quantity: TNumber({ description: "交易数量，必须为正整数" }),
			price: TOptional(TNumber({ description: "指定价格，不填则使用市价" })),
		}),
		execute: async (toolCallId: string, params: Static<typeof executeTradeTool.parameters>) => {
			const response = await fetch(`${baseUrl}/api/v1/trades`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					symbol: params.symbol,
					side: params.direction,
					volume: params.quantity,
					price: params.price,
				}),
			});
			if (!response.ok) {
				const errorText = await response.text();
				return {
					content: [{ type: "text", text: `交易执行失败: ${errorText}` }],
					details: { error: errorText },
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

	const getTradeHistoryTool: AgentTool = {
		name: "get_trade_history",
		description: "查询交易历史记录",
		parameters: TObject({
			symbol: TOptional(TString({ description: "股票代码，不填则返回全部" })),
			limit: TOptional(TNumber({ description: "返回数量限制，默认 50" })),
		}),
		execute: async (toolCallId: string, params: Static<typeof getTradeHistoryTool.parameters>) => {
			const query = new URLSearchParams();
			if (params.symbol) query.set("symbol", params.symbol);
			if (params.limit) query.set("limit", String(params.limit));

			const url = `${baseUrl}/api/v1/trades?${query.toString()}`;
			const response = await fetch(url);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取交易历史失败: ${response.status}` }],
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

	const getOpenOrdersTool: AgentTool = {
		name: "get_open_orders",
		description: "查询当前挂起的订单",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/trades?status=pending`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取挂起订单失败: ${response.status}` }],
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

	const cancelOrderTool: AgentTool = {
		name: "cancel_order",
		description: "取消一个挂起的订单",
		parameters: TObject({
			orderId: TString({ description: "订单ID" }),
		}),
		execute: async (toolCallId: string, params: Static<typeof cancelOrderTool.parameters>) => {
			const response = await fetch(`${baseUrl}/api/v1/trades/${params.orderId}`, {
				method: "DELETE",
			});
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `取消订单失败: ${response.status}` }],
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

	return [executeTradeTool, getTradeHistoryTool, getOpenOrdersTool, cancelOrderTool];
}