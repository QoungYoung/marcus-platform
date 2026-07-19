## 1. Core fix — calc_position in indicator.py

- [x] 1.1 rec_shares / probe_shares 兜底：在 `_round_lot` 归零后，若 `max_shares >= 100`，将 rec_shares 和 probe_shares 分别设为 100 股，相应更新 amount/pct，并追加警告标注占比偏高
- [x] 1.2 all_pass 修正：`max_shares == 0` 时强制 `all_pass = false`，warnings 中说明是哪个约束（单票上限/总仓/现金）限制了建仓
- [x] 1.3 验证层 `single_cap_detail` 适配：当兜底后的 probe/rec > effective_single_cap 时，detail 中如实反映但 `single_cap_ok` 保持 true（兜底放行）

## 2. calculate_position_quantity 同步

- [x] 2.1 max_shares=0 时添加 warning 说明限制来源（single cap / total cap / cash reserve）
- [x] 2.2 backtest sandbox（backtest.py `calc_position_sandbox`）同步三处修复（floor 逻辑 + all_pass + single_cap_ok），与 calc_position 一致

## 3. Verification

- [ ] 3.1 宁德时代 (300750.SZ)：验证 probe/rec 从 0 → 100，max=100 不变，all_pass=true，有占比偏高警告
- [ ] 3.2 贵州茅台 (600519.SH)：验证 max=0，all_pass=false，有约束说明
- [x] 3.3 低价股回归测试：`calculate_position_quantity` 低价股(15元)返回 shares=600 正常，不受影响
