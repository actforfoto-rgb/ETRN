"""Read-only local status for NORM ARB; no access to MT5 or broker secrets."""
from pathlib import Path
import json

root=Path(__file__).resolve().parent
print('NORM ARBITRAGE — статус (только чтение)')
p=root/'status.json'
if not p.is_file():
    print('NOT_STARTED: штатных результатов службы пока нет.')
else:
    try:
        v=json.loads(p.read_text(encoding='utf-8'))
        # Print only known allow-listed technical fields, never arbitrary state.
        for k in ('state','mode','updated_utc','cycles','completed_rounds','market_messages_total','book_rows_total','ws_snapshots_total','public_market_trades_total','empty_rounds','last_completed_status','last_data_messages','last_quality_status','failed_rounds','last_capture_started','last_capture_ended','last_file','reason'):
            if k in v: print(f'{k}: {str(v[k])[:180]}')
    except (ValueError,OSError,UnicodeError):
        print('STATUS_READ_FAILED')
print('data_files:',len(list((root/'data').glob('finam_ws_*.jsonl'))))
print('Прошедшие тесты и подключённый сокет сами по себе НЕ означают прибыль.')