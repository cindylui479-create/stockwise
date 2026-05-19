# 快速开始

## 安装

```bash
git clone https://github.com/cindylui479-create/stockwise.git
cd stockwise
pip install -e .
cp .env.example .env
```

`.env` 至少填一个 LLM provider（见下方"LLM 接入"）。

## 单股分析

```bash
python -m stockwise 600519              # A 股
python -m stockwise 00700 --hk          # 港股
python -m stockwise 600519 --no-llm     # 跳过 LLM，纯规则打分
python -m stockwise 600519 --brief      # 快读版（5 行决策表）
```

## 同业对标

```bash
python -m stockwise compare 600036 601166 601398    # 招行 vs 兴业 vs 工行
python -m stockwise compare 00700 09988 --hk         # 腾讯 vs 阿里港股
```

## Watchlist

```bash
# 加入并跟踪买入价
python -m stockwise watch add 600519 --price 1500 --shares 100

# 列出（含浮盈）
python -m stockwise watch list --holdings

# 批量更新评级（并行 4 worker，约 15-20 分钟）
python -m stockwise watch run

# 串行调试模式
python -m stockwise watch run --serial
```

## 组合视角

```bash
python -m stockwise portfolio summary
# 输出：总浮盈 / 加权 ROE/PE / 行业集中度 / 卖出信号热力 / 评级分布
```

## 行业筛选

```bash
python -m stockwise screen                           # 全 A 股按行业 top 3
python -m stockwise screen --include "银行|白酒"     # 按行业过滤
python -m stockwise screen --to-deep --top 10        # 深度分析 top 10
python -m stockwise screen --hk                       # 港股
```

## 历史回测

```bash
# 简单：从 watchlist 持有至今
python -m stockwise backtest --as-of 2025-01-02 --from-watchlist

# 真历史回测：as_of 时点重跑评级，验证预测能力
python -m stockwise backtest --as-of 2024-01-02 --from-watchlist --rerun-scoring
```

## LLM 接入

支持 Anthropic 官方 / Anthropic 代理 / OpenAI 兼容（DeepSeek / Kimi / GLM）。在 `.env` 中配置一种即可：

```env
# 方案 A：Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# 方案 B：OpenAI 兼容
STOCKWISE_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1
STOCKWISE_MODEL=deepseek-chat
```
