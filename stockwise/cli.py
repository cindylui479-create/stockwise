from __future__ import annotations

import sys
from pathlib import Path

import click

from stockwise.analyzer.llm import LLMAnalysis, analyze as llm_analyze
from stockwise.analyzer.scorer import score
from stockwise.config import Config
from stockwise.data.fetcher import fetch
from stockwise.data.market import parse_code
from stockwise.report.generator import render, write
from stockwise.watchlist import Watchlist


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


# 兼容老入口：`from stockwise.cli import main`
main = cli


if __name__ == "__main__":
    cli()
