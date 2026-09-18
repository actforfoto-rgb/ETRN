#!/usr/bin/env python3
import re, sys, json
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2])
targets = [
    "ru.tensor.sbis.cryptopro_config.CryptoProvider",
    "ru.tensor.sbis.cryptopro_config.CryptoProProvider",
    "ru.tensor.sbis.cryptopro_config.CryptoProPlugin",
    "ru.tensor.sbis.cryptopro_config.RutokenProvider",
]
terms = re.compile(r"(reader|container|keystore|store|provider|attach|detach|initialize|cryptopro|rutoken|pkcs|csp|certificate|sign|carrier|media|path|license|controller|intent|nfc|usb)", re.I)

report={}
for fq in targets:
    stem=fq.rsplit('.',1)[-1]
    candidates=list(src.rglob(stem+".java"))
    entry={"found":False,"file":None,"class_lines":[],"method_signatures":[],"relevant_lines":[]}
    if candidates:
        p=candidates[0]
        entry["found"]=True
        entry["file"]=str(p.relative_to(src))
        lines=p.read_text("utf-8",errors="ignore").splitlines()
        for i,line in enumerate(lines,1):
            s=line.strip()
            if re.search(r"\b(class|interface|enum)\b",s) and stem in s:
                entry["class_lines"].append({"line":i,"text":s[:500]})
            if re.search(r"\b(public|private|protected|final|static|suspend)\b.*\([^;]*\)\s*(throws [^{]+)?\{?\s*$",s):
                entry["method_signatures"].append({"line":i,"text":s[:500]})
            if terms.search(s):
                # exclude annotation metadata blobs
                if "@Metadata" in s or len(s)>1200: continue
                entry["relevant_lines"].append({"line":i,"text":s[:800]})
        entry["method_signatures"]=entry["method_signatures"][:100]
        entry["relevant_lines"]=entry["relevant_lines"][:300]
    report[fq]=entry

out.mkdir(parents=True,exist_ok=True)
(out/"TARGETED_PROVIDER_AUDIT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (out/"TARGETED_PROVIDER_AUDIT.txt").open("w",encoding="utf-8") as f:
    for fq,e in report.items():
        f.write(f"\n## {fq}\nfound={e['found']} file={e['file']}\n")
        f.write("METHODS:\n")
        for x in e["method_signatures"]: f.write(f"{x['line']}: {x['text']}\n")
        f.write("RELEVANT:\n")
        for x in e["relevant_lines"]: f.write(f"{x['line']}: {x['text']}\n")
print(json.dumps({k:{"found":v["found"],"methods":len(v["method_signatures"]),"relevant":len(v["relevant_lines"])} for k,v in report.items()},ensure_ascii=False))
