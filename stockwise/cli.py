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
    screen_industry_leaders, load_quick_results, save_quick_result,
)


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
    if not no_llm:
        if not cfg.llm.usable:
            click.secho(
                "警告：未配置 LLM API key，跳过 LLM 解读",
                fg="yellow", err=True,
            )
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
    else:
        click.echo("[3/4] 跳过 LLM 解读 (--no-llm)")

    click.echo(f"[4/4] 生成 Markdown 报告 …")
    report = render(snapshot, base_result, llm)
    if brief:
        marker = "## 综合判断"
        idx = report.find(marker)
        if idx > 0:
            report = report[:idx].rstrip() + "\n"
    path = write(report, sid.code, cfg.report_dir)

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
def watch_add(code: str, hk: bool):
    """加入 watchlist。"""
    try:
        sid = parse_code(code, hint_hk=hk)
    except ValueError as e:
        click.secho(f"错误：{e}", fg="red", err=True)
        sys.exit(2)
    wl = Watchlist.load()
    if wl.add(sid.code, sid.market):
        wl.save()
        click.secho(f"✓ 已加入 watchlist: {sid.code} ({sid.market})", fg="green")
    else:
        click.echo(f"  已存在: {sid.code} ({sid.market})")


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
def watch_list():
    """显示 watchlist 中所有股票最近一次评级。"""
    wl = Watchlist.load()
    if not wl.items:
        click.echo("watchlist 为空。用 `stockwise watch add <code>` 加入。")
        return
    click.echo(f"{'代码':<8} {'市场':<4} {'名称':<14} {'评级':<14} {'得分':>5} {'安全边际':<6} {'行动建议':<24}")
    click.echo("-" * 100)
    for i in wl.items:
        name = (i.name or "—")[:12]
        rating = (i.last_rating or "—")[:12]
        score_str = f"{i.last_score:>5}" if i.last_score else "    —"
        margin = (i.last_margin or "—")[:4]
        action = (i.last_action or "—")[:22]
        click.echo(f"{i.code:<8} {i.market:<4} {name:<14} {rating:<14} {score_str} {margin:<6} {action:<24}")


@watch.command("run")
@click.option("--no-llm", is_flag=True, help="跳过 LLM")
@click.option("--brief", is_flag=True, help="只生成快读版报告")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None)
def watch_run(no_llm: bool, brief: bool, out: Path | None):
    """跑 watchlist 中所有股票，更新评级；标记发生变化的标的。"""
    wl = Watchlist.load()
    if not wl.items:
        click.echo("watchlist 为空。")
        return
    changes: list[str] = []
    for item in wl.items:
        click.echo(f"\n========== {item.code} ==========")
        try:
            result = _run_analyze(
                item.code, hk=(item.market == "HK"),
                no_llm=no_llm, no_validate=False, no_governance=False,
                no_holders=False, brief=brief, out=out,
            )
        except SystemExit:
            click.secho(f"  跳过 {item.code}", fg="yellow")
            continue
        # 检测变化
        prev_action = item.last_action
        prev_score = item.last_score
        if prev_action and prev_action != result["action"]:
            changes.append(f"⚠ {item.code} 行动建议：{prev_action} → {result['action']}")
        elif prev_score is not None and abs(prev_score - result["score"]) >= 5:
            changes.append(f"⚠ {item.code} 得分变化 ≥ 5：{prev_score} → {result['score']}")
        wl.update_result(
            item.code,
            rating=result["rating"], score=result["score"],
            action=result["action"], margin=result["margin"],
            name=result["name"],
        )
    wl.save()
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
@click.option("--from-cache", is_flag=True, help="只查询 SQLite 已扫描结果，不重新扫描")
@click.option("--cache-only", is_flag=True,
              help="跳过 baostock 净利润拉取，仅用已缓存数据。首次跑后秒回")
@click.option("--list-industries", is_flag=True, help="列出所有可用行业及成分股数")
def screen(top_n: int, include: Optional[str], exclude: Optional[str], workers: int,
           show_top: int, min_score: Optional[int], to_watchlist: bool, from_cache: bool,
           cache_only: bool, list_industries: bool):
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


def _print_quick_results(results) -> None:
    click.echo()
    click.echo(f"{'代码':<8} {'名称':<12} {'行业':<26} {'排名':<4} "
               f"{'PE':>6} {'PB':>5} {'ROE':>6} {'负债':>5} {'FCF/股':>7} "
               f"{'Score':>6}  说明")
    click.echo("-" * 130)
    for r in results:
        pe = f"{r.pe:.1f}" if r.pe else "—"
        pb = f"{r.pb:.2f}" if r.pb else "—"
        roe = f"{r.roe_5y:.1f}%" if r.roe_5y else "—"
        debt = f"{r.debt_ratio:.0f}%" if r.debt_ratio is not None else "—"
        fcf = f"{r.fcf_per_share:.2f}" if r.fcf_per_share is not None else "—"
        ind = (r.industry or "—")[:24]
        flags = " ".join(r.quick_flags)[:40]
        click.echo(f"{r.code:<8} {r.name[:10]:<12} {ind:<26} #{r.industry_rank:<3} "
                   f"{pe:>6} {pb:>5} {roe:>6} {debt:>5} {fcf:>7} "
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


# 兼容老入口
main = cli


if __name__ == "__main__":
    cli()
