import json, time
from datetime import datetime, timezone
import feedparser

URL = "https://www.20minutos.es/rss/leon/"
MAX = 26

d = feedparser.parse(URL)
items = []
for e in d.entries:
    title = (e.get("title") or "").strip()
    link = (e.get("link") or "").strip()
    desc = e.get("summary") or e.get("description") or ""
    img = None
    m = e.get("media_content") or e.get("media_thumbnail")
    if m and isinstance(m, list) and m[0].get("url"):
        img = m[0]["url"]
    if not img:
        import re
        s = str(desc)
        mm = re.search(r'src=["\']([^"\']+)["\']', s)
        if mm: img = mm.group(1)
    pub = e.get("published_parsed") or e.get("updated_parsed")
    ts = None
    if pub:
        try: ts = int(datetime(*pub[:6], tzinfo=timezone.utc).timestamp())
        except: ts = None
    if not ts: ts = int(time.time())
    items.append({"title":title,"link":link,"img":img,"pub":ts})
# dedup
seen=set(); out=[]
for i in items:
    k=i["link"] or i["title"]
    if k in seen: continue
    seen.add(k); out.append(i)
out.sort(key=lambda x:x.get("pub",0), reverse=True)
out=out[:MAX]
with open("noticias.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
