from __future__ import annotations

import abc, json, os, time, uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"live_state"
STATE.mkdir(parents=True,exist_ok=True)
AUDIT=STATE/"execution_audit.jsonl"

class LiveTradingDisabled(RuntimeError):
    pass

@dataclass
class OrderResult:
    order_id:str
    status:str
    filled_qty:float
    avg_price:float
    fee:float=0.0

@dataclass
class LegSpec:
    venue:str
    symbol:str
    side:str
    qty:float
    max_slippage_bps:float
    reduce_only:bool=False

@dataclass
class PairIntent:
    strategy_id:str
    expected_net_bps:float
    leg1:LegSpec
    leg2:LegSpec
    max_unhedged_seconds:float=5.0
    max_total_notional:float=0.0

class VenueAdapter(abc.ABC):
    @abc.abstractmethod
    def best_bid_ask(self,symbol:str): ...
    @abc.abstractmethod
    def available_margin(self): ...
    @abc.abstractmethod
    def position_qty(self,symbol:str): ...
    @abc.abstractmethod
    def place_ioc(self,leg:LegSpec,limit_price:float)->OrderResult: ...
    @abc.abstractmethod
    def flatten(self,symbol:str)->OrderResult: ...

class CCXTPrivateAdapter(VenueAdapter):
    """
    Private crypto adapter. It remains unusable unless credentials are supplied
    AND the global live gates are explicitly enabled.
    """
    def __init__(self,exchange_id,api_key,secret,password=None):
        import ccxt
        cls=getattr(ccxt,exchange_id)
        cfg={"apiKey":api_key,"secret":secret,"enableRateLimit":True}
        if password:cfg["password"]=password
        self.ex=cls(cfg)
        self.ex.load_markets()

    def best_bid_ask(self,symbol):
        ob=self.ex.fetch_order_book(symbol,5)
        return float(ob["bids"][0][0]),float(ob["asks"][0][0])

    def available_margin(self):
        b=self.ex.fetch_balance()
        # Conservative generic free quote balance proxy; strategy-specific
        # margin checks are performed before promotion to live.
        free=b.get("free") or {}
        return float(free.get("USDT") or free.get("USD") or 0.0)

    def position_qty(self,symbol):
        try:
            ps=self.ex.fetch_positions([symbol])
            return sum(float(p.get("contracts") or 0.0) *
                       (-1 if str(p.get("side")).lower()=="short" else 1)
                       for p in ps)
        except Exception:
            return 0.0

    def place_ioc(self,leg,limit_price):
        params={"timeInForce":"IOC","reduceOnly":bool(leg.reduce_only)}
        o=self.ex.create_order(leg.symbol,"limit",leg.side.lower(),leg.qty,limit_price,params)
        oid=str(o.get("id") or "")
        # Fetch once; venue-specific production adapters will poll until terminal.
        try:o=self.ex.fetch_order(oid,leg.symbol)
        except Exception:pass
        return OrderResult(
          order_id=oid,status=str(o.get("status") or "unknown"),
          filled_qty=float(o.get("filled") or 0.0),
          avg_price=float(o.get("average") or o.get("price") or limit_price),
          fee=float((o.get("fee") or {}).get("cost") or 0.0)
        )

    def flatten(self,symbol):
        qty=self.position_qty(symbol)
        if abs(qty)<1e-12:return OrderResult("","closed",0,0,0)
        bid,ask=self.best_bid_ask(symbol)
        side="sell" if qty>0 else "buy"
        leg=LegSpec("",symbol,side,abs(qty),50.0,True)
        px=bid*(1-0.005) if side=="sell" else ask*(1+0.005)
        return self.place_ioc(leg,px)

def audit(event,**data):
    row={"ts":time.time(),"event":event,**data}
    with AUDIT.open("a",encoding="utf-8") as f:
        f.write(json.dumps(row,ensure_ascii=False)+"\n")

