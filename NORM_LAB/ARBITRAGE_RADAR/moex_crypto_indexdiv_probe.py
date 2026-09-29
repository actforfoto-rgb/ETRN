from __future__ import annotations

import json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 NORM-LAB/1.0"})
IDS=["BTCUSDF","ETHUSDF","SOLUSDF","XRPUSDF","TRXUSDF"]

def main():
 out={}
 for sid in IDS:
  url=f"https://www.moex.com/ru/derivatives/perpetual-futures/{sid}"
  try:
   r=S.get(url,timeout=30);r.raise_for_status()
   text=BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
   # Preserve snippets around the labels; server-rendering may or may not include values.
   snippets={}
   for label in ("Индекс дивидендов","Фандинг, руб."):
    i=text.find(label)
    snippets[label]=text[max(0,i-200):i+400] if i>=0 else None
   out[sid]={"status":r.status_code,"html_chars":len(r.text),"snippets":snippets}
  except Exception as e:
   out[sid]={"error":f"{type(e).__name__}: {e}"}
 (OUT/"moex_crypto_indexdiv_page_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
