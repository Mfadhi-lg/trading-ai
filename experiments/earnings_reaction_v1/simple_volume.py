"""Reference-supported saved Simple Volume settings on completed candles.

Defaults match indicator-settings (finallynitin version29). Records, colors
and U/D are validated against numeric alerts and server drawing references.
This module does not alter the preserved independent R4 volume features.
"""
from decimal import Decimal as D
from fractions import Fraction
from dataclasses import replace
from datetime import timedelta

from .indicator_alternatives import completed
from .indicators import daily_rvol, pine_average, _period, chronological_completed

VERSION = 'simple-volume-reference-v4'


def _round_display(numerator, denominator=1, places=0):
    """Exact ratio rounding with Pine's upward tie rule, including negatives.

    Do not round a quotient of an already rounded SMA: that can move an exact
    half-point across the boundary when every input volume scales equally.
    """
    value = Fraction(numerator)/Fraction(denominator)*10**places
    units = (2*value.numerator+value.denominator)//(2*value.denominator)
    return D(units).scaleb(-places)


def simple_volume_features(candles, *, cutoff, average=21, relative=21,
                           pocket=10, up_down=20, turnover=50,
                           previous_close=False, dry_divisor=5, daily=False,
                           daily_history=(), record_history=(),
                           record_history_complete=False, weekly=False):
    """Return saved-setting values, daily records and default histogram colors.

    SMA includes current chart volume; RVOL uses the last confirmed DAILY
    volume divided by its preceding daily mean, rounded half up. Intraday
    callers must supply native daily_history with explicit completion times.
    Weekly charts use their own preceding21 weekly bars for RVOL. Their record
    alerts/labels are suppressed, as observed in the original version29.
    PP uses the last pocket chart bars, not pocket down bars. Equal open/close
    can qualify as up. Warmups remain null. Only saved defaults have references.
    U/D always uses previous close, independent of the color setting; an equal
    close contributes to the down-volume sum, as verified on minute references.
    Daily record_history may supply a strictly earlier prefix. Caller must
    explicitly attest complete available history for an HVE classification;
    otherwise a new observed maximum remains unresolved. Record context does
    not silently enter chart averages or RVOL. All records are daily-only.
    """
    for n in (average, relative, pocket, up_down, turnover, dry_divisor):
        _period(n)
    if any(not isinstance(v,bool) for v in (previous_close,daily,weekly,record_history_complete)):
        raise ValueError('mode flags must be boolean')
    if daily and weekly:
        raise ValueError('daily and weekly modes are mutually exclusive')
    bars = chronological_completed(candles,cutoff=cutoff) if daily else completed(candles, cutoff)
    prior_records = chronological_completed(record_history,cutoff=cutoff)
    if prior_records and (not daily or not bars or
            prior_records[-1].completed_at > bars[0].start_ist or
            prior_records[0].instrument_key != bars[0].instrument_key):
        raise ValueError('record history must be an earlier same-instrument daily prefix')
    if any(not c.volume.is_finite() or c.volume < 0 for c in prior_records):
        raise ValueError('finite nonnegative record volume required')
    for c in bars:
        if (any(not x.is_finite() for x in (c.open,c.high,c.low,c.close,c.volume))
                or c.volume < 0 or c.low <= 0
                or not c.low <= min(c.open,c.close) <= max(c.open,c.close) <= c.high):
            raise ValueError('invalid OHLCV')
    volumes = tuple(c.volume for c in bars)
    means = pine_average(volumes, average)
    chart_source = daily or weekly
    source = bars if chart_source else chronological_completed(daily_history,cutoff=cutoff)
    if source and bars and source[0].instrument_key != bars[0].instrument_key:
        raise ValueError('daily and chart instrument mismatch')
    if any(not c.volume.is_finite() or c.volume < 0 for c in source):
        raise ValueError('finite nonnegative daily volume required')
    daily_ratios = daily_rvol((c.volume for c in source),relative)
    daily_display = []
    for i,c in enumerate(source):
        denominator = sum((Fraction(p.volume) for p in source[i-relative:i]),Fraction(0)) if i>=relative else None
        daily_display.append(_round_display(Fraction(c.volume)*100*relative,denominator) if denominator else None)
    ratios = list(daily_ratios) if chart_source else []
    displayed_ratios = list(daily_display) if chart_source else []
    if any(a.completed_at>=b.completed_at for a,b in zip(source,source[1:])):
        raise ValueError('daily completions must be chronological')
    cursor = -1
    for c in (() if chart_source else bars):
        while cursor+1 < len(source) and source[cursor+1].completed_at <= c.completed_at:
            cursor += 1
        ratios.append(daily_ratios[cursor] if cursor >= 0 else None)
        displayed_ratios.append(daily_display[cursor] if cursor >= 0 else None)
    turnover_prices = pine_average((c.close for c in bars), turnover)
    turnover_volumes = pine_average(volumes, turnover)
    directions = []
    for i, c in enumerate(bars):
        anchor = bars[i-1].close if previous_close and i else c.open
        directions.append(None if previous_close and not i else (c.close > anchor)-(c.close < anchor))
    ud_directions = [None]+[(c.close > bars[i-1].close)-(c.close < bars[i-1].close)
                           for i,c in enumerate(bars) if i]
    record_volumes = [c.volume for c in prior_records]
    ever = max(record_volumes) if record_volumes else None
    out = []
    supported = (average,relative,pocket,previous_close,dry_divisor) == (21,21,10,False,5)
    for i, c in enumerate(bars):
        ma, ratio, direction = means[i], ratios[i], directions[i]
        down = [volumes[j] for j in range(max(0,i-pocket),i) if directions[j] == -1]
        ready = i >= pocket and all(d is not None for d in directions[i-pocket:i+1])
        # Original uses a zero down-volume baseline when no down bar exists.
        pivot = bool(direction >= 0 and c.volume > max(down,default=D(0))) if ready else None
        dry = c.volume < ma/dry_divisor if ma is not None else None
        high_up = direction >= 0 and c.volume > ma if ma is not None and direction is not None else None
        high_down = direction == -1 and c.volume > ma if ma is not None and direction is not None else None
        ud = None
        if i >= up_down:
            indices = range(i-up_down+1,i+1)
            selling = sum((volumes[j] for j in indices if ud_directions[j] <= 0),D(0))
            buying = sum((volumes[j] for j in indices if ud_directions[j] == 1),D(0))
            ud = buying/selling if selling else None
        raw_pct = ratio*100 if ratio is not None else None
        # Base colors exclude record and IPO overrides. Do not present this as
        # a reproduced original final histogram color.
        base_color = ('#2196F3' if pivot else '#FF9800' if dry else
                      '#4CAF50' if high_up else '#FF5252' if high_down else '#363A45')
        hve = hvy = hvq = lvy = lvq = False if not daily else None
        record_kind = None
        if daily:
            record_volumes.append(c.volume)
            hve = (ever is None or c.volume >= ever) if record_history_complete else (
                False if ever is not None and c.volume < ever else None)
            ever = max(ever,c.volume) if ever is not None else c.volume
            if len(record_volumes)>=252:
                year_high = c.volume >= max(record_volumes[-252:])
                quarter_high = c.volume >= max(record_volumes[-63:])
                hvy = False if not year_high or hve is True else True if hve is False else None
                hvq = quarter_high and not year_high
                lvy = c.volume <= min(record_volumes[-252:])
            if len(record_volumes)>=63:
                lvq = c.volume <= min(record_volumes[-63:])
            record_kind = 'HVE' if hve else 'HVY' if hvy else 'HVQ' if hvq else None
        total = sum((Fraction(v) for v in volumes[i-average+1:i+1]),Fraction(0)) if ma else None
        record_pct = (_round_display(100*Fraction(c.volume)*average-100*total,total) if total else None)
        # All three default high-record colors are blue. Once a full252-bar
        # window is known, an unknown older HVE cannot change the final color:
        # an all-history high necessarily exceeds that rolling window as well.
        record_color_ready = not daily or len(record_volumes)>=252
        high_record = (daily and len(record_volumes)>=252 and
                       c.volume>=max(record_volumes[-63:]))
        color = None if ma is None or not record_color_ready else (
            '#2196F3' if pivot or high_record and direction is not None and direction>=0 else
            '#FF9800' if dry or lvy or lvq else
            '#4CAF50' if high_up else '#FF5252' if high_down else '#363A45')
        out.append(dict(bar_start_ist=c.start_ist,observation_at=c.completed_at, volume=c.volume, volume_sma=ma,
            rvol_ratio=ratio, rvol_pct_unrounded=raw_pct,rvol_source_timeframe='W' if weekly else 'D',
            rvol_pct=displayed_ratios[i],
            pocket_pivot=pivot, dry=dry, high_up=high_up, high_down=high_down,
            up_down_ratio=ud,
            up_down_table_value=_round_display(buying,selling,1) if ud is not None else None,
            up_down_ratio_estimate=ud, # Backward-compatible export alias.
            turnover_crores_estimate=(turnover_prices[i]*turnover_volumes[i]/D(10000000)
                if turnover_prices[i] is not None else None),
            base_color_without_records=base_color,
            original_hve=hve, original_hvy=hvy, original_hvq=hvq,
            original_lvy=lvy, original_lvq=lvq,
            highest_volume_kind=record_kind,highest_volume_change_pct=record_pct,
            lowest_volume_kind='Y' if lvy else 'Q' if lvq else None,
            original_final_color=color,
            core_status='SUPPORTED_BY_REFERENCE' if supported else 'INDEPENDENT_APPROXIMATION',
            base_color_status='INDEPENDENT_APPROXIMATION;RECORD_OVERRIDES_EXCLUDED',
            turnover_status='SUPPORTED_BY_REFERENCE' if turnover==50 else 'INDEPENDENT_APPROXIMATION',
            table_and_record_status='SUPPORTED_BY_REFERENCE' if not daily or record_history_complete
                else 'HVE_CONTEXT_INCOMPLETE',version=VERSION))
    return tuple(out)


