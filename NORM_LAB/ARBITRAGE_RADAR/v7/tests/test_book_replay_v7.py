"""Independent oneof/snapshot/delta reconstruction tests: no broker calls."""
import unittest
from datetime import datetime, timedelta, timezone
from book_replay import BookReplay, InvalidMarketData

SYM='W4X6W4Z6@RTSX'; NEXT='W4V6W4X6@RTSX'; T=datetime(2026,10,16,15,19,0,tzinfo=timezone.utc)

def row(price,b=None,s=None,action=None,ts=T):
    x={'price':str(price)}
    if b is not None: x['buy_size']=str(b)
    if s is not None: x['sell_size']=str(s)
    if action is not None: x['action']=action
    if ts is not None: x['timestamp']=ts.isoformat()
    return x

def snap(rows=None):
    return {'rows':rows if rows is not None else [row('1200',b=10),row('1220',s=5)],'is_data_snapshot':True}

def event(*books):
    return {'kind':'DATA','subscription_type':'ORDER_BOOK','provider_timestamp':1792163940,
            'payload':{'order_book':list(books)}}

class BookRecoveryTests(unittest.TestCase):
    def setUp(self):self.e=BookReplay((SYM,NEXT))
    def boot(self):return self.e.bootstrap_ws(SYM,snap(),T,1792163940)
    def test_ws_snapshot_oneof_is_two_sided(self):
        s=self.boot();self.assertEqual((s['bid'],s['ask']),('1200','1220'))
        self.assertEqual((s['bid_qty'],s['ask_qty']),('10','5'))
        self.assertTrue(s['ws_snapshot_received']);self.assertFalse(s['executable'])
        self.assertFalse(s['continuity_verified'])
    def test_delta_before_snapshot_does_not_create_quote(self):
        v=self.e.apply_delta(SYM,{'rows':[row('1201',b=20,action='ACTION_ADD')]},T,1792163940)
        self.assertEqual(v['status'],'UNBOOTSTRAPPED')
    def test_oneof_ask_update_preserves_bid(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('1218',s=13,action='ACTION_ADD')]},T,1792163941)
        self.assertEqual(v['bid'],'1200');self.assertEqual(v['ask'],'1218')
        self.assertEqual(v['bid_qty'],'10');self.assertEqual(v['ask_qty'],'13')
    def test_oneof_buy_update_preserves_ask(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('1201',b=2,action='ACTION_UPDATE')]},T,1792163941)
        self.assertEqual(v['bid'],'1201');self.assertEqual(v['ask'],'1220')
    def test_remove_specific_bid_does_not_drop_ask_same_price(self):
        self.e.bootstrap_ws(SYM,{'is_data_snapshot':True,'rows':[row('100',b=3),row('101',s=5)]},T)
        v=self.e.apply_delta(SYM,{'rows':[row('100',b=0,action='ACTION_REMOVE')]},T)
        self.assertEqual(v['status'],'ONE_SIDED');self.assertIsNone(v['bid'])
        self.assertIn(__import__('decimal').Decimal('101'),self.e.books[SYM].asks)
    def test_ambiguous_side_less_remove_invalidates(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[{'price':'1200','action':'ACTION_REMOVE'}]},T)
        self.assertEqual(v['status'],'INVALIDATED_REQUIRES_REST_BOOTSTRAP')
        self.assertEqual(v['reason'],'AMBIGUOUS_SIDE_NOT_PRESENT')
    def test_delta_action_unspecified_fails_closed(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('1201',b=4,action='ACTION_UNSPECIFIED')]},T)
        self.assertEqual(v['reason'],'AMBIGUOUS_WS_ACTION')
    def test_snapshot_action_unspecified_allowed(self):
        v=self.e.bootstrap_ws(SYM,{'isDataSnapshot':True,'rows':[row('1200',b=4,action='ACTION_UNSPECIFIED'),row('1210',s=4,action='ACTION_UNSPECIFIED')]},T)
        self.assertEqual(v['status'],'TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF')
    def test_crossed_frame_is_atomic_and_invalid(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('1215',s=9,action='ACTION_ADD'),row('1230',b=1,action='ACTION_ADD')]},T)
        self.assertEqual(v['status'],'INVALIDATED_REQUIRES_REST_BOOTSTRAP');self.assertIsNone(v['bid'])
    def test_snapshot_after_invalid_clears_book_and_restores(self):
        self.boot();self.e.invalidate_all('CONNECTION_CLOSED')
        s=self.e.bootstrap_ws(SYM,snap([row('12',b=2),row('13',s=2)]),T)
        self.assertEqual(s['bid'],'12');self.assertEqual(s['ask'],'13')
    def test_snapshots_replace_prior_levels(self):
        self.boot();s=self.e.bootstrap_ws(SYM,snap([row('10',b=1),row('11',s=1)]),T)
        self.assertEqual(s['bid'],'10');self.assertEqual(s['ask'],'11')
    def test_out_of_order_provider_invalidates(self):
        self.boot();self.e.apply_delta(SYM,{'rows':[row('1201',b=4,action='ACTION_ADD')]},T,1792163942)
        v=self.e.apply_delta(SYM,{'rows':[row('1201',b=5,action='ACTION_UPDATE')]},T,1792163941)
        self.assertEqual(v['reason'],'OUT_OF_ORDER_PROVIDER_TIME')
    def test_missing_level_timestamp_not_marked_fresh(self):
        s=self.e.bootstrap_ws(SYM,snap([row('1200',b=3,ts=None),row('1220',s=4)]),T)
        self.assertEqual(s['status'],'LEVEL_TIMESTAMP_UNVERIFIED')
    def test_old_row_time_does_not_claim_unfillable(self):
        s=self.e.bootstrap_ws(SYM,snap([row('1200',b=3,ts=T-timedelta(hours=1)),row('1220',s=4)]),T)
        self.assertEqual(s['status'],'LEVEL_TIMESTAMP_OLD_NOT_BOOK_STATUS');self.assertFalse(s['executable'])
    def test_negative_spread_prices(self):
        s=self.e.bootstrap_ws(SYM,snap([row('-12',b=3),row('-11',s=3)]),T)
        self.assertEqual((s['bid'],s['ask']),('-12','-11'))
    def test_corrupt_price_rejected_without_state_damage(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('NaN',b=2,action='ACTION_UPDATE')]},T)
        self.assertEqual(v['status'],'INVALIDATED_REQUIRES_REST_BOOTSTRAP')
    def test_rest_can_bootstrap_but_is_not_ws_snapshot(self):
        s=self.e.bootstrap_rest(SYM,{'orderbook':{'rows':[row('1',b=2),row('2',s=3)]}},T)
        self.assertFalse(s['ws_snapshot_received']);self.assertFalse(s['continuity_verified'])
    def test_ingest_multi_symbol_one_only_snapshot(self):
        v=self.e.on_sanitized_event(event({'symbol':SYM,**snap()}, {'symbol':NEXT,'rows':[row('5',b=9,action='ACTION_ADD')],'is_data_snapshot':False}),T)
        self.assertEqual(len(v),2);self.assertTrue(self.e.view(SYM)['ws_snapshot_received'])
        self.assertEqual(self.e.view(NEXT)['status'],'UNBOOTSTRAPPED')
    def test_disconnect_revokes_all_positions(self):
        self.boot();self.e.invalidate_all('STREAM_INTERRUPTED')
        self.assertFalse(self.e.view(SYM)['market_data_observed'])
        self.assertFalse(self.e.view(NEXT)['executable'])
    def test_zero_quantity_removes_just_that_side(self):
        self.boot();v=self.e.apply_delta(SYM,{'rows':[row('1200',b=0,action='ACTION_UPDATE')]},T)
        self.assertEqual(v['status'],'ONE_SIDED');self.assertIsNone(v['bid']);self.assertEqual(v['ask'],None)

if __name__=='__main__':unittest.main()