#!/usr/bin/env python3
from pathlib import Path
import re, sys, json

root = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
src = root / "sources"

targets = [
    "CryptographyApi.java",
    "CryptoMobileApi.java",
    "CertificateOperation.java",
    "SigningOperationFileInfo.java",
    "SigningOperationFile.java",
    "ExternalCertificateOperation.java",
    "CertificateOperationFileInfo.java",
    "SignedFile.java",
]

def compact(s):
    return re.sub(r"\s+", " ", s.strip())

def declaration_surface(p):
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    result=[]
    depth=0
    for i,line in enumerate(lines,1):
        raw=line.rstrip()
        t=compact(raw)
        if not t or t.startswith("@Metadata"):
            depth += raw.count("{") - raw.count("}")
            continue
        # Keep API/model declarations, constants and constructor/getter/setter signatures only.
        interesting = (
            re.search(r"\b(class|interface|enum)\b", t)
            or re.match(r"(public|private|protected)\s+(static\s+|final\s+|volatile\s+|transient\s+)*[\w<>, ?\[\].@]+\s+\w+\s*;", t)
            or re.match(r"(public|private|protected)\s+[^=]+\([^;{}]*\)\s*(throws\s+[^\{]+)?[;\{]$", t)
        )
        if interesting and len(t) <= 1800:
            # Do not copy method bodies; keep signature only.
            sig=t.split("{",1)[0].strip()
            result.append(f"{i}: {sig}")
        depth += raw.count("{") - raw.count("}")
    return result

surface=[]
target_files={}
for name in targets:
    matches=sorted(src.rglob(name))
    target_files[name]=[str(p.relative_to(root)) for p in matches]
    for p in matches:
        surface.append(f"===== {p.relative_to(root)} =====")
        surface.extend(declaration_surface(p))
        surface.append("")

# Find model classes even if JADX renamed/moved the file.
dynamic=[]
for p in src.rglob("*.java"):
    try:
        txt=p.read_text(encoding="utf-8",errors="replace")
    except Exception:
        continue
    if re.search(r"\b(class|interface)\s+(SignedFile|CertificateOperation|SigningOperationFileInfo)\b", txt):
        dynamic.append(p)
for p in sorted(set(dynamic)):
    key=str(p.relative_to(root))
    if not any(key in xs for xs in target_files.values()):
        surface.append(f"===== dynamic:{key} =====")
        surface.extend(declaration_surface(p))
        surface.append("")

(out/"CRYPTO_MODEL_SURFACE.txt").write_text("\n".join(surface),encoding="utf-8")

terms=[
    "createSigns","CreateSigns","ExternalCertificate","InitOperation",
    "DeeplinkForMobile","DocumentID","DocumentId","DocId","ObjectID","ObjectId","objectId",
    "AttachmentID","AttachmentId","FileID","FileId","fileId","SignedFile",
    "SigningOperationFileInfo","CertificateOperation","OperationData",
    "GOS_KEY","GOSKEY","createDetachedSign","repeatExternalCertificateRequest",
    "stopExternalCertificateOperation"
]
hits=[]
for p in src.rglob("*.java"):
    try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
    except Exception: continue
    rel=str(p.relative_to(root))
    for i,line in enumerate(lines,1):
        t=compact(line)
        if any(term.lower() in t.lower() for term in terms):
            if t.startswith("@Metadata"): continue
            hits.append((rel,i,t[:2000]))
hits=hits[:2400]
(out/"CRYPTO_FLOW_HITS.txt").write_text(
    "\n".join(f"{p}:{i}: {t}" for p,i,t in hits),encoding="utf-8"
)

# Focused evidence: only crypto package/model hits and operation construction references.
focus=[]
for p,i,t in hits:
    low=(p+" "+t).lower()
    if ("cryptography/generated" in p
        or "crypto_operation" in p
        or any(x in low for x in [
            "createsigns","externalcertificate","initoperation","signedfile",
            "signingoperationfileinfo","certificateoperation","operationdata",
            "deeplinkformobile"
        ])):
        focus.append((p,i,t))
(out/"CRYPTO_GATE_FOCUS.txt").write_text(
    "\n".join(f"{p}:{i}: {t}" for p,i,t in focus),encoding="utf-8"
)

summary={"target_files":target_files,"dynamic_models":[str(p.relative_to(root)) for p in sorted(set(dynamic))],"term_counts":{}}
flow_text="\n".join(x[2] for x in hits)
for term in terms:
    summary["term_counts"][term]=len(re.findall(re.escape(term),flow_text,flags=re.I))
(out/"CRYPTO_FLOW_SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(summary,ensure_ascii=False,indent=2))
