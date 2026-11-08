import inspect, sqlite3, unittest
from scrapper.governance import focus_p0 as legacy
from scrapper.pipeline import build_p0_governance as old_builder
from scrapper.research.governance import focus_p0 as owner
from scrapper.research.focus import build_p0_governance as builder
from .test_fg00_p0_governance import make_p0_rows
class GovernanceOwnershipTests(unittest.TestCase):
 def test_all_legacy_names_and_helpers_delegate(self):
  for old,new in [(legacy,owner),(old_builder,builder)]:
   for name,value in vars(new).items():
    if name.startswith('__'):continue
    self.assertIs(getattr(old,name),value,name)
  self.assertEqual(builder.QUARTERS,old_builder.QUARTERS)
  self.assertEqual(builder.select_p0_companies.__module__,'scrapper.research.governance.focus_p0')
 def test_governed_selection_and_unknown_label_parity(self):
  rows=make_p0_rows()
  self.assertEqual(owner.select_p0_companies(rows,seed=7),legacy.select_p0_companies(rows,seed=7))
  for state in owner.DecisionUniverseState:
   for failure in [False,True]:
    actual=owner.assign_outcome_label(is_focus_member=False,universe_state=state,technical_source_failure=failure)
    self.assertEqual(actual,0 if state==owner.DecisionUniverseState.IN_UNIVERSE and not failure else None)
 def test_retained_result_cutoff_is_read_only(self):
  conn=sqlite3.connect(':memory:')
  try:
   conn.execute('CREATE TABLE exchange_result_filings(isin,exchange_key,period_end_date,broadcast_at_utc)')
   conn.executemany('INSERT INTO exchange_result_filings VALUES (?,?,?,?)',[(None,'ALPHA','2026-06-30','2026-08-03T23:59:59+00:00'),(None,'ALPHA','2026-06-30','2026-08-04T00:00:00+00:00')])
   before=conn.total_changes
   kwargs=dict(metadata={'nse_symbol':'ALPHA'},period_end='2026-06-30',cutoff='2026-08-03')
   self.assertEqual(builder._result_availability(conn,**kwargs),{'result_availability':'AVAILABLE','result_filing_count':1})
   self.assertEqual(builder._result_availability(conn,**kwargs),old_builder._result_availability(conn,**kwargs))
   self.assertEqual(conn.total_changes,before)
  finally:conn.close()
