# -*- coding: utf-8 -*-
"""Autobuses del Consorcio de Transportes de Asturias (CTA): urbanos de Avilés, Oviedo (TUA) y Mieres
(y, más adelante, interurbanos), con el horario oficial (GTFS del Consorcio).

Ninguno publica tiempo real abierto (la app CTA Conecta lo tiene en Avilés y la de TUA en Oviedo, pero
no es un dato público): las horas son las del horario oficial y la interfaz lo dice claramente.

Formato que se manda al navegador (todo el día de una red, unos 25 KB comprimido):
    {"red", "nombre", "fecha", "lineas": {codigo: {codigo, nombre, color}},
     "paradas": {id: [nombre, localidad, lat, lon]},
     "variantes": [{"linea", "destino", "paradas": [id...], "forma": [[lat, lon]...] | None}],
     "patrones": [[minutos desde la salida en cada parada...]],
     "viajes": [[variante, salida (min desde medianoche), patrón]]}
"""
import csv
import io
import json
import os
import re
import time
import zipfile
from collections import Counter, defaultdict

from .util import CACHE, http_get

# Copia pública diaria del GTFS del Consorcio (el original del Punto de Acceso Nacional pide registro)
CTA_URLS = ["https://files.mobilitydatabase.org/mdb-2827/latest.zip"]
VERSION_CACHE = 2

REDES = {
    "aviles": {"nombre": "Avilés", "agencias": ["28"], "color": "#0b5cab",
               "operador": "Compañía del Tranvía Eléctrico de Avilés"},
    "oviedo": {"nombre": "Oviedo", "agencias": ["51"], "color": "#c8102e",
               "operador": "TUA · Transportes Unidos de Asturias"},
    "mieres": {"nombre": "Mieres", "agencias": ["59"], "color": "#00796b",
               "operador": "EMUTSA"},
}
NOCTURNOS = {"pie": "Búho", "buh": "Búho", "buho": "Búho", "búho": "Búho", "curuxa": "Curuxa"}
MENORES = {"de", "del", "la", "las", "los", "el", "y", "a", "en"}
PALETA = ["#e2231a", "#1d70b8", "#00965e", "#f39200", "#7b3f98", "#00a3e0", "#c2185b", "#5d4037",
          "#2e7d32", "#ef6c00", "#455a64", "#6a1b9a", "#00838f", "#ad1457"]


def obtener_zip(forzar=False):
    os.makedirs(CACHE, exist_ok=True)
    ruta = os.path.join(CACHE, "cta_gtfs.zip")
    if not forzar and os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < 20 * 3600:
        return ruta
    errores = []
    for url in CTA_URLS:
        try:
            datos = http_get(url, timeout=180)
            tmp = ruta + ".tmp"
            with open(tmp, "wb") as f:
                f.write(datos)
            zipfile.ZipFile(tmp).namelist()
            os.replace(tmp, ruta)
            return ruta
        except Exception as e:  # noqa: BLE001
            errores.append("%s: %s" % (url, e))
    if os.path.exists(ruta):
        return ruta
    raise RuntimeError("No se pudo descargar el horario del Consorcio: " + "; ".join(errores))


def _csv(z, nombre):
    with z.open(nombre) as f:
        rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace", newline=""))
        cab = [c.strip() for c in next(rd)]
        for fila in rd:
            yield dict(zip(cab, (c.strip() for c in fila)))


def _hora(s):
    h, m, sg = (s.split(":") + ["0", "0"])[:3]
    return int(h) * 60 + int(m) + int(sg or 0) / 60.0


def _titulo(s):
    s = s.strip().lower()
    s = re.sub(r"(^|[\s/(\-])(\w)", lambda m: m.group(1) + m.group(2).upper(), s)
    # «Mieres Del Camín» -> «Mieres del Camín» (salvo al principio)
    return re.sub(r"(?<=\s)(\w+)", lambda m: m.group(1).lower() if m.group(1).lower() in MENORES else m.group(1), s)


def _bonito(s):
    """Los textos en mayúsculas («LLAMAQUIQUE»), en minúsculas con mayúscula inicial."""
    s = (s or "").strip()
    return _titulo(s) if s and s.upper() == s and any(c.isalpha() for c in s) else s


