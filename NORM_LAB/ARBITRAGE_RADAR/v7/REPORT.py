"""NORM ARB research report: offline, read-only, no network/trades/file writes.

Each JSONL is a separate WS session; state is NEVER carried across reconnects.
Only allowlisted market-only records from the validated collector are read.
No actual account trades are queried. No model result can be named realized PnL.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from book_replay import BookReplay
from finam_ws_collector import DEFAULT_SYMBOLS
from economic_gate import AccountEconomics, quote_width_upper_bound

BASE=Path(__file__).resolve().parent
DATA=BASE/'data'
COSTS=BASE/'account_costs_verified.json'  # not included in any release archive
MAX_INPUT_FILES=200
MAX_FILE_BYTES=13*1024*1024
MAX_LINE_BYTES=1*1024*1024
MAX_LINES=20_000


def account_costs(path:Path, symbol:str)->AccountEconomics|None:
    """Fail-closed on missing costs; never infer broker tariffs by URL alone."""
    if not path.exists() or path.stat().st_size>30000:
        return None
    try:
        raw=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or symbol not in raw or not isinstance(raw[symbol],dict):
            return None
        d=raw[symbol]
        args={'symbol':symbol,
              'rub_per_spread_point':d['rub_per_spread_point'],
              'account_initial_margin_rub':d['account_initial_margin_rub'],
              'broker_buy_rub':d['broker_buy_rub'],
              'broker_sell_rub':d['broker_sell_rub'],
              'exchange_buy_rub':d['exchange_buy_rub'],
              'exchange_sell_rub':d['exchange_sell_rub'],
              'execution_stress_rub_each_way':d['execution_stress_rub_each_way'],
              'reserve_rub':d['reserve_rub'],
              'verified_source':d['verified_source'],
              'verified_account':d['verified_account'],
              'currency':d.get('currency','')}
        from decimal import Decimal
        for k in ('rub_per_spread_point','account_initial_margin_rub','broker_buy_rub',
                  'broker_sell_rub','exchange_buy_rub','exchange_sell_rub',
                  'execution_stress_rub_each_way','reserve_rub'):
            args[k]=Decimal(str(args[k]))
        result=AccountEconomics(**args)
        result.validate()
        return result
    except (ValueError,KeyError,TypeError,UnicodeError,OverflowError,LookupError,ArithmeticError,OSError):
        return None


def parse_received(event:dict[str,Any])->datetime|None:
    t=event.get('captured_utc')
    if not isinstance(t,str) or len(t)>48:return None
    try:
        d=datetime.fromisoformat(t.replace('Z','+00:00'))
    except ValueError:return None
    return d.astimezone(timezone.utc) if d.tzinfo is not None else None


def analyze(directory:Path=DATA, costs_path:Path=COSTS)->dict[str,Any]:
    report={'schema':'NORM_ARB_V7_RESEARCH_EVIDENCE_V1',
            'mode':'READ_ONLY_OFFLINE_NO_MARKET_REQUESTS',
            'broker_orders_checked':False,'realized_profit_rub':None,
            'profit_verified':False,'files_checked':0,'files_skipped':0,
            'market_book_data_events':0,'book_rows':0,'ws_snapshots_seen':0,
            'public_trade_prints':0,'book_quality_reset_per_file':True,
            'quality_events_two_sided':0,'model_positive_upper_bound_events':0,
            'model_numerical_money_results':0,
            'missing_verified_account_costs':True,
            'actual_fills':0,'continuity_verified':False,'last_market_utc':None,
            'status':'NO_MARKET_EVIDENCE'}
    if not directory.is_dir():return report
    # Load account-specific verified fees once; never repeatedly read it per quote.
    wheat_costs={symbol:account_costs(costs_path,symbol) for symbol in DEFAULT_SYMBOLS if symbol.startswith('W4')}
    files=sorted(directory.glob('finam_ws_*.jsonl'))[-MAX_INPUT_FILES:]
    for path in files:
        try:
            if not path.is_file() or path.stat().st_size>MAX_FILE_BYTES:
                report['files_skipped']+=1;continue
            report['files_checked']+=1
            book=BookReplay(DEFAULT_SYMBOLS)
            with path.open('r',encoding='utf-8') as stream:
                for i,line in enumerate(stream):
                    if i>=MAX_LINES or len(line.encode('utf-8'))>MAX_LINE_BYTES:
                        report['files_skipped']+=1;break
                    try:r=json.loads(line)
                    except (ValueError,UnicodeError):
                        report['files_skipped']+=1;book.invalidate_all('BAD_EVIDENCE_LINE');break
                    if not isinstance(r,dict):continue
                    if r.get('kind')=='TRADE_PRINTS' and r.get('subscription_type')=='INSTRUMENT_TRADES':
                        payload=r.get('payload')
                        if isinstance(payload,dict) and payload.get('symbol') in DEFAULT_SYMBOLS and isinstance(payload.get('trades'),list):
                            report['public_trade_prints']+=len(payload['trades'])
                        continue
                    if r.get('kind') != 'DATA' or r.get('subscription_type')!='ORDER_BOOK':continue
                    report['market_book_data_events']+=1
                    payload=r.get('payload');books=payload.get('order_book') if isinstance(payload,dict) else None
                    if not isinstance(books,list):
                        book.invalidate_all('UNRECOGNIZED_EVIDENCE');continue
                    stamp=parse_received(r)
                    if stamp is None:
                        book.invalidate_all('MISSING_RECEIVE_TIME');continue
                    for row in books:
                        if isinstance(row,dict):
                            report['book_rows']+=len(row.get('rows',[])) if isinstance(row.get('rows'),list) else 0
                            if row.get('is_data_snapshot') is True:report['ws_snapshots_seen']+=1
                    for view in book.on_sanitized_event(r,stamp):
                        if view.get('status')=='TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF':
                            report['quality_events_two_sided']+=1
                        # Prices alone are not profits. No account fees in release.
                        sym=view['symbol']
                        actual=wheat_costs.get(sym)
                        if actual is not None:
                            report['missing_verified_account_costs']=False
                            model=quote_width_upper_bound(view,actual,10_000)
                            if model.get('net_upper_bound_rub') is not None:
                                report['model_numerical_money_results']+=1
                            if model.get('status')=='MODEL_UPPER_BOUND_POSITIVE_UNPROVEN_FILL':
                                report['model_positive_upper_bound_events']+=1
                    report['last_market_utc']=stamp.isoformat()
        except (OSError,UnicodeError):
            report['files_skipped']+=1
    if report['market_book_data_events']:
        report['status']='MODEL_ONLY_BOOK_EVIDENCE_NO_FILL_PROOF'
    elif report['public_trade_prints']:
        report['status']='PUBLIC_TRADES_ONLY_NO_BOOK'
    return report


def main()->int:
    r=analyze()
    print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())