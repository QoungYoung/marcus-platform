import type { AgentTool as BaseAgentTool, Skill as BaseSkill } from "@earendil-works/pi-agent-core";

/** Stock quote information */
export interface StockQuote {
	symbol: string;
	name: string;
	price: number;
	change: number;
	changePercent: number;
	volume: number;
	amount: number;
	high: number;
	low: number;
	open: number;
	prevClose: number;
	time: string;
}

/** Market index data */
export interface MarketIndex {
	name: string;
	value: number;
	change: number;
	changePercent: number;
}

/** Sector performance data */
export interface SectorPerformance {
	name: string;
	changePercent: number;
	volume: number;
}

/** News item with sentiment */
export interface NewsItem {
	id: string;
	title: string;
	content: string;
	source: string;
	publishedAt: string;
	sentiment: "positive" | "negative" | "neutral";
	relatedSymbols?: string[];
}

/** Portfolio position */
export interface Position {
	symbol: string;
	name: string;
	quantity: number;
	availableQuantity: number;
	avgCost: number;
	currentPrice: number;
	marketValue: number;
	profitLoss: number;
	profitLossPercent: number;
}

/** Account summary */
export interface AccountSummary {
	totalAssets: number;
	cash: number;
	marketValue: number;
	totalProfitLoss: number;
	totalProfitLossPercent: number;
	todayProfitLoss: number;
	availableCash: number;
}

/** Trade execution result */
export interface TradeResult {
	orderId: string;
	symbol: string;
	direction: "buy" | "sell";
	quantity: number;
	price: number;
	totalAmount: number;
	status: "pending" | "filled" | "cancelled" | "rejected";
	filledQuantity?: number;
	filledAmount?: number;
	error?: string;
}

/** Strategy signal */
export interface StrategySignal {
	symbol: string;
	action: "buy" | "sell" | "hold";
	strength: number;
	reason: string;
	indicators: Record<string, number>;
}

/** Trading tools extend base AgentTool */
export interface TradingTool<TDetails = unknown> extends BaseAgentTool<any, TDetails> {
	label: string;
	category: "market" | "trade" | "news" | "portfolio";
}

/** Trading skill extends base Skill */
export interface TradingSkill extends BaseSkill {
	category: "analysis" | "execution" | "strategy";
}

/** Marcus API configuration */
export interface MarcusApiConfig {
	baseUrl: string;
	timeout: number;
}

/** Default Marcus API config for local development */
export const DEFAULT_MARCUS_API: MarcusApiConfig = {
	baseUrl: process.env.MARCUS_API_URL || "http://localhost:8000",
	timeout: 30000,
};