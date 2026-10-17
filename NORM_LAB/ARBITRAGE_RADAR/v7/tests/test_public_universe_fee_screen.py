import unittest
from decimal import Decimal
from public_universe_fee_screen import (PublicCostScenario, analyze_pair, scan_public_snapshot, UnverifiedInput)

S=PublicCostScenario(Decimal('0.0001'),Decimal('0'),Decimal('2000'),Decimal('10000'),'FINAM_PUBLIC_26_03_STRATEG_ASSUMED')
N={'symbol':'W4X6','short_name':'WHEAT-11.26','stepprice_rub':10,'minstep':10,'margin_rub':1482.83,'bid':11520,'ask':11530,'buy_sell_fee_rub':1.53}
F={'symbol':'W4Z6','short_name':'WHEAT-12.26','stepprice_rub':10,'minstep':10,'margin_rub':1571.23,'bid':12660,'ask':12670,'buy_sell_fee_rub':1.69}
W={'symbol':'WHEAT-11.26-12.26','bid':860,'ask':890,'trades':102}

class MarketPrescreen(unittest.TestCase):
 def test_static_taker_cannot_capture_positive_book_width(self):
  r=analyze_pair(W,{N['short_name']:N,F['short_name']:F},S,
     spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
  fee=Decimal(r['public_exchange_taker_each_spread_fill_rub'])
  broker=Decimal(r['public_broker_scenario_cost_rub'])
  width=Decimal(r['gross_bid_ask_width_rub'])
  self.assertEqual(Decimal(r['ideal_one_taker_upper_rub']),-broker-fee)
  self.assertEqual(Decimal(r['ideal_two_taker_upper_rub']),-width-broker-2*fee)
  self.assertLess(Decimal(r['ideal_two_taker_upper_rub']),0)
  self.assertEqual(r['scenario_price_basis'],'SAME_BBO_STATIC_PRICE_IDENTITY_NOT_FILL_PROOF')
 def test_any_two_taker_static_positive_spread_is_loss(self):
  for spread in ({**W,'bid':860,'ask':890},{**W,'bid':880,'ask':890},{**W,'bid':885,'ask':890}):
   r=analyze_pair(spread,{N['short_name']:N,F['short_name']:F},S,
      spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
   self.assertLess(Decimal(r['ideal_two_taker_upper_rub']),0)
   self.assertLess(Decimal(r['ideal_one_taker_upper_rub']),0)
 def test_scan_one_and_two_taker_positive_counts_are_zero(self):
  base={'retrieved_utc':'2026-10-13T13:11:28Z','rows':[W]}
  fut={'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]}
  r=scan_public_snapshot(base,fut,S)
  self.assertEqual(r['maker_upper_positive_with_100_trades'],1)
  self.assertEqual(r['one_taker_upper_positive_with_100_trades'],0)
  self.assertEqual(r['two_taker_upper_positive_with_100_trades'],0)
 def test_actual_oct13_futures_and_two_spread_slices(self):
  # Frozen PRIMARY data points from project Drive; no temporal fill assumption.
  near={**N,'bid':11840,'ask':11850,'margin_rub':1503.28,'buy_sell_fee_rub':1.55}
  far={**F,'bid':12710,'ask':12730,'margin_rub':1574.79,'buy_sell_fee_rub':1.65}
  books=[{**W,'bid':860,'ask':890,'trades':102},
         {**W,'bid':860,'ask':890,'trades':102}]
  a,b=[analyze_pair(s,{near['short_name']:near,far['short_name']:far},S,
     spread_captured_utc=t,futures_captured_utc='2026-10-13T13:17:08Z')
     for s,t in zip(books,['2026-10-13T13:11:28Z','2026-10-13T13:22:12Z'])]
  self.assertEqual(Decimal(a['ideal_two_maker_upper_rub']),Decimal('25.0870'))
  self.assertEqual(Decimal(b['ideal_two_maker_upper_rub']),Decimal('25.0870'))
  self.assertEqual(books[1]['trades']-books[0]['trades'],0)
  self.assertEqual(Decimal(a['ideal_one_taker_upper_rub']),Decimal('-8.1130'))
  self.assertEqual(Decimal(a['ideal_two_taker_upper_rub']),Decimal('-41.3130'))

 def test_expected_conditional_wheat_numbers(self):
  x=analyze_pair(W, {N['short_name']:N,F['short_name']:F}, S,
                 spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
  self.assertEqual(Decimal(x['gross_bid_ask_width_rub']),Decimal(30))
  self.assertAlmostEqual(float(x['ideal_two_maker_upper_rub']),25.162,places=2)
  self.assertEqual(x['one_adverse_leg_tick_rub'],'10')
  self.assertFalse(x['synchronized'])
  self.assertIsNone(x['realized_pnl_rub'])
  self.assertFalse(x['account_fees_verified'])
  self.assertEqual(x['unverified_lot_capacity_for_scenario'],2)
 def test_negative_spread_bid_valid(self):
  x=analyze_pair({**W,'bid':-40,'ask':-20}, {N['short_name']:N,F['short_name']:F},S,
                 spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
  self.assertEqual(x['gross_bid_ask_width_rub'],'20')
 def test_missing_fee_fails(self):
  bad={k:v for k,v in N.items() if k!='buy_sell_fee_rub'}
  with self.assertRaises(UnverifiedInput):analyze_pair(W, {N['short_name']:bad,F['short_name']:F}, S,spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
 def test_nonmatching_multiplier_fails(self):
  bad={**F,'stepprice_rub':11}
  with self.assertRaises(UnverifiedInput):analyze_pair(W, {N['short_name']:N,F['short_name']:bad}, S,spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
 def test_no_fabricated_margin(self):
  bad={k:v for k,v in F.items() if k!='margin_rub'}
  with self.assertRaises(UnverifiedInput):analyze_pair(W,{N['short_name']:N,F['short_name']:bad},S,spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
 def test_no_account_fee_source_fails(self):
  s=PublicCostScenario(Decimal('0.0001'),Decimal(0),Decimal(2000),Decimal(10000),'')
  with self.assertRaises(UnverifiedInput):s.validate()
 def test_untrusted_model_label_cannot_be_changed(self):
  s=PublicCostScenario(Decimal('0.0001'),Decimal(0),Decimal(2000),Decimal(10000),'S','LIVE_PROFIT')
  with self.assertRaises(UnverifiedInput):s.validate()
 def test_explicit_maker_cost(self):
  expensive=PublicCostScenario(Decimal('0.0001'),Decimal('1.5'),Decimal(2000),Decimal(10000),'S')
  a=analyze_pair(W,{N['short_name']:N,F['short_name']:F},S,spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
  b=analyze_pair(W,{N['short_name']:N,F['short_name']:F},expensive,spread_captured_utc='2026-10-13T13:11:28Z',futures_captured_utc='2026-10-13T13:17:08Z')
  self.assertEqual(Decimal(a['ideal_two_maker_upper_rub'])-Decimal(b['ideal_two_maker_upper_rub']),Decimal('3.0'))
 def test_trade_activity_not_fill(self):
  r=scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[W]},
                         {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]},S)
  self.assertEqual(r['matched'],1)
  self.assertEqual(r['maker_upper_positive_with_100_trades'],1)
  self.assertTrue(r['not_executable'])
  self.assertEqual(r['proven_profit_cycles'],0)
  self.assertIsNone(r['realized_profit_rub'])
 def test_no_crossed(self):
  r=scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[{**W,'bid':900,'ask':800}]},
                        {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]},S)
  self.assertEqual(r['matched'],0)
 def test_duplicate_future_is_invalid(self):
  with self.assertRaises(UnverifiedInput):scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[W]},
                         {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,N,F]},S)
 def test_no_futures_quote(self):
  r=scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[W]},
                        {'sample_utc':'2026-10-13T13:17:08Z','rows':[{**N,'bid':None},F]},S)
  self.assertEqual(r['matched'],0)
 def test_unknown_counterfeit_trade_day_rejected(self):
  r=scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[{**W,'trades':None}]},
                        {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]},S)
  self.assertEqual(r['matched'],0)
 def test_input_time_requires_offset(self):
  r=scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28Z','rows':[W]},
                        {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]},S)
  self.assertEqual(r['matched'],1)
  with self.assertRaises(UnverifiedInput):scan_public_snapshot({'retrieved_utc':'2026-10-13T13:11:28','rows':[W]},
                        {'sample_utc':'2026-10-13T13:17:08Z','rows':[N,F]},S)

if __name__=='__main__': unittest.main()