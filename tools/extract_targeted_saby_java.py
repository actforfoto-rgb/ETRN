#!/usr/bin/env python3
import json,re,sys
from pathlib import Path
root=Path(sys.argv[1])
out=Path("audit/saby_deep"); out.mkdir(parents=True,exist_ok=True)
terms=[
 "RutokenProvider","setPKCS11Path","pkcs11_dll","PNP cryptoki","RtPcscBridge",
 "TokenInterface","GetCryptoProReaders","GetCryptoProContainerPrefix",
 "HDIMAGE","REGISTRY","FAT12","CryptoMobileApi","createSignOnHashMass",
 "certificateType","providerType","container","reader"
]
files=list(root.rglob("*.java"))+list(root.rglob("*.kt"))
report={}
for term in terms:
 rx=re.compile(re.escape(term),re.I)
 ms=[]
 for p in files:
  try: lines=p.read_text("utf-8",errors="ignore").splitlines()
  except: continue
  for i,l in enumerate(lines):
   if rx.search(l):
    ms.append({
     "file":str(p.relative_to(root)),
     "line":i+1,
     "context":[x[:500] for x in lines[max(0,i-3):min(len(lines),i+4)]]
    })
    if len(ms)>=120: break
  if len(ms)>=120: break
 report[term]=ms
(out/"TARGETED_JAVA_CONTEXT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
# compact index
with (out/"TARGETED_JAVA_INDEX.txt").open("w",encoding="utf-8") as f:
 for term,ms in report.items():
  f.write(f"## {term}: {len(ms)} matches\n")
  for m in ms[:50]: f.write(f"{m['file']}:{m['line']}\n")
print("TARGETED_JAVA_AUDIT_PASS")
