"""Single-instance bounded readonly recorder supervisor for a Windows VPS.

No broker order APIs. No trade execution. One short websocket session per fresh JWT.
No scheduled tasks and no automatic restart are configured. Heartbeat reflects
completed bounded sessions; evidence files are local to the VPS and are never pushed
into the public GitHub repository.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time
from contextlib import contextmanager
from uuid import uuid4

from tls_setup import enable_native_tls
enable_native_tls()

from finam_ws_collector import DEFAULT_SYMBOLS, DEFAULT_TRADE_SYMBOLS, authorize_readonly, capture_stream, ControlledFailure

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
STATUS = BASE / "status.json"
MAX_DISK_BYTES = 100 * 1024 * 1024
MAX_AGE_SECONDS = 3 * 24 * 3600



@contextmanager
def single_instance_lock():
    """OS lock guards a process that might survive an RDP disconnect."""
    BASE.mkdir(parents=True, exist_ok=True)
    handle = (BASE / "collector.lock").open("a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                handle.write(b"\x00")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, IOError):
            acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        handle.close()

def clock():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state(**info):
    # Atomic status replacement. No tokens, account ids, request bodies or exception text.
    obj = {"updated_utc": clock(), "mode": "READ_ONLY", "orders": 0,
           "realized_pnl": 0, **info}
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATUS)


def cleanup():
    """Do not delete any evidentiary files. Stop when storage is exhausted."""
    DATA.mkdir(exist_ok=True)
    return sum(p.stat().st_size for p in DATA.glob("finam_ws_*.jsonl"))


def stop_requested():
    return (BASE / "STOP_REQUESTED").is_file()



def supervisor(round_seconds: int):
    """Never interpret handshake/files/empty rounds as successful market capture.

    A status transition to CONNECTING retains the last completed round and
    accumulated data counts, so dashboards cannot falsely imply live evidence.
    This supervisor never invokes any trading, OS install or MT5 operation.
    """
    if not 30 <= round_seconds <= 300:
        print('INVALID_SESSION_DURATION')
        return 2
    failures=0
    failures_total=0
    cycles=0
    rounds_empty=0
    messages_total=0
    book_rows_total=0
    ws_snapshots_total=0
    public_market_trades_total=0
    last_completed='NONE'
    latest_filename=None
    DATA.mkdir(exist_ok=True)

    def write_status(state_name: str, **extra):
        state(state=state_name, pid=os.getpid(), cycles=cycles,
              completed_rounds=cycles if state_name not in ('CONNECTING','STARTED') else max(0,cycles-1),
              failed_rounds=failures_total, consecutive_failed_rounds=failures,
              empty_rounds=rounds_empty, market_messages_total=messages_total,
              book_rows_total=book_rows_total, ws_snapshots_total=ws_snapshots_total,
              public_market_trades_total=public_market_trades_total,
              last_completed_status=last_completed, last_file=latest_filename,
              **extra)
    write_status('STARTED')
    print(json.dumps({'status':'STARTED','readonly':True,'orders':0}),flush=True)
    while True:
        if stop_requested():
            write_status('STOPPED_BY_USER')
            (BASE / 'STOP_REQUESTED').unlink(missing_ok=True)
            return 0
        if cleanup() >= MAX_DISK_BYTES:
            write_status('DISK_LIMIT_REACHED')
            return 4
        cycles+=1
        try:
            key=os.environ.get('FINAM_READONLY_API_KEY','')
            if not key:
                write_status('STOPPED_NO_KEY')
                return 3
            write_status('CONNECTING')
            jwt=authorize_readonly(key)
            path=DATA/f'finam_ws_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid4().hex[:10]}.jsonl'
            outcome=asyncio.run(capture_stream(jwt,DEFAULT_SYMBOLS,round_seconds,path,trade_symbols=DEFAULT_TRADE_SYMBOLS))
            latest_filename=path.name
            count=outcome.get('market_messages',0)
            messages_total+=count
            book_rows_total+=outcome.get('book_rows_received',0)
            ws_snapshots_total+=outcome.get('ws_snapshots_seen',0)
            public_market_trades_total+=outcome.get('public_market_trades',0)
            is_good=(outcome.get('status')=='DATA_RECORDED' and count>0
                     and outcome.get('errors')==0 and not outcome.get('limits_reached'))
            if is_good:
                failures=0
                last_completed='MARKET_EVENTS_RECORDED_NON_EXECUTABLE'
                write_status('MARKET_EVENTS_RECORDED_NON_EXECUTABLE',
                    last_data_messages=count,
                    last_quality_status=outcome.get('book_quality_by_symbol',{}),
                    last_capture_started=outcome.get('started_utc'),
                    last_capture_ended=outcome.get('ended_utc'))
            else:
                failures+=1
                failures_total+=1
                if not count:
                    rounds_empty+=1
                last_completed=(
                    'CONNECTED_NO_ORDERBOOK_DATA' if outcome.get('subscribed') and not count
                    else 'BROKER_STREAM_INCOMPLETE_OR_ERRORS'
                )
                write_status(last_completed,last_data_messages=count,
                    last_capture_status=outcome.get('status','UNKNOWN'),
                    last_quality_status=outcome.get('book_quality_by_symbol',{}),
                    last_capture_started=outcome.get('started_utc'),
                    last_capture_ended=outcome.get('ended_utc'))
            print(json.dumps({'cycle':cycles,'status':last_completed,
                              'market_messages':count,'book_rows':outcome.get('book_rows_received',0),
                              'ws_snapshots':outcome.get('ws_snapshots_seen',0),
                              'public_trades':outcome.get('public_market_trades',0),
                              'empty_rounds':rounds_empty,'failed_rounds':failures_total},
                             ensure_ascii=False),flush=True)
        except ControlledFailure as exc:
            failures+=1;failures_total+=1
            last_completed=str(exc)
            write_status(last_completed)
            print(json.dumps({'cycle':cycles,'status':last_completed,'orders':0}),flush=True)
        except Exception:
            failures+=1;failures_total+=1
            last_completed='SUPERVISOR_ERROR'
            write_status(last_completed)
            print(json.dumps({'cycle':cycles,'status':last_completed,'orders':0}),flush=True)
        # Adapt wait to empty/disconnected sessions; NEVER spin infinite 2s
        # loops claiming a successful capture with zero market data.
        pause=min(120,5*(2**min(failures,5))) if failures else 3
        for _ in range(pause):
            if stop_requested():
                break
            time.sleep(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--round-seconds", type=int, default=240)
    args = parser.parse_args()
    with single_instance_lock() as allowed:
        if not allowed:
            print(json.dumps({"status": "ALREADY_RUNNING", "orders": 0}), flush=True)
            return 19
        return supervisor(args.round_seconds)


if __name__ == "__main__":
    sys.exit(main())