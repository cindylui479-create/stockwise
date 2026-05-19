# backtest 历史回测

## 简单回测（持有至今）

```bash
python -m stockwise backtest --as-of 2025-01-02 --from-watchlist
python -m stockwise backtest --as-of 2024-12-31 --codes 600519,000858,600036
```

输出每只起点价 / 终点价 / 收益率，等权组合 vs 沪深 300 alpha。

## 真历史回测（v0.11）

```bash
python -m stockwise backtest --as-of 2024-01-02 --from-watchlist --rerun-scoring
```

**`--rerun-scoring`** 启用：
- 在 as_of 时点截断财务数据
- 用截断后的数据重跑 score() → 得到当时的评级
- 计算从 as_of 持有到今天的收益
- 验证："当时评级'值得长期持有'的标的 5 年后跑赢沪深 300 否"

输出表多两列：
- `as_of 评级`：当时的工具评级
- `as_of 分`：当时的总分

## 标的池来源

- `--from-watchlist`：用 watchlist 全部
- `--from-screen [--min-score N]`：用 SQLite 中最新 screen 结果
- `--codes 600519,000858,600036`：自定义

## 限制

- 不还原 as_of 时的市值（用当前股本 × as_of 价格估算）
- 不还原 LLM 评分（业务可理解性/管理层质量按当前默认）
- 行业景气数据用当前快照（无法回放 5 年前行业指数分位）

真历史回测主要用于**验证规则部分的预测能力**。
