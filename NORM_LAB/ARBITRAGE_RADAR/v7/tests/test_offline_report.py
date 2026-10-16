from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import REPORT

SYM='W4X6W4Z6@RTSX'
NOW='2026-10-16T15:20:00+00:00'

def e(rows,snap=False):
 return {'schema':'NORM_FINAM_WS_EVIDENCE_V7_1','kind':'DATA',
 'subscription_type':'ORDER_BOOK','provider_timestamp':1792164000,
 'payload':{'order_book':[{'symbol':SYM,'rows':rows,'is_data_snapshot':snap}]},
 'captured_utc':NOW}

def row(price,**kwargs):return {'price':str(price),'timestamp':NOW,**kwargs}

class OfflineReportTests(unittest.TestCase):
 def test_missing_directory_has_zero_and_no_profit(self):
  with tempfile.TemporaryDirectory() as td:
   r=REPORT.analyze(Path(td)/'no-data')
   self.assertEqual(r['files_checked'],0)
   self.assertIsNone(r['realized_profit_rub'])
 def test_one_session_snapshot_and_prints(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   path=d/'finam_ws_1.jsonl'
   v=e([row(100,buy_size='3'),row(110,sell_size='4')],True)
   trade={'kind':'TRADE_PRINTS','subscription_type':'INSTRUMENT_TRADES','captured_utc':NOW,
          'payload':{'symbol':SYM,'trades':[{'price':'101','size':'1','timestamp':NOW}]}}
   path.write_text(json.dumps(v)+'\n'+json.dumps(trade)+'\n')
   r=REPORT.analyze(d,d/'NOT_PRESENT.json')
   self.assertEqual(r['ws_snapshots_seen'],1)
   self.assertEqual(r['market_book_data_events'],1)
   self.assertEqual(r['book_rows'],2)
   self.assertEqual(r['public_trade_prints'],1)
   self.assertEqual(r['quality_events_two_sided'],1)
   self.assertTrue(r['missing_verified_account_costs'])
   self.assertIsNone(r['realized_profit_rub'])
 def test_session_resets_never_reuses_prior_book(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   (d/'finam_ws_1.jsonl').write_text(json.dumps(e([row(100,buy_size='3'),row(110,sell_size='4')],True))+'\n')
   (d/'finam_ws_2.jsonl').write_text(json.dumps(e([row(101,buy_size='3',action='ACTION_ADD')]))+'\n')
   r=REPORT.analyze(d)
   self.assertEqual(r['quality_events_two_sided'],1)
   self.assertEqual(r['ws_snapshots_seen'],1)
 def test_unverified_fees_cannot_create_number(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   (d/'account_costs_verified.json').write_text(json.dumps({SYM:{'rub_per_spread_point':'100000','verified_account':False}}))
   (d/'finam_ws_1.jsonl').write_text(json.dumps(e([row(100,buy_size='10'),row(110,sell_size='20')],True))+'\n')
   r=REPORT.analyze(d,d/'account_costs_verified.json')
   self.assertEqual(r['model_numerical_money_results'],0)
   self.assertTrue(r['missing_verified_account_costs'])
 def test_with_explicit_full_costs_only_upper_bound(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   cfg={SYM:{'rub_per_spread_point':'1','account_initial_margin_rub':'1000',
    'broker_buy_rub':'1','broker_sell_rub':'1','exchange_buy_rub':'1','exchange_sell_rub':'1',
    'execution_stress_rub_each_way':'1','reserve_rub':'2000','verified_source':'BROKER_VERIFIED',
    'verified_account':True,'currency':'RUB'}}
   (d/'fees.json').write_text(json.dumps(cfg))
   (d/'finam_ws_1.jsonl').write_text(json.dumps(e([row(100,buy_size='10'),row(110,sell_size='20')],True))+'\n')
   r=REPORT.analyze(d,d/'fees.json')
   self.assertEqual(r['model_numerical_money_results'],1)
   self.assertEqual(r['model_positive_upper_bound_events'],1)
   self.assertIsNone(r['realized_profit_rub'])
   self.assertFalse(r['profit_verified'])
 def test_no_account_or_token_data_in_output(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td)
   token='token-SENSITIVE-NOT_FOR_PRINT'
   entry=e([row(100,buy_size='10'),row(110,sell_size='10')],True)
   entry['token']=token
   (d/'finam_ws_1.jsonl').write_text(json.dumps(entry)+'\n')
   self.assertNotIn(token,json.dumps(REPORT.analyze(d)))
 def test_old_partial_line_is_not_profit(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td);(d/'finam_ws_1.jsonl').write_text('{bad json\n')
   r=REPORT.analyze(d)
   self.assertIsNone(r['realized_profit_rub']);self.assertGreater(r['files_skipped'],0)

if __name__=='__main__':unittest.main()