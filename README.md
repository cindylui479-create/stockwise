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

### 单股分析

```bash
# A 股
python -m stockwise 600519              # 贵州茅台
python -m stockwise 300750              # 宁德时代（自动用成长股版评估）
python -m stockwise 600036              # 招商银行（自动用银行业版）
python -m stockwise 601088              # 中国神华（自动用周期股版，Shiller PE 替代 PE）

# 港股
python -m stockwise 00700 --hk          # 腾讯控股
python -m stockwise 00939 --hk          # 建设银行（银行业版）

# 选项
python -m stockwise 600519 --no-llm     # 跳过 LLM，纯规则打分
python -m stockwise 600519 --brief      # 只输出快读版（5 行决策表）
python -m stockwise 600519 --no-validate     # 跳过 baostock 副源校验
python -m stockwise 600519 --no-governance   # 跳过 巨潮治理事件
python -m stockwise 600519 --no-holders      # 跳过 股东结构
```

### 行业 Top N 筛选（v0.5.0）

```bash
# 全 A 股按行业 top 3 筛选（约 83 行业 × 3 = 250 只，30 分制粗筛打分）
python -m stockwise screen

# 已扫描过用 --cache-only 秒回
python -m stockwise screen --cache-only

# 按行业筛
python -m stockwise screen --include "银行|白酒|医药"
python -m stockwise screen --exclude "煤炭|钢铁"

# 调每行业 top N
python -m stockwise screen --industry-top 5

# 高分入选直接加 watchlist
python -m stockwise screen --min-score 22 --to-watchlist

# 列出所有行业及成分股数
python -m stockwise screen --list-industries

# 从 SQLite 缓存查
python -m stockwise screen --from-cache --industry "银行"
```

quick_score 30 分制：ROE 10 + PE 5 + PB 5 + 负债率 5 + FCF/股 5。数据全部来自 baostock（独立于东财，不依赖 push2）。首次扫描约 30-45 分钟，缓存后秒回。

### Watchlist 监控

```bash
python -m stockwise watch add 600519        # 加入观察列表
python -m stockwise watch add 00700 --hk    # 港股
python -m stockwise watch remove 600519
python -m stockwise watch list              # 看所有标的最近一次评级
python -m stockwise watch run               # 批量跑 watchlist，标记评级变化
python -m stockwise watch run --no-llm --brief   # 批量快读版
```

`watch list` 输出示例：

```
代码      市场  名称       评级              得分  安全边际  行动建议
600036    A    招商银行   值得长期持有       91   充足     可以入场（折价充足）
601318    A    中国平安   质量好且估值合理   84   充足     可以入场（折价充足）
600519    A    贵州茅台   质量好但有瑕疵    72   偏贵     等待回调（估值偏贵）
688256    A    寒武纪     避免              35   偏贵     避免（触发一票否决） ⚠ 治理红旗
```

### 缓存

财报/分红/治理事件等不变数据缓存 24 小时-7 天（在 `~/.stockwise/cache.db`）。
跑批量 watchlist 时性能 ×10。禁用：`STOCKWISE_NO_CACHE=1`。

报告写入 `reports/<code>_<YYYY-MM-DD>.md`。

## 评估框架（按行业分发）

| Profile | 适用 | 安全边际口径 |
|---|---|---|
| **default** | 消费 / 制造 / 一般企业 | FCF Yield + Graham PE×PB + OE×12 + DCF 含增长 |
| **bank** | 银行 / 货币金融服务 | P/B + ROE÷PB 隐含回报 + 股息率 + 衰减 ROE 留存复利 |
| **insurance** | 保险 | P/B + 股息率 + 隐含回报 + 净利稳定性 |
| **growth** | 营收 5 年 CAGR ≥ 15-25%、研发驱动 | PEG（保守 g）+ (1/PE + g) + PS÷CAGR + DCF |
| **cyclical** | 钢铁/煤炭/有色/石油/航运/化工/水泥 | **Shiller PE**（10 年均值 EPS）+ P/B + 高股息率 + 周期顶部预警 |

**识别规则**：
- 银行/保险走对应 profile（A 股 INDUSTRYCSRC1 + 港股 yfinance industry，中英双语关键字）
- 高研发行业 + CAGR ≥ 15% 或 CAGR ≥ 25% 且非资源类 → growth profile
- 否则 default

**质量评级**（5 档 + 否决）：
- ≥85 + 充足 → **值得长期持有**
- ≥85 + 一般 → **优质合理估值**
- ≥85 + 其他 → **优质但偏贵**
- 70-84 + 充足/一般 → **质量好且估值合理**
- 70-84 + 其他 → **质量好但有瑕疵**
- < 70 → **未达伯克希尔标准**
- 一票否决 → **避免**

**行动建议**（独立于评级，给具体决策）：
- 评级 ≥70 + 折价 ≥30% → **可以入场（折价充足）**
- 评级 ≥70 + 折价 10-30% → **可以入场（谨慎）**
- 评级 ≥70 + 折价 -10-10% → **已持有可继续，新仓需等**
- 评级 ≥70 + 折价 <-10% → **等待回调**
- 50-70 → **观察，不建议新仓**
- <50 或否决 → **避免**

**安全边际** = (内在价值 - 当前市值) / 内在价值 × 100%。内在价值由各 profile 的 4 道关综合中位数得出。0-20 分按折价率连续线性映射，标签 4 档：充足 (≥30%) / 一般 (10-30%) / 不足 (-10-10%) / 偏贵 (<-10%)。

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
