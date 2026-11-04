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
import html as htmlmod
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

FUENTES = [
    ("La Nueva Crónica", [
        "https://www.lanuevacronica.com/uploads/feeds/feed_la-nueva-cronica_es.xml",
        "https://www.lanuevacronica.com/rss"
    ]),
    ("La Nueva Crónica · Opinión", ["https://www.lanuevacronica.com/opinion"]),
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
MAX_OPINIONES = 30     # opiniones conservadas aunque salgan del RSS
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


MARCADORES_OPINION = {
    "opinion", "opiniones", "columna", "columnas", "columnista", "columnistas",
    "comentario", "comentarios", "tribuna", "editorial", "firma", "firmas",
    "blog", "blogs", "vineta",
}
_CACHE_PERFILES = {}
_CACHE_IMAGENES = {}


def normalizar(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def es_opinion(link, *pistas):
    """Detecta opinion por la ruta de la URL o por la seccion del feed/articulo."""
    ruta = urllib.parse.urlparse(link or "").path
    segmentos = {normalizar(s) for s in ruta.split("/") if s}
    if segmentos & MARCADORES_OPINION:
        return True
    texto = normalizar(" ".join(str(p or "") for p in pistas))
    return any(re.search(r"(^| )%s( |$)" % re.escape(m), texto)
               for m in MARCADORES_OPINION)


def _json_ld(html):
    salida = []
    patron = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for m in re.finditer(patron, html, re.S | re.I):
        bruto = m.group(1).strip()
        for texto in (bruto, htmlmod.unescape(bruto)):
            try:
                salida.append(json.loads(texto))
                break
            except Exception:
                continue
    return salida


def _objetos(dato):
    if isinstance(dato, dict):
        yield dato
        for valor in dato.values():
            for obj in _objetos(valor):
                yield obj
    elif isinstance(dato, list):
        for valor in dato:
            for obj in _objetos(valor):
                yield obj


def _tipos(obj):
    t = obj.get("@type") if isinstance(obj, dict) else None
    if isinstance(t, str):
        return {t}
    return set(t or [])


def _url_de(valor):
    if isinstance(valor, str):
        return valor.strip() if valor.strip().startswith(("http://", "https://")) else ""
    if isinstance(valor, dict):
        return _url_de(valor.get("url") or valor.get("contentUrl") or valor.get("image"))
    if isinstance(valor, list):
        for item in valor:
            url = _url_de(item)
            if url:
                return url
    return ""


def _lista_autores(valor):
    if isinstance(valor, str):
        return [{"name": valor}]
    if isinstance(valor, dict):
        return [valor]
    if isinstance(valor, list):
        return [a for a in valor if isinstance(a, (str, dict))]
    return []


def _meta(html, nombre):
    patron = r'<meta\b[^>]*\b(?:property|name)=["\']%s["\'][^>]*>' % re.escape(nombre)
    for tag in re.findall(patron, html, re.I):
        m = re.search(r'\bcontent=["\']([^"\']*)["\']', tag, re.I)
        if m:
            return htmlmod.unescape(m.group(1)).strip()
    return ""


def _mismo_nombre(a, b):
    a, b = normalizar(a), normalizar(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return (" %s " % a) in (" %s " % b)


def _persona_valida(nombre):
    n = normalizar(nombre)
    if len(n) < 4:
        return False
    return not any(x in n for x in (
        "editorial", "redaccion", "agencia", "europa press", "efe",
        "anonimo", "colaborador", "lnc",
    ))


def _meta_articulo(html):
    articulo = None
    for dato in _json_ld(html):
        for obj in _objetos(dato):
            if not _tipos(obj) & {"NewsArticle", "Article", "ReportageNewsArticle",
                                   "OpinionNewsArticle", "BlogPosting"}:
                continue
            if articulo is None or obj.get("articleSection") or obj.get("author"):
                articulo = obj
    if not articulo:
        return "", [], ""
    return (articulo.get("articleSection") or "",
            _lista_autores(articulo.get("author")),
            articulo.get("genre") or "")


def _autores_html(html):
    autores = []
    patron = r'<a\b[^>]*\brel=["\'][^"\']*author[^"\']*["\'][^>]*>(.*?)</a>'
    for m in re.finditer(patron, html, re.S | re.I):
        tag = m.group(0)
        href = re.search(r'\bhref=["\']([^"\']+)', tag, re.I)
        nombre = htmlmod.unescape(re.sub(r"<[^>]*>", " ", m.group(1)))
        nombre = re.sub(r"\s+", " ", nombre).strip()
        autores.append({"name": nombre, "url": href.group(1).strip() if href else ""})
    return autores


def _imagen_ok(url):
    if url in _CACHE_IMAGENES:
        return _CACHE_IMAGENES[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            tipo = (resp.headers.get("Content-Type") or "").lower()
            ok = tipo.startswith("image/")
    except Exception:
        ok = False
    _CACHE_IMAGENES[url] = ok
    return ok


def _retrato_fiable(url, original, dimensiones=None):
    if not url or url == original:
        return False
    bajo = urllib.parse.unquote(url).lower()
    if any(x in bajo for x in ("logo", "placeholder", "sinfoto", "noimage",
                               "no-image", "default", "generico", "avatar")):
        return False
    if dimensiones:
        ancho, alto = dimensiones
        if ancho and alto and (ancho / float(alto) > 1.6 or alto / float(ancho) > 2.2):
            return False
    return _imagen_ok(url)


def _perfil(url):
    if url in _CACHE_PERFILES:
        return _CACHE_PERFILES[url]
    try:
        html = descargar(url, intentos=1).decode("utf-8", "ignore")
    except Exception:
        html = ""
    _CACHE_PERFILES[url] = html
    return html


def _src_imagen(tag):
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        m = re.search(r'\b%s=["\']([^"\']+)["\']' % attr, tag, re.I)
        if m:
            return htmlmod.unescape(m.group(1)).strip()
    m = re.search(r'\bsrcset=["\']([^"\']+)["\']', tag, re.I)
    if m:
        return htmlmod.unescape(m.group(1).split(",")[0].strip().split(" ")[0])
    return ""


def _retrato_html(html, nombre, original):
    """Retrato del autor publicado junto a su firma en el propio articulo."""
    if not html:
        return None
    texto = htmlmod.unescape(html)
    palabras = [re.escape(p) for p in nombre.split() if p]
    if not palabras:
        return None
    patron = r"\s+".join(palabras)
    for m in re.finditer(patron, texto, re.I):
        ventana = texto[max(0, m.start() - 2500):m.end() + 2500]
        for cont in re.finditer(
                r'<[^>]+class=["\'][^"\']*(?:author-photo|author-avatar|'
                r'avatar-author|photo-author|autor-foto|foto-autor)[^"\']*["\'][^>]*>',
                ventana, re.I):
            img = re.search(r'<img\b[^>]*>', ventana[cont.end():cont.end() + 1200], re.I)
            if not img:
                continue
            url = _src_imagen(img.group(0))
            if url and _retrato_fiable(url, original, None):
                return url
        for img in re.finditer(r'<img\b[^>]*>', ventana, re.I):
            tag = img.group(0)
            if not re.search(r'(?:class|alt|id)=["\'][^"\']*(?:author|autor|avatar|perfil)[^"\']*["\']',
                             tag, re.I):
                continue
            url = _src_imagen(tag)
            if url and _retrato_fiable(url, original, None):
                return url
    return None


def _retrato_autor(articulo, autores, original):
    """Busca el retrato en los metadatos del autor, nunca en la foto del articulo."""
    if not autores:
        autores = _autores_html(articulo)
    for autor in autores:
        if isinstance(autor, str):
            autor = {"name": autor}
        nombre = autor.get("name") or ""
        if not _persona_valida(nombre):
            continue
        url_perfil = autor.get("url") or ""
        imagen = _url_de(autor.get("image"))
        if imagen and _retrato_fiable(imagen, original, None):
            return imagen
        imagen = _retrato_html(articulo, nombre, original)
        if imagen:
            return imagen
        if not url_perfil:
            continue
        perfil = _perfil(url_perfil)
        if not perfil:
            continue
        for dato in _json_ld(perfil):
            for obj in _objetos(dato):
                if "Person" not in _tipos(obj) or not _mismo_nombre(obj.get("name"), nombre):
                    continue
                imagen = _url_de(obj.get("image") or obj.get("imageUrl"))
                if _retrato_fiable(imagen, original, None):
                    return imagen
        titulo = _meta(perfil, "og:title")
        imagen = _meta(perfil, "og:image")
        ancho = _meta(perfil, "og:image:width")
        alto = _meta(perfil, "og:image:height")
        dimensiones = (int(ancho), int(alto)) if ancho.isdigit() and alto.isdigit() else None
        if _mismo_nombre(titulo, nombre) and _retrato_fiable(imagen, original, dimensiones):
            return imagen
    return None


def _enriquecer(link, imagen_original, seccion):
    if not es_opinion(link, seccion):
        return "noticia", "", ""
    html = _perfil(link)
    if not html:
        return "opinion", "", ""
    section, autores, genre = _meta_articulo(html)
    tipo = "opinion" if es_opinion(link, seccion, section, genre) else "noticia"
    if tipo != "opinion":
        return tipo, "", ""
    autor = ""
    for a in autores:
        nombre = a.get("name") if isinstance(a, dict) else a
        if _persona_valida(nombre):
            autor = nombre.strip()
            break
    retrato = _retrato_autor(html, autores, imagen_original)
    return tipo, autor, retrato


def _es_lnc_opinion(url):
    partes = urllib.parse.urlparse(url or "")
    return partes.netloc.endswith("lanuevacronica.com") and partes.path.rstrip("/") == "/opinion"


def _fecha_articulo(html):
    for dato in _json_ld(html):
        for obj in _objetos(dato):
            if _tipos(obj) & {"NewsArticle", "Article", "ReportageNewsArticle",
                               "OpinionNewsArticle", "BlogPosting"}:
                pub = fecha(obj.get("datePublished") or obj.get("dateCreated"))
                if pub:
                    return pub
    return fecha(_meta(html, "article:published_time"))


def _lnc_opinion_items(url):
    """Lee el listado HTML de opinion de LNC, que no aparece en su RSS."""
    html = descargar(url).decode("utf-8", "ignore")
    out, vistos = [], set()
    for dato in _json_ld(html):
        for obj in _objetos(dato):
            if "ItemList" not in _tipos(obj):
                continue
            for elem in obj.get("itemListElement") or []:
                if not isinstance(elem, dict):
                    continue
                titulo = (elem.get("name") or "").strip()
                link = (elem.get("url") or "").strip()
                if not titulo or not link or link in vistos:
                    continue
                vistos.add(link)
                articulo = _perfil(link)
                out.append({
                    "titulo": titulo,
                    "link": link,
                    "desc": "",
                    "img": None,
                    "pub": _fecha_articulo(articulo) if articulo else None,
                })
    return out


def _noticia(nombre, titulo, link, desc, img, pub, autor_rss, seccion):
    tipo, autor, retrato = _enriquecer(link, img, seccion)
    if tipo == "opinion" and not autor and _persona_valida(autor_rss):
        autor = autor_rss
    if tipo != "opinion":
        autor, retrato = "", ""
    noticia = {
        "titulo": titulo,
        "link": link,
        "desc": resumen(desc),
        "img": img,
        "tipo": tipo,
        "pub": pub,
        "fuente": nombre,
    }
    if autor:
        noticia["autor"] = autor
    if retrato:
        noticia["autorImg"] = retrato
    return noticia


def leer(nombre, urls):
    """Devuelve (titulares, url_ok). Prueba las URLs candidatas en orden."""
    errores = []
    for url in urls:
        try:
            if _es_lnc_opinion(url):
                out = [_noticia(nombre, n["titulo"], n["link"], n["desc"],
                                n["img"], n["pub"], "", "opinion")
                       for n in _lnc_opinion_items(url)]
                if out:
                    return out, url
                errores.append("%s -> pagina vacia" % url)
                continue

            con = descargar(url)
            # Manejo basico de namespaces
            xml_str = con.decode("utf-8", "ignore")
            # dc:creator -> creator, content:encoded -> encoded
            xml_str = re.sub(r'<(dc|content|media|thumbnail):', r'<\1_', xml_str)
            xml_str = re.sub(r'</(dc|content|media|thumbnail):', r'</\1_', xml_str)
            
            raiz = ET.fromstring(xml_str)
            out = []
            for it in raiz.iter("item"):
                titulo = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if not titulo or not link:
                    continue
                
                out.append(_noticia(
                    nombre, titulo, link, it.findtext("description"),
                    imagen(it),
                    fecha(it.findtext("pubDate")),
                    (it.findtext("dc_creator") or it.findtext("author") or "").strip(),
                    (it.findtext("category") or "").strip(),
                ))
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


def noticias_previas():
    try:
        with open(SALIDA, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except Exception:
        return []


def main():
    ahora = int(time.time())
    previo = estado_previo()
    anteriores = noticias_previas()
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

    # 0) conserva opiniones ya publicadas aunque hayan salido del RSS
    for n in anteriores:
        link = n.get("link")
        if not link or link in vistos:
            continue
        if n.get("tipo") == "opinion" or es_opinion(link, n.get("seccion", "")):
            n = dict(n)
            n["tipo"] = "opinion"
            vistos.add(link)
            salida.append(n)

    # 1) reparte un minimo por fuente (para que todas esten representadas)
    salida.sort(key=lambda x: x["pub"] or 0, reverse=True)
    activas = sorted({n["fuente"] for n in salida})
    minimo_por_fuente = max(5, min(8, MAX // max(1, len(activas))))
    elegidos, por_fuente, ya = [], {}, set()

    # 1a) las opiniones entran primero, hasta MAX_OPINIONES
    opiniones = 0
    for n in salida:
        if n.get("tipo") != "opinion":
            continue
        if opiniones >= MAX_OPINIONES:
            break
        f = n["fuente"]
        elegidos.append(n)
        ya.add(id(n))
        por_fuente[f] = por_fuente.get(f, 0) + 1
        opiniones += 1

    # 1b) minimo por fuente, sin repetir lo ya elegido
    for n in salida:
        if id(n) in ya:
            continue
        f = n["fuente"]
        if por_fuente.get(f, 0) < minimo_por_fuente:
            por_fuente[f] = por_fuente.get(f, 0) + 1
            elegidos.append(n)
            ya.add(id(n))
    # 2) rellena el resto con lo mas reciente que quede
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
