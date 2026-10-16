import asyncio, json, tempfile, pathlib
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import NORM_ARB_WS_SHAPE_V2 as mod

SYM=mod.BOOK_SYMBOL
STOCK=mod.QUOTE_SYMBOL

def frame(kind, payload=object(), **rest):
    d={'type':'DATA','subscription_type':kind,'timestamp':1792160001}
    if type(payload) is not object: d['payload']=payload
    d.update(rest)
    return d

B={'order_book':[{'symbol':SYM,'rows':[{'price':'101.2','buy_size':'3','action':'ACTION_ADD','timestamp':'2026-10-16T11:24:01Z'},{'price':'101.3','sell_size':'2','action':'ACTION_ADD'}], 'is_data_snapshot': False}]}
Q={'quote':[{'symbol':STOCK,'bid':'285.5','ask':'285.6','last':'285.55'}]}

class ShapeTest(unittest.TestCase):
    def test_dict_payload_book_oneof(self):
        c=mod.Counter(); ev=mod.interpret_data(frame('ORDER_BOOK',B),c)
        self.assertEqual(len(ev['items'][0]['rows']),2)
        self.assertEqual(c['book_rows_sanitized'],2)
        self.assertNotIn('sell_size',ev['items'][0]['rows'][0]); self.assertNotIn('buy_size',ev['items'][0]['rows'][1])
    def test_json_string_payload(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',json.dumps(B)),c)
        self.assertIsNotNone(ev); self.assertEqual(c['payload_string_json_to_object'],1)
    def test_data_fallback_dict(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',data=B),c)
        self.assertIsNotNone(ev);self.assertEqual(c['location_data'],1)
    def test_data_fallback_str(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',None,data=json.dumps(B)),c)
        self.assertIsNotNone(ev);self.assertEqual(c['payload_type_payload_null'],1)
    def test_list_payload(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',B['order_book']),c)
        self.assertIsNotNone(ev)
    def test_direct_object(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',B['order_book'][0]),c)
        self.assertIsNotNone(ev)
    def test_quote(self):
        c=mod.Counter(); ev=mod.interpret_data(frame('QUOTES',Q),c)
        self.assertEqual(ev['items'][0]['bid'],'285.5')
    def test_quoted_json_string(self):
        c=mod.Counter();ev=mod.interpret_data(frame('QUOTES',json.dumps(Q)),c)
        self.assertIsNotNone(ev)
    def test_missing_payload_flag(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK'),c)
        self.assertIsNone(ev);self.assertEqual(c['payload_field_missing'],1)
    def test_null_payload_flag(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',None),c)
        self.assertIsNone(ev);self.assertEqual(c['payload_type_payload_null'],1)
    def test_unsupported_string(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK','not-json'),c)
        self.assertIsNone(ev);self.assertEqual(c['payload_string_not_json'],1)
    def test_reject_account(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ACCOUNT',B),c)
        self.assertIsNone(ev)
    def test_no_token_leak(self):
        secret='mock_token_SECRET_MUST_NEVER_LEAK'
        b={'order_book':[dict(B['order_book'][0], account_id=secret)]}
        c=mod.Counter(); ev=mod.interpret_data(frame('ORDER_BOOK',b,token=secret),c)
        self.assertNotIn(secret,json.dumps(ev))
    def test_malformed_price(self):
        c=mod.Counter(); ev=mod.interpret_data(frame('ORDER_BOOK',{'order_book':[{'symbol':SYM,'rows':[{'price':'nan','buy_size':'1'}]}]}),c)
        self.assertIsNone(ev)
    def test_negative_spread_and_remove(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',{'order_book':[{'symbol':SYM,'rows':[{'price':'-0.2','action':'ACTION_REMOVE'}]}]}),c)
        self.assertIsNotNone(ev)
    def test_reject_timestamp_token_smuggling(self):
        self.assertIsNone(mod.safe_timestamp('TOOLSECRET-TOKEN-KEPT-HIDDEN'))
        self.assertIsNone(mod.safe_timestamp('2026-10-16T10:10:10'))
        self.assertEqual(mod.safe_timestamp('2026-10-16T10:10:10Z'), '2026-10-16T10:10:10.000+00:00')
    def test_boolean_qty_blocked(self):
        c=mod.Counter();ev=mod.interpret_data(frame('ORDER_BOOK',{'order_book':[{'symbol':SYM,'rows':[{'price':'1','buy_size':True}]}]}),c)
        self.assertIsNone(ev)
    def test_evidence_file_local_only(self):
        with tempfile.TemporaryDirectory() as td,patch.object(mod,'OUTPUT',pathlib.Path(td)):
            d={'schema':'test'};e=mod.interpret_data(frame('ORDER_BOOK',B),mod.Counter())
            mod.save_only_sanitized(d,[e]); self.assertTrue(pathlib.Path(d['evidence_file']).exists())
            self.assertNotIn('TOKEN',pathlib.Path(d['evidence_file']).read_text())
    def test_zero_evidence_no_file(self):
        with tempfile.TemporaryDirectory() as td,patch.object(mod,'OUTPUT',pathlib.Path(td)):
            d={};mod.save_only_sanitized(d,[]);self.assertFalse(list(pathlib.Path(td).iterdir()))

class MockSocket:
    def __init__(self, frames):self.frames=frames;self.sent=[]
    async def recv(self):
        if self.frames: return json.dumps(self.frames.pop(0))
        await asyncio.sleep(0.1)
        raise asyncio.TimeoutError
    async def send(self,s):self.sent.append(json.loads(s))
class MockConnector:
    def __init__(self,s):self.s=s
    def __call__(self,*a,**kw):return self
    async def __aenter__(self):return self.s
    async def __aexit__(self,*a):return False
class NetworkTest(unittest.TestCase):
    def test_handshake_control_and_market_only(self):
        sock=MockSocket([{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},frame('ORDER_BOOK',json.dumps(B)),frame('QUOTES',Q)])
        with patch.object(mod,'DURATION_SECONDS',15):
            s,v=asyncio.run(mod.observe('fake-jwt',15,MockConnector(sock)))
        self.assertEqual(len(sock.sent),2);self.assertEqual({x['type'] for x in sock.sent},{'QUOTES','ORDER_BOOK'})
        self.assertEqual(s['counters']['book_rows_sanitized'],2)
        self.assertEqual(s['counters']['quotes_sanitized'],1)
        self.assertEqual(len(v),2)
    def test_frames_no_payload_provides_shape(self):
        sock=MockSocket([{'type':'EVENT','event_info':{'event':'HANDSHAKE_SUCCESS'}},frame('ORDER_BOOK',None)])
        s,v=asyncio.run(mod.observe('fake-jwt',15,MockConnector(sock)))
        self.assertEqual(s['conclusion'],'DATA_SEEN_PAYLOAD_SHAPE_UNKNOWN')
        self.assertEqual(s['counters']['payload_type_payload_null'],1)
        self.assertEqual(v,[])

if __name__=='__main__':unittest.main()