"""RUB MOEX cash-and-carry economic gate: 12 offline tests, no account actions."""
import unittest,sys
from decimal import Decimal as D
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import moex_rub_cash_carry as m

def share(asset="ASTR",lotsize=1):
    return {"SECID":asset,"LOTSIZE":lotsize}
def quote(sec, bid, ask,clock="13:26:20"):
    return {"SECID":sec,"BID":bid,"OFFER":ask,
           "UPDATETIME":clock,"SYSTIME":"2026-10-23 13:41:20"}
def future(asset="ASTR",lot=1,bid=120,margin=50,expiry="2026-12-17",clock="13:26:20"):
    f={"SECID":"ASZ6","ASSETCODE":asset,"SHORTNAME":asset+"-12.26",
       "LOTVOLUME":lot,"LASTTRADEDATE":expiry,
       "INITIALMARGIN":margin,"MINSTEP":1,"STEPPRICE":1,"BUYSELLFEE":1}
    return f,quote("ASZ6",bid,bid+1,clock)
class Accept(unittest.TestCase):
    def test_only_whitelisted_http_sources(self):
        self.assertRaises(ValueError,m.pull,"https://example.com/a")
        self.assertFalse(hasattr(m,"send_order"))
    def test_matching_asset(self):
        f,q=future()
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"SCREENED")
        self.assertEqual(x["asset"],"ASTR")
        self.assertTrue(D(x["net_after_cost_of_capital_rub"]["0.14"])>0)
        self.assertEqual(x["paper_fill"],False)
    def test_identical_ticker_is_not_identical_underlying(self):
        f,q=future("GOLD",bid=4000,margin=1000)
        x,reason=m.screen(share("GOLD"),quote("GOLD",2.6,2.7),f,q,date(2026,10,23))
        self.assertIsNone(x);self.assertEqual(reason,"KNOWN_UNDERLYING_COLLISION")
    def test_incompatible_parity_skipped(self):
        f,q=future(bid=1000)
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"ECONOMICALLY_INCOMPARABLE_UNDERLYING")
    def test_lot_constraints(self):
        f,q=future(lot=25)
        x,reason=m.screen(share(lotsize=10),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"SHARE_LOT_INCOMPATIBLE")
    def test_capital_ceiling(self):
        f,q=future(lot=500,margin=10000,bid=51000)
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"CASH_OR_CAPITAL_LIMIT")
    def test_future_expiry(self):
        f,q=future(expiry="2026-10-22")
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"DATE_EXPIRED_OR_TOO_DISTANT")
    def test_late_clock_reject(self):
        f,q=future(clock="13:25:00")
        x,reason=m.screen(share(),quote("ASTR",99,100,clock="13:26:20"),f,q,date(2026,10,23))
        self.assertEqual(reason,"SOURCE_EXCHANGE_CLOCK_MISMATCH")
    def test_no_price_quotes(self):
        f,q=future()
        x,reason=m.screen(share(),quote("ASTR",0,0),f,q,date(2026,10,23))
        self.assertEqual(reason,"BAD_QUOTES")
    def test_requires_matching_prefix(self):
        f,q=future();f["SHORTNAME"]="GOLD-12.26"
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertEqual(reason,"FUTURE_SHORT_NAME_MISMATCH")
    def test_margin_case_no_proven_executability(self):
        f,q=future()
        x,reason=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertFalse(x["settlement_type_verified"])
        self.assertFalse(x["broker_client_trading_tariff_verified"])
        self.assertFalse(x["economic_underlying_identity_verified_by_contract_id"])
        self.assertTrue(x["source_quotes_delayed"])
    def test_realistic_financing_decreases_profit(self):
        f,q=future()
        x,_=m.screen(share(),quote("ASTR",99,100),f,q,date(2026,10,23))
        self.assertGreater(D(x["net_after_cost_of_capital_rub"]["0.14"]),
                           D(x["net_after_cost_of_capital_rub"]["0.18"]))
if __name__=="__main__":unittest.main()
