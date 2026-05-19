# compare 同业对标

同行业多只股票横向对比表（v0.10 新增）。

## 用法

```bash
python -m stockwise compare CODE1 CODE2 CODE3 ... [--hk]
```

## 输出列

- 代码 / 名称 / 行业 / profile
- 评级 / 总分
- 5y ROE / PE / PB / 股息率 / 折价%
- 卖出信号数
- 行动建议

按总分降序排列，标记同列最高分。

## 示例

```bash
# 银行三连对比
python -m stockwise compare 600036 601166 601398

# 白酒
python -m stockwise compare 600519 000858 000568

# 港股
python -m stockwise compare 00700 00939 09988 --hk
```

## 跨行业警告

如果 codes 落在不同行业（例 600519 vs 600036），工具会给警告：
"⚠ 注意：N 种行业混合对比，评估口径不同，请谨慎横向比较"

## 速度

每只约 5-10 秒（跳过 governance / holders / validation 加速）。
含 LLM 调用的话会更慢；compare 默认不调 LLM。
