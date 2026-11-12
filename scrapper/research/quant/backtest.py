"""Deterministic long-only cash-account daily-bar simulation, version 1."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext
from typing import Mapping, Sequence

from .validation import aware

D = Decimal
ZERO = D(0)


@dataclass(frozen=True)
class Bar:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    open: Decimal
    close: Decimal
    liquidity_volume: int
    liquidity_available_at: datetime


@dataclass(frozen=True)
class Session:
    opened_at: datetime
    closed_at: datetime
    universe_available_at: datetime
    members: frozenset[str]
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class Target:
    decision_at: datetime
    information_available_at: datetime
    shares: Mapping[str, int]


@dataclass(frozen=True)
class Assumptions:
    initial_cash: Decimal
    brokerage_per_fill: Decimal
    fee_rate: Decimal
    slippage_rate: Decimal
    participation_rate: Decimal
    periods_per_year: int
    universe_provenance: str
    price_provenance: str


@dataclass(frozen=True)
class Fill:
    at: datetime
    symbol: str
    shares: int  # signed, negative for sales
    price: Decimal
    brokerage: Decimal
    fees: Decimal


@dataclass(frozen=True)
class Mark:
    at: datetime
    cash: Decimal
    holdings: tuple[tuple[str, int], ...]
    equity: Decimal
    period_return: Decimal
    drawdown: Decimal


@dataclass(frozen=True)
class BacktestResult:
    assumptions: Assumptions
    fills: tuple[Fill, ...]
    marks: tuple[Mark, ...]
    total_return: Decimal
    max_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_zero_rate: Decimal | None
    total_costs: Decimal  # brokerage + fees + absolute slippage vs open
    unfilled: tuple[tuple[datetime, str, int], ...]
    version: str = 'daily-cash-backtest-v1'


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _decimal(value, minimum=ZERO):
    return isinstance(value, D) and value.is_finite() and value >= minimum


def run_backtest(sessions: Sequence[Session], targets: Sequence[Target],
                 assumptions: Assumptions) -> BacktestResult:
    """Execute each target at the first session open strictly after decision.

    Targets are absolute integer share counts, omitted holdings target zero.
    Unfilled quantities expire each session; no implicit carry or final sale.
    Each session's historical eligible universe must include delisted names
    while eligible. Held names still require prices after leaving that universe.
    Unknown prices are errors, never silently dropped. Callers must supply
    terminal recovery bars and explicit exit targets for delistings.
    """
    # Keep Decimal arithmetic reproducible regardless of the caller's context.
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        return _run(sessions, targets, assumptions)


def _run(sessions, targets, a):
    if not _decimal(a.initial_cash) or a.initial_cash == 0:
        raise ValueError('initial cash must be positive Decimal')
    if any(not _decimal(v) for v in (a.brokerage_per_fill, a.fee_rate, a.slippage_rate)):
        raise ValueError('cost assumptions must be nonnegative finite Decimals')
    if a.fee_rate >= 1 or a.slippage_rate >= 1:
        raise ValueError('fee and slippage rates must be below one')
    if not _decimal(a.participation_rate) or not 0 < a.participation_rate <= 1:
        raise ValueError('participation must be in (0, 1]')
    if not _integer(a.periods_per_year, 1) or not a.universe_provenance.strip() or not a.price_provenance.strip():
        raise ValueError('annualization and historical universe/price provenance required')
    if len(sessions) < 2:
        raise ValueError('at least two complete sessions required')
    previous_close = None
    for s in sessions:
        for t in (s.opened_at, s.closed_at, s.universe_available_at):
            aware(t)
        if s.opened_at >= s.closed_at or (previous_close is not None and s.opened_at <= previous_close):
            raise ValueError('sessions must be chronological and nonoverlapping')
        previous_close = s.closed_at
        if s.universe_available_at > s.opened_at:
            raise ValueError('future universe membership')
        if not isinstance(s.members, frozenset) or any(not x for x in s.members):
            raise ValueError('immutable historical universe required')
        symbols = set()
        for b in s.bars:
            aware(b.liquidity_available_at)
            if b.liquidity_available_at > s.opened_at:
                raise ValueError('opening liquidity estimate uses future information')
            if not b.symbol or b.symbol in symbols:
                raise ValueError('duplicate or empty bar symbol')
            symbols.add(b.symbol)
            if b.opened_at != s.opened_at or b.closed_at != s.closed_at:
                raise ValueError('bar timestamps must match the session')
            if not _decimal(b.open) or not _decimal(b.close) or not _integer(b.liquidity_volume):
                raise ValueError('invalid price or volume')
            if b.open == 0 and b.close != 0:
                raise ValueError('zero open is only allowed for worthless terminal recovery')
        if not s.members <= symbols:
            raise ValueError('missing historical universe prices')
    scheduled = {}
    previous_decision = None
    for target in targets:
        aware(target.decision_at)
        aware(target.information_available_at)
        if target.information_available_at > target.decision_at:
            raise ValueError('target uses future information')
        if previous_decision is not None and target.decision_at <= previous_decision:
            raise ValueError('targets must be chronological and unique')
        previous_decision = target.decision_at
        if any(not k or not _integer(v) for k, v in target.shares.items()):
            raise ValueError('targets require nonnegative integer shares')
        index = next((i for i, s in enumerate(sessions) if s.opened_at > target.decision_at), None)
        if index is None or index in scheduled:
            raise ValueError('target has no execution session or collides with another target')
        s = sessions[index]
        if s.universe_available_at > target.decision_at:
            raise ValueError('universe was not known at decision time')
        if any(v and k not in s.members for k, v in target.shares.items()):
            raise ValueError('target outside historical eligible universe')
        scheduled[index] = dict(target.shares)
    cash = a.initial_cash
    holdings = {}
    fills, marks, unfilled = [], [], []
    costs = ZERO
    prior_equity = peak = a.initial_cash
    for index, s in enumerate(sessions):
        bars = {b.symbol: b for b in s.bars}
        if any(symbol not in bars for symbol in holdings):
            raise ValueError('held asset price missing; supply terminal recovery/exit evidence')
        if index in scheduled:
            wanted = scheduled[index]
            changes = {k: wanted.get(k, 0) - holdings.get(k, 0) for k in holdings.keys() | wanted.keys()}
            # Sales first, then buys in symbol order: deterministic cash priority.
            for symbol, change in sorted(changes.items(), key=lambda item: (item[1] >= 0, item[0])):
                if change == 0:
                    continue
                b = bars.get(symbol)
                if b is None:
                    raise ValueError('target price missing')
                quantity = min(abs(change), int(D(b.liquidity_volume) * a.participation_rate))
                direction = 1 if change > 0 else -1
                if direction > 0 and b.open == 0:
                    raise ValueError('cannot buy a worthless terminal recovery asset')
                price = b.open * (1 + direction * a.slippage_rate)
                if direction > 0:
                    affordable = max(0, int((cash - a.brokerage_per_fill) / (price * (1 + a.fee_rate))))
                    quantity = min(quantity, affordable)
                # Never execute a sale whose charges exceed available proceeds/cash.
                if quantity and direction < 0 and cash + D(quantity) * price * (1-a.fee_rate) < a.brokerage_per_fill:
                    quantity = 0
                if quantity:
                    signed = direction * quantity
                    fees = D(quantity) * price * a.fee_rate
                    cash -= D(signed) * price + a.brokerage_per_fill + fees
                    holdings[symbol] = holdings.get(symbol, 0) + signed
                    if holdings[symbol] == 0:
                        del holdings[symbol]
                    costs += a.brokerage_per_fill + fees + D(quantity) * abs(price - b.open)
                    fills.append(Fill(s.opened_at, symbol, signed, price, a.brokerage_per_fill, fees))
                if quantity < abs(change):
                    unfilled.append((s.opened_at, symbol, change - direction * quantity))
        equity = cash + sum((D(n) * bars[k].close for k, n in holdings.items()), ZERO)
        if prior_equity == 0 and equity != 0:
            raise ValueError('equity recovery from zero has undefined percentage return')
        period_return = equity / prior_equity - 1 if prior_equity else ZERO
        peak = max(peak, equity)
        marks.append(Mark(s.closed_at, cash, tuple(sorted(holdings.items())), equity,
                          period_return, equity / peak - 1))
        prior_equity = equity
    returns = [m.period_return for m in marks]
    mean = sum(returns, ZERO) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
    volatility = variance.sqrt() * D(a.periods_per_year).sqrt()
    sharpe = mean * a.periods_per_year / volatility if volatility else None
    return BacktestResult(a, tuple(fills), tuple(marks), marks[-1].equity / a.initial_cash - 1,
                          -min(m.drawdown for m in marks), volatility, sharpe, costs, tuple(unfilled))
