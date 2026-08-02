## 1. Backend API

- [x] 1.1 Add `GET /api/v1/market/concept-fund-flow-5d` endpoint in `backend/app/api/market.py`
- [x] 1.2 Implement trading day calculation using Tushare `trade_cal` for N-day window
- [x] 1.3 Implement per-concept aggregation: SUM(pct_change), COUNT(pct_change > 0), SUM(net_amount)
- [x] 1.4 Implement dark track scoring: pct_rank_score × 0.5 + up_days_rank_score × 0.5
- [x] 1.5 Implement today gatekeeper filter (main_net > 0)
- [x] 1.6 Add error handling (Tushare unavailable → 502, invalid params → 422)

## 2. Frontend Tool Registration

- [x] 2.1 Define `getConceptFundFlow5dTool` in `servers/pi-server/src/tools.ts`
- [x] 2.2 Register tool in tool list and call handler

## 3. Prompt Updates

- [x] 3.1 Update TRADE_SYSTEM_PROMPT tool table in `servers/pi-server/src/index.ts` (add get_concept_fund_flow_5d row)
- [x] 3.2 Replace single-track main line selection logic with dual-track logic in `index.ts`
- [x] 3.3 Add three-tier stock selection priority section in `index.ts`
- [x] 3.4 Update CHAT_SYSTEM_PROMPT tool list in `index.ts` (add get_concept_fund_flow_5d)
- [x] 3.5 Mirror all prompt changes to `backend/app/db/prompt_seeds.py`

## 4. Verification

- [ ] 4.1 Test API endpoint with curl against cloud server data (needs deploy)
- [ ] 4.2 Verify tool outputs correct format by inspecting AI tool call results (needs deploy)
- [ ] 4.3 Run existing test suite to confirm no regressions (no existing test suite)
