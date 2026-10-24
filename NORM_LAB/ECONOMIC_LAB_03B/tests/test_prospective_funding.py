"""Deterministic R22 prospective locked protocol tests; no network/no orders."""
import sys
from pathlib import Path
import unittest
from decimal import Decimal as D

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prospective_funding as p
import prior_artifact_lookup as lookup


def fresh_scan(now_ms=1000000, pair="TEST_USDT", funding_ms=4600000, net="0.08"):
    econ={
        "status":"FORWARD_WATCH_NOT_FILL","contract":pair,
        "contract_count":90,"qty_base":"90",
        "spot_entry_usdt":"90.09",
        "future_short_notional_usdt":"90.00",
        "quoted_single_payment_usdt":"0.54",
        "next_scheduled_funding_utc_s":funding_ms//1000,
        "clock":{"exchange_ms":[now_ms-500,now_ms-200]},
        "one_funding_projected_execution_stress":{
            "opportunity_cost_scenarios":[{
                "assumed_annual_alternative_yield":"0.14",
                "grid":[{"extra_cost_bp_each_leg":"5",
                         "central_net_after_extra_cost_usdt":net}]
            }]
        }
    }
    return {"results":[{"pair":pair,"asset":"TEST","economic":econ,
                        "source_digests":["first_book","second_book","fees","history"]}],
            "source_timestamp_utc_ms":now_ms,"paper_fills":0,"real_orders":0}


def later_observation(now_ms, event_s=4600, rate=".005", settled=True):
    sb={"update":now_ms-600,
        "bids":[["1.020","400"]],"asks":[["1.021","400"]]}
    fb={"update":(now_ms-500)/1000,
        "bids":[{"p":"1.018","s":500}],"asks":[{"p":"1.019","s":500}]}
    data={
        "spot":sb,"future":fb,
        "history":[{"t":event_s,"r":rate}] if settled else [],
        "mark":[{"t":event_s-60,"o":"1.0","h":"1.02","l":"0.98","c":"1.00"}],
        "contract":{"name":"TEST_USDT","type":"direct",
                    "in_delisting":False,"status":"trading",
                    "quanto_multiplier":"1"}
    }
    recv={
        "spot":{"received_ms":now_ms-200,"body_sha256":"hash_spot"},
        "future":{"received_ms":now_ms-200,"body_sha256":"hash_future"},
        "history":{"received_ms":now_ms-200,"body_sha256":"hash_history"},
        "mark":{"received_ms":now_ms-200,"body_sha256":"hash_mark"},
        "contract":{"received_ms":now_ms-200,"body_sha256":"hash_contract"}
    }
    return data,recv


