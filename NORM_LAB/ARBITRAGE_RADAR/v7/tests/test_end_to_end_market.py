"""Offline tests of real Finam ProtoJSON shape and our market-data handoff."""
import asyncio
from datetime import datetime,timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import finam_ws_collector as m
from book_replay import BookReplay

SYM='W4X6W4Z6@RTSX'
OTHER='W4V6W4X6@RTSX'
TS=datetime(2026,10,16,15,20,tzinfo=timezone.utc)

def frame(data,type_='ORDER_BOOK'):
    return {'type':'DATA','subscription_type':type_,'timestamp':1792164000,
            'payload':json.dumps(data,ensure_ascii=False,separators=(',',':'))}

def book(rows,snapshot=False):
    return {'orderBook':[{'symbol':SYM,'rows':rows,'isDataSnapshot':snapshot}]}

def row(price,b=None,s=None,action='ACTION_ADD'):
    x={'price':str(price),'action':action,'timestamp':'2026-10-16T15:20:00Z'}
    if b is not None:x['buySize']=str(b)
    if s is not None:x['sellSize']=str(s)
    return x

def trades(*events):
    return {'symbol':SYM,'trades':list(events)}

class Socket:
    def __init__(self,frames):self.frames=frames[:];self.sent=[]
    async def recv(self):
        if self.frames:return json.dumps(self.frames.pop(0))
        await asyncio.sleep(5)
    async def send(self,payload):self.sent.append(json.loads(payload))
class C:
    def __init__(self,s):self.s=s
    def __call__(self,*a,**k):return self
    async def __aenter__(self):return self.s
    async def __aexit__(self,*a):return False

