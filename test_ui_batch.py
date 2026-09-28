"""Offline Streamlit interaction checks; never connect to an actual broker."""
from datetime import datetime, timedelta
import json
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest


def chart_fixture(tickers, **kwargs):
    names = [tickers] if isinstance(tickers, str) else list(tickers)
    interval = kwargs.get('interval', '15m')
    freq = {'1d':'D','1wk':'W-MON','5m':'5min','15m':'15min','60m':'60min'}.get(interval,'15min')
    end = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')-pd.Timedelta(days=1)
    index = pd.date_range(end=end, periods=65, freq=freq)
    prices = np.linspace(100,110,65)
    frame = pd.DataFrame({'Open':prices-.1,'High':prices+.5,'Low':prices-.5,'Close':prices,'Volume':1000.},index=index)
    return pd.concat({name:frame.copy() for name in names},axis=1)


class MasterResponse:
    status = 200
    headers = {}
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self):
        expiry=(datetime.now()+timedelta(days=10)).strftime('%d%b%Y').upper()
        return json.dumps([{'exch_seg':'NFO','name':'NIFTY','instrumenttype':'OPTIDX','symbol':'NIFTYTEST24000CE','token':'123','expiry':expiry,'strike':'2400000','lotsize':'65','tick_size':'5'}]).encode()


def offline_url(request, **kwargs):
    url = request.full_url if hasattr(request,'full_url') else str(request)
    if 'OpenAPIScripMaster.json' in url:
        return MasterResponse()
    raise OSError('Offline UI test: source deliberately unavailable')


class UIBatchTests(unittest.TestCase):
    def test_unapproved_account_cannot_load_data(self):
        class User(dict):
            is_logged_in = True
        with patch('streamlit.user', User(email='other@example.com', email_verified=True, iss='https://accounts.google.com')), patch('streamlit.secrets', {'auth':{'client_id':'test'},'app_access':{'owner_email':'owner@example.com'}}), patch('requests.post') as database, patch('yfinance.download') as feed:
            app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run(timeout=15)
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(app.tabs),0)
            self.assertTrue(any('does not have access' in item.value for item in app.error))
            database.assert_not_called()
            feed.assert_not_called()

    def test_controls_long_term_journal_and_disconnected_fo(self):
        st.cache_data.clear()
        st.cache_resource.clear()
        fake_secrets = {'angel_one': {'auto_connect':False}, 'auth':{'client_id':'test'}, 'app_access':{'owner_email':'owner@example.com'}, 'supabase':{'url':'https://test.supabase.co','service_key':'sb_secret_test'}}
        class User(dict):
            is_logged_in = True
        user = User(email='owner@example.com', email_verified=True, iss='https://accounts.google.com')
        saved = {'revision':0, 'data':{'cash':500000.,'positions':[], 'closed_trades':[]}}
        def database(url, **kwargs):
            payload = kwargs['json']
            result = saved
            if url.endswith('mahi_save_account'):
                if payload['p_expected_revision'] != saved['revision']:
                    result = {'conflict':True}
                else:
                    saved.update(revision=saved['revision']+1, data=copy.deepcopy(payload['p_data']))
            class Reply:
                status_code = 200
                def json(self): return copy.deepcopy(result)
            return Reply()
        with patch('streamlit.user', user), patch('streamlit.secrets', fake_secrets), patch('requests.post',side_effect=database), patch('yfinance.download',side_effect=chart_fixture), patch('urllib.request.urlopen',side_effect=offline_url):
            app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run(timeout=45)
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(app.tabs),6)
            self.assertEqual(next(item.value for item in app.text_input if item.label == 'API Key'),'')
            self.assertEqual(next(item.value for item in app.text_input if item.label == 'Client ID'),'')
            self.assertNotIn('smart_api',app.session_state)
            # Universe controls belong to the Equity tab, not the global header.
            equity_keys=[item.key for item in app.tabs[0].selectbox]
            self.assertIn('asset_universe',equity_keys)
            self.assertNotIn('asset_universe',[item.key for item in app.tabs[1].selectbox])
            self.assertTrue(any((button.key or '').startswith('missing_buy_') and button.disabled for button in app.button))
            app.selectbox(key='trading_horizon').set_value('Long Term (Weekly)').run(timeout=45)
            self.assertFalse(app.exception, str(app.exception))
            stop = next(item for item in app.number_input if (item.key or '').startswith('investment_sl_'))
            target = next(item for item in app.number_input if (item.key or '').startswith('investment_tp_'))
            stop.set_value(99.0)
            target.set_value(130.0)
            app.run(timeout=45)
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(any('Planning only:' in item.value for item in app.caption))
            buy=next(button for button in app.button if (button.key or '').startswith('mkt_buy_') and 'MARKET' in button.label and 'PAPER' in button.label)
            buy.click().run(timeout=45)
            self.assertFalse(app.exception, str(app.exception))
            positions=app.session_state['paper_data']['positions']
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0]['sl_price'],99)
            self.assertEqual(positions[0]['tp_price'],130)
            self.assertIs(positions[0]['auto_exit'],False)
            self.assertNotIn('smart_api',app.session_state)
            reopened = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run(timeout=45)
            self.assertFalse(reopened.exception, str(reopened.exception))
            self.assertEqual(reopened.session_state['paper_data']['positions'][0]['id'], positions[0]['id'])


if __name__ == '__main__':
    unittest.main()
