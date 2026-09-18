#!/usr/bin/env python3
from pathlib import Path
import re, sys

root=Path(sys.argv[1])
out=Path(sys.argv[2])
src=root/"sources"
patterns=[
    re.compile(r"new\s+SignedFile\s*\("),
    re.compile(r"\.setDocId\s*\("),
    re.compile(r"\.getDocId\s*\("),
    re.compile(r"\.createSigns\s*\("),
    re.compile(r"createSigns\s*\("),
    re.compile(r"CreateSignsByExternalCert",re.I),
    re.compile(r"ExternalCertificate",re.I),
    re.compile(r"InitOperation",re.I),
]
blocks=[]
seen=set()
for p in src.rglob("*.java"):
    try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
    except Exception: continue
    joined="\n".join(lines)
    if ("SignedFile" not in joined and "createSigns" not in joined and
        "ExternalCertificate" not in joined and "InitOperation" not in joined):
        continue
    for i,line in enumerate(lines):
        if any(rx.search(line) for rx in patterns):
            # Ignore generated model getters/setters unless another target token is nearby.
            lo=max(0,i-8); hi=min(len(lines),i+10)
            context="\n".join(lines[lo:hi])
            key=(str(p.relative_to(root)),lo,hi)
            if key in seen: continue
            seen.add(key)
            blocks.append(
                f"===== {p.relative_to(root)} lines {lo+1}-{hi} hit {i+1} =====\n"
                + "\n".join(f"{n+1}: {lines[n]}" for n in range(lo,hi))
                + "\n"
            )
(out/"SIGNEDFILE_CALLSITES.txt").write_text("\n".join(blocks),encoding="utf-8")

# Higher-signal subset: exclude the generated SignedFile/CertificateOperation model files.
high=[]
for b in blocks:
    header=b.splitlines()[0]
    if "/generated/SignedFile.java" in header or "/generated/CertificateOperation.java" in header:
        continue
    high.append(b)
(out/"SIGNEDFILE_CALLSITES_HIGH_SIGNAL.txt").write_text("\n".join(high),encoding="utf-8")
print("blocks",len(blocks),"high",len(high))
