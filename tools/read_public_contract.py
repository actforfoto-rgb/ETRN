"""Read only the public Saby documentation, with no user session or credentials."""
from pathlib import Path
from urllib.request import urlopen, Request
from html.parser import HTMLParser
import re, html, hashlib
url = 'https://link.saby.ru/knowledge-bases/23014b22-bab8-4a8a-abcd-823f916aa7f2?article=a5755bb6-fdd5-43dd-b7d3-12bf843cee67&mode=readList&published=true'
Path('audit').mkdir(exist_ok=True)
try:
    with urlopen(Request(url,headers={'User-Agent':'ETRN-Contract-Audit/1.0'}),timeout=20) as r:
        data=r.read(8*1024*1024)
    text=data.decode('utf-8')
    Path('audit/public_contract.html').write_text(text,encoding='utf-8')
    out=['URL='+url,'SHA256='+hashlib.sha256(data).hexdigest()]
    class P(HTMLParser):
        def handle_data(self,s):
            if any(k in s for k in ['Params','sabyCryptoOperation','createDetachedSign']):
                out.append(s[:20000])
    P().feed(text)
    if len(out)<3:
        for m in list(re.finditer('sabyCryptoOperation|Params|createDetachedSign',html.unescape(text)))[:12]:
            out.append(html.unescape(text)[max(0,m.start()-200):m.end()+650])
    Path('audit/EXTRACT.txt').write_text('\n\n'.join(out),encoding='utf-8')
    print('\n\n'.join(out)[:24000])
except Exception as e:
    Path('audit/EXTRACT.txt').write_text('PUBLIC_DOC_FETCH_FAILED: '+str(e),encoding='utf-8')
    print('Public doc read did not succeed:',type(e).__name__)
