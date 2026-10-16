"""Offline regression: official Finam ProtoJSON WS payload, no live credentials."""
import asyncio, json, pathlib, tempfile, unittest
from unittest.mock import patch
from finam_ws_collector import scrub_market_payload, sanitize_envelope, capture_stream

SYM='SRZ6SRH7@RTSX'


def wire_row(*,buy=None,sell=None,price='1025',action='ACTION_ADD'):
    x={'price':price,'action':action,'timestamp':'2026-10-16T15:19:00Z'}
    if buy is not None:x['buySize']=buy
    if sell is not None:x['sellSize']=sell
    return x


def book_frame(*rows, snapshot=False):
    pay={'orderBook':[{'symbol':SYM,'rows':list(rows),'isDataSnapshot':snapshot}]}
    return {'type':'DATA','subscription_type':'ORDER_BOOK','timestamp':1792163940,
            'payload':json.dumps(pay,separators=(',',':'))}

class ProtoJSONCollectorTests(unittest.TestCase):
    def test_live_oneof_and_snapshot_flag(self):
        frame=book_frame(wire_row(buy='21',price='1025'),wire_row(sell='17',price='1026'), snapshot=True)
        obj=sanitize_envelope(frame,(SYM,))
        self.assertEqual(obj['kind'],'DATA');d=obj['payload']['order_book'][0]
        self.assertEqual(d['symbol'],SYM)
        self.assertEqual(d['is_data_snapshot'],True)
        self.assertEqual(d['rows'][0]['buy_size'],'21')
        self.assertNotIn('sell_size',d['rows'][0])
        self.assertEqual(d['rows'][1]['sell_size'],'17')
    def test_account_secrets_never_enter_sanitized_evidence(self):
        secret='MOCK_PRIVATE_TOKEN_DO_NOT_STORE'
        frame=book_frame(wire_row(buy='21'))
        frame['jwt']=secret
        p=json.loads(frame['payload']);p['token']=secret;p['orderBook'][0]['account_id']=secret
        p['orderBook'][0]['rows'][0]['order_id']=secret;frame['payload']=json.dumps(p)
        raw=json.dumps(sanitize_envelope(frame,(SYM,)))
        self.assertNotIn(secret,raw)
    def test_snake_original_proto_field_names_still_accepted(self):
        frame={'type':'DATA','subscription_type':'ORDER_BOOK','timestamp':1792163940,
          'payload':{'order_book':[{'symbol':SYM,'rows':[{'price':'1','sell_size':'2','action':'ACTION_UPDATE'}],'is_data_snapshot':False}]}}
        d=sanitize_envelope(frame,(SYM,))['payload']['order_book'][0]
        self.assertEqual(d['rows'][0]['sell_size'],'2')
    def test_invalid_unrecognized_payload_never_valid_data(self):
        frame=book_frame(wire_row(buy='2'))
        for payload in ('not-json',None,{'orderBook':[]},{'orderBook':[{'symbol':SYM,'rows':[]}]},'{}'):
            frame['payload']=payload
            self.assertIsNone(sanitize_envelope(frame,(SYM,)))
    def test_non_market_subscription_rejected(self):
        frame=book_frame(wire_row(buy='2'));frame['subscription_type']='ACCOUNT'
        self.assertIsNone(sanitize_envelope(frame,(SYM,)))
    def test_conflicting_proto_alias_price_side_rejected(self):
        p={'orderBook':[{'symbol':SYM,'rows':[{'price':'12','buySize':'2','buy_size':'7','action':'ACTION_ADD'}]}]}
        self.assertTrue(scrub_market_payload(json.dumps(p)).get('unrecognized'))
    def test_long_nested_json_rejected(self):
        self.assertTrue(scrub_market_payload(' '+('A'*200001)).get('unrecognized'))
    def test_missing_quantity_does_not_create_fake_row(self):
        p={'orderBook':[{'symbol':SYM,'rows':[{'price':'12','action':'ACTION_ADD'}]}]}
        self.assertTrue(scrub_market_payload(p).get('unrecognized'))
    def test_ws_mock_handshake_market_writes_only_sanitized(self):
        class Socket:
            def __init__(self):self.frames=[{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},
                                            book_frame(wire_row(buy='4'))];self.sent=[]
            async def recv(self):
                if self.frames:return json.dumps(self.frames.pop(0))
                await asyncio.sleep(20)
            async def send(self,p):self.sent.append(json.loads(p))
        class Conn:
            def __init__(self,s):self.s=s
            def __call__(self,*a,**k):return self
            async def __aenter__(self):return self.s
            async def __aexit__(self,*a):return False
        with tempfile.TemporaryDirectory() as td:
            s=Socket();p=pathlib.Path(td)/'test.jsonl'
            report=asyncio.run(capture_stream('ephemeral-jwt',(SYM,),1,p,connector=Conn(s)))
            self.assertEqual(report['market_messages'],1)
            self.assertEqual(report['status'],'DATA_RECORDED')
            self.assertEqual(len(s.sent),1)
            self.assertNotIn('ephemeral-jwt',p.read_text())
            self.assertIn('buy_size',p.read_text())

if __name__=='__main__':unittest.main()