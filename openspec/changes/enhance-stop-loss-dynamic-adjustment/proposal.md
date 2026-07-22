## Why

豫能控股 07-20 卖飞事件暴露了止损系统的三个独立但互补的缺陷：(1) 高波个股（振幅 12.37%）用了固定 -3.5% 止损线，正常波动范围内就被触发；(2) 止损只看价格不看趋势，60 分钟 MA10 > MA30 时趋势未破却被卖出；(3) 极端恐慌日（全市场流出 -372 亿）板块资金在净流入，规则 1 的 -3pp 阈值产生假阳性。三个缺陷指向同一个根因：止损规则不会看"天气"——既不会看个股的波动率天气，也不会看市场的恐慌天气。

## What Changes

- **P0a 规则 0b 振幅动态化**：成本止损阈值从固定值改为 `max(基础阈值, 振幅×系数)`，三档系数不同：
  - 从未盈利：`max(-4%, 振幅×0.40)`
  - 小盈转亏：`max(-3%, 振幅×0.40)`
  - 无 HWM：`max(-6%, 振幅×0.50)`
- **P0b 60 分钟趋势校验**：止损触发前强制调用 `get_intraday_min(freq='60min')`，若 MA10 > MA30 仍成立，暂停止损并标记"恐慌错杀警戒"。同日同股最多暂停 3 次
- **P0c 规则 1 极端行情阈值动态化**：当全市场主力净流出 > 300 亿时，规则 1 的 -3pp 触发门槛根据板块资金强度自动放宽至 -5pp 或 -8pp。个股自身主力在放量出货时（> 0.3 亿净流出）不调整，防止真出货被误判为错杀
- 三个改动均在各自方法内部完成，不改变规则优先级、不修改 API 接口、不新增数据库表

## Capabilities

### New Capabilities
<!-- No new capabilities — enhancements to existing stop loss behavior -->

### Modified Capabilities
- `trading`: 规则 0b（成本止损）阈值由固定改为振幅自适应；规则 1（板块背离）阈值在极端恐慌日动态放宽；止损执行层增加 60 分钟趋势前置校验

## Impact

- `backend/app/services/stop_loss_monitor.py` — `_check_cost_stop()` 振幅动态化三档扩宽，`_check_sector_divergence()` 增加极端行情阈值动态化，`_execute_stop()` 增加 60 分钟趋势校验
- `backend/app/api/market.py` — `get_intraday_min` 和 `get_moneyflow_mkt` 接口已存在，直接调用
- 不影响规则 0a、2、2.5、2.6、3，不改变规则优先级，不修改 API 接口
