import { TObject, TString, TOptional, TNumber } from "@sinclair/typebox";
import type { AgentTool, Static } from "@earendil-works/pi-agent-core";

const MARCUS_API_DEFAULT = "http://localhost:8000";

export function createNewsTools(config: { apiUrl?: string } = {}): AgentTool[] {
	const baseUrl = config.apiUrl || MARCUS_API_DEFAULT;

	const getNewsTool: AgentTool = {
		name: "get_news",
		description: "获取最新财经新闻，包括 A 股、港股、美股相关新闻",
		parameters: TObject({
			symbol: TOptional(TString({ description: "股票代码，获取相关新闻" })),
			limit: TOptional(TNumber({ description: "返回数量，默认 20" })),
		}),
		execute: async (toolCallId: string, params: Static<typeof getNewsTool.parameters>) => {
			const query = new URLSearchParams();
			if (params.symbol) query.set("symbol", params.symbol);
			if (params.limit) query.set("limit", String(params.limit));

			const url = `${baseUrl}/api/v1/news?${query.toString()}`;
			const response = await fetch(url);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取新闻失败: ${response.status}` }],
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

	const getSentimentTool: AgentTool = {
		name: "get_sentiment",
		description: "获取市场情绪分析，基于新闻情绪指数判断市场多空",
		parameters: TObject({}),
		execute: async () => {
			const response = await fetch(`${baseUrl}/api/v1/news/sentiment`);
			if (!response.ok) {
				return {
					content: [{ type: "text", text: `获取情绪分析失败: ${response.status}` }],
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

	return [getNewsTool, getSentimentTool];
}