class PrecommitTests(unittest.TestCase):
    def test_entry_frozen_before_future_settlement_and_exact_exit_rule(self):
        now=1000000
        first=p.evolve(None,fresh_scan(now),now,100,source=lambda a:None)
        self.assertEqual(len(first["pending"]),1)
        ev=first["pending"][0]
        a=ev["anchor"]
        self.assertEqual(a["signal_run"],100)
        self.assertEqual(a["funding_utc_s"],4600)
        self.assertEqual(a["exit_not_before_ms"],4660000)
        self.assertEqual(a["exit_deadline_ms"],4600000+50*60000)
        self.assertEqual(a["selection_status"],"POSITIVE_STRESSED_SIGNAL")
        self.assertEqual(a["owner_eligibility_verified"],False)
        self.assertFalse(a["actual_execution"])
        self.assertEqual(first["paper_fills"],0)
        self.assertEqual(first["virtual_cash_rub"],"50000")
        p.validate_state(first)

    def test_negative_control_frozen_and_not_called_trade(self):
        now=1000000
        scan=fresh_scan(now,net="-0.2")
        first=p.evolve(None,scan,now,100)
        a=first["pending"][0]["anchor"]
        self.assertEqual(a["selection_status"],"NEGATIVE_CONTROL_NOT_ENTRY")
        self.assertEqual(first["real_orders"],0)

    def test_unresolved_frozen_signal_cannot_be_requoted_as_fresh_signal(self):
        now=1000000
        scan=fresh_scan(now)
        first=p.evolve(None,scan,now,100)
        second=p.evolve(first,scan,now+1000,101)
        self.assertEqual(len(second["pending"]),1)
        self.assertEqual(second["pending"][0]["event_id"],
                         first["pending"][0]["event_id"])
        self.assertEqual(second["chain_status"],"VERIFIED_PREVIOUS_ARTIFACT")

    def test_before_precommitted_exit_never_looks_at_market(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        def never_fetch(anchor):
            raise AssertionError("premature quote read")
        second=p.evolve(first,{},4650000,101,source=never_fetch)
        self.assertEqual(len(second["pending"]),1)
        self.assertEqual(len(second["finished"]),0)

    def test_first_valid_future_quote_is_irrevocably_resolved(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        now=4780500
        second=p.evolve(first,{},now,101,
                        source=lambda a:later_observation(now))
        self.assertEqual(len(second["pending"]),0)
        self.assertEqual(len(second["finished"]),1)
        saved=second["finished"][0]["result"]
        self.assertEqual(saved["status"],"FULL_HISTORICAL_L2_QUOTE_PROXY_NOT_A_FILL")
        self.assertEqual(saved["paid_funding_rate"],".005")
        self.assertTrue(saved["central_positive_after_stress"])
        self.assertFalse(saved["owner_eligible"])
        self.assertEqual(saved["independently_verified_fills"],0)
        third=p.evolve(second,{},now+120000,102,
                       source=lambda a:(_ for _ in ()).throw(AssertionError("repriced")))
        self.assertEqual(third["finished"][0]["result"],saved)
        self.assertEqual(third["paper_fills"],0)

    def test_missing_paid_rate_keeps_candidate_pending_not_profitable(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        now=4780500
        second=p.evolve(first,{},now,101,source=lambda a:later_observation(now,settled=False))
        self.assertEqual(len(second["pending"]),1)
        self.assertEqual(second["pending"][0]["attempted_observations"][0]["status"],
                         "PAID_RATE_NOT_YET_AVAILABLE")
        self.assertEqual(len(second["finished"]),0)

    def test_missed_window_is_failure_not_virtual_fill(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        after=4600000+50*60000+1000
        second=p.evolve(first,{},after,101,source=lambda a:None)
        self.assertEqual(second["finished"][0]["state"],"MISSED_DEADLINE_NO_EXECUTABLE_QUOTE")
        self.assertNotIn("result",second["finished"][0])
        self.assertEqual(second["virtual_equity_rub"],"50000")

    def test_unsynchronized_books_refuse_hindsight_outcome(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        a=first["pending"][0]["anchor"];now=4780500
        data,receipts=later_observation(now)
        data["future"]["update"]=(now-10000)/1000
        v=p.prospective_exit(a,data,receipts,now)
        self.assertEqual(v["status"],"INVALID_L2_TIME_OR_SKEW")

    def test_lookahead_future_settlement_after_actual_quote_cannot_count(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        a=first["pending"][0]["anchor"];now=4780500
        data,receipts=later_observation(now,settled=False)
        data["history"]=[{"t":4600+3600,"r":".1"}]
        v=p.prospective_exit(a,data,receipts,now)
        self.assertEqual(v["status"],"PAID_RATE_NOT_YET_AVAILABLE")

    def test_source_anchor_or_state_edit_detected(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        first["pending"][0]["anchor"]["spot_buy_cost_usdt"]="1"
        self.assertRaises(ValueError,p.validate_state,first)
        second=p.evolve(None,fresh_scan(),1000000,100)
        second["virtual_cash_rub"]="0"
        self.assertRaises(ValueError,p.validate_state,second)

    def test_no_self_replay(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        self.assertRaises(ValueError,p.evolve,first,{},2000000,100)

    def test_negative_rates_are_numerically_debited(self):
        first=p.evolve(None,fresh_scan(),1000000,100)
        a=first["pending"][0]["anchor"];now=4780500
        data,receipts=later_observation(now,rate="-.005")
        v=p.prospective_exit(a,data,receipts,now)
        self.assertLess(D(v["funding_using_one_minute_pre_settlement_mark_usdt"]),0)
        self.assertLess(D(v["net_after_5bp_and_14pct_usdt"]),0)

    def test_no_trade_path_implemented(self):
        self.assertFalse(hasattr(p,"place_order"))
        self.assertFalse(hasattr(p,"send_order"))
        self.assertFalse(hasattr(p,"submit_order"))


class PriorArtifactTests(unittest.TestCase):
    def test_select_only_older_successful_with_unchanged_artifact(self):
        runs=[{"id":200,"status":"completed","conclusion":"success"},
              {"id":160,"status":"completed","conclusion":"failure"},
              {"id":150,"status":"completed","conclusion":"success"},
              {"id":100,"status":"completed","conclusion":"success"}]
        by_run={150:[{"id":900,"name":lookup.ARTIFACT_NAME,"expired":False,
                       "size_in_bytes":320,"created_at":"2026-10-24"}],
                200:[{"id":901,"name":lookup.ARTIFACT_NAME,"expired":False,
                       "size_in_bytes":320}]}
        x=lookup.choose_previous("x/y",200,runs,by_run)
        self.assertEqual(x["previous_run_id"],150)
        self.assertEqual(x["previous_artifact_id"],900)

    def test_first_run_and_missing_archive_must_be_distinct(self):
        self.assertEqual(lookup.choose_previous("x/y",100,[],{})["status"],
                         "FIRST_RUN_NO_HISTORY")
        fail=lookup.choose_previous("x/y",200,[
            {"id":100,"status":"completed","conclusion":"success"}],{})
        self.assertEqual(fail["status"],"PREVIOUS_SUCCESS_WITHOUT_VALID_ARTIFACT_FAIL_CLOSED")

    def test_future_run_artifact_never_used(self):
        x=lookup.choose_previous("x/y",20,[
            {"id":100,"status":"completed","conclusion":"success"}],{
                100:[{"id":2,"name":lookup.ARTIFACT_NAME,"expired":False,
                      "size_in_bytes":90}]})
        self.assertIsNone(x["previous_run_id"])


if __name__=="__main__":
    unittest.main()
