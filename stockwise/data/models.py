from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanyProfile:
    code: str
    market: str               # "A" or "HK"
    name: str
    industry: Optional[str]
    total_market_cap: Optional[float]      # 元 / HKD
    float_market_cap: Optional[float]
    listing_date: Optional[str]
    current_price: Optional[float]
    currency: str = "CNY"
    shares_outstanding: Optional[float] = None  # 总股本（股）

    @property
    def shares(self) -> Optional[float]:
        if self.shares_outstanding:
            return self.shares_outstanding
        if self.total_market_cap and self.current_price:
            return self.total_market_cap / self.current_price
        return None


@dataclass
class FinancialPeriod:
    """单个报告期的关键财务指标。"""
    period: str               # 例如 "20251231"
    revenue: Optional[float] = None             # 营业总收入
    net_profit: Optional[float] = None          # 归母净利润
    roe: Optional[float] = None                 # 百分数
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    operating_cashflow: Optional[float] = None
    free_cashflow: Optional[float] = None       # 企业自由现金流（总额，元）
    fcf_per_share: Optional[float] = None       # 每股企业自由现金流
    goodwill: Optional[float] = None
    revenue_yoy: Optional[float] = None         # 营收同比 %
    profit_yoy: Optional[float] = None          # 利润同比 %
    rd_exp: Optional[float] = None              # 研发支出总额（Tushare）
    rd_ratio: Optional[float] = None            # 研发投入占营收 %（Tushare）
    capex: Optional[float] = None               # 资本支出（Tushare）
    # v0.11 P #52：利润质量深度分解（资产负债表科目）
    accounts_receivable: Optional[float] = None     # 应收账款（坏账风险）
    contract_liabilities: Optional[float] = None    # 合同负债（客户预付，茅台经销商打款指标）
    prepayments: Optional[float] = None             # 预收账款（旧准则，2019 前 = 现 合同负债）


@dataclass
class Financials:
    annual: list[FinancialPeriod] = field(default_factory=list)
    """近 N 年年报数据，最近一年在前。"""

    def latest(self) -> Optional[FinancialPeriod]:
        return self.annual[0] if self.annual else None


@dataclass
class DividendRecord:
    year: int                       # 实施年份
    cash_per_10_shares: float       # 每 10 股派息（税前，元）


@dataclass
class DividendInfo:
    """连续分红与派息额度概览。"""
    history: list[DividendRecord] = field(default_factory=list)
    consecutive_years: int = 0          # 截至最近一年的连续派息年数
    avg_payout_5y: Optional[float] = None  # 近 5 年平均派息率
    ttm_per_10_shares: Optional[float] = None
    """滚动 12 个月（除权日在过去 365 天）的每 10 股派息合计；
    用于股息率计算，避免只取最近一笔（如银行的中期分红）。"""


@dataclass
class Valuation:
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    pe_percentile_5y: Optional[float] = None    # 0-100
    pb_percentile_5y: Optional[float] = None
    ps_percentile_5y: Optional[float] = None
    has_history: bool = False                   # 港股可能没有历史分位


@dataclass
class ValueGate:
    """安全边际中单个估值关。"""
    label: str
    current_str: str
    threshold_str: str
    passed: bool
    fair_value: Optional[float] = None
    """该口径推算的"合理市值"。综合 fair_value 取多口径中位数（保守视角）。"""


@dataclass
class IntrinsicValue:
    """内在价值估算。按行业分发到不同口径：

      industry_view = "default":  FCF Yield / Graham PE×PB / OE×12 / DCF
      industry_view = "bank":     P/B / 隐含回报 ROE÷PB / 股息率 / 留存复利
      industry_view = "insurance": P/B / 股息率 / 隐含回报 / EV(若有)
    """
    industry_view: str = "default"
    market_cap: Optional[float] = None
    gates: list[ValueGate] = field(default_factory=list)
    margin_of_safety: str = "未知"          # "充足" / "一般" / "不足" / "偏贵" / "未知"
    fair_value: Optional[float] = None
    """综合内在价值（多口径中位数，单位与市值一致）。"""
    discount: Optional[float] = None
    """安全边际百分比 = (fair_value - market_cap) / fair_value × 100。正=折价，负=溢价。"""

    def passes_count(self) -> int:
        return sum(1 for g in self.gates if g.passed)

    def total_gates(self) -> int:
        return len(self.gates)


