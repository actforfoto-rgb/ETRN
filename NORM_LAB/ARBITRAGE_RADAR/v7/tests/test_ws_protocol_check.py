import asyncio
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import importlib.util
import json

PATH = Path(__file__).resolve().parents[1] / 'WS_PROTOCOL_CHECK.py'
spec = importlib.util.spec_from_file_location('wire_probe', PATH)
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)


class MockSocket:
    def __init__(self, packets):
        self.packets = iter(packets)
        self.sent = []
    async def recv(self):
        try:
            return json.dumps(next(self.packets))
        except StopIteration:
            raise RuntimeError('mock stopped')
    async def send(self, data):
        self.sent.append(json.loads(data))


class MockConnector:
    def __init__(self, stream):
        self.stream = stream
    def __call__(self, *args, **kwargs):
        return self
    async def __aenter__(self):
        return self.stream
    async def __aexit__(self, *args):
        pass


class WireTests(TestCase):
    def test_handshake_sends_only_market_subscriptions(self):
        sock = MockSocket([
            {'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},
            {'type':'DATA','subscription_type':'ORDER_BOOK','payload':{'order_book':[{'symbol':wire.FUTURE_SPREAD, 'rows':[{'price':'1'}]}]}},
            {'type':'DATA','subscription_type':'QUOTES','payload':{'quote':[{'symbol':wire.CONTROL_STOCK}]}},
        ])
        r=asyncio.run(wire.observe('secret-jwt-one-time', 20, connector=MockConnector(sock)))
        self.assertEqual(len(sock.sent),2)
        self.assertEqual({s['type'] for s in sock.sent},{'ORDER_BOOK','QUOTES'})
        self.assertEqual(sock.sent[0]['data'],{'symbol':wire.FUTURE_SPREAD})
        self.assertEqual(sock.sent[1]['data'],{'symbols':[wire.CONTROL_STOCK]})
        self.assertEqual(r['counters']['FRAMES_DATA'],2)
        self.assertNotIn('secret-jwt-one-time',json.dumps(r))
        self.assertEqual(r['broker_orders_sent'],0)

    def test_empty_stream_not_claimed_data(self):
        s = MockSocket([{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}}])
        r=asyncio.run(wire.observe('jwt',20,connector=MockConnector(s)))
        self.assertEqual(r['conclusion'],'ONLY_WS_HANDSHAKE_OR_ERRORS_NO_DATA')
        self.assertNotIn('FRAMES_DATA',r['counters'])

    def test_before_handshake_do_not_subscribe(self):
        s = MockSocket([{'type':'DATA','subscription_type':'QUOTES','payload':{'quote':[1]}}])
        r=asyncio.run(wire.observe('jwt',20,connector=MockConnector(s)))
        self.assertEqual(s.sent,[])
        self.assertEqual(r['counters']['FRAMES_DATA'],1)

    def test_errors_only_expose_code(self):
        s = MockSocket([{'type':'ERROR','error_info':{'code':403,'message':'ACCESS_TOKEN_SECRET_JWT account_id G12345'}},
                        {'type':'ERROR','error_info':{'code':'token-secret','message':'sensitive'}}])
        r=asyncio.run(wire.observe('jwt',20,connector=MockConnector(s)))
        data=json.dumps(r)
        for value in ('ACCESS_TOKEN_SECRET_JWT','G12345','token-secret','sensitive'):
            self.assertNotIn(value,data)
        self.assertEqual(r['error_codes_only'],{'403':1,'UNKNOWN_CODE':1})

    def test_unknown_payload_shape_counted(self):
        cnt,errors=Counter(),Counter()
        wire.summarize_frame({'type':'DATA','subscription_type':'ORDER_BOOK','payload':{'unexpected':{'token':'xxxx'}}},cnt,errors)
        self.assertEqual(cnt['DATA_PAYLOAD_UNKNOWN_SHAPE'],1)

    def test_frame_category_does_not_echo_freeform(self):
        cnt,err=Counter(),Counter()
        wire.summarize_frame({'type':'CONNECTION_WITH_SECRET','account_id':'sensitive'},cnt,err)
        self.assertEqual(cnt['UNKNOWN_TYPE_FRAMES'],1)
        self.assertNotIn('sensitive',str(dict(cnt)))

    def test_https_empty_rows_not_book(self):
        class Resp:
            def __enter__(self):return self
            def __exit__(self,*a):return None
            def read(self,n):return b'{"orderbook":{"rows":[]}}'
        with mock.patch.object(wire,'urlopen',return_value=Resp()):
            v=wire.rest_public_structure('jwt','CRZ6CRH7@RTSX','orderbook')
        self.assertEqual(v['status'],'HTTP_200')
        self.assertFalse(v['has_rows'])
        self.assertFalse(v['has_bid_and_ask'])

    def test_rest_book_nonzero_on_both_sides(self):
        class Resp:
            def __enter__(self):return self
            def __exit__(self,*a):return None
            def read(self,n):return json.dumps({'orderbook':{'rows':[
                {'buy_size':{'value':'1'},'sell_size':{'value':'0'}},
                {'buy_size':{'value':'0'},'sell_size':{'value':'3'}},
            ]}}).encode()
        with mock.patch.object(wire,'urlopen',return_value=Resp()):
            v=wire.rest_public_structure('jwt',wire.FUTURE_SPREAD,'orderbook')
        self.assertTrue(v['has_bid_and_ask'])

    def test_rest_book_zero_quantities_fail(self):
        class Resp:
            def __enter__(self):return self
            def __exit__(self,*a):return None
            def read(self,n):return json.dumps({'orderbook':{'rows':[
                {'buy_size':{'value':'0'},'sell_size':{'value':'0'}},
            ]}}).encode()
        with mock.patch.object(wire,'urlopen',return_value=Resp()):
            v=wire.rest_public_structure('jwt',wire.FUTURE_SPREAD,'orderbook')
        self.assertFalse(v['has_bid_and_ask'])

    def test_stock_control_does_not_access_account(self):
        captured=[]
        class Resp:
            def __enter__(self):return self
            def __exit__(self,*a):return None
            def read(self,n):return b'{"symbol":"SBER@MISX","quote":{"last":"100"}}'
        def op(r,timeout):
            captured.append(r)
            return Resp()
        with mock.patch.object(wire,'urlopen',side_effect=op):
            v=wire.rest_public_structure('jwt',wire.CONTROL_STOCK,'quotes/latest')
        self.assertTrue(v['quote_object_present'])
        self.assertIn('/quotes/latest',captured[0].full_url)
        self.assertEqual(captured[0].method,'GET')

    def test_duration_fails_closed(self):
        with self.assertRaises(ValueError):
            asyncio.run(wire.observe('jwt',500,connector=lambda *_:None))

    def test_no_output_files_or_requests_placed_in_source(self):
        source=PATH.read_text(encoding='utf-8')
        self.assertNotIn('POST /v1/accounts',source)
        self.assertNotIn('PlaceOrder',source)
        self.assertNotIn('create_unverified_context',source)
        self.assertNotIn('print(jwt',source)

if __name__=='__main__':
    import unittest;unittest.main()