def codigo_linea(r):
    """Código corto de una línea. El GTFS del Consorcio corta los códigos a 3 caracteres
    («L1.» por «L1.1», «Mie» por los Curuxa de Mieres, «BUH» por el Búho de Oviedo)."""
    corto = (r.get("route_short_name") or "").strip() or r.get("route_id", "")
    largo = (r.get("route_long_name") or "").strip()
    if "curuxa" in largo.lower():
        return "Curuxa"
    m = re.match(r"^(\S+?)(?:\s|-|$)", largo)
    if m and m.group(1) != corto and m.group(1).startswith(corto.rstrip(".")) and re.search(r"\d", m.group(1)):
        return m.group(1)
    return corto


def _quita_codigo(texto, cod):
    """«A1-LLAMAQUIQUE» / «L2 Mieres» / «L1 - La Luz» -> sin el código de delante."""
    t = (texto or "").strip()
    t2 = re.sub(r"^\s*L?\w*\d[\w.]*\s*-\s*", "", t)
    if t2 != t:
        return t2.strip()
    if cod and re.match(re.escape(cod) + r"\d*(\s*-\s*|\s+)", t, re.I):
        return re.sub(re.escape(cod) + r"\d*(\s*-\s*|\s+)", "", t, count=1, flags=re.I).strip()
    return t


def limpia_parada(nombre):
    """«[AVILÉS]  Cristalería [CTA 04317]» -> («Cristalería», «Avilés»);
    «[PIEDRASBLANCAS|Piedras Blancas]  Eysines [CTA 03075]» -> («Eysines», «Piedras Blancas»)."""
    m = re.match(r"^\[([^\]]*)\]\s*(.*?)\s*(\[CTA[^\]]*\])?\s*$", nombre or "")
    if not m:
        return (nombre or "").strip(), ""
    loc = m.group(1)
    if "|" in loc:
        loc = loc.split("|", 1)[1]
    else:
        partes = [p.strip() for p in loc.split(",")]
        loc = _titulo(" ".join(reversed(partes))) if len(partes) > 1 else _titulo(loc)
    return m.group(2).strip(), loc.strip()


def _nombre_linea(nombres, cod=""):
    """Nombre más habitual de una línea, sin el «L1 - » ni los corchetes; las circulares, como tales."""
    c = Counter()
    for n in nombres:
        extra = re.findall(r"\[([^\]]*)\]", n)
        base = _quita_codigo(n, cod)
        base = re.sub(r"\bB[UÚ]HO\s+", "", base, flags=re.I)
        base = _bonito(re.sub(r"\s*\[[^\]]*\]", "", base).strip())
        a, _, b = base.partition("-")
        if a.strip() and a.strip() == b.strip():
            base = "Circular " + a.strip() + ("".join(" · " + x for x in extra if x.upper() not in ("CIRCULAR",)) if extra else "")
        else:
            base = base.replace("-", " – ")
        c[base] += 1
    return c.most_common(1)[0][0] if c else ""


def _simplificar(pts, tol_km=0.008):
    if len(pts) < 3:
        return pts
    import math
    lat0 = math.radians(sum(p[0] for p in pts) / len(pts))
    xy = [((p[1]) * 111.32 * math.cos(lat0), p[0] * 110.57) for p in pts]

    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    pila = [(0, len(pts) - 1)]
    while pila:
        i, j = pila.pop()
        dmax, k = 0.0, None
        for m in range(i + 1, j):
            d = dist(xy[m], xy[i], xy[j])
            if d > dmax:
                dmax, k = d, m
        if k is not None and dmax > tol_km:
            keep[k] = True
            pila.extend([(i, k), (k, j)])
    return [[round(p[0], 5), round(p[1], 5)] for p, kk in zip(pts, keep) if kk]