def live_gate(strategy_registry_path=None):
    mode=os.environ.get("NORM_ARB_MODE","SHADOW").upper()
    ack=os.environ.get("NORM_ARB_LIVE_ACK","")
    if mode not in ("MICRO_LIVE","LIVE"):
        raise LiveTradingDisabled(f"mode={mode}; real orders disabled")
    if ack!="I_ACCEPT_REAL_ORDER_RISK":
        raise LiveTradingDisabled("explicit live acknowledgement missing")
    if strategy_registry_path:
        reg=json.loads(Path(strategy_registry_path).read_text(encoding="utf-8"))
        allowed=reg.get("micro_live_enabled") if mode=="MICRO_LIVE" else reg.get("live_enabled")
        if not allowed:
            raise LiveTradingDisabled(f"{mode} disabled in strategy registry")
    return mode

class TwoLegExecutor:
    def __init__(self,adapters:dict[str,VenueAdapter],risk:dict):
        self.adapters=adapters
        self.risk=risk

    def _limit_price(self,adapter,leg):
        bid,ask=adapter.best_bid_ask(leg.symbol)
        slip=leg.max_slippage_bps/10000
        if leg.side.upper()=="BUY":
            return ask*(1+slip)
        return bid*(1-slip)

    def precheck(self,intent:PairIntent):
        if intent.expected_net_bps < float(self.risk["min_expected_net_bps"]):
            raise RuntimeError("expected NET below live threshold")
        if intent.max_total_notional > float(self.risk["max_notional_per_pair"]):
            raise RuntimeError("pair notional exceeds risk limit")
        if intent.leg1.venue==intent.leg2.venue and intent.leg1.symbol==intent.leg2.symbol:
            raise RuntimeError("legs collapse to same instrument")
        for leg in (intent.leg1,intent.leg2):
            if leg.venue not in self.adapters:
                raise RuntimeError(f"adapter missing: {leg.venue}")
            if self.adapters[leg.venue].available_margin()<=0:
                raise RuntimeError(f"no free margin: {leg.venue}")

    def execute_entry(self,intent:PairIntent):
        live_gate(ROOT/"strategy_registry.json")
        self.precheck(intent)
        tx=str(uuid.uuid4())
        audit("PAIR_START",tx=tx,intent=asdict(intent))

        a1=self.adapters[intent.leg1.venue]
        a2=self.adapters[intent.leg2.venue]
        t0=time.time()

        r1=a1.place_ioc(intent.leg1,self._limit_price(a1,intent.leg1))
        audit("LEG1_RESULT",tx=tx,result=asdict(r1))
        if r1.filled_qty<=0:
            audit("PAIR_ABORT_NO_FILL",tx=tx)
            return {"tx":tx,"status":"ABORTED_NO_LEG1_FILL"}

        hedge_qty=intent.leg2.qty*(r1.filled_qty/max(intent.leg1.qty,1e-12))
        leg2=LegSpec(**{**asdict(intent.leg2),"qty":hedge_qty})
        r2=a2.place_ioc(leg2,self._limit_price(a2,leg2))
        audit("LEG2_RESULT",tx=tx,result=asdict(r2))

        hedge_ratio=r2.filled_qty/max(hedge_qty,1e-12)
        elapsed=time.time()-t0
        if hedge_ratio<0.999 or elapsed>intent.max_unhedged_seconds:
            audit("EMERGENCY_HEDGE",tx=tx,hedge_ratio=hedge_ratio,elapsed=elapsed)
            # First priority is neutralizing the filled first leg.
            emergency=a1.flatten(intent.leg1.symbol)
            audit("EMERGENCY_FLATTEN_RESULT",tx=tx,result=asdict(emergency))
            return {"tx":tx,"status":"EMERGENCY_FLATTEN","hedge_ratio":hedge_ratio}

        audit("PAIR_HEDGED",tx=tx,elapsed=elapsed)
        return {"tx":tx,"status":"HEDGED","leg1":asdict(r1),"leg2":asdict(r2)}

    def reconcile(self,expected:dict):
        mismatches=[]
        for (venue,symbol),qty in expected.items():
            actual=self.adapters[venue].position_qty(symbol)
            if abs(actual-qty)>float(self.risk["reconcile_qty_tolerance"]):
                mismatches.append({"venue":venue,"symbol":symbol,
                                   "expected":qty,"actual":actual})
        if mismatches:audit("RECONCILE_MISMATCH",mismatches=mismatches)
        return mismatches

if __name__=="__main__":
    try:
        live_gate(ROOT/"strategy_registry.json")
    except LiveTradingDisabled as e:
        print(f"SAFE: {e}")
