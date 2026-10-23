"""NORM03B M9 relative-value financing tests: no fill and exact inverse contract math."""
import sys,unittest
from pathlib import Path
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import inverse_linear_funding_gate as m

def contracts(base="FIL"):
    return {"inverse":{"instId":base+"-USD-SWAP","instType":"SWAP",
      "state":"live","ctMult":"1","ctType":"inverse","ctVal":"10",
      "ctValCcy":"USD","settleCcy":base,"lotSz":"1","minSz":"1"},
      "linear":{"instId":base+"-USDT-SWAP","instType":"SWAP",
      "state":"live","ctMult":"1","ctType":"linear","ctVal":".1",
      "ctValCcy":base,"settleCcy":"USDT","lotSz":"1","minSz":"1"}}

def book(bid,ask,t=2000,qty=30000):
    return {"bids":[(D(str(bid)),D(qty))],
            "asks":[(D(str(ask)),D(qty))],"exchange_ms":t,"received_ms":t+20}

def gate(short=True,t=2000):
    iv=book(".9892",".9896",t)
    li=book(".9906",".9907",t+100)
    ins=contracts()
    fi={"rate":D(".0001"),"time_ms":3000}
    fl={"rate":D("-.00006"),"time_ms":3000}
    return ins,li,iv,fi,fl

class M9Accept(unittest.TestCase):
    def test_no_write_endpoint(self):
        self.assertRaises(ValueError,m.url,"orders","FIL")
        self.assertRaises(ValueError,m.url,"books","FIL","EUR")
        self.assertFalse(hasattr(m,"place_order"))
        self.assertFalse(hasattr(m,"send_order"))

    def test_contract_identity_and_multiplier(self):
        data=m.live_contracts(list(contracts().values()))
        self.assertIn("FIL",data)
        self.assertEqual(data["FIL"]["inverse"]["settleCcy"],"FIL")
        self.assertEqual(data["FIL"]["linear"]["settleCcy"],"USDT")

    def test_reject_not_fungible_underlying(self):
        a=contracts()
        a["linear"]["ctValCcy"]="BTC"
        self.assertEqual(m.live_contracts(list(a.values())),{})

    def test_reject_suspended_exchange_contract(self):
        a=contracts();a["inverse"]["state"]="suspend"
        self.assertEqual(m.live_contracts(list(a.values())),{})

    def test_funding_history_join_sort(self):
        iv=[{"fundingTime":"3000","realizedRate":".0001"},
            {"fundingTime":"1000","realizedRate":"-.0001"},
            {"fundingTime":"2000","realizedRate":".0003"}]
        li=[{"fundingTime":"2000","realizedRate":"-.0001"},
            {"fundingTime":"1000","realizedRate":"-.0001"},
            {"fundingTime":"3000","realizedRate":".00001"}]
        h=m.historical_diff(iv,li)
        self.assertEqual(h["time_ms"],[3000,2000,1000])
        self.assertEqual(D(h["last3_sum_bp"]),D("4.9"))
        self.assertEqual(h["last30_positive_count"],2)

    def test_reverse_funding_short_linear(self):
        iv=[{"fundingTime":"1000","realizedRate":"-.001"}]
        li=[{"fundingTime":"1000","realizedRate":".001"}]
        h=m.historical_diff(iv,li,side="LONG_INVERSE")
        self.assertEqual(D(h["last3_sum_bp"]),D("20"))

    def test_futures_book_validity(self):
        a=m.parse_book([{"ts":"2000","bids":[["0.99","10"]],
           "asks":[["0.991","10"]]}],2050)
        self.assertEqual(a["exchange_ms"],2000)
        self.assertRaises(ValueError,m.parse_book,[{"ts":"10","bids":[[".99","1"]],"asks":[["1","1"]]}],10000)

    def test_deep_contracts_exact_sweep(self):
        b=book(".99","1.01",qty=30)
        self.assertEqual(m.sweep(b,"bids",D("10")),D(".99"))
        self.assertIsNone(m.sweep(b,"bids",D("31")))

    def test_exact_neutrality_and_net(self):
        ins,lin,inv,ri,rl=gate()
        z=m.economics("FIL",ins,lin,inv,ri,rl,[],[],D("100"))
        self.assertEqual(z["status"],"HEDGED_FUNDING_BASIS_SCREEN_NO_FILL")
        self.assertEqual(z["direction"],"SHORT_INVERSE")
        self.assertEqual(D(z["inverse_face_usd"]),D("100"))
        self.assertLess(D(z["delta_mismatch_pct"]),D(".75"))
        self.assertLess(D(z["net_immediate_exit_usdt_proxy"]),0)
        self.assertEqual(D(z["next_indicative_carry_usdt_proxy"]),D(".016"))
        self.assertEqual(z["paper_fills"],0)
        self.assertEqual(z["real_orders"],0)

    def test_delayed_two_books_not_valid(self):
        ins,lin,inv,ri,rl=gate()
        lin["exchange_ms"]+=2000
        self.assertEqual(m.economics("FIL",ins,lin,inv,ri,rl,[],[],D("100"))["status"],"BOOKS_DESYNCHRONIZED")

    def test_inverse_underhedge_rejected(self):
        ins,lin,inv,ri,rl=gate()
        ins["linear"]["lotSz"]="200"
        self.assertEqual(m.economics("FIL",ins,lin,inv,ri,rl,[],[],D("100"))["status"],"DELTA_MISMATCH_TOO_HIGH")

    def test_amount_under_minimum_contract(self):
        ins,lin,inv,ri,rl=gate()
        ins["inverse"]["minSz"]="20"
        self.assertEqual(m.economics("FIL",ins,lin,inv,ri,rl,[],[],D("100"))["status"],"MIN_INVERSE_CONTRACT_NOT_MET")

    def test_reverse_direction_handled(self):
        ins,lin,inv,ri,rl=gate()
        ri["rate"]=D("-.0002");rl["rate"]=D("0")
        result=m.economics("FIL",ins,lin,inv,ri,rl,[],[],D("100"))
        self.assertEqual(result["direction"],"LONG_INVERSE")
        self.assertGreater(D(result["next_indicative_carry_usdt_proxy"]),0)

    def test_rounded_contracts_exact(self):
        self.assertEqual(m.rounded(D("100.3"),D("1")),D(100))
        self.assertEqual(m.rounded(D("100.6"),D("1")),D(101))

if __name__=="__main__":unittest.main()
