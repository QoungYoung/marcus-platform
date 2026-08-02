## 1. Data Pipeline (dump_direction_data.py)

- [x] 1.1 Add adj_factor to price adjustment in load_stock_daily (open/close/high/low × adj_factor)
- [x] 1.2 Add total_mv to keep_cols for leader filtering
- [x] 1.3 Change target from binary (0/1) to holding-period return `(T+N_close - T_open) / T_open × 100`
- [x] 1.4 Implement per-board winsorization (main board vs ChiNext/STAR independent 1%/99%)
- [x] 1.5 Implement leader filtering: top 3 by total_mv per industry per day, after full-market sector feature computation
- [x] 1.6 Add 3 new sector-level features: sector_money_flow, sector_breadth, sector_rank to _compute_sector_features
- [x] 1.7 Remove available flag from moneyflow logic (no 0-fill bias)
- [x] 1.8 Update CSV columns to match new feature set (26 features: 19 stock + 7 sector)

## 2. Model Service (direction_prediction.py)

- [x] 2.1 Update SECTOR_COLS to 7 dimensions (add sector_money_flow, sector_breadth, sector_rank)
- [x] 2.2 Remove MARKET_COLS and INDEX_COLS from ALL_FEATURES (26 total)
- [x] 2.3 Update derive_stock_features: all individual features lagged to T-1 (change_pct, turnover_rate, vol, rsi, ret, consec, gap, moneyflow)
- [x] 2.4 Update add_sector_features: compute 3 new industry-level features + sector rank
- [x] 2.5 Convert _tune_xgb from XGBClassifier to XGBRegressor with Pseudo-Huber loss (reg:pseudohubererror)
- [x] 2.6 Convert train_walk_forward: regression target, Spearman R + RMSE + R² evaluation, walk-forward window sensitivity test (60/120/180/250)
- [x] 2.7 Implement long-only portfolio evaluation: IR vs CSI 300, monthly win rate, max drawdown, Calmar, net of 0.18% transaction cost
- [x] 2.8 Update predict(): return expected_return instead of up_probability, remove calibrator logic, remove regime multiplier
- [x] 2.9 Update _predict_fallback(): heuristic based on T-1 momentum + volume
- [x] 2.10 Remove MarketRegimeClassifier class, remove unused imports
- [x] 2.11 Save/load model format updated for XGBRegressor (no calibrators, no regime classifier)

## 3. API Endpoint (direction.py)

- [x] 3.1 Implement moneyflow forward-fill with TTL (1 day) + 20-day median fallback, delete _moneyflow_fallback function
- [x] 3.2 Update predict_direction: expected_return output, industry diversification (≤2 per industry), reuse leaderboard's _detect_market_regime
- [x] 3.3 Update validate_models: new metric fields (rmse_mean, spearman_r_mean, direction_accuracy_mean)
- [x] 3.4 Document that inference runs at 15:01 with all features from completed T-day data → next-day execution

## 4. Risk Manager (risk_manager.py — new)

- [x] 4.1 Create RiskManager class with NAV tracking and peak tracking
- [x] 4.2 Implement position_scale(): 1.0 (dd<2%) → linear 0.6 (dd=5%) → accelerated 0.3 (dd=8%) → floor 0.2
- [x] 4.3 Implement emergency_check(): Top-10 underperformance vs benchmark for 3 consecutive days
- [x] 4.4 Implement emergency retrain rate limit (max once per 5 trading days)

## 5. Model Update Scheduler (model_update_scheduler.py — new)

- [x] 5.1 Create ModelUpdateScheduler class with should_retrain() for monthly schedule
- [x] 5.2 Implement emergency retrain trigger using RiskManager.emergency_check()
- [x] 5.3 Emergency retrain: 60-day data, reuse Optuna params, < 1 minute target
- [x] 5.4 Implement retrain frequency limit (emergency ≤ 1 per 5 days)

## 6. Integration & Validation

- [x] 6.1 Verify feature consistency between training (dump_direction_data.py) and inference (direction.py + direction_prediction.py)
- [ ] 6.2 Run data pipeline: `python scripts/dump_direction_data.py --days 500`
- [ ] 6.3 Run walk-forward training: `train_walk_forward("data/direction_data.csv", n_trials=100)` with 60/120/180/250 window sensitivity
- [ ] 6.4 Verify model saves and loads correctly
- [ ] 6.5 Test API endpoint returns correct response format
- [ ] 6.6 Document final Spearman R, IR, Calmar from walk-forward run
