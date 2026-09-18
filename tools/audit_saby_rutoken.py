#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, urllib.request, zipfile, hashlib
from pathlib import Path

PKG = "ru.tensor.sbis.courier.saby"
EXPECTED_VERSION = "26.3246.5"
BASE = "https://backapi.rustore.ru"
HEADERS = {
    "ruStoreVerCode": "1000",
    "User-Agent": "RuStore/1000 Android",
    "Accept": "application/json",
}

out = Path("audit/saby_rutoken")
work = Path("/tmp/saby_rutoken_audit")
out.mkdir(parents=True, exist_ok=True)
work.mkdir(parents=True, exist_ok=True)

def request_json(url, method="GET", data=None):
    body = None
    headers = dict(HEADERS)
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw)

def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent":"RuStore/1000 Android"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)

info = request_json(f"{BASE}/applicationData/overallInfo/{PKG}")
body = info.get("body") or {}
meta = {
    "packageName": body.get("packageName"),
    "appName": body.get("appName"),
    "versionName": body.get("versionName"),
    "versionCode": body.get("versionCode"),
    "appId": body.get("appId"),
    "fileSize": body.get("fileSize"),
    "signature": body.get("signature"),
}
(out/"RUSTORE_METADATA.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

if meta["packageName"] != PKG:
    raise SystemExit(f"PACKAGE_MISMATCH={meta}")
if meta["versionName"] != EXPECTED_VERSION:
    raise SystemExit(f"VERSION_MISMATCH expected={EXPECTED_VERSION} got={meta['versionName']}")

payload = {
    "appId": meta["appId"],
    "firstInstall": True,
    "mobileServices": ["GMS"],
    "supportedAbis": ["arm64-v8a", "armeabi-v7a"],
    "screenDensity": 480,
    "supportedLocales": ["ru"],
    "sdkVersion": 35,
    "withoutSplits": True,
}
dl = request_json(f"{BASE}/v3/showcase/apps/download-link", "POST", payload)
urls = dl.get("downloadUrls") or (dl.get("body") or {}).get("downloadUrls") or []
if not urls:
    raise SystemExit("NO_DOWNLOAD_URLS response_keys="+",".join(dl.keys()))

apk = work/"saby.apk"
download(urls[0]["url"], apk)
raw4 = apk.read_bytes()[:4]
if raw4 != b"PK\x03\x04":
    raise SystemExit(f"NOT_ZIP magic={raw4!r}")
sha256 = hashlib.sha256(apk.read_bytes()).hexdigest()
(out/"APK_IDENTITY.txt").write_text(
    f"package={PKG}\nversion={EXPECTED_VERSION}\nversionCode={meta['versionCode']}\n"
    f"size={apk.stat().st_size}\nsha256={sha256}\n", encoding="utf-8"
)

extract = work/"unzipped"
extract.mkdir(exist_ok=True)
with zipfile.ZipFile(apk) as z:
    names = z.namelist()
    (out/"ZIP_ENTRIES.txt").write_text("\n".join(names)+"\n", encoding="utf-8")
    for n in names:
        low = n.lower()
        if low.endswith(".dex") or low.endswith(".so") or low.endswith("androidmanifest.xml"):
            try:
                z.extract(n, extract)
            except Exception:
                pass

patterns = [
    "rutoken", "rtpcsc", "rttransport", "rtpkcs11", "pkcs11", "pcsc",
    "cryptopro", "cprocsp", "cpkey", "jcp",
    "masspassagesoperationimpl", "createsignonhashmass", "gos_key", "goskey",
    "externalcertificate", "createDetachedSign"
]

# archive-entry fingerprint
entry_hits = []
for n in names:
    ln = n.lower()
    matched = [p for p in patterns if p.lower() in ln]
    if matched:
        entry_hits.append({"entry": n, "matched": matched})
(out/"ARCHIVE_NAME_HITS.json").write_text(json.dumps(entry_hits, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

# binary strings fingerprint; no full proprietary dumps are retained
records = []
targets = sorted([p for p in extract.rglob("*") if p.is_file() and (p.suffix in {".so",".dex"})])
for p in targets:
    try:
        cp = subprocess.run(["strings","-a",str(p)], text=True, errors="ignore",
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180)
    except Exception as e:
        records.append({"file": str(p.relative_to(extract)), "error": type(e).__name__})
        continue
    lines = cp.stdout.splitlines()
    hits = {}
    lower_lines = [x.lower() for x in lines]
    for pat in patterns:
        found = []
        for original, lower in zip(lines, lower_lines):
            if pat.lower() in lower:
                # keep symbol/string context but cap to prevent source redistribution
                found.append(original[:500])
                if len(found) >= 30: break
        if found:
            hits[pat] = found
    if hits:
        records.append({"file": str(p.relative_to(extract)), "hits": hits})
(out/"BINARY_STRING_HITS.json").write_text(json.dumps(records, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

# concise summary
all_text = json.dumps(records, ensure_ascii=False).lower()
presence = {p: (p.lower() in all_text) for p in patterns}
libs = [n for n in names if n.lower().endswith(".so")]
relevant_libs = [x for x in libs if any(k in x.lower() for k in ("rutoken","rtpcsc","pkcs","crypto","csp","sign","sbis"))]
summary = {
    "metadata": meta,
    "apk_sha256": sha256,
    "apk_size": apk.stat().st_size,
    "dex_count": len([n for n in names if n.lower().endswith(".dex")]),
    "so_count": len(libs),
    "relevant_native_entries": relevant_libs,
    "presence_in_binary_strings": presence,
}
(out/"SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print("DOWNLOAD_AND_FINGERPRINT_PASS")
print(json.dumps(summary, ensure_ascii=False, indent=2))

# delete APK immediately after fingerprint stage; workflow decompiler receives a temporary copy only if requested before cleanup
# Cleanup is done by workflow after optional JADX stage.
