"""Regression: 98 empty-handshake cycles must never appear 'successful'."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import run_service
import finam_ws_collector as fc
from book_replay import BookReplay
from datetime import datetime, timezone
import asyncio
import json

SYM='W4X6W4Z6@RTSX'
NOW=datetime(2026,10,16,15,19,tzinfo=timezone.utc)

class SupervisorProofTests(unittest.TestCase):
 def test_empty_round_marks_failure_and_keeps_metric_during_connect(self):
  with TemporaryDirectory() as td:
   root=Path(td);collected=[]
   response={'subscribed':True,'status':'SUBSCRIPTIONS_SENT_NO_MARKET_DATA',
      'errors':0,'market_messages':0,'book_rows_received':0,'ws_snapshots_seen':0,
      'public_market_trades':0,'file':str(root/'dummy.jsonl')}
   def capture(*a,**kw):return response
   with patch.object(run_service,'DATA',root), patch.object(run_service,'BASE',root), \
       patch.object(run_service,'state',side_effect=lambda **kw:collected.append(kw)), \
       patch.object(run_service,'cleanup',return_value=0), \
       patch.object(run_service,'stop_requested',side_effect=[False,True,True]), \
       patch.object(run_service,'authorize_readonly',return_value='jwt'), \
       patch.object(run_service,'capture_stream',side_effect=capture), \
       patch.object(run_service.time,'sleep'), \
       patch.dict('os.environ',{'FINAM_READONLY_API_KEY':'tapi_mock'}):
    self.assertEqual(run_service.supervisor(90),0)
   failures=[s for s in collected if s.get('last_completed_status')=='CONNECTED_NO_ORDERBOOK_DATA']
   self.assertTrue(failures)
   self.assertGreaterEqual(failures[0]['failed_rounds'],1)
   self.assertEqual(failures[0]['empty_rounds'],1)
   self.assertEqual(failures[0]['market_messages_total'],0)
   self.assertFalse(any(s.get('state')=='DATA_RECEIVED' for s in collected))
 def test_outside_script_no_password_logging(self):
  import inspect
  source=inspect.getsource(run_service)
  self.assertNotIn('print(key)',source)
  self.assertNotIn('print(jwt)',source)
  self.assertNotIn('metaTrader',source)
 def test_malformed_oneof_critical_invalidates_book(self):
  fake={'type':'DATA','subscription_type':'ORDER_BOOK','timestamp':1792164000,
        'payload':json.dumps({'orderBook':[{'symbol':SYM,'rows':[
         {'price':'100','buySize':'1','action':'ACTION_ADD'},
         {'price':'110','sellSize':'2','action':'ACTION_ADD'}],'isDataSnapshot':True}]})}
  good=fc.sanitize_envelope(fake,(SYM,))
  book=BookReplay((SYM,));book.on_sanitized_event(good,NOW)
  self.assertEqual(book.view(SYM)['bid'],'100')
  fake['payload']=json.dumps({'orderBook':[{'symbol':SYM,'rows':[
   {'price':'101','buySize':'3','action':'ACTION_ADD'},
   {'price':'102','unknownFieldOnly':5,'action':'ACTION_ADD'}],
   'isDataSnapshot':False}]})
  self.assertIsNone(fc.sanitize_envelope(fake,(SYM,)))
  # Production capture invalidates all active books on the rejected delta.
  import inspect
  self.assertIn("replay.invalidate_all('UNPARSEABLE_WS_FRAME')",inspect.getsource(fc.capture_stream))
 def test_no_trade_orders_in_market_subscriptions(self):
  import inspect
  s=inspect.getsource(fc.capture_stream)
  self.assertNotIn("'type':'TRADES'",s)
  self.assertNotIn("'type':'ORDERS'",s)
  self.assertNotIn("POST /v1/accounts",s)

if __name__=='__main__':unittest.main()