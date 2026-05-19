# 进阶

## 缓存

财报 / 估值 / 治理事件 / 分红等数据按命名空间分层 TTL，存 `~/.stockwise/cache.db`（SQLite）。

| 命名空间 | TTL |
|---|---|
| financials | 72h |
| valuation | 6h |
| news | 4h |
| governance | 24h |
| dividends | 7d |
| industry-roe | 72h |

禁用缓存：`STOCKWISE_NO_CACHE=1 python -m stockwise ...`

## 子进程隔离

`watch run` / `screen --to-deep` 用 subprocess 隔离每只单股 + 480s 超时，避免某只 hang 拖死整批。

## LLM 调试

```env
# 调试自签证书代理
STOCKWISE_INSECURE_SSL=1
STOCKWISE_TRUST_ENV=0
```

LLM 调用失败时（网络/超时/解析）报告里会显示 `⚠ LLM 调用失败：...`，不再静默伪装为 "未启用 LLM"。

## 一票否决项

- 近 5 年任一年净利润为负（金融业也不豁免）
- 商誉 / 净资产 > 50%
- 资产负债率：> 70%（汽车/航空/建筑 75%、证券 80%、银行/保险豁免）
- 经营现金流连续 2 年为负（金融业豁免）

## 一票否决豁免行业

某些行业的高负债是商业模式特征：

- **银行 / 保险**：天然高杠杆，完全豁免
- **证券 / 资本市场服务**：80% 阈值
- **汽车 / 航空运输 / 建筑**：75% 阈值（产业链占款 + 大额借款）
- 其他：70%（巴菲特经典门槛）

## 数据源稳定性

- 东财 push2 偶发 502 → 自动降级 f10 → sina daily
- baostock 偶发 socket fail → 重试 3 次
- 港股 yfinance 失败 → 降级 akshare 港股摘要

所有数据源失败都不会让整个 analyze 崩溃，会以"数据缺失"展示。
