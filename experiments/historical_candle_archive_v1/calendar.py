"""Evidence-backed NSE calendar projection; older dates stay explicitly unresolved."""
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from .storage import OUT,dump

IST=ZoneInfo('Asia/Kolkata')
BASE='https://nsearchives.nseindia.com/content/circulars/'
ANNUAL={2022:'CMTR50560',2023:'CMTR54757',2024:'CMTR59722',2025:'CMTR65587',2026:'CMTR71775'}
HOLIDAYS={
2022:'01-26 03-01 03-18 04-14 04-15 05-03 08-09 08-15 08-31 10-05 10-26 11-08',
2023:'01-26 03-07 03-30 04-04 04-07 04-14 05-01 06-29 08-15 09-19 10-02 10-24 11-14 11-27 12-25',
2024:'01-22 01-26 03-08 03-25 03-29 04-11 04-17 05-01 05-20 06-17 07-17 08-15 10-02 11-15 11-20 12-25',
2025:'02-26 03-14 03-31 04-10 04-14 04-18 05-01 08-15 08-27 10-02 10-22 11-05 12-25',
2026:'01-15 01-26 03-03 03-26 03-31 04-03 04-14 05-01 05-28 06-26 09-14 10-02 10-20 11-10 11-24 12-25'}
SPECIAL={
'2022-10-24':([('18:15','19:15')],'CMTR54023'),
'2023-11-12':([('18:15','19:15')],'CMTR59124'),
'2024-01-20':([('09:15','15:30')],'CMTR60340'),
'2024-03-02':([('09:15','10:00'),('11:30','12:30')],'MSD60677'),
'2024-05-18':([('09:15','10:00'),('11:30','12:30')],'MSD61893'),
'2024-11-01':([('18:00','19:00')],'CMTR64628'),
'2025-02-01':([('09:15','15:30')],'CMTR65729'),
'2025-10-21':([('13:45','14:45')],'CMTR70319'),
'2026-02-01':([('09:15','15:30')],'CMTR72349'),
}
ADDENDA={'2023-06-29':'CMTR57285','2024-01-22':'CMTR60338','2024-05-20':'CMTR61516',
         '2024-11-20':'CMTR64960','2026-01-15':'CMTR72260'}

def day_record(day):
    if isinstance(day,str): day=date.fromisoformat(day)
    ds=str(day)
    if day.year not in ANNUAL:
        return dict(day=ds,status='UNRESOLVED_PRE2022',sessions=[],source_refs=[])
    sources=[BASE+ANNUAL[day.year]+'.pdf']
    if ds=='2026-11-08':
        # CMTR76941 distinguishes CAS and non-CAS securities. Do not turn the
        # announced Sunday session into a closed weekend or invent a universal
        # per-security grid before CAS eligibility is reconciled.
        return dict(day=ds,status='SPECIAL_LIVE_SECURITY_GRID_UNRESOLVED',sessions=[],
            source_refs=sources+[BASE+'CMTR76941.pdf'],
            completeness='Muhurat session announced; CAS/non-CAS grids require security-specific review',
            publication_availability='Circular 2026-10-19; candle delivery is separate')
    if ds in ADDENDA: sources.append(BASE+ADDENDA[ds]+'.pdf')
    if ds in SPECIAL:
        intervals,ref=SPECIAL[ds];sources.append(BASE+ref+'.pdf');kind='SPECIAL_LIVE'
    elif ds[5:] in HOLIDAYS[day.year].split() or day.weekday()>4:
        intervals=[];kind='CLOSED'
    else:
        intervals=[('09:15','15:30')];kind='REGULAR_SCHEDULED'
        sources.append('https://www.nseindia.com/static/market-data/market-timings')
    return dict(day=ds,status=kind,
        sessions=[dict(opens_at=f'{ds}T{a}:00+05:30',closes_at=f'{ds}T{b}:00+05:30',
                       evidence_ref=';'.join(sources)) for a,b in intervals],
        source_refs=sources,
        completeness='ANNUAL_CIRCULAR_PLUS_LISTED_ADDENDA; exhaustive exceptional closure review pending',
        publication_availability='Dates of circular publication separate from candle delivery')

def build():
    records=[];d=date(2022,1,1)
    while d<=date(2026,11,4):
        records.append(day_record(d));d+=timedelta(days=1)
    dump(OUT/'exchange-calendar.json',dict(exchange='NSE',timezone='Asia/Kolkata',
         status='PARTIAL_EXCEPTION_REVIEW; NOT_FULL_HISTORY_CERTIFIED',days=records,
         boundary_reference_dates=['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30'],
         notes=['2023 Bakri Id moved June28 to June29 by CMTR57285',
                'Muhurat sessions replace regular hours; never treated as a normal holiday or full session',
                '2024 split DR sessions are distinct segments; no volume filling in the break',
                'Mock sessions excluded; dates before2022 unresolved, no weekday-only certification',
                'Some addendum URLs require retained-document verification; unresolved evidence blocks production scheduling']))

if __name__=='__main__': build()
