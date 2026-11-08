"""Local-only import/normalization. No scraper imports or network dependencies."""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

MISSING = {'', 'na', 'nan', 'null', 'n/a'}


def timestamp(raw, tz=None, epoch_unit='seconds'):
    if raw is None or raw.strip().lower() in MISSING:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            if not tz:
                raise ValueError('Naive timestamps require an explicit timezone')
            dt = dt.replace(tzinfo=ZoneInfo(tz))
    else:
        if not number.is_finite():
            raise ValueError('Nonfinite timestamp')
        dt = datetime.fromtimestamp(float(number / (1000 if epoch_unit == 'milliseconds' else 1)), timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def import_csv(path, *, symbol, timeframe, time_column='time', tz=None,
               epoch_unit='seconds', expected=()):
    if not symbol or not timeframe:
        raise ValueError('Symbol and timeframe must be supplied; filename inference is forbidden')
    raw = Path(path).read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
    if not rows or rows[0].count(time_column) != 1:
        raise ValueError('Exactly one explicitly selected timestamp column is required')
    header = rows[0]
    ti = header.index(time_column)
    records = []
    for rn, row in enumerate(rows[1:], 2):
        if not row:
            continue
        if len(row) > len(header):
            raise ValueError(f'Row {rn}: excess unnamed fields')
        raw_time = row[ti] if ti < len(row) else None
        instant = timestamp(raw_time, tz, epoch_unit)
        for ci, name in enumerate(header):
            if ci == ti:
                continue
            value = row[ci] if ci < len(row) else None
            missing = value is None or value.strip().lower() in MISSING
            numeric = None
            if not missing:
                try:
                    dec = Decimal(value)
                    numeric = str(dec) if dec.is_finite() else None
                except InvalidOperation:
                    pass
            records.append(dict(row_number=rn, column_index=ci, original_column_name=name,
                                indicator_or_plot_name=name, symbol=symbol, timeframe=timeframe,
                                timestamp=instant, original_timestamp=raw_time,
                                raw_value=value, value=numeric, missing=missing))
    return dict(source='USER_SUPPLIED_CSV_OFFLINE', file_sha256=hashlib.sha256(raw).hexdigest(),
                original_columns=header, original_rows=rows[1:], symbol=symbol, timeframe=timeframe,
                time_column=time_column, assumed_timezone=tz, epoch_unit=epoch_unit,
                unavailable_columns=[name for name in expected if name not in header], records=records)


def normalize_studies(decoded, *, symbol, timeframe, source='SYNTHETIC_MOCK_ONLY'):
    """Keep positional plot IDs; never infer proprietary plot semantics."""
    result = []
    for indicator_id, observations in decoded.items():
        for observation in observations:
            for plot_id, value in observation.items():
                if plot_id in ('index', 'timestamp'):
                    continue
                result.append(dict(symbol=symbol, timeframe=timeframe,
                                   timestamp=timestamp(str(observation['timestamp'])),
                                   indicator_id=indicator_id, plot_id=plot_id, value=value, source=source))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--timeframe', required=True)
    parser.add_argument('--time-column', default='time')
    parser.add_argument('--timezone')
    parser.add_argument('--epoch-unit', choices=['seconds', 'milliseconds'], default='seconds')
    parser.add_argument('--expected', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = import_csv(args.csv, symbol=args.symbol, timeframe=args.timeframe,
                      time_column=args.time_column, tz=args.timezone,
                      epoch_unit=args.epoch_unit, expected=args.expected)
    if args.output.resolve() == args.csv.resolve():
        parser.error('Output must differ from source')
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
