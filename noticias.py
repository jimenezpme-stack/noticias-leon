#!/usr/bin/env python3
"""Genera noticias.json con los titulares de León de varias fuentes frescas.

Solo biblioteca estandar: se ejecuta igual en GitHub Actions y en local.
Fuentes (todas de León / provincia, con foto):
  - La Nueva Cronica: https://www.lanuevacronica.com/rss
  - El Bierzo Digital: https://www.elbierzodigital.com/feed/
"""
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

FUENTES = [
    ("La Nueva Crónica", "https://www.lanuevacronica.com/rss"),
    ("El Bierzo Digital", "https://www.elbierzodigital.com/feed/"),
    ("20minutos", "https://www.20minutos.es/rss/castilla-y-leon/leon/"),
]
MAX = 80
HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(HERE, "noticias.json")
UA = "Mozilla/5.0 (compatible; noticias-leon/1.0)"


def texto_bruto(s):
    return re.sub(r"<[^>]*>", " ", s or "").replace("\n", " ").strip()


def resumen(s, largo=200):
    t = re.sub(r"\s+", " ", texto_bruto(s)).strip()
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
                "%d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return int(datetime.strptime(s.strip(), fmt).timestamp())
        except ValueError:
            continue
    return None


def leer(nombre, url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    con = urllib.request.urlopen(req, timeout=45).read()
    raiz = ET.fromstring(con)
    out = []
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not titulo or not link:
            continue
        out.append({
            "titulo": titulo,
            "link": link,
            "desc": resumen(it.findtext("description")),
            "img": imagen(it),
            "pub": fecha(it.findtext("pubDate")),
            "fuente": nombre,
        })
    return out


def main():
    salida, vistos = [], set()
    for nombre, url in FUENTES:
        try:
            for n in leer(nombre, url):
                if n["link"] in vistos:
                    continue
                vistos.add(n["link"])
                salida.append(n)
        except Exception as e:
            print("aviso: %s no respondio (%s)" % (nombre, e))

    salida.sort(key=lambda x: x["pub"] or 0, reverse=True)
    salida = salida[:MAX]

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    print("noticias.json: %d titulares" % len(salida))


if __name__ == "__main__":
    main()
