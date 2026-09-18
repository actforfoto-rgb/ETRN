#!/usr/bin/env python3
import json, re, subprocess
from pathlib import Path

work = Path("/tmp/saby_rutoken_audit")
root = work/"unzipped"
out = Path("audit/saby_deep")
out.mkdir(parents=True, exist_ok=True)

libs = list(root.rglob("libsbis-app-controller.so"))
if not libs:
    raise SystemExit("libsbis-app-controller.so not found")
# Prefer x86_64 for easier symbol inspection, otherwise arm64.
lib = next((p for p in libs if "x86_64" in str(p)), libs[0])

terms = [
    r"GetCryptoProReaders", r"GetCryptoProContainerPrefix", r"CryptoPro",
    r"Provider[A-Za-z0-9_:<>]*GOST[A-Za-z0-9_:<>]*",
    r"ProviderPKCS11[A-Za-z0-9_:<>]*", r"Rutoken",
    r"HDIMAGE", r"REGISTRY", r"FAT12", r"FLASH", r"MEDIA",
    r"cryptoki", r"pkcs11", r"reader", r"container",
    r"createSignOnHashMass", r"certificate", r"provider"
]
rx = re.compile("|".join(terms), re.I)

cp = subprocess.run(["strings","-a","-t","x",str(lib)], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, errors="ignore", check=True)
lines = cp.stdout.splitlines()
hits=[]
for idx,line in enumerate(lines):
    if rx.search(line):
        lo=max(0,idx-5); hi=min(len(lines),idx+6)
        hits.append({
            "index": idx,
            "line": line[:1000],
            "context": [x[:1000] for x in lines[lo:hi]]
        })
# Deduplicate overlapping windows by hit line
seen=set(); uniq=[]
for h in hits:
    if h["line"] not in seen:
        seen.add(h["line"]); uniq.append(h)
(out/"NATIVE_STRING_CONTEXT.json").write_text(json.dumps(uniq[:2500],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

# Dynamic/symbol tables, demangled where possible
symbol_cmds = [
    ["readelf","-Ws",str(lib)],
    ["nm","-D","-C",str(lib)],
    ["objdump","-T",str(lib)],
]
sym=[]
for cmd in symbol_cmds:
    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors="ignore",timeout=240)
        text=p.stdout
        filt=[ln for ln in text.splitlines() if rx.search(ln)]
        sym.append({"cmd":" ".join(cmd[:2]),"returncode":p.returncode,"lines":filt[:3000]})
    except Exception as e:
        sym.append({"cmd":" ".join(cmd[:2]),"error":type(e).__name__})
(out/"NATIVE_SYMBOL_HITS.json").write_text(json.dumps(sym,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

# Extract names that look like crypto provider classes/types.
all_strings="\n".join(lines)
provider_names=sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_:]{2,120}(?:Provider|provider)[A-Za-z0-9_:]{0,120}", all_strings)))
provider_names += sorted(set(re.findall(r"Provider[A-Za-z0-9_:]{3,160}", all_strings)))
provider_names=sorted(set(x.strip() for x in provider_names if len(x)<220))
(out/"PROVIDER_NAMES.txt").write_text("\n".join(provider_names[:5000])+"\n",encoding="utf-8")

# Useful exact literals and nearby strings.
focus = ["hdimage","registry","fat12","flash","pnp cryptoki","pkcs11_dll",
         "cryptopro readers","container prefix","rutoken30","rutokenbase",
         "createsignonhashmass","mass operations do not support signing"]
focus_out={}
for f in focus:
    arr=[]
    for i,line in enumerate(lines):
        if f in line.lower():
            arr.append([x[:1000] for x in lines[max(0,i-8):min(len(lines),i+9)]])
            if len(arr)>=30: break
    focus_out[f]=arr
(out/"FOCUS_CONTEXT.json").write_text(json.dumps(focus_out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

print("DEEP_NATIVE_AUDIT_PASS", lib)
