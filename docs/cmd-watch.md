# watch 监控

管理个人 watchlist 并批量监控评级变化。

## 子命令

### watch add

```bash
python -m stockwise watch add CODE [--hk] [--price PRICE] [--shares SHARES]
```

加入 watchlist；可选指定买入价 + 股数（跟踪浮盈浮亏）。

### watch set

```bash
python -m stockwise watch set CODE --price PRICE --shares SHARES
```

更新已有 watchlist 项的买入价 / 股数。

### watch remove

```bash
python -m stockwise watch remove CODE
```

### watch list

```bash
python -m stockwise watch list [--holdings]
```

显示所有股票最近一次评级；`--holdings` 只显示有买入价的持仓。

### watch run

```bash
python -m stockwise watch run [--no-llm] [--brief] [--workers N] [--serial]
```

批量跑 watchlist，更新评级；标记发生变化的标的。

- **`--workers 4`**（默认）：并行 4 个 subprocess，49 只约 15-20 分钟
- **`--serial`**：强制串行，约 60-90 分钟（调试用）
- **`--no-llm`**：跳过 LLM，每只约 5-10 秒
- **`--brief`**：快读版报告

变化检测规则：
- 行动建议变化 → 警告
- 得分变化 ≥ 5 分 → 警告

## 数据存储

watchlist 持久化到 `~/.stockwise/watchlist.json`，JSON 格式，便于备份。
