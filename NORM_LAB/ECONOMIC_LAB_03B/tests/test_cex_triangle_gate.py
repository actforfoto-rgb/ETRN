"""Offline economic and causal gates for OKX three-leg currency loops."""
import sys,unittest
from pathlib import Path
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cex_triangle_gate as m

def mm(symbol,base,quote,bid,ask,bid_sz=10000,ask_sz=10000,t=200000):
    return {"symbol":symbol,"base":base,"quote":quote,
            "min":D(".001"),"lot":D(".001"),
            "bid":D(str(bid)),"ask":D(str(ask)),
            "bid_sz":D(str(bid_sz)),"ask_sz":D(str(ask_sz)),"exchange_ms":t}

def triangle(same=False):
    # USDT buys A -> A buys B (B-A market) -> sell B for USDT.
    return {
      "A-USDT":mm("A-USDT","A","USDT","9.9","10"),
      "B-A":mm("B-A","B","A","1.9","2"),
      "B-USDT":mm("B-USDT","B","USDT","21","21.1"),
    }

class Triangle(unittest.TestCase):
    def test_public_only(self):
        self.assertRaises(ValueError,m.fetch,"https://www.okx.com/api/v5/trade/order")
        self.assertFalse(hasattr(m,"send_order"))
        self.assertFalse(hasattr(m,"open_position"))

    def test_three_legs_only_and_identity(self):
        paths=m.triangles(triangle())
        self.assertEqual(len(paths),2)
        self.assertTrue(all(len(p)==3 for p in paths))
        self.assertEqual({a for a,b,_,_ in paths[0]}|{paths[0][-1][1]}, {"USDT","A","B"})

    def test_exact_three_taker_fees(self):
        markets=triangle()
        paths=m.triangles(markets)
        selected=next(p for p in paths if p[0][1]=="A")
        r=m.evaluate(selected,markets,"100")
        self.assertIsNotNone(r)
        self.assertGreater(D(r["net_usdt_conservative"]),0)
        self.assertEqual(r["paper_fills"],0)
        self.assertTrue(r["requires_three_fill_confirmation"])
        # buy A: 10 minus fee, buy B: floor to lots and reduce fee, sell B:
        self.assertLess(D(r["end_usdt_conservative"]),D("105"))

    def test_second_direction_and_fees_can_be_negative(self):
        markets=triangle()
        paths=m.triangles(markets)
        selected=next(p for p in paths if p[0][1]=="B")
        r=m.evaluate(selected,markets,"100")
        self.assertLess(D(r["net_usdt_conservative"]),0)

    def test_price_and_depth(self):
        markets=triangle()
        markets["B-USDT"]["bid_sz"]=D(".5")
        selected=next(p for p in m.triangles(markets) if p[0][1]=="A")
        self.assertIsNone(m.evaluate(selected,markets,"100"))

    def test_min_quantity(self):
        markets=triangle()
        markets["A-USDT"]["min"]=D("20")
        selected=next(p for p in m.triangles(markets) if p[0][1]=="A")
        self.assertIsNone(m.evaluate(selected,markets,"100"))

    def test_quote_clock_skew(self):
        markets=triangle()
        markets["B-A"]["exchange_ms"]+=1700
        path=next(p for p in m.triangles(markets) if p[0][1]=="A")
        self.assertIsNone(m.evaluate(path,markets,"100"))

    def test_lot_rounding(self):
        self.assertEqual(m.quantized(D("10.019"),D(".01")),D("10.01"))
        self.assertRaises(ValueError,m.quantized,D("10"),D("0"))

    def test_sell_executes_at_bid(self):
        a=mm("A-USDT","A","USDT","10","11")
        self.assertEqual(m.hop(a,"A",D("1"),"SELL",fee=D(0)),D("10"))

    def test_buy_executes_at_ask(self):
        a=mm("A-USDT","A","USDT","10","11")
        self.assertEqual(m.hop(a,"USDT",D("11"),"BUY",fee=D(0)),D("1"))

    def test_malformed_market_data(self):
        defs=m.instruments_map([{"instType":"SPOT","state":"live","instId":"A-USDT","baseCcy":"A","quoteCcy":"USDT","minSz":"0.01","lotSz":"0.001"}])
        self.assertIn("A-USDT",defs)
        q,b=m.quote_map(defs,[{"instId":"A-USDT","ts":"2000","askPx":"10","bidPx":"9.9","askSz":"50","bidSz":"70"}],received=2100)
        self.assertIn("A-USDT",q)
        self.assertEqual(b,0)

    def test_stale_provider_rejected(self):
        defs=m.instruments_map([{"instType":"SPOT","state":"live","instId":"A-USDT","baseCcy":"A","quoteCcy":"USDT","minSz":"0.01","lotSz":"0.001"}])
        q,b=m.quote_map(defs,[{"instId":"A-USDT","ts":"1","askPx":"10","bidPx":"9.9","askSz":"50","bidSz":"70"}],received=10000)
        self.assertEqual(len(q),0)
        self.assertEqual(b,1)

    def test_unknown_or_suspended_instruments_rejected(self):
        l=[{"instType":"SPOT","state":"suspend","instId":"A-USDT","baseCcy":"A","quoteCcy":"USDT","minSz":"0.01","lotSz":"0.001"}]
        self.assertEqual(m.instruments_map(l),{})

    def test_large_profit_scenario_is_still_not_fill(self):
        mk=triangle()
        path=next(p for p in m.triangles(mk) if p[0][1]=="A")
        r=m.evaluate(path,mk,"100")
        self.assertEqual(r["status"],"UNEXECUTED_NONATOMIC_QUOTE_SCREEN")
        self.assertEqual(r["real_orders"],0)

if __name__=="__main__":
    unittest.main()
