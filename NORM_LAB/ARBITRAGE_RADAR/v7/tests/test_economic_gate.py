"""Proof that no model numbers appear without actual account cost inputs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from economic_gate import AccountEconomics,quote_width_upper_bound,cost_per_roundtrip,conservative_trade_through,TradeAllocationLedger

SYM='W4X6W4Z6@RTSX'
T=datetime(2026,10,16,14,0,tzinfo=timezone.utc)

def spec(**kwargs):
    d={'symbol':SYM,'rub_per_spread_point':Decimal('1'),
    'account_initial_margin_rub':Decimal('2000'),
    'broker_buy_rub':Decimal('4'),'broker_sell_rub':Decimal('4'),
    'exchange_buy_rub':Decimal('2'),'exchange_sell_rub':Decimal('2'),
    'execution_stress_rub_each_way':Decimal('3'),'reserve_rub':Decimal('2000'),
    'verified_source':'BROKER_DOCUMENT_ID_2026_10_16',
    'verified_account':True}
    d.update(kwargs)
    return AccountEconomics(**d)

def book(bid='740',ask='770',bidqty='30',askqty='30',**kwargs):
    d={'symbol':SYM,'status':'TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF','ws_snapshot_received':True,
    'market_data_observed':True,'bid':bid,'ask':ask,'bid_qty':bidqty,'ask_qty':askqty}
    d.update(kwargs);return d

class MoneyFirstTests(unittest.TestCase):
    def test_missing_fees_no_pnl(self):
        r=quote_width_upper_bound(book(),None,10000)
        self.assertEqual(r['status'],'BLOCKED_ACCOUNT_COSTS_UNKNOWN')
        self.assertIsNone(r['net_upper_bound_rub'])
        self.assertIsNone(r['realized_profit_rub'])
    def test_model_net_is_not_profit(self):
        r=quote_width_upper_bound(book(),spec(),10000)
        self.assertEqual(r['tradable_lots'],4)
        self.assertEqual(r['width_points'],'30')
        self.assertEqual(r['gross_upper_bound_rub'],'120')
        self.assertEqual(r['roundtrip_costs_rub'],'72')
        self.assertEqual(r['net_upper_bound_rub'],'48')
        self.assertFalse(r['executable']);self.assertIsNone(r['realized_profit_rub'])
        self.assertFalse(r['fill_probability_estimated'])
    def test_unknown_margin_no_money(self):
        r=quote_width_upper_bound(book(),spec(account_initial_margin_rub=None),10000)
        self.assertEqual(r['status'],'UNVERIFIED_MONETARY_PARAMETER')
    def test_unverified_account_no_assumptions(self):
        r=quote_width_upper_bound(book(),spec(verified_account=False),10000)
        self.assertEqual(r['status'],'ACCOUNT_COST_EVIDENCE_NOT_VERIFIED')
    def test_missing_source_no_money(self):
        r=quote_width_upper_bound(book(),spec(verified_source=''),10000)
        self.assertEqual(r['status'],'COST_SOURCE_MISSING')
    def test_negative_fee_stops(self):
        r=quote_width_upper_bound(book(),spec(broker_sell_rub=Decimal('-1')),10000)
        self.assertEqual(r['status'],'INVALID_MONETARY_PARAMETER')
    def test_symbol_other_than_account_quote_stops(self):
        r=quote_width_upper_bound(book(symbol='SRZ6SRH7@RTSX'),spec(),10000)
        self.assertEqual(r['status'],'BOOK_SYMBOL_MISMATCH')
    def test_without_ws_snapshot_no_economic_claim(self):
        r=quote_width_upper_bound(book(ws_snapshot_received=False),spec(),10000)
        self.assertEqual(r['status'],'NO_VERIFIED_WS_BOOK_SNAPSHOT')
    def test_stale_timestamp_no_pnl(self):
        r=quote_width_upper_bound(book(status='LEVEL_TIMESTAMP_OLD_NOT_BOOK_STATUS'),spec(),10000)
        self.assertEqual(r['status'],'BOOK_QUALITY_UNVERIFIED')
    def test_liquidity_caps_size(self):
        r=quote_width_upper_bound(book(bidqty='2',askqty='2'),spec(),10000)
        self.assertEqual(r['tradable_lots'],2)
    def test_costs_eat_optimistic_width(self):
        r=quote_width_upper_bound(book(bid='740',ask='750'),spec(),10000)
        self.assertEqual(r['net_upper_bound_rub'],'-32')
        self.assertEqual(r['status'],'MODEL_UPPER_BOUND_NOT_POSITIVE')
    def test_no_margin_capacity(self):
        r=quote_width_upper_bound(book(),spec(account_initial_margin_rub='12000'),10000)
        self.assertEqual(r['status'],'CAPITAL_OR_DEPTH_INSUFFICIENT')
    def test_one_sided_cannot_show_upper_profit(self):
        r=quote_width_upper_bound(book(ask=None),spec(),10000)
        self.assertEqual(r['status'],'NO_TWO_SIDED_BOOK')
    def test_invalid_crossed_quote_no_money(self):
        r=quote_width_upper_bound(book(bid='790',ask='780'),spec(),10000)
        self.assertEqual(r['status'],'INVALID_BOOK_OR_COST_VALUES')
    def test_negative_spread_price_works(self):
        r=quote_width_upper_bound(book(bid='-50',ask='-20'),spec(),10000)
        self.assertEqual(r['width_points'],'30')
    def _trade(self, price='99', size='5', seconds=1, symbol=SYM,
               session='2026-10-16:RTSX', tid='a'*24):
        return {'symbol':symbol,'session_id':session,'price':price,'size':size,
                'timestamp':(T+timedelta(seconds=seconds)).isoformat(),
                'public_trade_id_hash':tid}

    def _through(self, prints, ledger=None, **kw):
        args={'instrument_symbol':SYM,'requested_volume':'1',
              'ledger':ledger or TradeAllocationLedger('2026-10-16:RTSX'),
              'volume_units_verified':True}
        args.update(kw)
        return conservative_trade_through('BUY','100',T,prints,**args)

    def test_old_unscoped_api_fails_closed(self):
        self.assertEqual(conservative_trade_through('BUY','100',T,[self._trade()])['status'],
                         'INSTRUMENT_CONTEXT_REQUIRED')

    def test_trade_touch_not_fill(self):
        self.assertEqual(self._through([self._trade(price='100')])['status'],'NO_TRADE_THROUGH')

    def test_trade_through_is_still_not_fill(self):
        r=self._through([self._trade()])
        self.assertEqual(r['status'],'TRADE_THROUGH_PROXY_NO_FILL_PROOF')
        self.assertFalse(r['actual_fill_proven'])
        self.assertFalse(r['executable'])

    def test_trade_print_before_posting_cannot_fill(self):
        self.assertEqual(self._through([self._trade(seconds=-1)])['status'],'NO_TRADE_THROUGH')

    def test_sell_through(self):
        t=self._trade(price='101')
        r=conservative_trade_through('SELL','100',T,[t],instrument_symbol=SYM,
                                    requested_volume='1',ledger=TradeAllocationLedger('2026-10-16:RTSX'),
                                    volume_units_verified=True)
        self.assertEqual(r['status'],'TRADE_THROUGH_PROXY_NO_FILL_PROOF')

    def test_invalid_time_latency_stops(self):
        self.assertEqual(conservative_trade_through('BUY','100',T,[],0)['status'],'INVALID_LATENCY_BOUND')

    def test_no_fabrication_if_no_trades(self):
        self.assertEqual(self._through([])['status'],'NO_TRADE_THROUGH')

    def test_other_instrument_print_cannot_satisfy_wheat(self):
        r=self._through([self._trade(symbol='NLMKZ6NLMKH7@RTSX')])
        self.assertEqual(r['status'],'NO_TRADE_THROUGH')

    def test_other_session_print_is_rejected(self):
        self.assertEqual(self._through([self._trade(session='2026-10-15:RTSX')])['status'],
                         'NO_TRADE_THROUGH')

    def test_same_trade_id_counted_only_once_within_batch(self):
        t=self._trade(size='1')
        r=self._through([t,dict(t)],requested_volume='2')
        self.assertEqual(r['status'],'INSUFFICIENT_UNCONSUMED_TRADE_VOLUME')

    def test_conflicting_duplicate_ids_are_not_usable(self):
        a=self._trade(size='10')
        b=dict(a);b['price']='98'
        self.assertEqual(self._through([a,b])['status'],'NO_TRADE_THROUGH')

    def test_shared_ledger_prevents_double_use_across_orders(self):
        ledger=TradeAllocationLedger('2026-10-16:RTSX')
        rows=[self._trade(size='1')]
        self.assertEqual(self._through(rows,ledger=ledger)['status'],'TRADE_THROUGH_PROXY_NO_FILL_PROOF')
        self.assertEqual(self._through(rows,ledger=ledger)['status'],'NO_TRADE_THROUGH')
        self.assertEqual(ledger.used_volume[(SYM,'a'*24)],Decimal('1'))

    def test_trade_volume_allocates_only_once_across_different_orders(self):
        ledger=TradeAllocationLedger('2026-10-16:RTSX')
        t=[self._trade(size='5')]
        self.assertEqual(self._through(t,ledger=ledger,requested_volume='2')['status'],
                         'TRADE_THROUGH_PROXY_NO_FILL_PROOF')
        self.assertEqual(self._through(t,ledger=ledger,requested_volume='3')['status'],
                         'TRADE_THROUGH_PROXY_NO_FILL_PROOF')
        self.assertEqual(self._through(t,ledger=ledger,requested_volume='1')['status'],
                         'NO_TRADE_THROUGH')
        self.assertEqual(ledger.used_volume[(SYM,'a'*24)],Decimal('5'))

    def test_partial_volume_fails_without_reserving(self):
        ledger=TradeAllocationLedger('2026-10-16:RTSX')
        r=self._through([self._trade(size='2')],ledger=ledger,requested_volume='3')
        self.assertEqual(r['status'],'INSUFFICIENT_UNCONSUMED_TRADE_VOLUME')
        self.assertEqual(ledger.used_volume,{})

    def test_two_distinct_trade_ids_can_jointly_cover_size(self):
        ledger=TradeAllocationLedger('2026-10-16:RTSX')
        rows=[self._trade(size='2',tid='a'*24),self._trade(size='3',tid='b'*24)]
        r=self._through(rows,ledger=ledger,requested_volume='5')
        self.assertEqual(r['status'],'TRADE_THROUGH_PROXY_NO_FILL_PROOF')
        self.assertEqual(r['independent_trade_ids_used'],2)
        self.assertEqual(sum(ledger.used_volume.values()),Decimal('5'))

    def test_missing_print_id_never_counts(self):
        self.assertEqual(self._through([self._trade(tid='')])['status'],'NO_TRADE_THROUGH')

    def test_unknown_trade_unit_cannot_generate_proxy(self):
        r=self._through([self._trade()],volume_units_verified=False)
        self.assertEqual(r['status'],'PRINT_VOLUME_UNITS_UNVERIFIED')

    def test_invalid_requested_volume_and_missing_ledger_fail(self):
        self.assertEqual(self._through([self._trade()],requested_volume=None)['status'],
                         'INVALID_REQUESTED_VOLUME')
        r=conservative_trade_through('BUY','100',T,[self._trade()],instrument_symbol=SYM,
                                     requested_volume='1',volume_units_verified=True)
        self.assertEqual(r['status'],'SHARED_SESSION_LEDGER_REQUIRED')

if __name__=='__main__':unittest.main()