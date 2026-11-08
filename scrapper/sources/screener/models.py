"""Source-specific Screener records shared by the company-page parsers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceField:
    position: int
    key_raw: str
    raw_text: str
    display_text: str
    role: str
    link_url: str | None = None
    visibility_state: str = "visible"


@dataclass(frozen=True)
class SourceCell:
    row: int
    column: int
    row_label: str | None
    column_label: str | None
    raw_text: str
    display_text: str
    role: str
    link_url: str | None
    rowspan: int
    colspan: int
    table: int = 0


@dataclass(frozen=True)
class Metric:
    position: int
    label_raw: str
    metric_code: str | None
    display_value: str
    value_decimal: str | None
    value_json: str | None
    unit_code: str | None
    scale_multiplier: str | None
    missing_kind: str | None


@dataclass(frozen=True)
class FinancialObservation:
    row: int
    column: int
    section_code: str
    line_item_raw: str
    line_item_code: str | None
    period_label_raw: str
    period_end: str | None
    period_type: str
    fiscal_year: int | None
    display_value: str
    value_decimal: str | None
    unit_code: str
    scale_multiplier: str
    missing_kind: str | None
    is_expandable: bool = False
    schedule_key: str | None = None


@dataclass(frozen=True)
class GrowthObservation:
    block: int
    row: int
    metric_code: str | None
    metric_label_raw: str
    period_code: str | None
    period_label_raw: str
    display_value: str
    value_decimal: str | None
    unit_code: str
    missing_kind: str | None


@dataclass(frozen=True)
class ShareholdingObservation:
    row: int
    column: int
    frequency: str
    holder_code: str | None
    holder_label_raw: str
    period_label_raw: str
    period_end: str | None
    display_value: str
    value_decimal: str | None
    unit_code: str
    classification_regime: str
    missing_kind: str | None


@dataclass(frozen=True)
class DocumentLink:
    position: int
    document_type: str
    title_raw: str
    period_label_raw: str | None
    publisher: str | None
    source_url: str | None
    availability_status: str


@dataclass(frozen=True)
class CompanyProfileLink:
    position: int
    link_type: str
    label_raw: str
    identifier_value: str | None
    source_url: str


@dataclass(frozen=True)
class CompanyTextObservation:
    position: int
    text_type: str
    heading_raw: str
    display_text: str
    content_origin: str
    is_source_generated: bool


@dataclass(frozen=True)
class Quote:
    display_price: str
    price_decimal: str | None
    currency: str | None
    timestamp_raw: str
    timestamp_utc: str | None
    timezone: str
    timestamp_status: str


@dataclass
class ParsedCompany:
    company_name: str
    nse_symbol: str
    bse_code: str
    reporting_scope: str
    source_company_id: str | None
    source_warehouse_id: str | None
    canonical_url: str | None
    quote: Quote
    identity_fields: list[SourceField] = field(default_factory=list)
    ratio_fields: list[SourceField] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    profile_fields: list[SourceField] = field(default_factory=list)
    profile_links: list[CompanyProfileLink] = field(default_factory=list)
    profile_texts: list[CompanyTextObservation] = field(default_factory=list)
    profile_heading: str = "About"
    analysis_fields: list[SourceField] = field(default_factory=list)
    analysis_texts: list[CompanyTextObservation] = field(default_factory=list)
    analysis_heading: str = "Pros and Cons"
    quarter_cells: list[SourceCell] = field(default_factory=list)
    financials: list[FinancialObservation] = field(default_factory=list)
    quarter_heading: str = "Quarterly Results"
    pnl_cells: list[SourceCell] = field(default_factory=list)
    pnl_financials: list[FinancialObservation] = field(default_factory=list)
    pnl_heading: str = "Profit & Loss"
    balance_cells: list[SourceCell] = field(default_factory=list)
    balance_financials: list[FinancialObservation] = field(default_factory=list)
    balance_heading: str = "Balance Sheet"
    cash_cells: list[SourceCell] = field(default_factory=list)
    cash_financials: list[FinancialObservation] = field(default_factory=list)
    cash_heading: str = "Cash Flows"
    growth_cells: list[SourceCell] = field(default_factory=list)
    growth_observations: list[GrowthObservation] = field(default_factory=list)
    growth_heading: str = "Compounded Growth"
    ratios_cells: list[SourceCell] = field(default_factory=list)
    ratios_financials: list[FinancialObservation] = field(default_factory=list)
    ratios_heading: str = "Ratios"
    shareholding_q_cells: list[SourceCell] = field(default_factory=list)
    shareholding_q_observations: list[ShareholdingObservation] = field(default_factory=list)
    shareholding_y_cells: list[SourceCell] = field(default_factory=list)
    shareholding_y_observations: list[ShareholdingObservation] = field(default_factory=list)
    shareholding_heading: str = "Shareholding Pattern"
    shareholding_classification_warning: str | None = None
    shareholding_unavailable_frequencies: list[str] = field(default_factory=list)
    document_fields: list[SourceField] = field(default_factory=list)
    document_links: list[DocumentLink] = field(default_factory=list)
    documents_heading: str = "Documents"
    commentary_texts: list[CompanyTextObservation] = field(default_factory=list)


# Retain the public serialization identity used before the structural extraction.
# company.py re-exports these exact classes for existing imports and pickle data.
for _model in (
    SourceField,
    SourceCell,
    Metric,
    FinancialObservation,
    GrowthObservation,
    ShareholdingObservation,
    DocumentLink,
    CompanyProfileLink,
    CompanyTextObservation,
    Quote,
    ParsedCompany,
):
    _model.__module__ = "scrapper.sources.screener.company"
del _model
