"""Quarter-end and candidate call-month interpretation (not content proof)."""
import calendar
from datetime import date, datetime

def predates_quarter(labels, period):
    if not labels:
        return False
    try:
        months=[datetime.strptime(label,'%b %Y') for label in labels]
        end=date.fromisoformat(period)
    except ValueError:
        return False
    return all((m.year,m.month)<(end.year,end.month) for m in months)


def call_months(period):
    end=date.fromisoformat(period)
    if end.month not in (3,6,9,12) or end.day!=calendar.monthrange(end.year,end.month)[1]:
        raise ValueError("Period must be a calendar quarter end")
    result=[]
    for offset in (1,2,3):
        total=end.year*12+end.month-1+offset
        result.append(f"{calendar.month_abbr[total%12+1]} {total//12}")
    return tuple(result)
