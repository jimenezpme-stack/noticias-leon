#!/usr/bin/env python3
"""Genera noticias.json con los titulares de León de varias fuentes frescas.

Solo biblioteca estandar: se ejecuta igual en GitHub Actions y en local.
Cada fuente puede declarar varias URLs candidatas: se usa la primera que
responda con un RSS valido.

Reglas de publicacion:
  - Se ordenan por fecha (lo mas reciente primero).
  - MAX total de titulares y tope por fuente, pero con un minimo garantizado
    por fuente para que ningun medio se quede fuera de la lista.
  - Si una ejecucion recoge muy pocos titulares NO se sobrescribe el json
    bueno que habia (evita que un fallo puntual deje la web sin noticias).
  - Los feeds que fallan se anotan en estado.json y no se reintentan hasta
    12 horas despues (el workflow corre cada 5 minutos).
"""
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

FUENTES = [
    ("La Nueva Crónica", ["https://www.lanuevacronica.com/rss"]),
    ("El Bierzo Digital", ["https://www.elbierzodigital.com/feed/"]),
    ("Heraldo de León", ["https://www.heraldodeleon.es/rss/"]),
    ("Heraldo de León · León", ["https://www.heraldodeleon.es/rss/leon/"]),
    ("El Español · CyL", ["https://www.elespanol.com/rss/castilla-y-leon/"]),
    ("eldiario.es · CyL", ["https://www.eldiario.es/rss/castilla-y-leon/"]),
    ("20minutos León", ["https://www.20minutos.es/rss/castilla-y-leon/leon/"]),
    ("20minutos CyL", ["https://www.20minutos.es/rss/castilla-y-leon/"]),
    # Canales locales extra: se activan solos si su feed responde.
    ("León4Sport", [
        "https://www.leon4sport.com/feed/",
        "https://www.leon4sport.com/rss/",
        "https://leon4sport.com/feed/",
    ]),
    ("Pueblo de León", [
        "https://www.puebledeleon.es/feed/",
        "https://puebledeleon.es/feed/",
        "https://www.puebledeleon.es/rss/",
    ]),
    ("León14 Actualidad", [
        "https://www.leon14actualidad.com/feed/",
        "https://leon14actualidad.com/feed/",
        "https://www.leon14actualidad.com/rss/",
    ]),
    ("Onda Regional", [
        "https://www.ondaregional.es/rss/",
        "https://ondaregional.es/feed/",
        "https://www.ondaregional.es/rss.xml",
    ]),
    ("Televisión CyL", [
        "https://www.rtvcyltv.es/rss/",
        "https://www.rtvcyltv.es/feed/",
        "https://www.televisioncastillayleon.com/rss/",
        "https://www.televisioncastillayleon.com/feed/",
    ]),
    ("Alfarje", [
        "https://alfarje.com/feed/",
        "https://www.alfarje.com/feed/",
        "https://alfarje.com/rss/",
    ]),
    ("León Semanal", [
        "https://www.leonsemanal.com/feed/",
        "https://leonsemanal.com/feed/",
    ]),
]

MAX = 100              # titulares publicados en total
MAX_POR_FUENTE = 30    # tope por fuente
MINIMO = 20            # por debajo de esto no se sobrescribe el json bueno
MAX_DIAS = 14          # se descartan titulares mas antiguos de esto
REINTENTO_HORAS = 12   # espera antes de volver a probar un feed caido
HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(HERE, "noticias.json")
ESTADO = os.path.join(HERE, "estado.json")
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


