# 卖出信号框架

巴菲特卖出三种情形：**①生意变质 ②质量变质 ③估值离谱**。stockwise 把它们映射到具体的可计算规则。

## 三类信号

### ① 生意变质（business deterioration）

| 信号 | 触发条件 | 严重度 |
|---|---|---|
| ROE 连续衰减 | 连续 2 年下降，累计 ≥ 5pct | medium，跌 ≥ 8pct → high |
| 毛利率连续衰减 | 连续 2 年下降，累计 ≥ 5pct | 同上 |
| 营收连续负增长 | 连续 2 年 YoY < 0 | medium，单年 < -10% → high |

### ② 质量变质（quality deterioration）

| 信号 | 触发条件 | 严重度 |
|---|---|---|
| 负债率突增 | 一年内 ≥ +10pct | medium，≥ +15pct → high |
| 商誉激增 | 一年内 ≥ +50% | high |
| 治理 high 红旗 | 巨潮 / 东财港股新闻识别 | high |
| CFO/净利持续低于 0.5 | 连续 2 年 | medium |

### ③ 估值离谱（valuation extreme）

普通企业：

- discount ≤ -50%（市值 ≥ 2× 内在价值） → high「估值严重离谱」
- discount ≤ -30% → medium「估值显著偏贵」

**高 ROE 调整（v0.7.1）**：ROE 5y均 ≥ 25% 的伟大企业放宽阈值——

- discount ≤ -80% → high
- discount ≤ -50% → medium

> 原因：default profile 的 4 道关里 3 道偏 Graham deep value，对 ROE 30%+ 公司系统性偏严。
> 巴菲特/芒格后期范式："以合理价格买伟大企业" > "以便宜价格买平庸企业"。

## 行动建议降档

| 情形 | 行动建议 |
|---|---|
| 估值 high severity + 70+ 分 | **强制** "考虑减仓（估值严重离谱）" |
| 生意/质量 high severity | 在原档后追加 "⚠ 生意/质量恶化" |
| 任意 medium severity | 追加 "⚠ N 项卖出信号" |
