"""Offline regressions for planning, signals, source integrity and broker reads."""
import ast
from datetime import datetime, timedelta
import html
import json
from pathlib import Path
import re
import time
import unittest
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def load_helpers():
    source = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
    names = {'_as_float', '_as_int', 'calculate_directional_preview', 'calculate_investment_plan',
             'risk_budget_warnings', 'completed_signal_candles', 'detect_price_action_setup',
             'brief_published_time', 'classify_market_headline', 'build_brief_market_rows',
             'build_market_quick_brief', 'fetch_broker_account_view', 'fetch_selected_option_snapshot'}
    namespace = dict(globals(), IST_TIMEZONE=ZoneInfo('Asia/Kolkata'))
    namespace['ist_now'] = lambda: datetime(2026, 9, 28, 12, 35, tzinfo=namespace['IST_TIMEZONE'])
    selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<offline helpers>', 'exec'), namespace)
    return namespace


class UpdateBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_helpers()

    def test_equity_submission_failure_is_visible(self):
        tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Name) and node.test.id == 'success'
                    and any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                            and 'LIVE market order submitted' in child.value
                            for stmt in node.body for child in ast.walk(stmt))]
        self.assertTrue(branches)
        for branch in branches:
            self.assertTrue(any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                                and 'Live order was not submitted:' in child.value
                                for stmt in branch.orelse for child in ast.walk(stmt)),
                            'A rejected order must render its error outside the success branch')

    def candles(self):
        stamps = pd.date_range('2026-09-28 09:15', periods=40, freq='5min', tz='Asia/Kolkata')
        close = np.linspace(100, 110, 40)
        result = pd.DataFrame({'Open': close-.1, 'High': close+.2, 'Low': close-.3, 'Close': close, 'Volume': 1000.}, index=stamps)
        result.iloc[-1] = [109.5, 112.2, 109.4, 112., 2000.]
        return result

    def test_delivery_plan_and_validation(self):
        plan = self.h['calculate_investment_plan'](100, 90, 125, 10)
        self.assertEqual(plan['loss_pnl'], -100)
        self.assertEqual(plan['target_pnl'], 250)
        self.assertEqual(plan['stop_percent'], 10)
        for args in [(100,101,120,1), (100,90,99,1), (100,90,120,1.5), (float('nan'),90,120,1)]:
            with self.assertRaises(ValueError):
                self.h['calculate_investment_plan'](*args)

    def test_risk_budgets_only_count_matching_dates(self):
        trades = [{'timestamp':'2026-09-28 10:00', 'exit_time':'2026-09-28 11:00', 'pnl':-600},
                  {'timestamp':'2026-09-27 10:00', 'exit_time':'2026-09-27 11:00', 'pnl':-5000}]
        result = self.h['risk_budget_warnings'](200,trades, {'per_trade':100,'daily_loss':500,'max_trades':1}, '2026-09-28')
        self.assertEqual(len(result['warnings']),3)
        self.assertEqual(result['realized_pnl'],-600)
        self.assertEqual(result['trade_count'],1)

    def test_breakout_requires_volume(self):
        frame = self.candles()
        self.assertEqual(self.h['detect_price_action_setup'](frame,'5m')['state'], 'BULLISH SETUP')
        frame['Volume'] = 0
        self.assertEqual(self.h['detect_price_action_setup'](frame,'5m')['state'], 'WAIT / NO CLEAN SETUP')

    def test_incomplete_bar_cannot_repaint_signal(self):
        frame = self.candles()
        before = self.h['detect_price_action_setup'](frame,'5m')
        frame.loc[pd.Timestamp('2026-09-28 12:35',tz='Asia/Kolkata')] = [112,113,1,2,999999]
        after = self.h['detect_price_action_setup'](frame,'5m')
        self.assertEqual(before,after)

    def test_stale_invalid_and_broker_time_column(self):
        frame = self.candles()
        stale = self.h['detect_price_action_setup'](frame,'5m', datetime(2026,9,29,12,35,tzinfo=ZoneInfo('Asia/Kolkata')))
        self.assertEqual(stale['state'],'STALE / REVIEW ONLY')
        broker = frame.reset_index().rename(columns={'index':'time'})
        self.assertEqual(self.h['detect_price_action_setup'](broker,'FIVE_MINUTE')['state'], 'BULLISH SETUP')
        frame.iloc[-1, frame.columns.get_loc('High')] = 1
        self.assertEqual(self.h['detect_price_action_setup'](frame,'5m')['state'],'DATA REQUIRED')

    def test_weekly_forming_bar_is_excluded(self):
        frame = self.candles().iloc[:2].copy()
        frame.index = pd.DatetimeIndex(['2026-09-21','2026-09-28'],tz='Asia/Kolkata')
        completed, issue = self.h['completed_signal_candles'](frame,'1wk')
        self.assertEqual(len(completed),1)
        self.assertEqual(issue,'')

    def test_news_excludes_old_negated_and_routine_cues(self):
        classify = self.h['classify_market_headline']
        self.assertEqual(classify('RBI: inflation falls')['category'],'good')
        self.assertEqual(classify('RBI: inflation may fall')['category'],'watch')
        self.assertFalse(classify('SEBI recovery certificate for defaulter')['relevant'])
        brief = self.h['build_market_quick_brief']([
            {'title':'RBI inflation falls','published':'Mon, 28 Sep 2026 10:00:00 +0530','source':'RBI','link':'https://rbi.org.in'},
            {'title':'Inflation surges','published':'Mon, 21 Sep 2026 10:00:00 +0530','source':'RBI','link':'https://rbi.org.in'}], [])
        self.assertEqual(len(brief['groups']['good']),1)
        self.assertFalse(brief['groups']['bad'])
        self.assertEqual(brief['mood'],'Not enough data')
        rbi_time = self.h['brief_published_time']('Fri, 25 Sep 2026 21:50:00', 'RBI')
        self.assertIsNotNone(rbi_time)
        self.assertIsNone(self.h['brief_published_time']('Fri, 25 Sep 2026 21:50:00', 'Unknown'))

    def test_quote_mismatch_and_disconnected(self):
        contract={'exchange':'NFO','symbol':'MOCKCE','token':'123'}
        self.assertEqual(self.h['fetch_selected_option_snapshot'](None,contract)['state'],'disconnected')
        class Broker:
            def ltpData(self,*args):
                return {'status':True,'data':{'ltp':100,'symboltoken':'999'}}
        self.assertEqual(self.h['fetch_selected_option_snapshot'](Broker(),contract)['state'],'mismatch')

    def test_readonly_broker_view_no_orders_sent(self):
        class Broker:
            def orderBook(self):
                return {'status':True,'data':[{'orderid':'42','status':'open','filledshares':'0','averageprice':'0','private_field':'must not appear'}]}
            def position(self):
                return {'status':True,'data':[]}
            def placeOrder(self,*args):
                raise AssertionError('Must never submit an order')
        result=self.h['fetch_broker_account_view'](Broker())
        self.assertEqual(result['orders'][0]['Status'],'open')
        self.assertEqual(result['orders'][0]['Filled qty'],'0')
        self.assertNotIn('private_field',str(result))
        self.assertTrue(self.h['fetch_broker_account_view'](None)['errors'])


if __name__ == '__main__':
    unittest.main()
