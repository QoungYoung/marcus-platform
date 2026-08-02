## 1. Data Pipeline

- [ ] 1.1 Create `scripts/dump_direction_data.py` — iterate trading days, fetch moneyflow (date-batched), daily OHLCV, stk_factor, index_daily via Tushare
- [ ] 1.2 Implement feature derivation: vol_ratio_5d/1d, consecutive_up/down_days, gap_up/down_pct, gap_fill_pct, big_order_net_flow, main_force_ratio
- [ ] 1.3 Implement market breadth features: up_ratio, limit_up_count, advance_decline_ratio, market_volume_trend — computed from candidate cross-section per date
- [ ] 1.4 Merge forward returns (1d/3d/5d) using batch `daily` query, construct binary labels (target_1d/3d/5d)
- [ ] 1.5 Add `--start-date` and `--days` CLI arguments, CSV output with feature columns + target columns
- [ ] 1.6 Add moneyflow fallback: when Tushare `moneyflow` fails, use volume×close_pct_change as proxy net flow

## 2. Market Regime Classifier

- [ ] 2.1 Implement `MarketRegimeClassifier` class in `backend/app/services/direction_prediction.py`
- [ ] 2.2 Compute regime features from daily cross-section: up_ratio, limit_up_count, advance_decline, market_mean_return, market_vol, index_ret_5d/20d
- [ ] 2.3 Implement heuristic regime rule: favorable when up_ratio > 20d median AND index_ret_5d > -1%
- [ ] 2.4 Implement confidence penalty factor for unfavorable days (default 0.7)

## 3. Direction Prediction Model

- [ ] 3.1 Implement `DirectionPredictionService` class — loads trained XGBoost models for 1d/3d/5d horizons
- [ ] 3.2 Implement walk-forward training method: expanding window, train=30d, test=3d, separate model per horizon
- [ ] 3.3 Implement `predict(date, symbols)` method: compute features → regime check → stock prediction → calibrated probabilities
- [ ] 3.4 Implement probability calibration with `sklearn.isotonic.IsotonicRegression` on held-out validation fold
- [ ] 3.5 Implement fallback mode: when model accuracy < baseline + 5%, use regime-only output (P ≈ up_ratio)

## 4. API Endpoint

- [ ] 4.1 Add `GET /api/v1/direction/predict` endpoint with query params: `date` (optional, for historical), `horizon` (1d/3d/5d, default 5d), `min_confidence` (default 0.55)
- [ ] 4.2 Response format: `{market_regime, confidence_penalty, predictions: [{symbol, name, industry, up_probability, confidence}]}`
- [ ] 4.3 Add `GET /api/v1/direction/validate` endpoint returning latest walk-forward metrics

## 5. Validation

- [ ] 5.1 Run `dump_direction_data.py --days 60 --output data/direction_data.csv` to generate training data
- [ ] 5.2 Train and walk-forward validate: compare XGBoost vs LogisticRegression vs RandomForest accuracy vs baseline
- [ ] 5.3 Report per-horizon metrics: accuracy, precision, recall, ROC-AUC, calibration error
- [ ] 5.4 If XGBoost accuracy < baseline + 5%, diagnose feature importance and iterate on weak features
