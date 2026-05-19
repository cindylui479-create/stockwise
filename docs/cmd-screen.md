# screen 行业筛选

按行业 top N 扫描 A 股 / 港股，30 分制粗筛打分。

## 用法

```bash
python -m stockwise screen [OPTIONS]
```

## 选项

| 选项 | 说明 |
|---|---|
| `--industry-top N` | 每个行业取 top N（默认 3）|
| `--include "银行\|白酒"` | 只看含这些关键词的行业 |
| `--exclude "煤炭\|钢铁"` | 排除关键词的行业 |
| `--workers N` | 并发数（默认 4）|
| `--top N` | 显示总榜前 N 名（默认 30）|
| `--min-score N` | 只显示 quick_score ≥ N |
| `--to-watchlist` | 入选直接加 watchlist |
| `--to-deep` | top N 自动跑完整深度分析 + 加 watchlist |
| `--cache-only` | 仅用已缓存数据（首次后秒回）|
| `--from-cache` | 只查询 SQLite 已扫描结果 |
| `--list-industries` | 列出所有行业及成分股数 |
| `--hk` | 筛选港股 |
| `--heatmap PATH` | 生成 HTML 热图 |

## quick_score 30 分制

- ROE 10 分 + PE 5 + PB 5 + 负债率 5 + FCF/股 5
- 数据全部来自 baostock（独立于东财 push2）
- 首次约 30-45 分钟，缓存后秒回

## 示例

```bash
# 全 A 股 top 3，约 83 行业 × 3 ≈ 250 只
python -m stockwise screen

# 已扫描后秒回
python -m stockwise screen --cache-only

# 自动加 watchlist + 跑深度分析
python -m stockwise screen --to-deep --top 10

# 港股
python -m stockwise screen --hk --industry-top 3

# 热图可视化
python -m stockwise screen --cache-only --heatmap ./reports/heat.html
```
