#!/usr/bin/env python3
"""Genera noticias.json con los titulares de León de 20minutos.

Solo biblioteca estandar: se ejecuta igual en GitHub Actions y en local.
Fuente: https://www.20minutos.es/rss/castilla-y-leon/leon/
"""
import calendar
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

URL = "https://www.20minutos.es/rss/castilla-y-leon/leon/"
MAX = 26
FUENTE = "20minutos"
HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(HERE, "noticias.json")
UA = "Mozilla/5.0 (compatible; noticias-leon/1.0)"


def texto_bruto(s):
    return re.sub(r"<[^>]*>", " ", s or "").replace("\n", " ").strip()


def resumen(s, largo=200):
    t = texto_bruto(s)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > largo:
        t = t[:largo - 1].rsplit(" ", 1)[0] + "…"
    return t


def imagen(item):
    for tag in ("enclosure", "media:content", "media:thumbnail", "thumbnail"):
        el = item.find(tag)
        if el is not None:
            u = el.get("url") or el.get("href")
            if u:
                return u.strip()
    desc = item.findtext("description") or ""
    m = re.search(r'src=["\']([^"\']+)["\']', desc)
    if m:
        return m.group(1)
    return None


def fecha(s):
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(s.strip(), fmt).timestamp())
        except ValueError:
            continue
    return None


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    con = urllib.request.urlopen(req, timeout=45).read()
    raiz = ET.fromstring(con)

    salida, vistos = [], set()
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not titulo or not link or link in vistos:
            continue
        vistos.add(link)
        salida.append({
            "titulo": titulo,
            "link": link,
            "desc": resumen(it.findtext("description")),
            "img": imagen(it),
            "pub": fecha(it.findtext("pubDate")),
            "fuente": FUENTE,
        })

    salida.sort(key=lambda x: x["pub"] or 0, reverse=True)
    salida = salida[:MAX]

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    print("noticias.json: %d titulares" % len(salida))


if __name__ == "__main__":
    main()