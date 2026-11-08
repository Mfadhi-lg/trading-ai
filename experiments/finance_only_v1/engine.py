"""Decimal calculations on one facade-selected parse, with explicit null states.

Input rows remain acquisition facts. Metrics are derived research outputs, never
another financial truth store. No network, model, database or membership writes.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext

VERSION = 'finance_only_v1.experimental.1'


def shifted(period, months):
    d = date.fromisoformat(period)
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return f'{y:04}-{m+1:02}-{monthrange(y, m+1)[1]:02}'


def metric(value=None, status='MISSING', unit='ratio', formula='', inputs=()):
    if value is not None:
        with localcontext() as ctx:
            ctx.prec = 50
            value = str(Decimal(value).quantize(Decimal('.0001'), rounding=ROUND_HALF_EVEN))
        status = 'PRESENT'
    return dict(value=value, status=status, unit=unit, formula=formula,
                inputs=sorted(set(inputs)))


def calc(parts, fn, *, formula, unit='ratio', positive=(), nonnegative=()):
    ids = [i for p in parts for i in p['inputs']]
    bad = next((p['status'] for p in parts if p['value'] is None), None)
    if bad:
        return metric(status=bad, formula=formula, unit=unit, inputs=ids)
    values = [Decimal(p['value']) for p in parts]
    if any(values[i] <= 0 for i in positive) or any(values[i] < 0 for i in nonnegative):
        return metric(status='INVALID_COMPARATOR', formula=formula, unit=unit, inputs=ids)
    with localcontext() as ctx:
        ctx.prec = 50
        return metric(fn(*values), formula=formula, unit=unit, inputs=ids)


def growth(now, before, years=1):
    # Negative current earnings can yield a valid signed YoY decline. CAGR needs
    # strictly positive endpoints; never take a root of negative/zero earnings.
    return calc([now, before], lambda a, b: ((a/b) ** (Decimal(1)/years)-1)*100,
                formula=f'100*((current/base)^(1/{years})-1)', unit='percent',
                positive=(0, 1) if years > 1 else (1,))


class Statements:
    def __init__(self, rows, scope, sector, cutoff):
        if sector != 'industrial':
            raise ValueError('Industrial engine requires reviewed non-financial classification')
        self.rows = {}
        self.warnings = []
        for r in rows:
            if r['reporting_scope'] != scope:
                raise ValueError('mixed accounting scopes')
            if datetime.fromisoformat(r['available_at_utc'].replace('Z','+00:00')) > datetime.fromisoformat(cutoff.replace('Z','+00:00')):
                raise ValueError('observation after cutoff')
            end = r['period_end']
            if not end or r['period_type'] == 'ttm':
                continue
            d = date.fromisoformat(end)
            if d.year < 2000 or d > date.fromisoformat(cutoff[:10]):
                raise ValueError('invalid/unresolved reporting period')
            if d.day != monthrange(d.year, d.month)[1]:
                raise ValueError('non-month-end reporting period requires review')
            key = (r['section_code'], r['period_type'], end, r['line_item_code'])
            if key in self.rows:
                raise ValueError(f'ambiguous observation {key}')
            self.rows[key] = r

    def get(self, section, period, code, interval='fiscal_year'):
        r = self.rows.get((section, interval, period, code))
        if r is None:
            return metric()
        ids = [r['financial_observation_id']]
        expected = ('INR', '1') if code == 'eps_inr' else (
            ('DAYS', '1') if code.endswith('days') or code in {'days_payable', 'cash_conversion_cycle'}
            else ('PERCENT_POINT', '1') if code.endswith('_pct') else ('INR_CRORE', '10000000'))
        if (r['unit_code'], r['scale_multiplier']) != expected:
            return metric(status='INCOMPATIBLE_UNIT', inputs=ids)
        if r['missing_kind']:
            if r['value_decimal'] is not None:
                raise ValueError('numeric content on missing observation')
            return metric(status='MISSING', inputs=ids)
        if r['value_decimal'] is None:
            return metric(status='MISSING', inputs=ids)
        try:
            value = Decimal(r['value_decimal'])
            if not value.is_finite():
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            return metric(status='INVALID_NUMBER', inputs=ids)
        # Preserve source precision until the final derived result is rounded.
        return dict(value=str(value), status='PRESENT', unit=expected[0],
                    formula='stored_value', inputs=ids)

    def periods(self, section, interval):
        return sorted({k[2] for k in self.rows if k[:2] == (section, interval)})


def analyse(payload):
    if any(r['parse_id'] != payload['facade']['provenance']['parse_id'] for r in payload['observations']):
        raise ValueError('mixed accepted parse versions')
    s = Statements(payload['observations'], payload['scope'], payload['sector'], payload['cutoff'])
    warnings = list(payload.get('warnings', []))
    annual = []
    for p in s.periods('pnl', 'fiscal_year'):
        # Accept only annual P&L columns with matching cash and balance columns;
        # do not annualize unmatched interim balances or combine report scopes.
        get = lambda code: s.get('pnl', p, code)
        bs = lambda code, q=p: s.get('balance_sheet', q, code)
        cf = lambda code: s.get('cash_flow', p, code)
        prev = shifted(p, -12)
        exact_year = prev in s.periods('pnl', 'fiscal_year')
        has_cash = p in s.periods('cash_flow', 'fiscal_year')
        has_balance = p in s.periods('balance_sheet', 'fiscal_year')
        m = {k: get(code) for k, code in [('revenue', 'sales'), ('pat', 'net_profit'),
             ('eps', 'eps_inr'), ('operating_profit', 'operating_profit'), ('interest', 'interest')]}
        m['operating_margin'] = calc([m['operating_profit'], m['revenue']], lambda a,b: a/b*100,
            formula='100*operating_profit/revenue', unit='percent', positive=(1,))
        m['net_margin'] = calc([m['pat'], m['revenue']], lambda a,b: a/b*100,
            formula='100*PAT/revenue', unit='percent', positive=(1,))
        m['ebit'] = calc([get('profit_before_tax'), m['interest']], lambda a,b:a+b,
            formula='PBT+interest (includes other income)', unit='INR_CRORE')
        m['interest_coverage'] = calc([m['ebit'], m['interest']], lambda a,b:a/b,
            formula='(PBT+interest)/interest', positive=(1,))
        equity = calc([bs('equity_capital'), bs('reserves')], lambda a,b:a+b,
            formula='equity_capital+reserves', unit='INR_CRORE')
        prior_equity = calc([bs('equity_capital',prev), bs('reserves',prev)], lambda a,b:a+b,
            formula='prior equity_capital+reserves', unit='INR_CRORE')
        avg_equity = calc([equity,prior_equity], lambda a,b:(a+b)/2,
            formula='(opening_equity+closing_equity)/2', unit='INR_CRORE', positive=(0,1))
        m['roe'] = calc([m['pat'], avg_equity],lambda a,b:a/b*100,
            formula='100*PAT/average_book_equity',unit='percent',positive=(1,))
        m['borrowings'] = bs('borrowings')
        m['debt_to_equity'] = calc([m['borrowings'],equity],lambda a,b:a/b,
            formula='borrowings/closing_book_equity',positive=(1,),nonnegative=(0,))
        m['debt_to_assets'] = calc([m['borrowings'],bs('total_assets')],lambda a,b:a/b,
            formula='borrowings/total_assets',positive=(1,),nonnegative=(0,))
        m['roce'] = metric(status='UNSUPPORTED',unit='percent',
            formula='EBIT/average(total_assets-current_liabilities); current liabilities unavailable')
        m['source_roce'] = s.get('ratios',p,'roce_pct')
        m['cfo'] = cf('cash_from_operating_activity')
        m['fcf'] = cf('free_cash_flow')
        m['cfo_to_pat'] = calc([m['cfo'],m['pat']],lambda a,b:a/b,
            formula='annual_CFO/annual_PAT',positive=(1,))
        m['implied_capex'] = calc([m['cfo'],m['fcf']],lambda a,b:a-b,
            formula='CFO-source_FCF; implied only, source FCF definition unverified',unit='INR_CRORE')
        m['capex'] = metric(status='UNSUPPORTED',unit='INR_CRORE',
            formula='direct purchases of property/plant/intangibles unavailable; investing CF is not capex')
        for code in ('debtor_days','inventory_days','days_payable','cash_conversion_cycle','working_capital_days'):
            m[code] = s.get('ratios',p,code)
        m['recomputed_ccc'] = calc([m['debtor_days'],m['inventory_days'],m['days_payable']],
            lambda a,b,c:a+b-c,formula='debtor_days+inventory_days-days_payable',unit='DAYS')
        if m['recomputed_ccc']['value'] is not None and m['cash_conversion_cycle']['value'] is not None:
            delta=abs(Decimal(m['recomputed_ccc']['value'])-Decimal(m['cash_conversion_cycle']['value']))
            if delta:
                warnings.append(f'{p}: source CCC differs from recomputed rounded components by {delta} days; preserve both.')
        for name in ('revenue','pat','eps'):
            code = {'revenue':'sales','pat':'net_profit','eps':'eps_inr'}[name]
            m[name+'_yoy'] = growth(m[name],s.get('pnl',prev,code))
            for n in (3,5):
                history = [shifted(p,-12*i) for i in range(n+1)]
                continuous = all(q in s.periods('pnl','fiscal_year') for q in history)
                m[f'{name}_cagr_{n}y'] = growth(m[name],s.get('pnl',shifted(p,-12*n),code),n) if continuous else metric(status='NONCOMPARABLE',unit='percent')
        if not (has_cash and has_balance and exact_year):
            warnings.append(f'{p}: incomplete aligned annual sections or opening balance')
        annual.append(dict(period=p, metrics=m))
    quarters = []
    for p in s.periods('quarters','quarter'):
        get = lambda code,q=p:s.get('quarters',q,code,'quarter')
        m = {name:get(code) for name,code in [('revenue','sales'),('pat','net_profit'),('eps','eps_inr')]}
        for name,code in [('revenue','sales'),('pat','net_profit'),('eps','eps_inr')]:
            for suffix,offset in [('yoy',-12),('qoq',-3)]:
                m[name+'_'+suffix] = growth(m[name],get(code,shifted(p,offset)))
        m['operating_margin'] = calc([get('operating_profit'),m['revenue']],lambda a,b:a/b*100,
            formula='100*operating_profit/revenue',unit='percent',positive=(1,))
        m['net_margin'] = calc([m['pat'],m['revenue']],lambda a,b:a/b*100,
            formula='100*PAT/revenue',unit='percent',positive=(1,))
        quarters.append(dict(period=p,metrics=m))
    recent = annual[-5:]
    continuous = len(recent)==5 and all(recent[i]['period']==shifted(recent[i-1]['period'],12) for i in range(1,5))
    consistency = {}
    for name in ('pat','cfo','fcf'):
        vals = [r['metrics'][name]['value'] for r in recent]
        consistency[name+'_positive_years_of_5'] = (sum(Decimal(v)>0 for v in vals)
            if continuous and all(v is not None for v in vals) else None)
    latest = annual[-1]['metrics'] if annual else {}
    tests = [('revenue_cagr_3y', Decimal('0'), 'positive three-year revenue CAGR'),
             ('pat_cagr_3y', Decimal('0'), 'positive three-year PAT CAGR'),
             ('roe', Decimal('12'), 'ROE above experimental 12% threshold'),
             ('cfo_to_pat',Decimal('0.8'),'CFO/PAT above experimental 0.8 threshold')]
    strengths,weaknesses,unknown = [],[],[]
    for name,limit,reason in tests:
        v = latest.get(name,{}).get('value')
        if v is None:
            unknown.append(reason)
        elif Decimal(v)>limit:
            strengths.append(reason)
        else:
            weaknesses.append('Does not meet: '+reason)
    debt = latest.get('debt_to_equity',{}).get('value')
    if debt is None:
        unknown.append('borrowings/equity below experimental 1.0 threshold')
    elif Decimal(debt)<1:
        strengths.append('borrowings/equity below experimental 1.0 threshold')
    else:
        weaknesses.append('borrowings/equity at least 1.0')
    deterioration = []
    if len(annual)>1 and annual[-2]['period']==shifted(annual[-1]['period'],-12):
        for name in ('operating_margin','net_margin','roe'):
            a,b = annual[-2]['metrics'][name]['value'], latest[name]['value']
            if a is not None and b is not None and Decimal(b)<Decimal(a):
                deterioration.append(name+' declined versus prior year')
    for name in ('revenue','pat'):
        v = latest.get(name+'_yoy',{}).get('value')
        if v is not None and Decimal(v)<0:
            deterioration.append('annual '+name+' declined')
    weaknesses += deterioration
    if quarters:
        for name in ('revenue','pat'):
            v = quarters[-1]['metrics'][name+'_yoy']['value']
            if v is not None and Decimal(v)<0:
                weaknesses.append('latest quarter '+name+' declined YoY')
    warnings += ['Standalone only; subsidiary economics excluded.',
        'Annual intervals inferred from same-month source columns and annual section labels; audited duration unverified.',
        'Source statements are rounded; EPS corporate-action comparability and restatement history unverified.',
        'No filing-time backtest claim; publication dates may be absent.',
        'Source ROCE and working-capital ratios are provider values, not independently verified denominator calculations.',
        'Direct capex, independently calculated ROCE and net debt unsupported by selected inputs.']
    return dict(version=VERSION,company_id=payload['company_id'],company=payload['company'],
        symbol=payload['symbol'],focus_member=payload['focus_member'],scope=payload['scope'],
        provenance=payload['facade']['provenance'],annual=annual,quarters=quarters,
        consistency=consistency,strengths=strengths,weaknesses=weaknesses,warnings=sorted(set(warnings)),
        assessment=dict(label='PROVISIONAL_FINANCIAL_PROFILE',passed=len(strengths),
            evaluated=5-len(unknown),total_checks=5,unknown=unknown,
            basis='Five experimental checks; deterioration shown separately; no trained score or membership action'),
        focus_consideration=dict(supporting=strengths,opposing=weaknesses,
            interpretation='Financial review only. Absence from supplied list is not rejection. Membership unchanged.'))
