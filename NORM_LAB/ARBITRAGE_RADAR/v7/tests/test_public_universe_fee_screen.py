import unittest
from decimal import Decimal
from public_universe_fee_screen import (PublicCostScenario, analyze_pair, scan_public_snapshot, UnverifiedInput)

S=PublicCostScenario(Decimal('0.0001'),Decimal('0'),Decimal('2000'),Decimal('10000'),'FINAM_PUBLIC_26_03_STRATEG_ASSUMED')
N={'symbol':'W4X6','short_name':'WHEAT-11.26','stepprice_rub':10,'minstep':10,'margin_rub':1482.83,'bid':11520,'ask':11530,'buy_sell_fee_rub':1.53}
F={'symbol':'W4Z6','short_name':'WHEAT-12.26','stepprice_rub':10,'minstep':10,'margin_rub':1571.23,'bid':12660,'ask':12670,'buy_sell_fee_rub':1.69}
W={'symbol':'WHEAT-11.26-12.26','bid':860,'ask':890,'trades':102}

class MarketPrescreen(unittest.TestCase):
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