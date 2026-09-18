#!/usr/bin/env python3
import json, re, sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

patterns = {
  "rutoken": re.compile(r"rutoken|рутк?окен|рутокен", re.I),
  "rtpcsc": re.compile(r"RtPcscBridge|rtpcsc|rttransport", re.I),
  "rtpkcs11": re.compile(r"rtpkcs11ecp|RtPkcs11|pkcs11wrapper", re.I),
  "cryptopro": re.compile(r"CryptoPro|КриптоПро|ru\.CryptoPro|ru/CryptoPro|cprocsp|JCP|JCSP|CAdES", re.I),
  "pkcs11": re.compile(r"PKCS.?11|pkcs11", re.I),
  "mass_sign": re.compile(r"MassPassagesOperationImpl|createSignOnHashMass|Mass operations do not support signing", re.I),
  "goskey": re.compile(r"GOS_KEY|GOSKEY|Goskey|Госключ", re.I),
  "external_cert": re.compile(r"ExternalCertificate|CreateSignsByExternalCert|createDetachedSign|DeeplinkForMobile", re.I),
}

report = {}
files = list(root.rglob("*.java")) + list(root.rglob("*.kt"))
for name, rx in patterns.items():
    matches, matched_files = [], set()
    for p in files:
        try:
            lines = p.read_text("utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                rel = str(p.relative_to(root))
                matched_files.add(rel)
                # evidence only; cap proprietary line size/count
                matches.append({"file":rel,"line":i,"text":line.strip()[:300]})
                if len(matches) >= 150:
                    break
        if len(matches) >= 150:
            break
    report[name] = {"file_count":len(matched_files),"files":sorted(matched_files)[:100],"matches":matches}

(out/"JADX_KEYWORD_HITS.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
with (out/"JADX_SUMMARY.txt").open("w", encoding="utf-8") as f:
    for k,v in report.items():
        f.write(f"{k}: files={v['file_count']} captured_matches={len(v['matches'])}\n")
        for p in v["files"][:30]:
            f.write(f"  {p}\n")
print("JADX_SUMMARY_WRITTEN")
