# stockwise

巴菲特/林奇范式 **A 股 + 港股价值投资分析工具**。

输入股票代码，自动按**行业分发**用对应的巴菲特/格雷厄姆/林奇标准评估，整合 5 个数据源 + LLM 定性分析，输出结构化 Markdown 报告。

> 本工具是 **GARP / 价值投资视角**的辅助分析，**不做短线、不做量化**。
> 所有结论仅供参考，**不构成投资建议**。

## 核心能力

- **6 类行业 profile**：default / bank / insurance / growth / **semi_growth** / cyclical
- **5 数据源**：akshare / baostock / yfinance / 巨潮 / Tushare（可选）
- **7 维度评分** 共 100 分：护城河 25 + 盈利质量 20 + 资本配置 15 + 长期增长 5 + 安全边际 20 + 业务可理解 10 + 管理层 5
- **卖出信号框架**：生意变质 / 质量变质 / 估值离谱三类
- **行业周期位置**：5 年指数价格分位
- **行业 ROE 横截面分位**：识别"周期顶部高 ROE 假象"
- **同业对标**、**持仓买入价跟踪**、**真历史回测**
- **港股治理事件**（v0.9 起）

## 一分钟上手

```bash
pip install -e .
cp .env.example .env       # 填入至少一个 LLM Key

# 单股分析
python -m stockwise 600519              # 贵州茅台
python -m stockwise 600036              # 招商银行（自动用银行业版）

# 同业对标
python -m stockwise compare 600036 601166 601398

# 加入观察列表 + 跟踪持仓
python -m stockwise watch add 600519 --price 1500 --shares 100
python -m stockwise watch run                    # 并行批量更新评级
python -m stockwise portfolio summary            # 组合视角
```

更多示例见 [快速开始](quickstart.md)。
