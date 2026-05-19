# analyze 单股分析

## 用法

```bash
python -m stockwise CODE [OPTIONS]
```

`analyze` 是默认命令，可以省略。

## 选项

| 选项 | 说明 |
|---|---|
| `--hk` | 强制按港股识别（4-5 位数字代码默认按 A 股）|
| `--no-llm` | 跳过 LLM，仅用规则打分 |
| `--no-validate` | 跳过 baostock 副源校验 |
| `--no-governance` | 跳过巨潮治理事件抓取 |
| `--no-holders` | 跳过股东结构抓取 |
| `--brief` | 只输出快读版（5 行决策表）|
| `--out PATH` | 报告输出目录（默认 `./reports`）|

## 示例

```bash
python -m stockwise 600519              # 贵州茅台（消费 default profile）
python -m stockwise 300750              # 宁德时代（自动 growth）
python -m stockwise 600036              # 招商银行（自动 bank）
python -m stockwise 601088              # 中国神华（自动 cyclical，Shiller PE）
python -m stockwise 00700 --hk          # 腾讯（港股）
```

## 输出

报告写入 `reports/<CODE>_<NAME>_<YYYY-MM-DD>.md`，含 12+ 章节：

1. 综合判断（评级 + 总分 + 安全边际 + 评估口径）
2. 巴菲特检查清单
3. 公司速览与主业（LLM）
4. 护城河分析（LLM + 财务证据 + 5 年 mermaid 趋势图）
5. 盈利质量与资本配置（含**利润质量深度分解**：应收/合同负债）
6. 长期增长
7. **内在价值估算 + 安全边际**（4 道关，按 profile 不同）+ **个人买卖价位区间**
8. 如果你买下整家公司
9. 管理层质量（LLM）
10. 业务可理解性（LLM）
11. Munger 反向思考（LLM）
12. 风险与警示
13. **卖出信号 / 减仓提示**
14. 综合判断（LLM verdict）
15. 附录：股东结构 / 治理事件 / 副源校验 / 近期新闻
