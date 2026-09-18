"""Minimal hash-guarded migration from tested R2 FIX1. No live Saby requests."""
from pathlib import Path
import hashlib
p = Path('app/src/main/java/ru/komus/etrnprobe/MainActivity.java')
raw = p.read_bytes()
s = raw.decode('utf-8')
if '// R2_FIX2_RPC_PARAMS' in s:
    print('FIX2_ALREADY_APPLIED=YES')
    raise SystemExit(0)
blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
assert blob == '402299330bcbdbc2a9a434ca26124ff758a06b91', ('unexpected parent', blob)
def replace(a, b):
    global s
    assert s.count(a) == 1, (a[:100], s.count(a))
    s = s.replace(a,b)
replace('    // R2_FIX1_CERTIFICATE_PREFLIGHT', '    // R2_FIX2_RPC_PARAMS\n    // R2_FIX1_CERTIFICATE_PREFLIGHT')
replace('KOMUS-ETRN-GOSKEY-BATCH-PROBE-R2-FIX1/2.1', 'KOMUS-ETRN-GOSKEY-BATCH-PROBE-R2-FIX2/2.2')
replace('ЭТрН — Госключ · R2 FIX1', 'ЭТрН — Госключ · R2 FIX2')
replace('R2_FIX1 START.', 'R2_FIX2 START. RPC Params включён для Create и GetStatus.')
replace('new JSONObject();\n        request.put("jsonrpc", "2.0");\n        request.put("method", method);\n        request.put("params", params);\n        request.put("id", System.currentTimeMillis());', 'SabyRpcContract.envelope(method, params, System.currentTimeMillis());')
replace('CREATE: отправляю ОДНУ crypto operation; docs=2, files=', 'CREATE: RPC=params.Params.Operation; одна crypto operation; docs=2, files=')
replace('JSONObject response = rpc(ONLINE_SERVICE_URL, "sabyCryptoOperation.GetStatus", params, sessionId);', 'logOnUi("STATUS: RPC=params.Params.OperationID; запрос результата существующей операции.");\n                JSONObject response = rpc(ONLINE_SERVICE_URL, "sabyCryptoOperation.GetStatus", params, sessionId);')
replace('(ogrnip.isEmpty() ? "MISSING" : "AUTO")', '(ogrnip.isEmpty() ? "MISSING" : "CONFIRMED")')
p.write_text(s,encoding='utf-8')
print('FIX2_PATCH_APPLIED=YES', hashlib.sha256(p.read_bytes()).hexdigest())
