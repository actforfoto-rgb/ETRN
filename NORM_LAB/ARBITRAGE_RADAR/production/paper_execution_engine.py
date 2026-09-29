from __future__ import annotations

import json, time, uuid
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"paper_state"
STATE.mkdir(parents=True,exist_ok=True)

@dataclass
class Leg:
    venue:str
    symbol:str
    side:str
    qty:float
    entry_price:float
    current_price:float
    filled_qty:float=0.0
    fee_paid:float=0.0
    status:str="NEW"

@dataclass
class PairPosition:
    position_id:str
    strategy:str
    opened_ms:int
    leg_a:Leg
    leg_b:Leg
    status:str="OPENING"
    realized_pnl:float=0.0
    funding_pnl:float=0.0
    emergency_reason:str=""

class PaperExecutionEngine:
    """
    Venue-agnostic two-leg state machine.
    No real orders. It models the exact states the live engine will need.
    """
    MAX_UNHEDGED_MS=5000

    def __init__(self,name:str):
        self.path=STATE/f"{name}.json"
        self.positions=self._load()

    def _load(self):
        if not self.path.exists():return {}
        raw=json.loads(self.path.read_text(encoding="utf-8"))
        out={}
        for pid,p in raw.items():
            p["leg_a"]=Leg(**p["leg_a"]);p["leg_b"]=Leg(**p["leg_b"])
            out[pid]=PairPosition(**p)
        return out

    def _save(self):
        raw={}
        for pid,p in self.positions.items():
            d=asdict(p);raw[pid]=d
        self.path.write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding="utf-8")

    def open_pair(self,strategy,leg_a:Leg,leg_b:Leg):
        pid=str(uuid.uuid4())
        p=PairPosition(position_id=pid,strategy=strategy,opened_ms=int(time.time()*1000),
                       leg_a=leg_a,leg_b=leg_b,status="OPENING")
        self.positions[pid]=p
        # PAPER fill model: both legs must fill in same state transition.
        self._fill_leg(p.leg_a)
        self._fill_leg(p.leg_b)
        if p.leg_a.status=="FILLED" and p.leg_b.status=="FILLED":
            p.status="HEDGED"
        else:
            p.status="EMERGENCY_HEDGE"
            p.emergency_reason="PAIR_NOT_FULLY_FILLED"
        self._save()
        return p

    def _fill_leg(self,leg:Leg):
        # Real engine replaces this with venue order routing + partial-fill loop.
        leg.filled_qty=leg.qty
        leg.status="FILLED"

    def mark(self,pid,price_a,price_b):
        p=self.positions[pid]
        p.leg_a.current_price=price_a;p.leg_b.current_price=price_b
        self._save()

    def add_funding(self,pid,amount):
        self.positions[pid].funding_pnl+=amount
        self._save()

    def close_pair(self,pid,exit_a,exit_b,fee_a=0.0,fee_b=0.0):
        p=self.positions[pid]
        def pnl(leg,exit_price):
            sign=1 if leg.side.upper()=="BUY" else -1
            return sign*(exit_price-leg.entry_price)*leg.qty
        p.realized_pnl=(pnl(p.leg_a,exit_a)+pnl(p.leg_b,exit_b)
                        +p.funding_pnl-fee_a-fee_b-p.leg_a.fee_paid-p.leg_b.fee_paid)
        p.status="CLOSED"
        p.leg_a.current_price=exit_a;p.leg_b.current_price=exit_b
        self._save()
        return p

    def reconcile(self,venue_truth:dict):
        """
        venue_truth: {(venue,symbol): signed_qty}
        Returns mismatches. Live engine uses this before every order cycle.
        """
        mismatches=[]
        expected={}
        for p in self.positions.values():
            if p.status!="HEDGED":continue
            for leg in (p.leg_a,p.leg_b):
                sign=1 if leg.side.upper()=="BUY" else -1
                k=(leg.venue,leg.symbol)
                expected[k]=expected.get(k,0.0)+sign*leg.filled_qty
        keys=set(expected)|set(venue_truth)
        for k in keys:
            e=expected.get(k,0.0);a=float(venue_truth.get(k,0.0))
            if abs(e-a)>1e-12:
                mismatches.append({"venue":k[0],"symbol":k[1],"expected":e,"actual":a})
        return mismatches

if __name__=="__main__":
    print("PaperExecutionEngine ready; real order routing disabled.")
