# stockwise — 巴菲特/林奇范式 A股 + 港股价值投资分析工具

输入股票代码，自动按**行业分发**用对应的巴菲特/格雷厄姆/林奇标准评估，
整合 5 个数据源 + LLM 定性分析，输出结构化 Markdown 报告。

> 本工具是 GARP / 价值投资视角的辅助分析，**不做短线、不做量化**。
> 所有结论仅供参考，**不构成投资建议**。

## 安装

```bash
pip install -e .
cp .env.example .env       # 填入至少一个 LLM Key（见下）
```

## 使用

```bash
# A 股
python -m stockwise 600519              # 贵州茅台
python -m stockwise 002594              # 比亚迪
python -m stockwise 300750              # 宁德时代（自动识别为成长股版评估）
python -m stockwise 600036              # 招商银行（自动识别为银行业版评估）

# 港股（自动用 yfinance 拿数据）
python -m stockwise 00700 --hk          # 腾讯控股
python -m stockwise 00939 --hk          # 建设银行（自动识别为银行业版）

# 跳过 LLM，纯规则打分
python -m stockwise 600519 --no-llm

# 各级数据源开关（按需关闭）
python -m stockwise 600519 --no-validate    # 跳过 baostock 副源校验
python -m stockwise 600519 --no-governance  # 跳过 巨潮治理事件
python -m stockwise 600519 --no-holders     # 跳过 股东结构
```

报告写入 `reports/<code>_<YYYY-MM-DD>.md`。

## 评估框架（按行业分发）

| Profile | 适用 | 安全边际口径 |
|---|---|---|
| **default** | 消费 / 制造 / 一般企业 | FCF Yield + Graham PE×PB + OE×12 + DCF 含增长 |
| **bank** | 银行 / 货币金融服务 | P/B + ROE÷PB 隐含回报 + 股息率 + 留存复利 |
| **insurance** | 保险 | P/B + 股息率 + 隐含回报 + 净利稳定性 |
| **growth** | 营收 5 年 CAGR ≥ 15-25%、研发驱动 | PEG + (1/PE + g) 隐含回报 + PS÷增长率 + DCF 含增长 |

**识别规则**：
- 银行/保险走对应 profile（A 股 INDUSTRYCSRC1 + 港股 yfinance industry，中英双语关键字）
- 高研发行业 + CAGR ≥ 15% 或 CAGR ≥ 25% 且非资源类 → growth profile
- 否则 default

**评级标签**（伯克希尔风格）：
- 综合分 ≥85 + 安全边际充足 → **值得长期持有**
- ≥85 + 其他 → **优质但偏贵**（进 watchlist）
- 70-84 → **质量好但有瑕疵**
- < 70 → **未达伯克希尔标准**
- 一票否决 → **避免**

**一票否决项**：
- 近 5 年任一年净利润为负
- 商誉/净资产 > 50%
- 资产负债率 > 70%（汽车/航空/建筑 75%、证券 80%、银行/保险豁免）
- 经营现金流连续 2 年为负

## 7 维度评分（共 100）

| 维度 | 权重 | 默认 default 体系 |
|---|---:|---|
| 护城河 | 25 | 5 年 ROE ≥ 15% / 毛利 ≥ 40% / 毛利稳定性 |
| 盈利质量 | 20 | 5 年累计 CFO/净利 ≥ 0.85 / 无亏损年 / 净利率波动 < 25% |
| 资本配置 | 15 | 负债率 ≤ 50% / 连续派息 ≥ 5 年 / 商誉 ≤ 1 倍净利 |
| 长期增长 | 10 | 营收/净利 CAGR / 滚动 3 年连续增长 |
| 安全边际 | 20 | 见上方四道关 |
| 业务可理解性（LLM）| 5 | 巴菲特"10 年后仍可预测" |
| 管理层质量（LLM）| 5 | 资本配置 / 治理事件 / 股东沟通 |

各 profile 维度阈值会自动调整（如银行用 ROA 1% / ROE 13%；成长股用 CAGR 20% / 毛利 40%）。

## 数据源

| 来源 | 用途 | 是否必需 |
|---|---|---|
| **akshare** (push2 + legulegu + sina) | 主要财报、估值、新闻；A 股股东、分红 | ✅ |
| **baostock** | 副源校验：ROE/净利率/毛利率/营收 跨源对比 | ✅ |
| **yfinance** | 港股估值 (PE/PB/PS/dividend)、内部人/机构持股 | ✅ 港股 |
| **巨潮资讯网**（cninfo）| A 股治理事件原文（关联交易/担保/质押/立案/诉讼）| ✅ A 股 |
| **东财 f10**（emweb）| push2 故障时的名称/行业/上市日期后备 | 自动 |
| **Tushare Pro** | 研发占比 / 资本支出（成长股 profile 用）| 可选（需 token）|

## LLM 接入

支持 3 类 provider（在 `.env` 配置）：

| Provider | 配置 | 例 |
|---|---|---|
| Anthropic 官方 | `ANTHROPIC_API_KEY` | claude-sonnet-4-6 |
| Anthropic 代理（Claude Code 风格 Bearer Token）| `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` | 第三方中转 |
| OpenAI 兼容（DeepSeek / Kimi / GLM 等）| `STOCKWISE_PROVIDER=openai` + `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `STOCKWISE_MODEL` | gpt-5.x / deepseek-chat |

特殊场景开关（自签证书代理 + 绕过系统 SOCKS）：
```
STOCKWISE_INSECURE_SSL=1
STOCKWISE_TRUST_ENV=0
```

LLM 给出：
- 主业概述
- 护城河定性分析
- 业务可理解性评分（0-5）
- 管理层质量评分（0-5，参考治理事件输入）
- Munger 反向思考（"什么会让这家公司死"）
- 内在价值定性判断
- 综合 verdict

## 报告章节

1. **综合判断**（评级 + 总分 + 安全边际 + 评估口径）
2. **巴菲特检查清单**（每项✅/❌+ 实际数值）
3. 公司速览与主业
4. 护城河分析（LLM 定性 + 财务证据）
5. 盈利质量与资本配置（含分红记录）
6. 长期增长
7. **内在价值估算与安全边际**（4 道关，按 profile 不同）
8. 如果你买下整家公司（回本期）
9. 管理层质量（LLM）
10. 业务可理解性（LLM）
11. Munger 反向思考
12. 风险与警示
13. 综合判断（LLM verdict）
14. 附 A: 股东结构 & 持仓变动
15. 附 B: 治理事件（巨潮近 180 天，按严重度分级）
16. 附 C: 数据可靠性校验（baostock 副源对比）
17. 附 D: 近期相关新闻

## 开发

```bash
pytest -q     # 16 个单测
```

代码结构：
```
stockwise/
├── cli.py              # CLI 入口
├── config.py           # .env 加载 + LLMConfig 多 provider
├── data/
│   ├── fetcher.py      # akshare + sina + 东财 f10 后备 + 内在价值分发
│   ├── models.py       # dataclass: StockSnapshot 等
│   ├── market.py       # 代码解析（A/HK 识别）
│   ├── validator.py    # baostock 副源校验
│   ├── governance.py   # 巨潮治理事件
│   ├── holders.py      # A 股流通股东 + HK 内部人
│   └── tushare_extra.py# Tushare Pro 研发/capex 增强
├── analyzer/
│   ├── scorer.py       # 7 维度 + 4 profile 分发
│   └── llm.py          # Anthropic / OpenAI 兼容
└── report/
    ├── generator.py
    └── template.md.j2
```
