# P2 轮动细分宇宙配置（冻结版 v1，2026-09-02）

> 冻结说明：词表目前按用户确认采用代码内 SUB_UNIVERSE 硬编码（2026-09-02 收一版），
> 运行时概念成员/基金拥挤全部来自真实数据：DB stock_concept_map(东财成分) + fund_portfolio_holdings(公募) + position_class_result。
> 每周一 8:05 自动运行 classify_rotation_universe.py，把新增/未归类概念写入 data/rotation_universe_classified.json，
> rotation_universe 导入时自动并入对应组（LLM 分类兜底，失败不影响硬编码基线）。

## 组定义（L2 细分 + L1 粗方向）

| 组 | 类型 | 关键词锚点（截取后） | 2026-09-02 概念数 |
|---|---|---|---|
| 科技/AI(总集) | L1 META（只展示不计象限） | AI/算力/云/液冷/存储/芯片/半导体/材料/光/CPO/软件/数据/终端/铜缆电源 52+ | 52~56 |
| AI应用 | L2 | AI应用/AIGC/AI智能体/AI语料/多模态/智谱/信创/软件(国产·垂直·横向)/数字经济/数据要素 | 15 |
| AI终端 | L2 | AIPC/AI手机/AI眼镜/消费电子 | 8 |
| 国算/算力 | L2 | 算力/数据中心/云计算/大数据/国资云/腾讯云/算力租赁 | 8 |
| 液冷 | L2 | 液冷概念/液冷服务器 | 2 |
| 存储 | L2 | 存储芯片 | 1 |
| 材料 | L2 | 半导体材料/光刻胶/光刻机(胶)/碳基材料 | 4 |
| 芯片/半导体 | L2 | 半导体概念/设备/国产芯片/AI芯片/三代·四代半导体/数字·模拟芯片设计/光刻机 | 11 |
| 光通信 | L2 | 光通信模块/CPO/光纤概念 | 4 |
| 铜缆/电源 | L2 | 铜缆高速连接/电源设备 | 3 |

## 计分与使用
- 拥挤度：fund_portfolio_holdings(真实公募 Q2) → 概念成分 → avg_float（build_crowding）
- 位置空间：position_class_result 距高折让/rel低位/LOW-MID（rotation_universe）
- 四象限：可埋伏 / 拥挤但有空间(做T) / 拥挤无空间(回避) / 中性
- 生产：trade_graph 轮动门控 context + LOW 埋伏候选过滤
