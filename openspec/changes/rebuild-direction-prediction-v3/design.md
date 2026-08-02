## Context

The current direction prediction system uses a binary classification approach (XGBClassifier) to predict P(return > 0) with 32 features including 9 market-level constant features. Three rounds of expert review identified critical flaws: target variable mismatch (predicting single-day pulse vs holding-period return), feature set contamination (constant features wasting column sampling), training-inference distribution drift (moneyflow OOD, price adjustment), and evaluation metric mismatch (Long-Short spread for a Long-Only strategy).

The expert-vetted v3 architecture addresses all identified issues with specific design decisions documented below.

## Goals / Non-Goals

**Goals:**
- Predict holding-period return `(T+N_close - T_open) / T_open` for industry leader stocks
- Use only cross-sectional features (26 dimensions) with all individual stock features lagged to T-1
- Forward-adjusted prices throughout the pipeline
- Robust regression with Pseudo-Huber loss and per-board winsorization
- Dynamic position scaling via RiskManager (1.0→0.2, never zero)
- Long-only evaluation: Information Ratio, monthly win rate, Calmar, max drawdown
- Monthly scheduled retraining + emergency retrain trigger

**Non-Goals:**
- Short selling or hedging capabilities
- Intraday prediction (model runs at 15:01 for next-day execution)
- Real-time streaming features (batch prediction once per day)
- Macro/fundamental features (PE/PB, PMI, interest rates)

## Decisions

### D1: Target = (T+N_close - T_open) / T_open

**Rationale**: The v2 target `(T+N_close - T+N_open) / T+N_open` predicted "what happens intraday on day T+N" — which is unrelated to "what your account earns buying at T_open and holding N days." The correct target is the total holding-period return including overnight gaps.

**Alternatives considered:**
- `(T+N_close - T_close) / T_close`: Predicts from T_close but execution is at T_open next day → mismatch
- `(T+N_open - T_open) / T_open`: Predicts only overnight moves, misses intraday on exit day
- Chosen: Includes all components of actual PnL

### D2: All features lagged to T-1

The model uses T-1 day data (all complete) to predict `(T+N_close - T_open) / T_open`. At inference time (15:01), T day just closed → features computed from T day data → predict T+1 execution. No look-ahead, no incomplete data.

### D3: Remove 9 market-level features from model input

MARKET_COLS(6) + INDEX_COLS(3) have zero within-day variance. They cannot help XGBoost rank stocks within a day's cross-section. Column sampling (colsample_bytree=0.8) wastes probability mass on them. Market timing is handled independently via the RiskManager's regime awareness and position scaling.

### D4: Pseudo-Huber loss instead of MSE

The `reg:pseudohubererror` objective in XGBoost applies linear loss to large residuals and quadratic loss to small ones. This naturally handles the fat tails of return distributions without artificially clipping legitimate ChiNext/STAR board volatility.

### D5: Per-board winsorization

Main board (±10% limit) and ChiNext/STAR (±20% limit) have different natural volatility ranges. A single 1%/99% winsorize across all boards would clip legitimate +15% moves on ChiNext. Each board's quantiles are computed independently.

### D6: Moneyflow forward-fill with TTL

East Money API failure → use T-1 day's moneyflow data (within 1-day TTL) → use 20-day median of that stock's moneyflow (falls within training distribution density). Never use 0-fill or the "amount × change_pct" proxy.

### D7: RiskManager uses continuous position scaling, not binary circuit breaker

Position scale drops linearly from 1.0 (dd < 2%) → 0.6 (dd = 5%) → 0.3 (dd = 8%) → 0.2 floor. Never zero — ensures the strategy stays on the table during policy-driven violent rebounds.

### D8: Monthly retrain + emergency trigger, not drawdown-triggered retrain

Drawdown-triggered retraining during a systemic crash would bake panic behavior into the model. Instead, retraining happens on a fixed calendar (month-end) with an emergency override only when the model consistently underperforms the benchmark for 3 consecutive days.

## Risks / Trade-offs

- [Risk] Model cannot predict overnight policy shocks (e.g., 2024-09-24) → Mitigation: RiskManager preserves minimum 20% position, ensuring partial participation in rebounds
- [Risk] 60-day emergency retrain may be too short to capture regime change → Mitigation: The next monthly retrain (250-day data) will overwrite it
- [Risk] Forward-fill from T-1 is stale if stock had an earnings announcement → Mitigation: TTL=1 day + 20-day median fallback is the least-bad option among all alternatives
- [Risk] Per-board winsorization requires sufficient samples per board per day → Mitigation: If a board has < 10 stocks active, fall back to 1%/99% on the combined sample