@dataclass
class NewsItem:
    title: str
    publish_time: str
    source: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class GovernanceEvent:
    """治理瑕疵事件：来自巨潮信息披露的公告。"""
    date: str            # YYYY-MM-DD
    title: str
    category: str        # 公告类型，如「关联交易」「股权质押」「立案调查」
    severity: str        # "high" / "medium" / "low" —— 由关键词分级
    url: Optional[str] = None


@dataclass
class HolderRecord:
    """单个股东及其持仓变动。"""
    name: str
    nature: Optional[str] = None     # 股东性质：控股股东/基金/QFII/北上资金等
    pct: Optional[float] = None      # 占流通股本 %
    change: Optional[str] = None     # 增减说明：「不变」「减持」「新进」「+5.2%」
    change_pct: Optional[float] = None  # 变动比率 %


@dataclass
class HolderInfo:
    """股东结构 + 最新变动。

    A 股：来自 akshare 十大流通股东（含季度变动）
    港股：来自 yfinance major_holders（内部人/机构总览）
    """
    source: str = ""
    report_date: Optional[str] = None
    top_holders: list[HolderRecord] = field(default_factory=list)
    insider_pct: Optional[float] = None       # 内部人持股比例（港股）
    institution_pct: Optional[float] = None   # 机构持股比例（港股）
    institution_count: Optional[int] = None   # 机构数量（港股）
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class GovernanceReport:
    """治理事件汇总，按严重度分组。"""
    events: list[GovernanceEvent] = field(default_factory=list)
    skipped: bool = False
    error: Optional[str] = None

    @property
    def high(self) -> list[GovernanceEvent]:
        return [e for e in self.events if e.severity == "high"]

    @property
    def medium(self) -> list[GovernanceEvent]:
        return [e for e in self.events if e.severity == "medium"]

    @property
    def low(self) -> list[GovernanceEvent]:
        return [e for e in self.events if e.severity == "low"]

    @property
    def has_red_flags(self) -> bool:
        return len(self.high) > 0


@dataclass
class ValidationDiff:
    """跨源数据校验：某一字段、某一报告期的差异。"""
    field: str           # "ROE" / "营收" / "净利率"
    period: str          # "2024-12-31"
    primary: float       # akshare 值（主源）
    secondary: float     # baostock 值
    pct_diff: float      # |primary - secondary| / max(|primary|, ε) × 100


@dataclass
class ValidationReport:
    """数据可靠性校验报告（baostock 作为副源校验 akshare 主源）。"""
    source: str = "baostock"
    checked_fields: int = 0
    diffs: list[ValidationDiff] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False                 # True = 用户禁用或副源不可达

    @property
    def has_warnings(self) -> bool:
        return any(d.pct_diff > 10 for d in self.diffs)

    @property
    def major_diffs(self) -> list[ValidationDiff]:
        return [d for d in self.diffs if d.pct_diff > 10]


@dataclass
class StockSnapshot:
    profile: CompanyProfile
    financials: Financials
    valuation: Valuation
    dividends: DividendInfo = field(default_factory=DividendInfo)
    intrinsic: IntrinsicValue = field(default_factory=IntrinsicValue)
    news: list[NewsItem] = field(default_factory=list)
    validation: ValidationReport = field(default_factory=ValidationReport)
    governance: GovernanceReport = field(default_factory=GovernanceReport)
    holders: HolderInfo = field(default_factory=HolderInfo)
    industry_cycle: Optional["IndustryCycle"] = None       # v0.9
    industry_roe_rank: Optional["IndustryRoeRank"] = None  # v0.11 #51
    business_segments: Optional["BusinessSegments"] = None # v0.13 #59
