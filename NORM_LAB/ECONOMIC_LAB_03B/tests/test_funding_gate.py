import sys
from pathlib import Path
import unittest
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import funding_gate as f
import m2_irys_fixed_watch as irys
import json

def spot_book(ts=1_000_000, ask="1.001", bid="1.000", qty="2000"):
    return {"update":ts,"asks":[[ask,qty]],"bids":[[bid,qty]]}

def fut_book(ts=1000.100, ask="1.0006", bid="1.0005", n=3000):
    return {"update":ts,"bids":[{"p":bid,"s":n}],"asks":[{"p":ask,"s":n}]}

def contract(rate="0.001",mult="1",typ="direct",interval=28800):
    return {"name":"TEST_USDT","type":typ,"quanto_multiplier":mult,
        "order_size_min":1,"funding_rate":rate,"funding_interval":interval,
        "funding_next_apply":2000,"in_delisting":False}

class FundingGateTests(unittest.TestCase):
    def test_ticker_prefers_documented_next_funding_rate(self):
        fut=[{"contract":"TEST_USDT","funding_rate":"0.00001",
              "funding_rate_next":"0.002","highest_bid":"1.005","quanto_multiplier":"1"}]
        spot=[{"currency_pair":"TEST_USDT","lowest_ask":"1"}]
        result=f.shortlist(fut,spot)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["funding_rate"],D("0.002"))
        self.assertEqual(result[0]["ticker_rate_field"],"funding_rate_next")

    def test_instantly_due_funding_is_not_projected_profit(self):
        ct=contract(rate=".01")
        ct["funding_next_apply"]=1001  # less than 30s after timestamp
        out=f.economic(spot_book(),fut_book(),ct,[],1_000_200,1_000_200)
        self.assertEqual(out["status"],"FUNDING_SCHEDULE_NOT_ACTIONABLE")
        self.assertFalse("quoted_single_payment_usdt" in out)

    def test_already_settled_funding_never_credited(self):
        ct=contract(rate=".01")
        ct["funding_next_apply"]=999
        out=f.economic(spot_book(),fut_book(),ct,[],1_000_200,1_000_200)
        self.assertEqual(out["status"],"FUNDING_SCHEDULE_NOT_ACTIONABLE")

    def test_implausibly_distant_next_settlement_rejected(self):
        ct=contract(rate=".01")
        ct["funding_next_apply"]=100_000
        out=f.economic(spot_book(),fut_book(),ct,[],1_000_200,1_000_200)
        self.assertEqual(out["status"],"FUNDING_SCHEDULE_NOT_ACTIONABLE")

    def test_suspended_contract_never_passes_economic_gate(self):
        ct=contract(rate=".01")
        ct["status"]="prelaunch"
        out=f.economic(spot_book(),fut_book(),ct,[],1_000_200,1_000_200)
        self.assertEqual(out["status"],"CONTRACT_NOT_TRADING")

    def irys_fixture(self, ts=1792786000000):
        original=json.loads(irys.ANCHOR_FILE.read_text(encoding="utf-8"))
        sb={"update":ts,"bids":[["0.1310","1000"]],"asks":[["0.1315","1000"]]}
        fb={"update":ts/1000,"bids":[{"p":"0.1309","s":20}],
            "asks":[{"p":"0.1316","s":20}]}
        return original,sb,fb

    def test_irys_fixed_forward_is_never_paper_fill(self):
        a,sb,fb=self.irys_fixture()
        tm=1792785600
        r=irys.evaluate(a,sb,fb,[{"t":tm,"r":"0.005845"}],
                        sb["update"]+200, sb["update"]+200,sb["update"]+450)
        self.assertEqual(r["status"],"CAUSAL_UNFILLED_COUNTERFACTUAL_MARK")
        self.assertEqual(r["post_signal_settled_points"],1)
        self.assertEqual(r["paper_fills"],0)
        self.assertEqual(r["real_orders"],0)
        self.assertEqual(r["virtual_cash_rub_unchanged"],50000)

    def test_irys_cannot_credit_settlement_after_exit_quote(self):
        a,sb,fb=self.irys_fixture()
        later=sb["update"]//1000+100
        r=irys.evaluate(a,sb,fb,[{"t":later,"r":"0.005"}],
                        sb["update"]+200,sb["update"]+200,sb["update"]+450)
        self.assertEqual(r["post_signal_settled_points"],0)
        self.assertEqual(r["post_exit_history_excluded"],1)
        self.assertEqual(D(r["post_signal_settled_rate_sum"]),D("0"))

    def test_irys_cannot_credit_history_before_signal(self):
        a,sb,fb=self.irys_fixture()
        old=int(a["first_observed_utc_ms"])//1000-100
        r=irys.evaluate(a,sb,fb,[{"t":old,"r":"0.05"}],
                        sb["update"]+200,sb["update"]+200,sb["update"]+450)
        self.assertEqual(r["post_signal_settled_points"],0)
        self.assertEqual(r["pre_signal_history_excluded"],1)

    def test_irys_rejects_old_or_desync_books(self):
        a,sb,fb=self.irys_fixture()
        stale=irys.evaluate(a,sb,fb,[],sb["update"]+20000,
                            sb["update"]+200,sb["update"]+20450)
        self.assertEqual(stale["status"],"STALE_OR_DESYNCHRONIZED_EXIT_BOOK")
        fb["update"]-=40
        desync=irys.evaluate(a,sb,fb,[],sb["update"]+200,
                             sb["update"]+200,sb["update"]+450)
        self.assertEqual(desync["status"],"STALE_OR_DESYNCHRONIZED_EXIT_BOOK")

    def test_irys_rejects_crossed_exit_book(self):
        a,sb,fb=self.irys_fixture()
        sb["bids"][0][0]="0.14"
        r=irys.evaluate(a,sb,fb,[],sb["update"]+200,
                        sb["update"]+200,sb["update"]+450)
        self.assertEqual(r["status"],"CROSSED_EXIT_BOOK")

    def test_irys_settlement_is_independent_of_exit_liquidity(self):
        a,sb,fb=self.irys_fixture()
        x=irys.settled_since_signal(a,[
            {"t":a["initial_next_payment_utc_s"],"r":"0.0015"},
            {"t":a["initial_next_payment_utc_s"],"r":"0.0015"},
            {"t":a["initial_next_payment_utc_s"]-20000,"r":"0.1"},
            {"t":a["initial_next_payment_utc_s"]+90000,"r":"0.1"},
            {"t":a["initial_next_payment_utc_s"]+100,"r":"0.5"}],
            a["initial_next_payment_utc_s"]*1000+2000)
        self.assertEqual(len(x),1)
        self.assertEqual(x[0]["realized_rate"],"0.0015")
        self.assertEqual(x[0]["settled_utc_ms"],a["initial_next_payment_utc_s"]*1000)

    def test_irys_settled_rate_before_observation_is_not_credited(self):
        a,sb,fb=self.irys_fixture()
        x=irys.settled_since_signal(a,[
            {"t":a["first_observed_utc_ms"]//1000-1,"r":"0.09"}],
            a["first_observed_utc_ms"]+60000)
        self.assertEqual(x,[])

    def test_mark_price_funding_revalues_actual_changing_notional(self):
        a,sb,fb=self.irys_fixture()
        st=a["initial_next_payment_utc_s"]
        payments=[{"settled_utc_ms":st*1000,"realized_rate":"0.01"},
                  {"settled_utc_ms":(st+14400)*1000,"realized_rate":"-0.002"}]
        marks=[{"t":st-60,"o":"0.101","h":"0.105","l":"0.095","c":"0.100"},
               {"t":st+14400-60,"o":"0.101","h":"0.105","l":"0.095","c":"0.100"}]
        r=irys.mark_replay_from_candles(a,payments,marks)
        self.assertEqual(r["status"],"COMPLETE_1M_PRE_SETTLEMENT_MARK_PROXY")
        self.assertEqual(D(r["total_funding_mark_close_proxy_usdt"]),D("0.56"))
        self.assertEqual(D(r["total_funding_minute_low_bound_proxy_usdt"]),D("0.518"))
        self.assertEqual(D(r["total_funding_minute_high_bound_proxy_usdt"]),D("0.602"))
        self.assertTrue(r["not_actual_funding_receipts"])

    def test_mark_price_after_settlement_is_forbidden_lookahead(self):
        a,sb,fb=self.irys_fixture()
        st=a["initial_next_payment_utc_s"]
        payments=[{"settled_utc_ms":st*1000,"realized_rate":"0.01"}]
        marks=[{"t":st,"o":"0.1","h":"0.12","l":"0.09","c":"0.11"}]
        r=irys.mark_replay_from_candles(a,payments,marks)
        self.assertEqual(r["status"],"INCOMPLETE_CANDLE_DATA_NO_ECONOMIC_PROMOTION")
        self.assertEqual(r["settlements_missing_mark_price"],[st*1000])

    def test_mark_replay_fail_closed_for_bad_ohlc(self):
        a,sb,fb=self.irys_fixture()
        st=a["initial_next_payment_utc_s"]
        payments=[{"settled_utc_ms":st*1000,"realized_rate":"0.01"}]
        marks=[{"t":st-60,"o":"0.12","h":"0.1","l":"0.09","c":"0.11"}]
        r=irys.mark_replay_from_candles(a,payments,marks)
        self.assertEqual(r["status"],"INCOMPLETE_CANDLE_DATA_NO_ECONOMIC_PROMOTION")

    def test_irys_contract_identity_and_multiplier_gate(self):
        a,sb,fb=self.irys_fixture()
        ct={"name":"IRYS_USDT","type":"direct","status":"trading",
            "in_delisting":False,"quanto_multiplier":"100"}
        self.assertIsNone(irys.contract_check(a,ct))
        ct["quanto_multiplier"]="1"
        self.assertEqual(irys.contract_check(a,ct),"CONTRACT_IDENTITY_CHANGED_OR_NOT_TRADING")
        ct["quanto_multiplier"]="100"
        ct["in_delisting"]=True
        self.assertEqual(irys.contract_check(a,ct),"CONTRACT_IDENTITY_CHANGED_OR_NOT_TRADING")

    def test_no_order_placement_possible(self):
        self.assertFalse(hasattr(f,"place_order"))
        self.assertFalse(hasattr(f,"send_order"))
        self.assertFalse(hasattr(f,"authorize_account"))

    def test_only_approved_public_endpoint(self):
        self.assertRaises(ValueError,f.public_json,"https://example.com/path")
        self.assertRaises(ValueError,f.public_json,"http://api.gateio.ws/api/v4/foo")

    def test_shortlist_requires_matching_spot_pair(self):
        fut=[{"contract":"CATL_USDT","funding_rate":"0.01","highest_bid":"44","quanto_multiplier":"0.1"},
             {"contract":"TEST_USDT","funding_rate":"0.001","highest_bid":"1.005","quanto_multiplier":"1"}]
        spot=[{"currency_pair":"TEST_USDT","lowest_ask":"1"}]
        result=f.shortlist(fut,spot)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["pair"],"TEST_USDT")

    def test_shortlist_denies_noncomparable_price(self):
        fut=[{"contract":"TEST_USDT","funding_rate":"0.01","highest_bid":"0.5","quanto_multiplier":"1"}]
        spot=[{"currency_pair":"TEST_USDT","lowest_ask":"1"}]
        self.assertEqual(f.shortlist(fut,spot),[])

    def test_shortlist_denies_low_funding_rate(self):
        fut=[{"contract":"TEST_USDT","funding_rate":"0.00001","highest_bid":"1","quanto_multiplier":"1"}]
        spot=[{"currency_pair":"TEST_USDT","lowest_ask":"1"}]
        self.assertEqual(f.shortlist(fut,spot),[])

    def test_contract_multiplier_and_ladders(self):
        sp=spot_book()
        fu=fut_book(n=500)
        ct=contract(mult="10")
        hist=[{"t":500,"r":"0.001"}]
        out=f.economic(sp,fu,ct,hist,1_000_120,1_000_140)
        self.assertEqual(out["status"],"FORWARD_WATCH_NOT_FILL")
        self.assertEqual(out["qty_base"],"90")
        self.assertEqual(out["contract_count"],9)
        self.assertLess(D(out["immediate_liquidation_net_usdt"]),0)

    def test_books_forward_time_rejected(self):
        s=spot_book(ts=2_020_000)
        p=fut_book(ts=2000)
        good,reason=f.valid_time(s,p,2_000_000,2_000_000)
        self.assertFalse(good)

    def test_stale_or_async_books_rejected(self):
        out=f.economic(spot_book(1_000_000),fut_book(1040),contract(),[],1_000_100,1_040_100)
        self.assertEqual(out["status"],"STALE_OR_DESYNCHRONIZED_BOOK")

    def test_malformed_non_direct_futures_rejected(self):
        self.assertIsNone(f.economic(spot_book(),fut_book(),contract(typ="inverse"),[],1_000_200,1_000_200))

    def test_hist_rates_sorted_no_future_or_duplicates(self):
        h=[{"t":910,"r":"0.004"},{"t":901,"r":"0.001"},
           {"t":910,"r":"0.004"},{"t":1200,"r":"0.9"}]
        self.assertEqual(f.rates(h,1_000_000),[(910,D(".004")),(901,D(".001"))])

    def test_one_payment_not_complete_cycle(self):
        sp=spot_book(); fu=fut_book()
        out=f.economic(sp,fu,contract(rate=".003"),[{"t":900,"r":"0.002"}],1_000_200,1_000_200)
        self.assertEqual(out["status"],"FORWARD_WATCH_NOT_FILL")
        self.assertEqual(out["trade_status"],"ZERO_ORDERS_NO_PAPER_FILL")
        self.assertFalse(out["owner_eligible"])
        self.assertEqual(D(out["four_taker_fees_usdt"]),D(out["four_taker_fees_usdt"]))

    def test_gate_future_levels_converted_by_contract_multiplier(self):
        lv=f.levels({"bids":[{"p":"2","s":3},{"p":"1.9","s":10}]},True,"bids")
        self.assertEqual(f.walk(lv,D("40"),D("10")),D("3")*D("10")*D("2")+D("10")*D("1.9"))

    def test_reject_less_than_contract_min_size(self):
        c=contract()
        c["order_size_min"]=1000
        out=f.economic(spot_book(),fut_book(),c,[],1_000_200,1_000_200)
        self.assertEqual(out["status"],"BELOW_MIN_CONTRACTS")

if __name__=="__main__":unittest.main()