def aggregate_weekly_daily(candles, *, cutoff, sessions=()):
    """Observed native daily bars grouped into Monday-start exchange weeks.

    Drop a leading week that begins midweek. A trailing week is included only
    after the next Monday or when an explicit supplied session schedule covers
    Monday-Friday and its exact supplied session set has completed. Missing
    interior sessions cannot be certified without a complete calendar; this is
    an observed-daily aggregation, never a claim of calendar certification.
    """
    bars = chronological_completed(candles,cutoff=cutoff)
    if not bars:return ()
    sessions=tuple(sessions)
    if any(a.day>=b.day for a,b in zip(sessions,sessions[1:])):
        raise ValueError('ordered unique sessions required')
    groups={}
    for c in bars:
        monday=c.start_ist.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=c.start_ist.weekday())
        groups.setdefault(monday,[]).append(c)
    weeks=sorted(groups)
    out=[]
    for week in weeks:
        part=groups[week]
        if week==weeks[0] and part[0].start_ist.date()!=week.date():continue
        boundary=week+timedelta(days=7)
        if boundary>cutoff:
            covered=bool(sessions and sessions[0].day<=week.date() and
                         sessions[-1].day>=(week+timedelta(days=4)).date())
            expected=tuple(s for s in sessions if week.date()<=s.day<boundary.date())
            if not covered or not expected or expected[-1].closes_at>cutoff:
                continue
            if {c.start_ist.date() for c in part}!={s.day for s in expected}:
                raise ValueError('daily bars do not cover supplied weekly sessions')
        completion=max(c.completed_at for c in part)
        out.append(replace(part[0],provider_timestamp=week.isoformat(),start_ist=week,
            end_ist=completion,completed_at=completion,
            high=max(c.high for c in part),low=min(c.low for c in part),close=part[-1].close,
            volume=sum((c.volume for c in part),D(0)),open_interest=part[-1].open_interest,
            session_id=f'ISO_WEEK:{week.date()}',
            timestamp_semantics='ISO_WEEK_OBSERVED_DAILY;CALENDAR_NOT_CERTIFIED'))
    return tuple(out)


def simple_volume_labels(rows, *, cutoff, latest_high_only=True):
    """Snapshot presentation only; immutable per-bar event flags are preserved.

    Mirrors the saved latest HVE/HVY/HVQ + all Y/Q low-label toggles. Rendering
    engines may cap the number of drawings; event history itself is not erased.
    """
    from .events import aware
    aware(cutoff)
    high, low = [], []
    for row in rows:
        if row['observation_at']>cutoff: continue
        kind = row.get('highest_volume_kind')
        if kind:
            pct = row.get('highest_volume_change_pct')
            high.append(dict(observation_at=row['observation_at'],kind=kind,volume=row['volume'],
                             bar_start_ist=row['bar_start_ist'],
                             text=kind+(f' {pct}%' if pct is not None else '')))
        if row.get('lowest_volume_kind'):
            low.append(dict(observation_at=row['observation_at'],kind=row['lowest_volume_kind'],
                            bar_start_ist=row['bar_start_ist'],
                            volume=row['volume'],text=row['lowest_volume_kind']))
    if latest_high_only:
        latest = {item['kind']:item for item in high}
        high = sorted(latest.values(),key=lambda item:item['observation_at'])
    return dict(high=high,low=low)
