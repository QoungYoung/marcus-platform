## 1. UI — Add load button

- [x] 1.1 在按钮组中新增「加载持仓」按钮（`:loading` 状态绑定）

## 2. Logic — fetch and populate

- [x] 2.1 实现 `loadPortfolioFromServer()` 函数：fetch `GET /api/v1/portfolio/positions`，映射字段，去重追加
- [x] 2.2 符号格式转换：复用 `formatCode()` 将 `000725` 转为 `sz000725`

## 3. Verification

- [ ] 3.1 服务运行时测试：点击「加载持仓」验证持仓数据正确填入，斐波那契位自动计算
- [ ] 3.2 重复点击测试：再次点击不产生重复条目，仅刷新行情
