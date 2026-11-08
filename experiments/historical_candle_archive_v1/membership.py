"""Point-in-time eligibility uses dated acquisition observations, never today's set."""
from datetime import datetime
from decimal import Decimal

def eligibility_asof(observations,company_paths,decision_time):
    cutoff=datetime.fromisoformat(decision_time) if isinstance(decision_time,str) else decision_time
    if cutoff.tzinfo is None: raise ValueError('aware decision required')
    available=[r for r in observations if r['company_path'] in company_paths and
               datetime.fromisoformat(r['observed_at_utc'])<=cutoff]
    if not available:
        return dict(status='UNKNOWN',reason='NO_CAPITALIZATION_OBSERVATION_AVAILABLE_BY_DECISION',eligible=None)
    latest=max(available,key=lambda r:datetime.fromisoformat(r['observed_at_utc']))
    if latest['eligibility']=='unknown' or latest.get('market_cap_crore') is None:
        return dict(status='UNKNOWN',eligible=None,observation=latest)
    eligible=Decimal(latest['market_cap_crore'])>Decimal(580)
    return dict(status='ELIGIBLE_AT_LATEST_OBSERVED_CAP' if eligible else 'BELOW_OR_EQUAL_CUTOFF',
                eligible=eligible,observation=latest,
                limitation='Last observed capitalization; no invented intervening daily market caps or lifetime membership')
