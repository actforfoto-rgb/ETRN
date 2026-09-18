#!/usr/bin/env python3
import hashlib, json, re, sys, urllib.request, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

URL = "https://gu-st.ru/content/partners/api_for_gu/Specifikaciya_API_EPGU_Prilozhenie_60025907.docx"
EXPECTED_SHA256 = "383ecf11f75bcee0221c57990b0a4be6943e0e56d1e76effebfa815f44dc3343"
OUT = Path("audit/goskey_60025907")
TMP = Path("/tmp/goskey_60025907.docx")
OUT.mkdir(parents=True, exist_ok=True)

req = urllib.request.Request(URL, headers={"User-Agent":"ETRN-Contract-Audit/1.0"})
with urllib.request.urlopen(req, timeout=90) as r:
    data = r.read()
TMP.write_bytes(data)
sha = hashlib.sha256(data).hexdigest()
if sha != EXPECTED_SHA256:
    raise SystemExit(f"SPEC_SHA256_MISMATCH expected={EXPECTED_SHA256} got={sha}")

NS = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
with zipfile.ZipFile(TMP) as z:
    names=z.namelist()
    if "word/document.xml" not in names:
        raise SystemExit("NO_WORD_DOCUMENT_XML")
    root=ET.fromstring(z.read("word/document.xml"))

    paragraphs=[]
    for p in root.findall(".//w:p",NS):
        text="".join(t.text or "" for t in p.findall(".//w:t",NS)).strip()
        if text:
            paragraphs.append(text)

    tables=[]
    for ti,tbl in enumerate(root.findall(".//w:tbl",NS),1):
        rows=[]
        for tr in tbl.findall("./w:tr",NS):
            cells=[]
            for tc in tr.findall("./w:tc",NS):
                txt=" ".join(
                    "".join(t.text or "" for t in p.findall(".//w:t",NS)).strip()
                    for p in tc.findall(".//w:p",NS)
                ).strip()
                cells.append(re.sub(r"\s+"," ",txt))
            rows.append(cells)
        tables.append(rows)

    # Search XML text and printable embedded binary strings only for narrow contract terms.
    blobs=[]
    for n in names:
        if n.lower().endswith((".xml",".xsd",".rels",".bin")):
            try: raw=z.read(n)
            except Exception: continue
            for enc in ("utf-8","utf-16le"):
                try: s=raw.decode(enc,errors="ignore")
                except Exception: continue
                if any(k.lower() in s.lower() for k in (
                    "sign_document_ukep_legalperson","signrequest","rforg","ogrn","snils","oid"
                )):
                    blobs.append((n,enc,s))

keywords=[
    "60025907","SignRequest","sign_document_ukep_legalperson","RFOrg","OGRN",
    "СНИЛС","Snils","OID","ИП","индивидуальн","документ","Document",
    "20","Description","SignExpiration","Attribute"
]
rx=re.compile("|".join(re.escape(k) for k in keywords),re.I)
matches=[]
for i,p in enumerate(paragraphs,1):
    if rx.search(p):
        matches.append({"kind":"paragraph","index":i,"text":p[:1200]})
for ti,rows in enumerate(tables,1):
    for ri,row in enumerate(rows,1):
        txt=" | ".join(row)
        if rx.search(txt):
            matches.append({"kind":"table","table":ti,"row":ri,"text":txt[:1800]})

# Deduplicate while preserving order and cap evidence to avoid reproducing the full specification.
seen=set(); compact=[]
for m in matches:
    key=m["text"]
    if key in seen: continue
    seen.add(key); compact.append(m)
    if len(compact)>=160: break

embedded=[]
for n,enc,s in blobs:
    snippets=[]
    for term in ("sign_document_ukep_legalperson","SignRequest","RFOrg","OGRN","Snils","OID"):
        pos=s.lower().find(term.lower())
        if pos>=0:
            clean=re.sub(r"\s+"," ",s[max(0,pos-250):pos+700])
            snippets.append(clean[:1000])
    if snippets:
        embedded.append({"entry":n,"encoding":enc,"snippets":snippets[:6]})

report={
    "url":URL,
    "sha256":sha,
    "bytes":len(data),
    "paragraph_count":len(paragraphs),
    "table_count":len(tables),
    "archive_entries":len(names),
    "key_windows": {
        "archive_rules_p135_150": [{"index": i+1, "text": paragraphs[i]} for i in range(134, min(150, len(paragraphs)))],
        "ogrn_xsd_p414_455": [{"index": i+1, "text": paragraphs[i]} for i in range(413, min(455, len(paragraphs)))],
        "response_root_p248_332": [{"index": i+1, "text": paragraphs[i]} for i in range(247, min(332, len(paragraphs)))],
        "result_xsd_p277_332": [{"index": i+1, "text": paragraphs[i]} for i in range(276, min(332, len(paragraphs)))],
        "request_xsd_p333_405": [{"index": i+1, "text": paragraphs[i]} for i in range(332, min(405, len(paragraphs)))],
        "snils_xsd_p406_421": [{"index": i+1, "text": paragraphs[i]} for i in range(405, min(421, len(paragraphs)))],
        "rest_examples_p566_648": [{"index": i+1, "text": paragraphs[i]} for i in range(565, min(648, len(paragraphs)))],
        "test_scenarios_p560_566": [{"index": i+1, "text": paragraphs[i]} for i in range(559, min(566, len(paragraphs)))],
    },
    "evidence":compact,
    "embedded_schema_or_contract_hits":embedded,
}
(OUT/"PRIMARY_SPEC_EVIDENCE.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

# Human-readable narrow report.
lines=[
    "# Госключ 60025907 — первичная спецификация",
    "",
    f"URL: {URL}",
    f"SHA-256: {sha}",
    f"Размер: {len(data)} байт",
    f"Параграфов: {len(paragraphs)}; таблиц: {len(tables)}",
    "",
    "## Выбранные строки/таблицы",
]
for m in compact:
    loc=(f"p{m['index']}" if m["kind"]=="paragraph" else f"table {m['table']} row {m['row']}")
    lines.append(f"- [{loc}] {m['text']}")
lines += ["","## Ключевые окна первичного текста"]
for title, window in report["key_windows"].items():
    lines.append("### " + title)
    for item in window:
        lines.append("- [p{0}] {1}".format(item["index"], item["text"]))
lines += ["","## Встроенная схема/контракт — найденные фрагменты"]
for e in embedded:
    lines.append(f"- {e['entry']} ({e['encoding']})")
    for s in e["snippets"]:
        lines.append(f"  - {s}")
(OUT/"PRIMARY_SPEC_EVIDENCE.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

print("PRIMARY_SPEC_AUDIT_PASS")
print(json.dumps({"sha256":sha,"evidence_count":len(compact),"embedded_hits":len(embedded)},ensure_ascii=False))
