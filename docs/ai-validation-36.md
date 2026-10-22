# AI 风向标 36 case 验证

> 2026-10-22；自动抽样 36 个 as-of case，无人工标签。

## 结果

```text
case 36
有效 34
错误 2
与机械一致 13
一致率 38.2%
平均 evidence 5.6 条
```

## 混淆矩阵

| 机械 \ AI | alive | warning | rotated | ERROR |
|---|---:|---:|---:|---:|
| alive | 7 | 2 | 0 | 1 |
| warning | 4 | 5 | 0 | 1 |
| dead | 1 | 9 | 0 | 0 |
| rotated | 3 | 2 | 1 | 0 |

## 问题

1. **AI 明显偏向 warning**：34 个有效里 18 个 warning；
2. **rotated 基本识别不出**：6 个里只有 1 个 rotated；
3. **warning 与 alive 互相误判**；
4. 2 个解析错误；
5. 一致率 38%，不能算通过。

## 但有一个信号

机械 dead 的 10 个 case：

```text
AI warning 9
AI alive   1
```

说明 AI 在“机械 dead 是否假阳性”上比较积极，但也可能只是偏 warning。

## 结论

```text
AI 不能接管全部状态        ❌
AI 只适合 dead 复核        ⚠️
rotated 不能交给 AI        ❌
```

## 决定

不按“没问题就这样”处理。  
AI 不进生产，只能继续 shadow，且只考虑用于：

```text
机械 dead → AI 复核 warning/alive
```

alive/warning/rotated 继续用机械状态。
