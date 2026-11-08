"""Regression tests for existing scraped-identifier linkage, including stale ISINs."""
import unittest
from .identity import IdentityResolver


class IdentityTests(unittest.TestCase):
    def provider(self,exchange='BSE',isin='INE777X01017',symbol='EXAMPLE',token='540718'):
        return dict(exchange=exchange,segment=exchange+'_EQ',isin=isin,trading_symbol=symbol,
                    exchange_token=token,instrument_key=exchange+'_EQ|'+isin,name='Example Ltd',instrument_type='EQ')
    def security(self,isin='INE777X01017'):
        return dict(exchange='BSE',bse_scrip_code='540718',nse_symbol=None,current_isin=isin,
                    exchange_security_id=1,current_status='active',verification_status='verified')
    def scope(self,path='/company/540718/'):
        return dict(company_id=4270,company_path=path,observed_at_utc='2026-11-05T00:00:00+00:00')
    def test_scraped_bse_code_resolves_without_security_identifier_or_registry_link(self):
        r=IdentityResolver([self.security()],[],[self.scope()],[self.provider()]).resolve(4270)
        self.assertEqual(r['instrument_key'],'BSE_EQ|INE777X01017')
    def test_nse_is_preferred_for_same_confirmed_isin(self):
        providers=[self.provider(),self.provider('NSE',token='123')]
        r=IdentityResolver([self.security()],[],[self.scope()],providers).resolve(4270)
        self.assertEqual(r['instrument_key'],'NSE_EQ|INE777X01017')
    def test_existing_identifier_resolves_trust_without_exchange_master_entry(self):
        identifier=dict(company_id=4270,identifier_type='NSE',identifier_value='EXAMPLE',is_current=1,valid_to=None)
        provider=self.provider('NSE',isin='INE219X23014',token='123');provider['instrument_type']='IV'
        r=IdentityResolver([],[identifier],[],[provider]).resolve(4270)
        self.assertEqual(r['instrument_type'],'IV');self.assertFalse(r['historical_continuity_verified'])
    def test_changed_isin_is_explicit_and_old_isin_preserved(self):
        r=IdentityResolver([self.security('INE777X01025')],[],[self.scope()],[self.provider()]).resolve(4270)
        self.assertEqual(r['historical_isins'],['INE777X01025'])
        self.assertIn('HISTORY_REVIEW_REQUIRED',r['status'])
        self.assertFalse(r['historical_continuity_verified'])
    def test_conflicting_exchange_identifiers_are_not_guessed(self):
        ids=[dict(company_id=4270,identifier_type='NSE',identifier_value='EXAMPLE',is_current=1,valid_to=None)]
        r=IdentityResolver([self.security()],ids,[self.scope()],
            [self.provider(),self.provider('NSE',isin='INE777X01025',token='123')]).resolve(4270)
        self.assertIsNone(r['instrument_key']);self.assertEqual(r['status'],'CONFLICTING_PROVIDER_IDENTIFIERS')
    def test_missing_provider_security_keeps_known_bse_code(self):
        r=IdentityResolver([self.security()],[],[self.scope()],[]).resolve(4270)
        self.assertIsNone(r['instrument_key']);self.assertEqual(r['known_identifiers'][0]['value'],'540718')
    def test_name_similarity_never_creates_a_match(self):
        r=IdentityResolver([],[],[self.scope('/company/OTHER/')],[self.provider()]).resolve(4270)
        self.assertIsNone(r['instrument_key'])

if __name__=='__main__':unittest.main()
