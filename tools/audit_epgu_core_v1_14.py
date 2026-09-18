#!/usr/bin/env python3
import hashlib, json, re, urllib.request, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

URL="https://gu-st.ru/content/partners/api_for_gu/Specifikaciya_API_EPGU_v1_14.docx"
EXPECTED_SHA256="0c50f630f233935621d4c4f0b3ac0f3a714e355222120b5ef8bfd4fd48c36d31"
OUT=Path("audit/epgu_core_v1_14"); TMP=Path("/tmp/epgu_core_v1_14.docx")
OUT.mkdir(parents=True,exist_ok=True)
req=urllib.request.Request(URL,headers={"User-Agent":"ETRN-Contract-Audit/1.0"})
with urllib.request.urlopen(req,timeout=90) as r:data=r.read()
TMP.write_bytes(data)
sha=hashlib.sha256(data).hexdigest()
if sha!=EXPECTED_SHA256: raise SystemExit(f"CORE_SPEC_SHA256_MISMATCH expected={EXPECTED_SHA256} got={sha}")

NS={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
with zipfile.ZipFile(TMP) as z:
    root=ET.fromstring(z.read("word/document.xml"))
paragraphs=[]
for p in root.findall(".//w:p",NS):
    t="".join(x.text or "" for x in p.findall(".//w:t",NS)).strip()
    if t: paragraphs.append(re.sub(r"\s+"," ",t))
tables=[]
for ti,tbl in enumerate(root.findall(".//w:tbl",NS),1):
    for ri,tr in enumerate(tbl.findall("./w:tr",NS),1):
        cells=[]
        for tc in tr.findall("./w:tc",NS):
            txt=" ".join("".join(x.text or "" for x in p.findall(".//w:t",NS)).strip() for p in tc.findall(".//w:p",NS)).strip()
            cells.append(re.sub(r"\s+"," ",txt))
        tables.append({"table":ti,"row":ri,"text":" | ".join(cells)})

terms=[
 "Authorization","Bearer","api/gusmev/order","api/gusmev/push","push/chunked",
 "multipart/form-data","orderResponseFiles","currentStatusHistoryId","objectType",
 "mnemonic","serviceCode","targetCode","region","chunk","chunks","50 МБ","50 Мб",
 "5 МБ","5 Мб","meta","orderId","files/download","getUpdatedAfter","accessTkn"
]
rx=re.compile("|".join(re.escape(x) for x in terms),re.I)
matches=[]
for i,t in enumerate(paragraphs,1):
    if rx.search(t):matches.append({"kind":"p","index":i,"text":t[:1400]})
for x in tables:
    if rx.search(x["text"]):matches.append({"kind":"table",**x,"text":x["text"][:2200]})
# Deduplicate and keep enough surrounding evidence without reproducing the specification.
seen=set(); selected=[]
for m in matches:
    if m["text"] in seen:continue
    seen.add(m["text"]);selected.append(m)
    if len(selected)>=220:break

# Add small context windows around endpoint paragraphs.
anchors={}
for term in ["api/gusmev/order","/api/gusmev/push","/api/gusmev/push/chunked","Authorization","orderResponseFiles","50 МБ","50 Мб"]:
    found=[]
    for i,t in enumerate(paragraphs):
        if term.lower() in t.lower():
            found.append([{"index":j+1,"text":paragraphs[j]} for j in range(max(0,i-3),min(len(paragraphs),i+6))])
            if len(found)>=4:break
    anchors[term]=found

report={"url":URL,"sha256":sha,"bytes":len(data),"paragraph_count":len(paragraphs),
"key_windows":{
    "chunked_rules_p232_307":[{"index":i+1,"text":paragraphs[i]} for i in range(231,min(307,len(paragraphs)))],
    "push_rules_p308_358":[{"index":i+1,"text":paragraphs[i]} for i in range(307,min(358,len(paragraphs)))],
    "order_details_p520_860":[{"index":i+1,"text":paragraphs[i]} for i in range(519,min(860,len(paragraphs))) if any(k.lower() in paragraphs[i].lower() for k in ("currentstatushistoryid","orderresponsefiles","filename","mnemonic","id","type","mimetype"))],
},
"selected":selected,"anchors":anchors}
(OUT/"CORE_SPEC_EVIDENCE.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["# API ЕПГУ v1.14 — первичный контракт","",f"URL: {URL}",f"SHA-256: {sha}",f"Размер: {len(data)} байт","","## Выбранные строки и таблицы"]
for m in selected:
    loc=f"p{m['index']}" if m["kind"]=="p" else f"table {m['table']} row {m['row']}"
    lines.append(f"- [{loc}] {m['text']}")
lines+=["","## Контекст вокруг ключевых терминов"]
for term,groups in anchors.items():
    lines.append("### "+term)
    for group in groups:
        lines.append("- окно:")
        for x in group:lines.append(f"  - [p{x['index']}] {x['text']}")
(OUT/"CORE_SPEC_EVIDENCE.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print("EPGU_CORE_SPEC_AUDIT_PASS")
print(json.dumps({"sha256":sha,"matches":len(selected)},ensure_ascii=False))
