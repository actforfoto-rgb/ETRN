"""NORM03B FIL forward research watch: chronology and no fake fills."""
import sys,unittest
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import m9_fil_forward_watch as m

SOURCE=1792757459400
BOOK=1792757457852
def anchor():
 return {"first_book_exchange_utc_ms":[BOOK,BOOK],
    "source_entry_was_filled":False,"cash_account_rub_untouched":50000,
    "inverse_contract_count":"10","linear_contract_count":"1012",
    "linear_base_qty":"101.2","inverse_usd_face_total":"100",
    "inverse_short_bid_entry_usd":"0.988",
    "linear_long_ask_entry_usdt":"0.9895475296442687747035573123"}

def book(bid,ask,clock):
 return {"bids":[(D(str(bid)),D("999999"))],
         "asks":[(D(str(ask)),D("999999"))],
         "exchange_ms":clock}

class Validation(unittest.TestCase):
 def test_only_later_realized_rates(self):
  iv=[{"fundingTime":str(SOURCE-1000),"realizedRate":".05"},
      {"fundingTime":str(SOURCE+3000),"realizedRate":".0001"}]
  li=[{"fundingTime":str(SOURCE-1000),"realizedRate":".05"},
      {"fundingTime":str(SOURCE+3000),"realizedRate":"-.0001"}]
  h=m.paid_after_start(iv,li,SOURCE,SOURCE+4000)
  self.assertEqual(len(h["pairs"]),1)
  self.assertEqual(h["pairs"][0][0],SOURCE+3000)
 def test_no_same_day_future_lookahead(self):
  iv=[{"fundingTime":str(SOURCE+3000),"realizedRate":".0001"}]
  li=[{"fundingTime":str(SOURCE+3000),"realizedRate":"-.0001"}]
  self.assertEqual(m.paid_after_start(iv,li,SOURCE,SOURCE+2000)["pairs"],[])
 def test_reference_mark_unfilled(self):
  a=anchor()
  ib=book(".988",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  r=m.hypothetical_exit(a,ib,lb,{"pairs":[]})
  self.assertEqual(r["state"],"RESEARCH_MARK_UNFILLED")
  self.assertFalse(r["actual_trade_filled"])
  self.assertLess(D(r["net_current_mark_to_exit_usdt_proxy"]),0)
 def test_post_entry_funding_only(self):
  a=anchor()
  ib=book(".988",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  first=m.hypothetical_exit(a,ib,lb,{"pairs":[]})
  second=m.hypothetical_exit(a,ib,lb,{"pairs":[(SOURCE+2000,".0001","-.0001")]})
  self.assertGreater(D(second["net_current_mark_to_exit_usdt_proxy"]),D(first["net_current_mark_to_exit_usdt_proxy"]))
  self.assertEqual(second["post_entry_settled_funding_count"],1)
 def test_old_quotes_rejected(self):
  a=anchor()
  x=m.hypothetical_exit(a,book(".988",".989",BOOK),book(".989",".99",SOURCE+3000),{"pairs":[]})
  self.assertEqual(x["state"],"NOT_NEWER_THAN_ENTRY_QUOTE")
 def test_desync_rejected(self):
  a=anchor()
  x=m.hypothetical_exit(a,book(".988",".989",SOURCE+3000),book(".989",".99",SOURCE+9000),{"pairs":[]})
  self.assertEqual(x["state"],"DESYNCHRONIZED_MARK")
 def test_post_exit_funding_cannot_be_credited_against_old_book(self):
  a=anchor()
  a["first_observed_utc_ms"]=SOURCE
  ib=book(".988",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  future=[(SOURCE+4000,".005","-.005")]
  r=m.hypothetical_exit(a,ib,lb,{"pairs":future},asof_ms=SOURCE+4000)
  self.assertEqual(r["post_entry_settled_funding_count"],0)
  self.assertEqual(r["funding_points_excluded_after_exit"],1)
  self.assertEqual(D(r["post_entry_funding_usdt_proxy"]),D("0"))
 def test_stale_exit_books_block_mark(self):
  a=anchor()
  ib=book(".988",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  r=m.hypothetical_exit(a,ib,lb,{"pairs":[]},asof_ms=SOURCE+23000)
  self.assertEqual(r["state"],"STALE_OR_FUTURE_EXIT_BOOK")
 def test_future_dated_exit_books_block_mark(self):
  a=anchor()
  ib=book(".988",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  r=m.hypothetical_exit(a,ib,lb,{"pairs":[]},asof_ms=SOURCE+1000)
  self.assertEqual(r["state"],"STALE_OR_FUTURE_EXIT_BOOK")
 def test_anchor_timestamp_excludes_pre_signal_settlement(self):
  a=anchor()
  a["first_observed_utc_ms"]=SOURCE+4000
  ib=book(".988",".989",SOURCE+5000)
  lb=book(".989",".990",SOURCE+5200)
  r=m.hypothetical_exit(a,ib,lb,{"pairs":[(SOURCE+2000,".001","0")]},asof_ms=SOURCE+5300)
  self.assertEqual(r["post_entry_settled_funding_count"],0)
  self.assertEqual(r["funding_points_excluded_pre_anchor"],1)
 def test_crossed_book_is_not_exit(self):
  a=anchor()
  ib=book(".995",".989",SOURCE+3000)
  lb=book(".989",".990",SOURCE+3200)
  r=m.hypothetical_exit(a,ib,lb,{"pairs":[]},asof_ms=SOURCE+3500)
  self.assertEqual(r["state"],"CROSSED_EXIT_BOOK")
 def test_no_real_order_call(self):
  self.assertFalse(hasattr(m,"send_order"))
  self.assertFalse(hasattr(m,"place_order"))

if __name__=="__main__":unittest.main()