def extraer(red, dia, zip_cta=None):
    """La red de un día (ver el formato arriba). Se guarda en caché por día."""
    conf = REDES[red]
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, "cta_%s_%s_v%d.json" % (red, dia.strftime("%Y%m%d"), VERSION_CACHE))
    if zip_cta is None and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    z = zipfile.ZipFile(zip_cta or obtener_zip())
    nombres = set(z.namelist())
    ds = dia.strftime("%Y%m%d")
    rutas = {r["route_id"]: r for r in _csv(z, "routes.txt") if r.get("agency_id") in conf["agencias"]}
    act = set()
    if "calendar.txt" in nombres:
        col = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][dia.weekday()]
        act = {c["service_id"] for c in _csv(z, "calendar.txt")
               if c.get("start_date", "") <= ds <= c.get("end_date", "") and c.get(col) == "1"}
    if "calendar_dates.txt" in nombres:
        for c in _csv(z, "calendar_dates.txt"):
            if c.get("date") == ds:
                (act.discard if c.get("exception_type") == "2" else act.add)(c["service_id"])
    viajes = {t["trip_id"]: t for t in _csv(z, "trips.txt") if t["route_id"] in rutas and t["service_id"] in act}
    horas = defaultdict(list)
    for r in _csv(z, "stop_times.txt"):
        if r["trip_id"] in viajes:
            try:
                horas[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], _hora(r["departure_time"] or r["arrival_time"])))
            except (ValueError, KeyError):
                continue
    usadas = {s for v in horas.values() for _, s, _ in v}
    paradas = {}
    for p in _csv(z, "stops.txt"):
        if p["stop_id"] in usadas:
            n, loc = limpia_parada(p["stop_name"])
            if not loc:     # Oviedo y Mieres no ponen la localidad: la calle, que orienta más
                loc = _bonito(re.sub(r"\s+", " ", p.get("stop_desc") or ""))
            paradas[p["stop_id"]] = [n, loc, round(float(p["stop_lat"]), 6), round(float(p["stop_lon"]), 6)]
    variantes, vidx, patrones, pidx, lista, forma_de = [], {}, [], {}, [], {}
    for tid, filas in horas.items():
        filas.sort()
        t = viajes[tid]
        cod = codigo_linea(rutas[t["route_id"]])
        seq = tuple(s for _, s, _ in filas)
        clave = (cod, seq)
        if clave not in vidx:
            dest = _bonito(_quita_codigo(t.get("trip_headsign") or "", cod))
            if not dest or dest.lower() == cod.lower():
                dest = paradas.get(seq[-1], [""])[0]
            vidx[clave] = len(variantes)
            variantes.append({"linea": cod, "destino": dest, "paradas": list(seq), "forma": None})
            if t.get("shape_id"):
                forma_de[vidx[clave]] = t["shape_id"]
        off = tuple(round(h - filas[0][2], 1) for _, _, h in filas)
        if off not in pidx:
            pidx[off] = len(patrones)
            patrones.append(list(off))
        lista.append([vidx[clave], round(filas[0][2], 1), pidx[off]])
    lista.sort(key=lambda x: x[1])
    if forma_de and "shapes.txt" in nombres:
        quiero = set(forma_de.values())
        pts = defaultdict(list)
        for r in _csv(z, "shapes.txt"):
            if r["shape_id"] in quiero:
                pts[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
        for i, sid in forma_de.items():
            if pts.get(sid):
                variantes[i]["forma"] = _simplificar([[la, lo] for _, la, lo in sorted(pts[sid])])
    por_linea = defaultdict(list)
    for r in rutas.values():
        por_linea[codigo_linea(r)].append(r.get("route_long_name", ""))
    codigos = sorted({v["linea"] for v in variantes}, key=_orden)
    lineas = {}
    for i, c in enumerate(codigos):
        r0 = next((r for r in rutas.values() if codigo_linea(r) == c), {})
        col = r0.get("route_color")
        lineas[c] = {"codigo": NOCTURNOS.get(c.lower(), c),
                     "nombre": _nombre_linea(por_linea[c], c),
                     "color": ("#" + col) if col and len(col) == 6 else PALETA[i % len(PALETA)]}
    datos = {"red": red, "nombre": conf["nombre"], "operador": conf.get("operador", ""), "color": conf["color"],
             "fecha": dia.isoformat(), "lineas": lineas, "paradas": paradas,
             "variantes": variantes, "patrones": patrones, "viajes": lista}
    if zip_cta is None:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    return datos


def _orden(c):
    m = re.match(r"[A-Za-z]*(\d+)", c or "")
    return (int(m.group(1)) if m else 999, c)
