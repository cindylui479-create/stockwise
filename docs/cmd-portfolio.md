# portfolio 持仓视角

v0.12 新增。需要 watchlist 项有 `buy_price` + `shares` 信息。

## 用法

```bash
# 先添加持仓
python -m stockwise watch add 600519 --price 1500 --shares 100
python -m stockwise watch add 600036 --price 38 --shares 5000

# 看组合
python -m stockwise portfolio summary
```

## 输出

### 总览
- 总成本 / 总市值 / 总浮盈（金额 + %）
- 加权 5y ROE / PE / 股息率

### 行业集中度
- 各行业权重（百分比 + 条形图）
- 单行业 > 30% 标红集中风险

### 卖出信号热力
- 🔴 high severity 计数
- 🟡 medium severity 计数

### 评级分布
- 各评级有多少只

### 单股浮盈明细
- 按浮盈率降序
- 浮亏 > 5% 标红，浮盈 > 5% 标绿