class MarketCaptureTests(unittest.TestCase):
    def test_snapshot_two_sides_after_protojson_decode(self):
        o=m.sanitize_envelope(frame(book([row(1200,b=10),row(1220,s=6)],snapshot=True)),(SYM,))
        self.assertEqual(o['payload']['order_book'][0]['is_data_snapshot'],True)
        r=BookReplay((SYM,));r.on_sanitized_event(o,TS)
        v=r.view(SYM)
        self.assertEqual((v['bid'],v['ask']),('1200','1220'))
        self.assertFalse(v['continuity_verified']);self.assertFalse(v['executable'])
    def test_snapshot_then_oneof_buy_and_sell(self):
        r=BookReplay((SYM,))
        for blob in (book([row(1200,b=10),row(1220,s=6)],True),
                     book([row(1205,b=3,action='ACTION_ADD')],False),
                     book([row(1219,s=8,action='ACTION_ADD')],False)):
            r.on_sanitized_event(m.sanitize_envelope(frame(blob),(SYM,)),TS)
        v=r.view(SYM)
        self.assertEqual(v['bid'],'1205')
        self.assertEqual(v['ask'],'1219')
        self.assertEqual(v['bid_qty'],'3')
        self.assertEqual(v['ask_qty'],'8')

    def test_trade_protojson_subscribe_extract_market_only(self):
        secret='NOT_STORED_BROKER_SECRET'
        raw=frame(trades({'price':'1200','size':'2','timestamp':'2026-10-16T15:20:00Z',
                           'side':'SIDE_BUY','tradeId':'trade-001','jwt':secret}), 'INSTRUMENT_TRADES')
        raw['secret']=secret
        x=m.sanitize_envelope(raw,(SYM,),(SYM,))
        self.assertIsNotNone(x)
        self.assertEqual(x['kind'],'TRADE_PRINTS')
        self.assertEqual(x['payload']['trades'][0]['price'],'1200')
        self.assertNotIn('tradeId',json.dumps(x))
        self.assertNotIn(secret,json.dumps(x))
    def test_trade_account_stream_never_captured(self):
        raw=frame(trades({'price':'1200','size':'2','timestamp':'2026-10-16T15:20:00Z'}),'TRADES')
        self.assertIsNone(m.sanitize_envelope(raw,(SYM,),(SYM,)))
    def test_negative_or_missing_qty_discarded(self):
        p=trades({'price':'1','size':'-1','timestamp':'2026-10-16T15:20:00Z'},
                 {'price':'1','timestamp':'2026-10-16T15:20:00Z'})
        self.assertTrue(m.scrub_market_trades(p,(SYM,)).get('unrecognized'))
    def test_full_handshake_books_and_prints_persisted(self):
        handshake={'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}}
        snap=frame(book([row(1200,b=10),row(1220,s=6)],snapshot=True))
        d=frame(book([row(1205,b=3,action='ACTION_ADD')],snapshot=False))
        tr=frame(trades({'price':'1205','size':'1','timestamp':'2026-10-16T15:20:02Z',
                         'tradeId':'trade-002'}),'INSTRUMENT_TRADES')
        ws=Socket([handshake,snap,d,tr])
        with tempfile.TemporaryDirectory() as td:
            fn=Path(td)/'only-market.jsonl'
            result=asyncio.run(m.capture_stream('jwt',(SYM,),1,fn,connector=C(ws),trade_symbols=(SYM,)))
            self.assertEqual((result['market_messages'],result['market_trade_messages'],result['public_market_trades']),(2,1,1))
            self.assertEqual(result['ws_snapshots_seen'],1)
            self.assertEqual(result['orderbook_tops_reconstructed'],1)
            self.assertEqual(len(ws.sent),2)
            self.assertEqual([x['type'] for x in ws.sent],['ORDER_BOOK','INSTRUMENT_TRADES'])
            self.assertNotIn('jwt',fn.read_text().lower())
            self.assertNotIn('trade-002',fn.read_text())
            self.assertEqual(result['status'],'DATA_RECORDED')
            self.assertFalse(result['continuity_verified'])
    def test_market_only_no_book_no_money_claim(self):
        ws=Socket([{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},
                   frame(trades({'price':'12','size':'1','timestamp':'2026-10-16T15:20:00Z'}),'INSTRUMENT_TRADES')])
        with tempfile.TemporaryDirectory() as td:
            r=asyncio.run(m.capture_stream('jwt',(SYM,),1,Path(td)/'e.jsonl',connector=C(ws),trade_symbols=(SYM,)))
            self.assertEqual(r['market_messages'],0)
            self.assertEqual(r['status'],'TRADE_PRINTS_ONLY_BOOK_NOT_VERIFIED')
            self.assertFalse(r['profit_proven'])
    def test_broker_error_causes_bad_stream_and_book_no_go(self):
        ws=Socket([{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},
                   {'type':'ERROR','error_info':{'code':401,'message':'private-info-no-save'}}])
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'e.jsonl'
            r=asyncio.run(m.capture_stream('jwt',(SYM,),1,path,connector=C(ws)))
            self.assertEqual(r['status'],'STREAM_ERRORS_UNVERIFIED')
            self.assertNotIn('private-info',path.read_text())
    def test_ambiguous_book_row_missing_side_doesnt_count_as_data(self):
        p=book([{'price':'12','action':'ACTION_ADD'}])
        self.assertIsNone(m.sanitize_envelope(frame(p),(SYM,)))
    def test_bool_envelope_timestamp_never_counted(self):
        p=frame(book([row(100,b=1)],False));p['timestamp']=True
        r=m.sanitize_envelope(p,(SYM,))
        self.assertIsNone(r['provider_timestamp'])
    def test_trade_id_is_hash_not_raw(self):
        s=m.scrub_market_trades(trades({'price':'13','size':'1','timestamp':'2026-10-16T15:20:00Z',
            'tradeId':'public-exchange-id'}),(SYM,))
        self.assertTrue(len(s['trades'][0]['public_trade_id_hash'])==24)
        self.assertNotIn('public-exchange-id',json.dumps(s))

if __name__=='__main__':unittest.main()