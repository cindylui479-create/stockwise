from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from stockwise.analyzer.llm import LLMAnalysis, analyze as llm_analyze
from stockwise.analyzer.scorer import score
from stockwise.config import Config
from stockwise.data.fetcher import fetch
from stockwise.data.market import parse_code
from stockwise.report.generator import render, write
from stockwise.watchlist import Watchlist
from stockwise.screening import (
    screen_industry_leaders, screen_hk, load_quick_results, save_quick_result,
)
from stockwise.backtest import run_backtest


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """按伯克希尔范式生成 A 股 / 港股的投资分析报告。

    \b
    示例:
      stockwise 600519                  # 自动识别为 analyze
      stockwise analyze 600519
      stockwise watch add 600519
      stockwise watch list
      stockwise watch run
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ============================================================================
# analyze 命令（默认）
# ============================================================================

@cli.command()
@click.argument("code")
@click.option("--hk", is_flag=True, help="强制按港股识别（4-5 位数字代码默认为港股）")
@click.option("--no-llm", is_flag=True, help="跳过 LLM，仅用规则打分")
@click.option("--no-validate", is_flag=True, help="跳过 baostock 副源校验")
@click.option("--no-governance", is_flag=True, help="跳过 巨潮治理事件抓取")
@click.option("--no-holders", is_flag=True, help="跳过 股东结构抓取")
@click.option("--brief", is_flag=True, help="只输出快读版（5 行核心决策）")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="报告输出目录（默认 ./reports）")
def analyze(code: str, hk: bool, no_llm: bool, no_validate: bool, no_governance: bool,
            no_holders: bool, brief: bool, out: Path | None):
    """分析单只股票并生成报告。"""
    return _run_analyze(code, hk, no_llm, no_validate, no_governance, no_holders, brief, out)


def _run_analyze(code, hk, no_llm, no_validate, no_governance, no_holders, brief, out) -> dict:
    """实际分析逻辑，watch run 也会复用。返回 final score 字段方便 watch 更新。"""
    cfg = Config.load(report_dir=out)

    try:
        sid = parse_code(code, hint_hk=hk)
    except ValueError as e:
        click.secho(f"错误：{e}", fg="red", err=True)
        sys.exit(2)

    click.echo(f"[1/4] 拉取 {sid.market} 市场 {sid.code} 数据 …")
    snapshot = fetch(sid, validate=not no_validate, governance=not no_governance,
                     holders=not no_holders)
    click.echo(f"      公司：{snapshot.profile.name}    当前价：{snapshot.profile.current_price}")

    v = snapshot.validation
    if not v.skipped:
        if v.error:
            click.secho(f"      副源校验失败：{v.error}（继续）", fg="yellow")
        elif v.has_warnings:
            click.secho(f"      ⚠ 副源校验：{len(v.major_diffs)} 处显著差异（>10%），详见报告", fg="yellow")
        else:
            click.echo(f"      副源校验通过（baostock 对比 {v.checked_fields} 个字段，差异 ≤10%）")

    g = snapshot.governance
    if not g.skipped:
        if g.error:
            click.secho(f"      治理事件抓取失败：{g.error}（继续）", fg="yellow")
        elif g.has_red_flags:
            click.secho(f"      ⚠ 治理：{len(g.high)} 条红旗 + {len(g.medium)} 条关注", fg="yellow")
        elif g.medium:
            click.echo(f"      治理：无红旗，{len(g.medium)} 条需关注")
        else:
            click.echo(f"      治理：无重大事件")

    h = snapshot.holders
    if not h.skipped and not h.error:
        if h.top_holders:
            click.echo(f"      股东结构：前 10 流通股东（{h.report_date}），见报告")
        elif h.insider_pct is not None:
            click.echo(f"      股东结构：内部人 {h.insider_pct:.1f}%，机构 {h.institution_pct or 0:.1f}%")

    click.echo(f"[2/4] 规则打分（伯克希尔范式 7 维）…")
    base_result = score(snapshot)
    click.echo(f"      初始得分 {base_result.total}/100  →  {base_result.rating}（安全边际 {base_result.margin_of_safety}）")
    if base_result.vetoes:
        click.secho(f"      ⚠ 触发一票否决：{'; '.join(base_result.vetoes)}", fg="yellow")

    llm: LLMAnalysis | None = None
    llm_error: str | None = None
    if not no_llm:
        if not cfg.llm.usable:
            click.secho(
                "警告：未配置 LLM API key，跳过 LLM 解读",
                fg="yellow", err=True,
            )
            llm_error = "未配置 LLM API key"
        else:
            provider_label = {
                "anthropic": "Anthropic",
                "openai": "OpenAI 兼容",
            }.get(cfg.llm.provider, cfg.llm.provider)
            endpoint = cfg.llm.base_url or "默认端点"
            click.echo(f"[3/4] 调用 {provider_label} ({cfg.llm.model} @ {endpoint}) 做定性分析 …")
            try:
                llm = llm_analyze(snapshot, cfg.llm)
                biz = llm.business_understandability
                mgmt = llm.management_quality
                click.echo(
                    f"      LLM 业务可理解性：{biz if biz is not None else '—'}/5；"
                    f"管理层质量：{mgmt if mgmt is not None else '—'}/5"
                )
            except Exception as e:
                click.secho(f"      LLM 调用失败：{e}（继续以规则结果生成报告）", fg="yellow", err=True)
                llm = None
                llm_error = f"{type(e).__name__}: {e}"
    else:
        click.echo("[3/4] 跳过 LLM 解读 (--no-llm)")

    click.echo(f"[4/4] 生成 Markdown 报告 …")
    report = render(snapshot, base_result, llm, llm_error=llm_error)
    if brief:
        marker = "## 综合判断"
        idx = report.find(marker)
        if idx > 0:
            report = report[:idx].rstrip() + "\n"
    path = write(report, sid.code, cfg.report_dir, name=snapshot.profile.name)

    final = base_result
    if llm is not None:
        final = score(snapshot,
                      llm_business_score=llm.business_understandability,
                      llm_management_score=llm.management_quality)
    click.secho(
        f"\n✓ 报告已生成：{path.resolve()}\n"
        f"  评级：{final.rating}  得分 {final.total}/100  安全边际：{final.margin_of_safety}\n"
        f"  行动建议：{final.action}",
        fg="green",
    )
    return {
        "code": sid.code,
        "market": sid.market,
        "name": snapshot.profile.name,
        "rating": final.rating,
        "score": final.total,
        "action": final.action,
        "margin": final.margin_of_safety,
    }


# ============================================================================
# watch 子命令组
# ============================================================================

@cli.group()
def watch():
    """管理个人 watchlist 并批量监控评级变化。"""
    pass


@watch.command("add")
@click.argument("code")
@click.option("--hk", is_flag=True, help="按港股识别")
@click.option("--price", type=float, default=None, help="买入价（v0.10 持仓跟踪）")
@click.option("--shares", type=int, default=None, help="持有股数")
def watch_add(code: str, hk: bool, price: float | None, shares: int | None):
    """加入 watchlist；可选指定买入价 + 股数（跟踪浮盈浮亏）。"""
    try:
        sid = parse_code(code, hint_hk=hk)
    except ValueError as e:
        click.secho(f"错误：{e}", fg="red", err=True)
        sys.exit(2)
    wl = Watchlist.load()
    if wl.add(sid.code, sid.market, buy_price=price, shares=shares):
        wl.save()
        hold = f" 买入价 ¥{price:.2f} × {shares} 股" if price and shares else ""
        click.secho(f"✓ 已加入 watchlist: {sid.code} ({sid.market}){hold}", fg="green")
    else:
        click.echo(f"  已存在: {sid.code} ({sid.market})")


@watch.command("set")
@click.argument("code")
@click.option("--price", type=float, default=None, help="买入价")
@click.option("--shares", type=int, default=None, help="持有股数")
def watch_set(code: str, price: float | None, shares: int | None):
    """更新已有 watchlist 项的买入价 / 股数。"""
    if price is None and shares is None:
        click.secho("至少指定 --price 或 --shares", fg="yellow")
        return
    wl = Watchlist.load()
    if wl.update_holding(code, buy_price=price, shares=shares):
        wl.save()
        click.secho(f"✓ 已更新 {code} 持仓信息", fg="green")
    else:
        click.echo(f"  未找到 {code}（用 watch add 先加入）")


@watch.command("remove")
@click.argument("code")
def watch_remove(code: str):
    """从 watchlist 移除。"""
    wl = Watchlist.load()
    if wl.remove(code):
        wl.save()
        click.secho(f"✓ 已移除 {code}", fg="green")
    else:
        click.echo(f"  未找到 {code}")


@watch.command("list")
@click.option("--holdings", is_flag=True, help="只显示有买入价的持仓 + 浮盈浮亏")
def watch_list(holdings: bool):
    """显示 watchlist 中所有股票最近一次评级 + 浮盈浮亏（若已记录买入价）。"""
    wl = Watchlist.load()
    if not wl.items:
        click.echo("watchlist 为空。用 `stockwise watch add <code>` 加入。")
        return
    rows = [i for i in wl.items if (not holdings or i.buy_price)]
    if not rows:
        click.echo("无持仓记录。用 `watch add CODE --price X --shares Y` 或 `watch set` 添加。")
        return

    click.echo(f"{'代码':<8} {'市场':<4} {'名称':<14} {'评级':<14} {'得分':>5} {'安全边际':<6} "
               f"{'买入价':>8} {'股数':>6} {'浮盈%':>8} {'行动建议':<24}")
    click.echo("-" * 130)
    total_cost = total_market = 0.0
    for i in rows:
        name = (i.name or "—")[:12]
        rating = (i.last_rating or "—")[:12]
        score_str = f"{i.last_score:>5}" if i.last_score else "    —"
        margin = (i.last_margin or "—")[:4]
        action = (i.last_action or "—")[:22]
        buy = f"¥{i.buy_price:.2f}" if i.buy_price else "—"
        shares = f"{i.shares}" if i.shares else "—"
        # 浮盈：用 v0.10 不实时拉价；用 last_score 时点的 current_price 不存，所以仅在 watch run 之后通过外部填
        pnl_str = "—"
        if i.buy_price and i.shares:
            total_cost += i.buy_price * i.shares
            # 简化：用 last_action 中的价格信息不可得 → 持仓浮盈需 watch run 时另算
        click.echo(f"{i.code:<8} {i.market:<4} {name:<14} {rating:<14} {score_str} {margin:<6} "
                   f"{buy:>8} {shares:>6} {pnl_str:>8} {action:<24}")
    if holdings and total_cost:
        click.secho(f"\n持仓成本：¥{total_cost:,.0f}  （浮盈需运行 `watch run` 后看 portfolio summary）",
                    fg="cyan")


@watch.command("run")
@click.option("--no-llm", is_flag=True, help="跳过 LLM")
@click.option("--brief", is_flag=True, help="只生成快读版报告")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None)
def watch_run(no_llm: bool, brief: bool, out: Path | None):
    """跑 watchlist 中所有股票，更新评级；标记发生变化的标的。

    用 subprocess 隔离每只单股 + 480s 超时（含 LLM 调用上限 120s × 1 次重试 + 数据采集 + 余量）。
    """
    import re
    import subprocess
    wl = Watchlist.load()
    if not wl.items:
        click.echo("watchlist 为空。")
        return
    changes: list[str] = []
    timeouts = failures = 0
    for item in wl.items:
        click.echo(f"\n========== {item.code} {item.name or ''} ==========")
        cmd = ["python3", "-m", "stockwise", item.code]
        if item.market == "HK":
            cmd.append("--hk")
        if no_llm:
            cmd.append("--no-llm")
        if brief:
            cmd.append("--brief")
        try:
            proc = subprocess.run(cmd, timeout=480, check=False,
                                   capture_output=True, text=True)
            if proc.stdout:
                click.echo(proc.stdout, nl=False)
        except subprocess.TimeoutExpired:
            click.secho(f"  ⚠ {item.code} 超过 480s 超时，跳过", fg="yellow")
            timeouts += 1
            continue
        except Exception as e:
            click.secho(f"  ⚠ {item.code} 出错：{e}", fg="yellow")
            failures += 1
            continue
        # 解析评级 / 得分 / 安全边际 / 行动建议
        out_text = proc.stdout
        rating_m = re.search(r"评级：(\S+)\s+得分\s+(\d+)/100\s+安全边际：(\S+)", out_text)
        action_m = re.search(r"行动建议：(.+)", out_text)
        if not rating_m:
            failures += 1
            continue
        new_rating = rating_m.group(1)
        new_score = int(rating_m.group(2))
        new_margin = rating_m.group(3)
        new_action = action_m.group(1).strip() if action_m else "—"
        # 检测变化
        if item.last_action and item.last_action != new_action:
            changes.append(f"⚠ {item.code} {item.name or ''} 行动建议：{item.last_action} → {new_action}")
        elif item.last_score is not None and abs(item.last_score - new_score) >= 5:
            changes.append(f"⚠ {item.code} {item.name or ''} 得分 ≥5 变化：{item.last_score} → {new_score}")
        wl.update_result(
            item.code,
            rating=new_rating, score=new_score, margin=new_margin,
            action=new_action, name=item.name,
        )
    wl.save()
    if timeouts or failures:
        click.echo(f"\n超时 {timeouts} / 失败 {failures}")
    if changes:
        click.secho("\n\n== 评级变化（需关注） ==", fg="yellow", bold=True)
        for c in changes:
            click.secho(f"  {c}", fg="yellow")
    else:
        click.echo("\n\n所有标的评级 / 行动建议无显著变化。")


# ============================================================================
# screen 子命令
# ============================================================================

@cli.command()
@click.option("--industry-top", "top_n", type=int, default=3,
              help="每个行业取 top N（按净利润）。默认 3")
@click.option("--include", default=None,
              help="只看含这些关键词的行业，用 | 分隔。如 '银行|保险|白酒'")
@click.option("--exclude", default=None,
              help="排除这些关键词的行业")
@click.option("--workers", type=int, default=4, help="并发数")
@click.option("--top", "show_top", type=int, default=30,
              help="显示总榜前 N 名（按 quick_score 降序）")
@click.option("--min-score", type=int, default=None,
              help="只显示 quick_score ≥ N 的标的")
@click.option("--to-watchlist", is_flag=True, help="将筛选结果加入 watchlist")
@click.option("--to-deep", is_flag=True,
              help="对筛选 top N 自动跑完整深度分析（含 LLM），并加入 watchlist")
@click.option("--from-cache", is_flag=True, help="只查询 SQLite 已扫描结果，不重新扫描")
@click.option("--cache-only", is_flag=True,
              help="跳过 baostock 净利润拉取，仅用已缓存数据。首次跑后秒回")
@click.option("--list-industries", is_flag=True, help="列出所有可用行业及成分股数")
@click.option("--hk", is_flag=True, help="筛选港股（从 80+ 恒生主流标的池）")
@click.option("--heatmap", "heatmap_path", type=click.Path(path_type=Path), default=None,
              help="生成 HTML 热图（行业 × 标的二维表）到指定路径")
def screen(top_n: int, include: Optional[str], exclude: Optional[str], workers: int,
           show_top: int, min_score: Optional[int], to_watchlist: bool, to_deep: bool,
           from_cache: bool, cache_only: bool, list_industries: bool, hk: bool,
           heatmap_path: Optional[Path]):
    """按行业 top N 扫描 A 股，30 分制粗筛打分。"""
    if list_industries:
        from stockwise.industry import list_industries as _list
        click.echo("所有 A 股行业（按成分股数降序）：")
        for ind, n in _list():
            click.echo(f"  {n:>5}  {ind}")
        return
    if from_cache:
        results = _load_results(include, min_score, show_top)
        _print_results(results)
        if to_watchlist:
            _add_results_to_watchlist(results)
        return

    industry_filter = include.split("|") if include else None
    exclude_list = exclude.split("|") if exclude else None

    click.echo(f"[扫描] 行业 top {top_n}"
               + (f"，含: {include}" if include else "")
               + (f"，排除: {exclude}" if exclude else ""))

    last_phase = [""]
    def progress_cb(done, total, phase="industry"):
        if phase != last_phase[0]:
            stage = "拉取行业净利润" if phase == "industry" else "Quick scan 财务+估值"
            click.echo(f"\n[{stage}] 进度: {done}/{total}", nl=False)
            last_phase[0] = phase
        else:
            click.echo(f"\r[{'拉取净利润' if phase=='industry' else 'Quick scan'}] {done}/{total}",
                       nl=False)

    if hk:
        results = screen_hk(
            top_n=top_n, industry_filter=industry_filter,
            exclude=exclude_list, progress_cb=progress_cb,
        )
    else:
        results = screen_industry_leaders(
            top_n=top_n, industry_filter=industry_filter,
            exclude=exclude_list, workers=workers, progress_cb=progress_cb,
            cache_only=cache_only,
        )
    click.echo()  # newline after progress
    if not results:
        click.secho("无结果（可能行业过滤过严或 baostock 接口失败）", fg="yellow")
        return

    # 过滤显示
    sorted_results = sorted(results, key=lambda r: r.quick_score, reverse=True)
    if min_score is not None:
        sorted_results = [r for r in sorted_results if r.quick_score >= min_score]
    shown = sorted_results[:show_top]

    _print_quick_results(shown)
    click.secho(f"\n扫描完成：{len(results)} 只 / {len(set(r.industry for r in results))} 个行业",
                fg="green")

    if to_watchlist:
        _add_quick_results_to_watchlist(shown)

    if heatmap_path:
        from stockwise.visualize import render_heatmap
        title = "stockwise 港股头部筛选" if hk else "stockwise A 股头部筛选"
        path = render_heatmap(results, heatmap_path, title=title)
        click.secho(f"✓ 热图已生成：{path.resolve()}", fg="green")

    if to_deep:
        click.echo(f"\n[深度分析] 对筛选出的 {len(shown)} 只标的批量跑完整分析…")
        _add_quick_results_to_watchlist(shown)
        import re
        import subprocess
        wl = Watchlist.load()
        done = failed = timeout = 0
        for r in shown:
            click.echo(f"\n========== {r.code} {r.name} ==========")
            cmd = ["python3", "-m", "stockwise", r.code]
            try:
                proc = subprocess.run(cmd, timeout=300, check=False,
                                       capture_output=True, text=True)
                if proc.stdout:
                    click.echo(proc.stdout, nl=False)
                done += 1
                # 解析评级 / 得分 / 安全边际 / 行动建议，回填 watchlist
                rating_m = re.search(r"评级：(\S+)\s+得分\s+(\d+)/100\s+安全边际：(\S+)", proc.stdout)
                action_m = re.search(r"行动建议：(.+)", proc.stdout)
                if rating_m:
                    wl.update_result(
                        r.code,
                        rating=rating_m.group(1),
                        score=int(rating_m.group(2)),
                        margin=rating_m.group(3),
                        action=action_m.group(1).strip() if action_m else "—",
                        name=r.name,
                    )
            except subprocess.TimeoutExpired:
                click.secho(f"  ⚠ {r.code} 超过 480s 超时，跳过", fg="yellow")
                timeout += 1
            except Exception as e:
                click.secho(f"  ⚠ {r.code} 出错：{e}", fg="yellow")
                failed += 1
        wl.save()
        click.secho(f"\n✓ 深度分析批次完成：成功 {done}，超时 {timeout}，失败 {failed}",
                    fg="green")
        click.echo("  watchlist 已更新最新评级，运行 `stockwise watch list` 查看")


def _print_quick_results(results) -> None:
    click.echo()
    click.echo(f"{'代码':<8} {'名称':<12} {'行业':<26} {'排名':<4} "
               f"{'PE':>6} {'PB':>5} {'ROE':>6} {'负债':>5} {'CFO/NP':>7} "
               f"{'Score':>6}  说明")
    click.echo("-" * 130)
    for r in results:
        pe = f"{r.pe:.1f}" if r.pe else "—"
        pb = f"{r.pb:.2f}" if r.pb else "—"
        roe = f"{r.roe_5y:.1f}%" if r.roe_5y else "—"
        debt = f"{r.debt_ratio:.0f}%" if r.debt_ratio is not None else "—"
        cfo = f"{r.cfo_to_np:.2f}" if r.cfo_to_np is not None else "—"
        ind = (r.industry or "—")[:24]
        flags = " ".join(r.quick_flags)[:40]
        click.echo(f"{r.code:<8} {r.name[:10]:<12} {ind:<26} #{r.industry_rank:<3} "
                   f"{pe:>6} {pb:>5} {roe:>6} {debt:>5} {cfo:>7} "
                   f"{r.quick_score:>3}/30  {flags}")


def _load_results(industry, min_score, limit):
    return load_quick_results(industry=industry, min_score=min_score, limit=limit)


def _print_results(rows) -> None:
    if not rows:
        click.echo("缓存里没有结果。先跑一次 `stockwise screen` 扫描。")
        return
    click.echo(f"{'代码':<8} {'名称':<12} {'行业':<26} {'排名':<4} {'Score':>6}")
    click.echo("-" * 70)
    for d in rows:
        ind = (d.get("industry") or "—")[:24]
        click.echo(f"{d['code']:<8} {d['name'][:10]:<12} {ind:<26} "
                   f"#{d.get('industry_rank') or '—':<3} {d.get('quick_score', 0):>3}/30")


def _add_quick_results_to_watchlist(results) -> None:
    wl = Watchlist.load()
    added = 0
    for r in results:
        if wl.add(r.code, "A", r.name):
            added += 1
    wl.save()
    click.secho(f"✓ 加入 watchlist {added} 只（重复的跳过）", fg="green")


def _add_results_to_watchlist(rows) -> None:
    wl = Watchlist.load()
    added = 0
    for d in rows:
        if wl.add(d["code"], "A", d.get("name") or d["code"]):
            added += 1
    wl.save()
    click.secho(f"✓ 加入 watchlist {added} 只（重复的跳过）", fg="green")


# ============================================================================
# compare 子命令（v0.10）
# ============================================================================

@cli.command()
@click.argument("codes", nargs=-1, required=True)
@click.option("--hk", is_flag=True, help="按港股识别全部代码")
def compare(codes: tuple, hk: bool):
    """同行业多只股票横向对比表。

    示例：
      stockwise compare 600036 601166 601398    # 招行 vs 兴业 vs 工行
      stockwise compare 00700 00939 --hk         # 腾讯 vs 建行港股
    """
    if len(codes) < 2:
        click.secho("compare 至少需要 2 个代码", fg="red")
        sys.exit(2)
    rows = []
    for code in codes:
        try:
            sid = parse_code(code, hint_hk=hk)
        except ValueError as e:
            click.secho(f"  {code}: {e}", fg="red")
            continue
        click.echo(f"拉取 {sid.code} …", nl=False)
        try:
            # 不跑 governance / holders，加速 compare（默认 ~5-10s/只）
            snapshot = fetch(sid, validate=False, governance=False, holders=False)
            result = score(snapshot)
        except Exception as e:
            click.secho(f"  失败：{e}", fg="yellow")
            continue
        # 5y ROE 均值
        roes = [p.roe for p in snapshot.financials.annual[:5] if p.roe is not None]
        roe5y = sum(roes) / len(roes) if roes else None
        # 派息率
        div_yield = None
        if snapshot.dividends.ttm_per_10_shares and snapshot.profile.current_price:
            per_share = snapshot.dividends.ttm_per_10_shares / 10
            div_yield = per_share / snapshot.profile.current_price * 100
        rows.append({
            "code": sid.code,
            "name": snapshot.profile.name or "—",
            "industry": (snapshot.profile.industry or "—")[:14],
            "view": result.industry_view,
            "rating": result.rating,
            "total": result.total,
            "roe5y": roe5y,
            "pe": snapshot.valuation.pe_ttm,
            "pb": snapshot.valuation.pb,
            "div_yield": div_yield,
            "margin": result.margin_of_safety,
            "discount": snapshot.intrinsic.discount,
            "action": result.action,
            "sell_signals": len(result.sell_signals),
        })
        click.echo(f" ✓ {result.rating} {result.total}/100")

    if not rows:
        click.secho("无可对比标的", fg="red")
        return

    # 输出对比表
    click.echo()
    click.echo(f"{'代码':<8} {'名称':<10} {'行业':<14} {'profile':<10} "
               f"{'评级':<12} {'得分':>5} {'5y ROE':>7} "
               f"{'PE':>6} {'PB':>5} {'股息率':>7} {'折价%':>7} {'卖出':>4} {'行动':<26}")
    click.echo("-" * 150)
    # 按得分降序
    rows.sort(key=lambda r: r["total"], reverse=True)
    for r in rows:
        roe = f"{r['roe5y']:.1f}%" if r["roe5y"] is not None else "—"
        pe = f"{r['pe']:.1f}" if r["pe"] else "—"
        pb = f"{r['pb']:.2f}" if r["pb"] else "—"
        dy = f"{r['div_yield']:.2f}%" if r["div_yield"] else "—"
        dc = f"{r['discount']:+.1f}%" if r["discount"] is not None else "—"
        ss = f"{r['sell_signals']}" if r["sell_signals"] else "—"
        action = r["action"][:24]
        click.echo(f"{r['code']:<8} {r['name'][:8]:<10} {r['industry']:<14} {r['view']:<10} "
                   f"{r['rating'][:10]:<12} {r['total']:>5} {roe:>7} "
                   f"{pe:>6} {pb:>5} {dy:>7} {dc:>7} {ss:>4} {action:<26}")

    # 行业一致性提示
    industries = set(r["industry"] for r in rows)
    if len(industries) > 1:
        click.secho(f"\n⚠ 注意：{len(industries)} 种行业混合对比（{', '.join(industries)}），"
                    f"评估口径不同，请谨慎横向比较。", fg="yellow")
    # 推荐
    top = rows[0]
    click.secho(f"\n🏆 同列最高分：{top['code']} {top['name']} ({top['total']}/100, {top['rating']})",
                fg="green", bold=True)


# ============================================================================
# backtest 子命令
# ============================================================================

@cli.command()
@click.option("--as-of", required=True,
              help="回测起点日期 YYYY-MM-DD，如 2024-12-31")
@click.option("--horizon", default=None,
              help="终点日期，默认今天")
@click.option("--from-screen", is_flag=True,
              help="使用 SQLite 中最新 screen 结果作为标的池")
@click.option("--from-watchlist", is_flag=True,
              help="使用 watchlist 作为标的池")
@click.option("--codes", default=None,
              help="自定义标的代码列表，逗号分隔（如 600519,600036,000858）")
@click.option("--min-score", type=int, default=None,
              help="仅从 screen 中取 quick_score ≥ N 的标的")
@click.option("--rerun-scoring", is_flag=True,
              help="v0.11 真历史回测：在 as_of 时点重跑评级，验证工具预测能力（慢，每只 +30s）")
def backtest(as_of: str, horizon: Optional[str], from_screen: bool,
             from_watchlist: bool, codes: Optional[str], min_score: Optional[int],
             rerun_scoring: bool):
    """回测：从指定日期持有筛选标的至今的收益（含与沪深 300 对比）。"""
    # 准备标的池
    pool: list[tuple[str, str]] = []
    quick_scores: dict = {}

    if codes:
        for c in codes.split(","):
            c = c.strip()
            if c:
                pool.append((c, c))
    elif from_watchlist:
        wl = Watchlist.load()
        for i in wl.items:
            pool.append((i.code, i.name or i.code))
    elif from_screen:
        rows = load_quick_results(min_score=min_score, limit=200)
        for d in rows:
            pool.append((d["code"], d.get("name") or d["code"]))
            quick_scores[d["code"]] = d.get("quick_score")
    else:
        click.secho("请指定 --from-screen / --from-watchlist / --codes 之一", fg="red")
        sys.exit(2)

    if not pool:
        click.secho("标的池为空", fg="red")
        sys.exit(2)

    click.echo(f"[回测] 起点 {as_of} → 终点 {horizon or '今天'}，{len(pool)} 只标的"
               + (" [真历史回测]" if rerun_scoring else ""))
    result = run_backtest(as_of, pool, horizon, quick_scores=quick_scores,
                          rerun_scoring=rerun_scoring)
    if result.error:
        click.secho(f"失败：{result.error}", fg="red")
        return

    _print_backtest(result)


def _print_backtest(result) -> None:
    click.echo()
    has_hist = any(r.historical_rating for r in result.rows)
    if has_hist:
        click.echo(f"{'代码':<8} {'名称':<12} {'as_of 评级':<14} {'as_of 分':>6} "
                   f"{'起点价':>9} {'终点价':>9} {'收益率':>8}")
        click.echo("-" * 95)
    else:
        click.echo(f"{'代码':<8} {'名称':<14} {'Score':>6} {'起点价':>9} {'终点价':>9} {'收益率':>8}")
        click.echo("-" * 70)
    rows_sorted = sorted(result.rows, key=lambda r: r.return_pct or -999, reverse=True)
    for r in rows_sorted:
        ps = f"{r.price_start:.2f}" if r.price_start else "—"
        pe = f"{r.price_end:.2f}" if r.price_end else "—"
        ret = f"{r.return_pct:+.1f}%" if r.return_pct is not None else "—"
        if has_hist:
            hr = (r.historical_rating or "—")[:12]
            hs = f"{r.historical_score}/100" if r.historical_score else "—"
            click.echo(f"{r.code:<8} {r.name[:10]:<12} {hr:<14} {hs:>6} "
                       f"{ps:>9} {pe:>9} {ret:>8}")
        else:
            score = f"{r.quick_score}/30" if r.quick_score else "—"
            click.echo(f"{r.code:<8} {r.name[:12]:<14} {score:>6} {ps:>9} {pe:>9} {ret:>8}")
    click.echo("-" * 70)
    if result.portfolio_return is not None:
        click.secho(f"等权组合收益率：{result.portfolio_return:+.2f}%", fg="green", bold=True)
    if result.benchmark_return is not None:
        click.echo(f"{result.benchmark_label}：{result.benchmark_return:+.2f}%")
    alpha = result.alpha
    if alpha is not None:
        color = "green" if alpha > 0 else "red"
        click.secho(f"超额收益 alpha：{alpha:+.2f}%", fg=color, bold=True)


# 兼容老入口
main = cli


if __name__ == "__main__":
    cli()
