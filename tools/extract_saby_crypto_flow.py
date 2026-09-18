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
]

def compact(s):
    return re.sub(r"\s+", " ", s.strip())

surface = []
for name in targets:
    matches = sorted(src.rglob(name))
    for p in matches:
        rel = p.relative_to(root)
        surface.append(f"===== {rel} =====")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, 1):
            t = compact(line)
            if not t or t.startswith("@Metadata"):
                continue
            if re.search(r"\b(class|interface|enum)\b", t) or re.search(
                r"\b(public|private|protected)\b.*[;{]$", t
            ):
                if len(t) <= 1500:
                    surface.append(f"{i}: {t}")
        surface.append("")
(out / "CRYPTO_MODEL_SURFACE.txt").write_text("\n".join(surface), encoding="utf-8")

terms = [
    "createSigns", "CreateSigns", "ExternalCertificate", "InitOperation",
    "DeeplinkForMobile", "DocumentID", "DocumentId", "DocId",
    "AttachmentID", "AttachmentId", "SigningOperationFileInfo",
    "CertificateOperation", "GOS_KEY", "GOSKEY", "createDetachedSign",
    "repeatExternalCertificateRequest", "stopExternalCertificateOperation",
]
hits = []
for p in src.rglob("*.java"):
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        continue
    rel = str(p.relative_to(root))
    for i, line in enumerate(lines, 1):
        t = compact(line)
        if any(term.lower() in t.lower() for term in terms):
            if t.startswith("@Metadata"):
                continue
            hits.append((rel, i, t[:1800]))
hits = hits[:1200]
(out / "CRYPTO_FLOW_HITS.txt").write_text(
    "\n".join(f"{p}:{i}: {t}" for p,i,t in hits), encoding="utf-8"
)

# Summarize which model appears to carry document/file identity.
summary = {
    "target_files": {},
    "term_counts": {},
}
for name in targets:
    summary["target_files"][name] = [str(p.relative_to(root)) for p in sorted(src.rglob(name))]
flow_text = "\n".join(x[2] for x in hits)
for term in terms:
    summary["term_counts"][term] = len(re.findall(re.escape(term), flow_text, flags=re.I))
(out / "CRYPTO_FLOW_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
