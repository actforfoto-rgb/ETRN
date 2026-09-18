#!/usr/bin/env python3
import re, sys, json
from pathlib import Path

root=Path(sys.argv[1])
out=Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

pkg=root/"sources/ru/tensor/sbis/cryptopro_config"
files=[]
if pkg.exists():
    files=sorted(pkg.glob("*.java"))

summary={"package_files":[str(p.name) for p in files],"classes":{}}
tokens=[
    "RtPcscBridge","RtTransport","InitParameters","TokenInterface",
    "librtpkcs11ecp","rutoken","CryptoPro","JCSP","ACSP","KeyStore",
    "setAppContext","initialize","finalize","attachToLifecycle",
    "USB","NFC","BLUETOOTH","SPI","reader","provider"
]
for p in files:
    lines=p.read_text("utf-8",errors="ignore").splitlines()
    decls=[]
    for i,line in enumerate(lines,1):
        s=line.strip()
        if re.search(r'\b(class|interface|enum)\b',s) or re.search(r'\b(public|private|protected|internal)\b.*\([^;]*\)\s*(\{|throws|$)',s):
            if len(s)<500: decls.append({"line":i,"text":s[:450]})
    calls=[]
    for i,line in enumerate(lines,1):
        if any(t.lower() in line.lower() for t in tokens):
            s=line.strip()
            if len(s)>0:
                calls.append({"line":i,"text":s[:500]})
    summary["classes"][p.name]={"declarations":decls[:120],"relevant_lines":calls[:200]}

# CryptoPro config references to key stores/readers.
extra=[]
for pattern in [
    "sources/ru/cprocsp/ACSP/tools/config/Config.java",
    "sources/ru/cprocsp/ACSP/tools/store/util/UtilKeyStore.java"
]:
    p=root/pattern
    if not p.exists(): continue
    for i,line in enumerate(p.read_text("utf-8",errors="ignore").splitlines(),1):
        if re.search(r'rutoken|reader|keystore|HDIMAGE|REGISTRY|FAT|librtpkcs11ecp|PKCS.?11',line,re.I):
            extra.append({"file":pattern,"line":i,"text":line.strip()[:500]})
summary["cryptopro_reader_evidence"]=extra[:300]

(out/"CRYPTOPRO_RUTOKEN_STRUCTURE.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (out/"CRYPTOPRO_RUTOKEN_STRUCTURE.txt").open("w",encoding="utf-8") as f:
    f.write("PACKAGE FILES\n")
    for x in summary["package_files"]: f.write("  "+x+"\n")
    for name,v in summary["classes"].items():
        f.write(f"\n## {name}\n")
        for x in v["declarations"]: f.write(f"D {x['line']}: {x['text']}\n")
        for x in v["relevant_lines"]: f.write(f"R {x['line']}: {x['text']}\n")
    f.write("\n## CryptoPro reader evidence\n")
    for x in extra: f.write(f"{x['file']}:{x['line']}: {x['text']}\n")
print("STRUCTURE_PASS files=",len(files),"extra=",len(extra))
