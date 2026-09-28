"""Offline security/storage/quote tests; no real accounts or orders."""
import ast
import copy
import json
from pathlib import Path
import re
import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd
import requests


NAMES = {'_as_float', 'owner_identity_allowed', 'validate_paper_account',
         'PaperStore', 'portfolio_totals', 'RecoveringBroker', 'stock_row_colours'}
tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NAMES], type_ignores=[]), 'helpers', 'exec'))


def account():
    return {'cash':499000., 'positions':[{'id':'one','symbol':'TEST','action':'BUY',
        'qty':10, 'entry_price':100.,'sl_price':90.,'tp_price':120.,'timestamp':'2026-09-28 10:00:00'}], 'closed_trades':[]}


class PrivateUpdateTests(unittest.TestCase):
    def test_owner_only_verified_google(self):
        user = {'email':'Owner@example.com','email_verified':True,'iss':'https://accounts.google.com'}
        self.assertTrue(owner_identity_allowed(user, 'owner@example.com'))
        for override in ({'email':'other@example.com'}, {'email_verified':False}, {'iss':'untrusted'}):
            self.assertFalse(owner_identity_allowed(dict(user, **override), 'owner@example.com'))

    def test_invalid_journal_rejected(self):
        valid = account()
        self.assertEqual(validate_paper_account(valid), valid)
        for field, value in [('qty',float('nan')), ('qty',1.5), ('entry_price',0)]:
            bad = account()
            bad['positions'][0][field] = value
            with self.assertRaises(ValueError): validate_paper_account(bad)
        valid['positions'].append(copy.deepcopy(valid['positions'][0]))
        with self.assertRaises(ValueError): validate_paper_account(valid)

    def test_revision_conflict_and_timeout(self):
        reply = Mock(status_code=200)
        reply.json.return_value = {'conflict':True}
        store = PaperStore('https://test.supabase.co','sb_secret_test','owner',post=Mock(return_value=reply))
        with self.assertRaisesRegex(RuntimeError, 'Another tab'): store.save(account(),0)
        reply.json.return_value = {'revision':1}
        self.assertEqual(store.save(account(),0)['revision'],1)
        store.post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(RuntimeError, 'unreachable'): store.save(account(),1)

    def test_missing_quote_is_not_zero_pnl(self):
        self.assertIsNone(portfolio_totals(account(),{})['total'])
        self.assertEqual(portfolio_totals(account(),{'TEST':110})['total'],100)
        sell = account()
        sell['positions'][0]['action'] = 'SELL'
        self.assertEqual(portfolio_totals(sell,{'TEST':110})['total'],-100)

    def test_read_refresh_once_but_order_never_retried(self):
        client = Mock()
        expired = {'status':False,'errorcode':'AG8001'}
        client.ltpData.side_effect = [expired, {'status':True,'data':{'ltp':100}}]
        client.generateToken.return_value = {'status':True,'data':{}}
        wrapper = RecoveringBroker(client,clock=lambda:1000,sleep=lambda _:None)
        self.assertTrue(wrapper.ltpData('NSE','TEST','1')['status'])
        self.assertEqual(client.generateToken.call_count,1)
        client.placeOrder.return_value = expired
        self.assertEqual(wrapper.placeOrder({}), expired)
        self.assertEqual(client.placeOrder.call_count,1)
        self.assertEqual(client.generateToken.call_count,1)

    def test_rate_limit_does_not_reauthenticate(self):
        client = Mock()
        client.ltpData.return_value = {'status':False,'message':'Rate limit exceeded'}
        wrapper = RecoveringBroker(client,clock=lambda:1000,sleep=lambda _:None)
        wrapper.ltpData()
        client.generateToken.assert_not_called()


if __name__ == '__main__':
    unittest.main()
