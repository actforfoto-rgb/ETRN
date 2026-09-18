#!/usr/bin/env python3
import json,re,sys
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True)
terms=re.compile(r"(CryptoProvider|CryptoProProvider|RutokenProvider|attachCrypto|detachCrypto|createSignOnHashMass|certificateObjId|CryptoMobileApi|CSPConfig|HDIMAGE|HD_STORE|PFX|reader|container|GetCryptoPro|setCrypto|sign)",re.I)
report={}
for p in root.glob("*.java"):
 lines=p.read_text("utf-8",errors="ignore").splitlines()
 hits=[]
 for i,s in enumerate(lines,1):
  if terms.search(s) and "@Metadata" not in s and len(s)<1600:
   hits.append({"line":i,"text":s.strip()[:1000]})
 report[p.name]={"hits":hits[:500]}
(out/"SIGNING_ROUTE_AUDIT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (out/"SIGNING_ROUTE_AUDIT.txt").open("w",encoding="utf-8") as f:
 for n,e in report.items():
  f.write("\\n## "+n+"\\n")
  for h in e["hits"]: f.write(f"{h['line']}: {h['text']}\\n")
print({k:len(v["hits"]) for k,v in report.items()})
