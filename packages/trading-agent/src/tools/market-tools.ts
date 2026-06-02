import { TObject, TString, TNumber, TOptional } from "@sinclair/typebox";
import type { AgentTool, Static } from "@earendil-works/pi-agent-core";
import type { StockQuote, MarketIndex, SectorPerformance, MarcusApiConfig } from "../types.js";

const MARCUS_API_DEFAULT = "http://localhost:8000";

export interface MarketToolsConfig {
	apiUrl?: string;
	timeout?: number;
}

export function createMarketTools(config: MarketToolsConfig = {}): AgentTool[] {
	const baseUrl = config.apiUrl || MARCUS_API_DEFAULT;

	const getMarketIndicesTool: AgentTool = {
		name: "get_market_indices",
		description: "获取主要股票市场指数（上证指数、深证成指、创业板指、科创50等）",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/market/indices`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取指数失败: ${response.status}` }],
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

	const getQuoteTool: AgentTool = {
		name: "get_quote",
		description: "查询个股实时行情，包括当前价格、涨跌幅、成交量等",
		parameters: TObject({
			symbol: TString({ description: "股票代码，如 000001 或 600519" }),
		}),
		execute: async (toolCallId: string, params: Static<typeof getQuoteTool.parameters>) => {
			const response = await fetch(`${baseUrl}/api/v1/market/quote/${params.symbol}`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取行情失败: ${response.status}` }],
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

	const getSectorPerformanceTool: AgentTool = {
		name: "get_sector_performance",
		description: "获取概念板块行情排行（涨幅排序），帮助识别当日量价最强的概念方向。返回 name/pct_change/vol/amount/turnover_rate",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/market/concept-fund-flow`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取板块数据失败: ${response.status}` }],
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

	const getMarketMoneyflowTool: AgentTool = {
		name: "get_market_moneyflow",
		description: "获取大盘资金流向（主力/超大单/大单/中单/小单净流入）。返回上证/深证收盘涨跌+五类资金净流入，用于判断大盘整体资金情绪",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/market/moneyflow-mkt`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取大盘资金流失败: ${response.status}` }],
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

	const getHotStocksTool: AgentTool = {
		name: "get_hot_stocks",
		description: "获取今日热门股票排行榜",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/market/hot`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取热门股票失败: ${response.status}` }],
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

	const getGlobalMarketTool: AgentTool = {
		name: "get_global_market",
		description: "获取全球主要市场指数（港股、美股、A50等）",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/market/global`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取全球市场数据失败: ${response.status}` }],
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

	return [
		getMarketIndicesTool,
		getQuoteTool,
		getSectorPerformanceTool,
		getMarketMoneyflowTool,
		getHotStocksTool,
		getGlobalMarketTool,
	];
}