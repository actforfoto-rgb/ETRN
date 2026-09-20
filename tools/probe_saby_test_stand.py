#!/usr/bin/env python3
import json, urllib.request, urllib.error, time
from pathlib import Path

ENDPOINT = "https://fix-online.sbis.ru/service/?srv=1"
OUT = Path("audit/saby_test_stand")
OUT.mkdir(parents=True, exist_ok=True)

def call(method, params):
    body = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "ETRN-Test-Stand-Gate/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(1024 * 1024)
            return {"http": r.status, "body": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read(1024 * 1024)
        return {"http": e.code, "body": raw.decode("utf-8", "replace")}
    except Exception as e:
        return {"transport_error": type(e).__name__ + ": " + str(e)}

fake_operation = {
    "Params": {
        "Operation": {
            "CertificateType": "Госключ",
            "GoskeySignatureKind": "КЭПЮЛ",
            "GoskeyOgrnip": "000000000000000",
            "DocumentID": "00000000-0000-0000-0000-000000000001",
            "Files": [
                {"AttachmentID": "00000000-0000-0000-0000-000000000002"},
                {"AttachmentID": "00000000-0000-0000-0000-000000000003"}
            ]
        }
    }
}

results = {
    "endpoint": ENDPOINT,
    "timestamp_epoch": int(time.time()),
    "real_method_no_session": call("sabyCryptoOperation.Create", fake_operation),
    "fake_method_no_session": call("ETRNProbe.DefinitelyNotAMethod", {"x": 1})
}

def norm(x):
    if "body" not in x:
        return x
    b = x["body"]
    # Never persist cookie/session-like headers; only response body is captured.
    return {"http": x.get("http"), "body": b[:20000]}

results = {k: norm(v) if isinstance(v, dict) else v for k, v in results.items()}
(OUT / "PROBE.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

real = results["real_method_no_session"]
fake = results["fake_method_no_session"]
same = real == fake
summary = [
    "# Saby test-stand unauthenticated crypto probe",
    "",
    f"- endpoint: {ENDPOINT}",
    f"- real/fake responses identical: {same}",
    "",
    "This probe uses no Saby credentials and no real document identifiers.",
    "It cannot prove cross-document acceptance. It only checks whether the test stand exposes distinguishable method routing before authentication.",
]
(OUT / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=2))
