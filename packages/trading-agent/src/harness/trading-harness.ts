import {
	AgentHarness,
	type AgentHarnessOptions,
	type AgentTool,
	type ExecutionEnv,
	type Session,
} from "@earendil-works/pi-agent-core";
import type { Model } from "@earendil-works/pi-ai";
import type { ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { AgentMessage, Skill } from "@earendil-works/pi-agent-core";
import type { TradingTool, TradingSkill, MarcusApiConfig, AccountSummary, Position } from "../types.js";
import { DEFAULT_MARCUS_API } from "../types.js";

export interface TradingHarnessOptions {
	env: ExecutionEnv;
	session: Session;
	tools: TradingTool[];
	skills?: TradingSkill[];
	model: Model<any>;
	thinkingLevel?: ThinkingLevel;
	marcusApi?: MarcusApiConfig;
	getApiKeyAndHeaders?: (
		model: Model<any>,
	) => Promise<{ apiKey: string; headers?: Record<string, string> } | undefined>;
}

export class TradingHarness {
	private harness: AgentHarness<TradingSkill, any, TradingTool>;
	private marcusApi: MarcusApiConfig;

	constructor(options: TradingHarnessOptions) {
		this.marcusApi = options.marcusApi || DEFAULT_MARCUS_API;

		const harnessOptions: AgentHarnessOptions<TradingSkill, any, TradingTool> = {
			env: options.env,
			session: options.session,
			tools: options.tools,
			model: options.model,
			thinkingLevel: options.thinkingLevel ?? "medium",
			resources: {
				skills: options.skills ?? [],
			},
			getApiKeyAndHeaders: options.getApiKeyAndHeaders,
			systemPrompt: this.buildSystemPrompt(),
		};

		this.harness = new AgentHarness(harnessOptions);
	}

	private buildSystemPrompt(): string {
		return `你是 Marcus 股市分析与交易助手，一个专业的 AI 投资顾问。

你的职责：
1. 分析市场走势、板块热点、个股机会
2. 提供交易决策建议（买入/卖出/持有）
3. 管理投资组合，监控持仓风险
4. 解读新闻情绪对市场的影响

可用工具：
- get_market_indices: 获取主要指数（上证、深证、创业板、科创板）
- get_quote: 查询个股实时行情
- get_sector_performance: 获取板块表现
- get_hot_stocks: 获取热点股票
- get_news: 获取最新财经新闻
- get_sentiment: 获取市场情绪分析
- get_portfolio: 获取当前持仓
- get_account: 获取账户信息
- execute_trade: 执行交易（模拟）
- get_strategy_signals: 获取策略信号

重要原则：
- 始终基于数据分析给出建议
- 风险控制优先于盈利追求
- 不承诺收益，只提供分析参考
- paper trading 模式，不涉及真实资金`;
	}

	async prompt(text: string): Promise<import("@earendil-works/pi-ai").AssistantMessage> {
		return this.harness.prompt(text);
	}

	async skill(name: string, additionalInstructions?: string): Promise<import("@earendil-works/pi-ai").AssistantMessage> {
		return this.harness.skill(name, additionalInstructions);
	}

	async steer(text: string): Promise<void> {
		return this.harness.steer(text);
	}

	async followUp(text: string): Promise<void> {
		return this.harness.followUp(text);
	}

	async abort(): Promise<{ clearedSteer: AgentMessage[]; clearedFollowUp: AgentMessage[] }> {
		return this.harness.abort();
	}

	async waitForIdle(): Promise<void> {
		return this.harness.waitForIdle();
	}

	subscribe(
		listener: (event: any, signal?: AbortSignal) => Promise<void> | void,
	): () => void {
		return this.harness.subscribe(listener);
	}

	on<TType extends string>(
		type: TType,
		handler: (event: any) => any,
	): () => void {
		return this.harness.on(type as any, handler);
	}

	getModel(): Model<any> {
		return this.harness.getModel();
	}

	getThinkingLevel(): ThinkingLevel {
		return this.harness.getThinkingLevel();
	}

	async setModel(model: Model<any>): Promise<void> {
		return this.harness.setModel(model);
	}

	async setThinkingLevel(level: ThinkingLevel): Promise<void> {
		return this.harness.setThinkingLevel(level);
	}

	getMarcuApiConfig(): MarcusApiConfig {
		return { ...this.marcusApi };
	}
}