def descargar(url, intentos=2, espera=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for n in range(intentos):
        try:
            return urllib.request.urlopen(req, timeout=45).read()
        except Exception:
            if n + 1 < intentos:
                time.sleep(espera)
            else:
                raise


def leer(nombre, urls):
    """Devuelve (titulares, url_ok). Prueba las URLs candidatas en orden."""
    errores = []
    for url in urls:
        try:
            con = descargar(url)
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
            if out:
                return out, url
            errores.append("%s -> feed vacio" % url)
        except Exception as e:
            errores.append("%s -> %s" % (url, str(e)[:60]))
    raise RuntimeError(" | ".join(errores[:2]))


def estado_previo():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            datos = json.load(f)
        return {e["fuente"]: e for e in datos.get("estado", []) if e.get("fuente")}
    except Exception:
        return {}


def main():
    ahora = int(time.time())
    previo = estado_previo()
    salida, vistos, estado = [], set(), []

    for nombre, urls in FUENTES:
        ant = previo.get(nombre)
        if ant and not ant.get("ok") and ahora - int(ant.get("ts", 0)) < REINTENTO_HORAS * 3600:
            faltan = REINTENTO_HORAS - (ahora - int(ant.get("ts", 0))) // 3600
            estado.append({"fuente": nombre, "url": ant.get("url"), "ok": False,
                           "error": "omitida, se reintenta en %dh" % faltan,
                           "ts": int(ant.get("ts", ahora))})
            print("skip %-28s caida hace poco (reintento en %dh)" % (nombre, faltan))
            continue
        candidatas = list(urls)
        if ant and ant.get("ok") and ant.get("url"):
            candidatas = [ant["url"]] + [u for u in urls if u != ant["url"]]
        try:
            titulares, url_ok = leer(nombre, candidatas)
            frescos = [t for t in titulares
                       if not t["pub"] or t["pub"] >= ahora - MAX_DIAS * 86400]
            nuevos = 0
            for n in frescos:
                if n["link"] in vistos:
                    continue
                vistos.add(n["link"])
                salida.append(n)
                nuevos += 1
            estado.append({
                "fuente": nombre, "url": url_ok, "ok": True,
                "items": len(titulares), "nuevos": nuevos, "ts": ahora,
                "con_foto": sum(1 for t in frescos if t["img"]),
            })
            print("ok   %-28s %2d titulares (%2d frescos, %2d con foto) %s"
                  % (nombre, len(titulares), len(frescos),
                     sum(1 for t in frescos if t["img"]), url_ok))
        except Exception as e:
            estado.append({"fuente": nombre, "url": candidatas[0], "ok": False,
                           "error": str(e)[:200], "ts": ahora})
            print("aviso %-28s no respondio (%s)" % (nombre, str(e)[:80]))

    # 1) reparte un minimo por fuente (para que todas esten representadas)
    salida.sort(key=lambda x: x["pub"] or 0, reverse=True)
    activas = sorted({n["fuente"] for n in salida})
    minimo_por_fuente = max(5, min(8, MAX // max(1, len(activas))))
    elegidos, por_fuente = [], {}
    for n in salida:
        f = n["fuente"]
        if por_fuente.get(f, 0) < minimo_por_fuente:
            por_fuente[f] = por_fuente.get(f, 0) + 1
            elegidos.append(n)
    # 2) rellena el resto con lo mas reciente que quede
    ya = {id(n) for n in elegidos}
    for n in salida:
        if len(elegidos) >= MAX:
            break
        if id(n) in ya:
            continue
        f = n["fuente"]
        if por_fuente.get(f, 0) >= MAX_POR_FUENTE:
            continue
        por_fuente[f] = por_fuente.get(f, 0) + 1
        elegidos.append(n)
    # 3) orden final por fecha
    elegidos.sort(key=lambda x: x["pub"] or 0, reverse=True)
    finales = elegidos[:MAX]

    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump({"estado": estado, "publicadas": len(finales),
                   "reparto": por_fuente, "minimo_por_fuente": minimo_por_fuente},
                  f, ensure_ascii=False, indent=2)

    if len(finales) < MINIMO:
        print("ATENCION: solo %d titulares, no se sobrescribe el json" % len(finales))
        sys.exit(0)

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(finales, f, ensure_ascii=False, indent=2)
    print("noticias.json: %d titulares" % len(finales))
    for f, c in sorted(por_fuente.items(), key=lambda x: -x[1]):
        print("   %-28s %d" % (f, c))


if __name__ == "__main__":
    main()
