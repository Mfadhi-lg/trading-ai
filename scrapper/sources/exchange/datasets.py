"""The one place that says what an exchange dataset is and where it comes from.

Before this registry the same facts lived in four modules: the dataset tuples in
`security_master.py`, `corporate_changes.py` and `result_filings.py`, the row
floors in `config.py`, and the URLs in a Compose file and a README. Automation
needs them in one structure so a scheduler can ask "which datasets refresh on a
trading day, and what do I fetch for each" without hard-coding an answer.

The registry holds configuration only: no network, no database, no parsing. Every
default URL is still re-validated through `validate_exchange_dataset_url` before
it is fetched, so being listed here grants a URL nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from .corporate_changes import NSE_NAME_CHANGE, NSE_SYMBOL_CHANGE
from .result_filings import BSE_FINANCIAL_RESULTS, NSE_FINANCIAL_RESULTS
from .security_master import (
    BSE_SECURITY_MASTER,
    NSE_EQUITY_MASTER,
    NSE_MII_SECURITY_FILE,
    NSE_SME_MASTER,
)


SECURITY_MASTER = "security_master"
CORPORATE_CHANGE = "corporate_change"
RESULT_FILING = "result_filing"

TRADING_DAYS = "trading_days"
WEEKLY = "weekly"
ON_DEMAND = "on_demand"

# The NSE result export addresses one reporting period at a time, so its URL is
# a template rather than a constant. This is the only placeholder in use.
PERIOD_ENDED = "{period_ended}"


@dataclass(frozen=True)
class DatasetConfig:
    """Everything the pipeline needs to know about one published dataset.

    `authoritative_segments` is the scope the file is a *complete* statement of.
    It is what stops a partial master from marking securities absent that it was
    never going to list: the NSE equity master says nothing about SME securities,
    and the MII file says nothing about either exchange's SME segment.
    """

    key: str
    exchange: str
    kind: str
    default_url: str | None
    cadence: str
    row_floor: int
    authoritative_segments: tuple[tuple[str, str], ...] = ()
    notes: str = ""

    @property
    def url_needs_result_period(self) -> bool:
        return PERIOD_ENDED in (self.default_url or "")

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "exchange": self.exchange,
            "kind": self.kind,
            "default_url": self.default_url,
            "cadence": self.cadence,
            "row_floor": self.row_floor,
            "authoritative_segments": [list(pair) for pair in self.authoritative_segments],
            "notes": self.notes,
        }


DATASETS: Mapping[str, DatasetConfig] = {
    config.key: config
    for config in (
        DatasetConfig(
            key=NSE_EQUITY_MASTER,
            exchange="NSE",
            kind=SECURITY_MASTER,
            default_url="https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            cadence=TRADING_DAYS,
            row_floor=1500,
            authoritative_segments=(("NSE", "equity"),),
        ),
        DatasetConfig(
            key=NSE_SME_MASTER,
            exchange="NSE",
            kind=SECURITY_MASTER,
            default_url=(
                "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
            ),
            cadence=TRADING_DAYS,
            row_floor=200,
            authoritative_segments=(("NSE", "sme"),),
        ),
        DatasetConfig(
            key=NSE_MII_SECURITY_FILE,
            exchange="NSE",
            kind=SECURITY_MASTER,
            # Deliberately unwired. The published shape of this report has not
            # been observed here, and a default URL would make an unverified
            # parser run unattended. Confirm it with `exchange-inspect-dataset`,
            # correct the alias map in security_master.py to what NSE actually
            # publishes, then set the URL.
            default_url=None,
            cadence=TRADING_DAYS,
            row_floor=1500,
            authoritative_segments=(("NSE", "equity"), ("BSE", "equity")),
            notes=(
                "Combined NSE-listed and BSE-exclusive universe. Authoritative for "
                "the equity segment of both exchanges and for neither SME segment. "
                "Inspect the live report before wiring a URL."
            ),
        ),
        DatasetConfig(
            key=BSE_SECURITY_MASTER,
            exchange="BSE",
            kind=SECURITY_MASTER,
            default_url=(
                "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
                "?Group=&Scripcode=&industry=&segment=Equity&status=Active"
            ),
            cadence=TRADING_DAYS,
            row_floor=3000,
            authoritative_segments=(("BSE", "equity"), ("BSE", "sme")),
        ),
        DatasetConfig(
            key=NSE_SYMBOL_CHANGE,
            exchange="NSE",
            kind=CORPORATE_CHANGE,
            default_url="https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
            cadence=TRADING_DAYS,
            row_floor=1,
        ),
        DatasetConfig(
            key=NSE_NAME_CHANGE,
            exchange="NSE",
            kind=CORPORATE_CHANGE,
            default_url="https://nsearchives.nseindia.com/content/equities/namechange.csv",
            cadence=TRADING_DAYS,
            row_floor=1,
        ),
        DatasetConfig(
            key=NSE_FINANCIAL_RESULTS,
            exchange="NSE",
            kind=RESULT_FILING,
            # Quarter-ended March 2025 and later are published in the Integrated
            # Filing export, which addresses exactly one period at a time.
            default_url=(
                "https://www.nseindia.com/api/integrated-filing-results"
                "?type=Integrated%20Filing-%20Financials"
                f"&period_ended={PERIOD_ENDED}&csv=true"
            ),
            cadence=TRADING_DAYS,
            row_floor=1,
        ),
        DatasetConfig(
            key=BSE_FINANCIAL_RESULTS,
            exchange="BSE",
            kind=RESULT_FILING,
            default_url=(
                "https://api.bseindia.com/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w"
                "?SCRIP_CD=&FlagDur=5&HFQ=3&ISUBGROUP_CODE=&segment=C"
            ),
            cadence=TRADING_DAYS,
            row_floor=1,
        ),
    )
}

DATASET_KEYS = tuple(DATASETS)


def dataset_config(dataset: str) -> DatasetConfig:
    try:
        return DATASETS[dataset]
    except KeyError:
        raise ValueError(f"Unknown exchange dataset: {dataset!r}") from None


def datasets_of_kind(kind: str) -> tuple[DatasetConfig, ...]:
    return tuple(config for config in DATASETS.values() if config.kind == kind)


def _period_ended(result_period: str) -> str:
    """Render a YYYY-MM reporting period as the NSE export's period-ended value."""
    try:
        year, month = (int(part) for part in result_period.split("-", 1))
        first_of_month = date(year, month, 1)
    except (TypeError, ValueError):
        raise ValueError(f"result period must be YYYY-MM: {result_period!r}") from None
    following = date(year + month // 12, month % 12 + 1, 1)
    last_day = following.toordinal() - 1
    return date.fromordinal(last_day).strftime("%d-%b-%Y")


def default_url_for(dataset: str, *, result_period: str | None = None) -> str | None:
    """The registry's URL for a dataset, or None when it is not wired for fetch.

    A dataset whose URL names a reporting period cannot be fetched without one:
    guessing the period would silently collect the wrong quarter, so it raises.
    """
    config = dataset_config(dataset)
    if config.default_url is None:
        return None
    if not config.url_needs_result_period:
        return config.default_url
    if not result_period:
        raise ValueError(
            f"{dataset} publishes one reporting period per request; "
            "pass --result-period (or --url) to say which"
        )
    return config.default_url.replace(PERIOD_ENDED, _period_ended(result_period))


def authoritative_segments(dataset: str) -> tuple[tuple[str, str], ...]:
    """The (exchange, segment) pairs a dataset may sweep for absence."""
    return dataset_config(dataset).authoritative_segments


def row_floors() -> dict[str, int]:
    return {config.key: config.row_floor for config in DATASETS.values()}
