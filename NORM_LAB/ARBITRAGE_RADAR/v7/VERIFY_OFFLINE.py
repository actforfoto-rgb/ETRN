"""Offline file-integrity gate; never reads or modifies FX, accounts or market data."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import sys

BASE=Path(__file__).resolve().parent
MANIFEST=BASE/'SHA256SUMS.json'
DYNAMIC={'data','logs','__pycache__'}
TRUSTED_DYNAMIC={'status.json','service.pid','collector.lock','STOP_REQUESTED',
                 'account_costs_verified.json'}

def verify()->dict[str,object]:
    try:
        m=json.loads(MANIFEST.read_text(encoding='utf-8'))
        files=m.get('files')
        if m.get('schema')!='NORM_ARB_V7_SHA256_RELEASE_V1' or not isinstance(files,dict) or len(files)<20:
            return {'status':'MANIFEST_INVALID'}
        for name,digest in files.items():
            if not isinstance(name,str) or not name or name.startswith('/') or '\\' in name or '..' in Path(name).parts:
                return {'status':'MANIFEST_UNSAFE_PATH'}
            if not isinstance(digest,str) or len(digest)!=64:
                return {'status':'MANIFEST_HASH_INVALID'}
            path=BASE/name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                return {'status':'RELEASE_FILE_MISSING_OR_CHANGED','files_expected':len(files)}
        # A rogue Python module in the import path could bypass sanitizer.
        for path in BASE.rglob('*.py'):
            rel=path.relative_to(BASE)
            if any(seg in DYNAMIC for seg in rel.parts):continue
            if rel.as_posix() not in files:
                return {'status':'UNEXPECTED_EXECUTABLE_PYTHON_FILE'}
        return {'status':'RELEASE_INTEGRITY_OK','files_checked':len(files),
                'trading_enabled':False,'broker_orders_sent':0}
    except (ValueError,OSError,UnicodeError):
        return {'status':'RELEASE_INTEGRITY_NOT_VERIFIED'}

if __name__=='__main__':
    result=verify()
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result.get('status')=='RELEASE_INTEGRITY_OK' else 3)