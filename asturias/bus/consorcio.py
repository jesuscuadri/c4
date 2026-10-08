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
import unicodedata
import time
import zipfile
from collections import Counter, defaultdict

from ..util import CACHE, http_get

# Copia pública diaria del GTFS del Consorcio (el original del Punto de Acceso Nacional pide registro)
CTA_URLS = ["https://files.mobilitydatabase.org/mdb-2827/latest.zip"]
VERSION_CACHE = 6

REDES = {
    "aviles": {"nombre": "Avilés", "agencias": ["28"], "color": "#0b5cab",
               "operador": "Compañía del Tranvía Eléctrico de Avilés"},
    "oviedo": {"nombre": "Oviedo", "agencias": ["51"], "color": "#c8102e",
               "operador": "TUA · Transportes Unidos de Asturias"},
    "mieres": {"nombre": "Mieres", "agencias": ["59"], "color": "#00796b",
               "operador": "EMUTSA"},
    # todo lo demás del Consorcio: ALSA y las demás empresas entre pueblos y ciudades
    "interurbano": {"nombre": "Asturias", "titulo": "Interurbanos de Asturias", "excluir": ["28", "51", "59"],
                    "color": "#6d28d9", "operador": "Consorcio de Transportes de Asturias",
                    "tipo": "interurbano", "formas_aparte": True, "tolerancia_km": 0.02},
}
OPERADORES = {"19": "ALSA", "31": "Sama·Mariano", "774": "La Fresneda", "53": "Zapico", "24": "Autos Sama",
              "9": "Asturiana", "10": "A. Langreo", "56": "Villa", "50": "Bimenes", "82": "Casablanca"}
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
    s = re.sub(r"(^|[\s/(\-'])(\w)", lambda m: m.group(1) + m.group(2).upper(), s)
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


def _asturianez(t):
    """Cuánto «suena» a la forma asturiana un topónimo (para enseñar la castellana, que es la que
    busca casi todo el mundo, y dejar la otra para la búsqueda)."""
    t = t.upper()
    palabras = re.findall(r"[\wÁÉÍÓÚÜÑ']+", t)
    return (2 * ("X" in t) + sum(1 for w in palabras if w.startswith("LL")) + sum(1 for w in palabras if w.endswith("U"))
            + sum(1 for w in palabras if w in ("LES", "L'", "ELS")) + ("L'" in t) + ("D'" in t) + ("CUA" in t)
            + sum(1 for w in palabras if w.endswith("UES")))


def _localidad(loc):
    """«OVIEDO/UVIÉU» -> («Oviedo», «Uviéu»); «LLANGRÉU/LANGREO» -> («Langreo», «Llangréu»);
    «PIEDRASBLANCAS|Piedras Blancas» -> («Piedras Blancas», «»); «PEÑA, LA» -> («La Peña», «»)."""
    def bonito(x):
        partes = [p.strip() for p in x.split(",")]
        return (_titulo(" ".join(reversed(partes))) if len(partes) > 1 else _titulo(x)).replace("' ", "'")
    if "|" in loc:
        return loc.split("|", 1)[1].strip(), ""
    partes = [x.strip() for x in loc.split("/") if x.strip()]
    if len(partes) < 2:
        return bonito(loc).strip(), ""
    a, b = partes[0], partes[1]
    de = lambda x: bool(re.search(r"\bDE(L)?\b", x.upper()))     # «Soto de Rey» / «Soto Rei»
    if _asturianez(b) + de(a) < _asturianez(a) + de(b):
        a, b = b, a
    return bonito(a), bonito(b)


ALIAS = {}      # localidad -> su otro nombre (se rellena al leer las paradas)


# palabras que el Consorcio escribe sin tilde en algunos nombres (Oviedo sobre todo)
def _sin(w):
    return "".join(c for c in unicodedata.normalize("NFD", w.lower()) if unicodedata.category(c) != "Mn")


_TILDES = {_sin(w): w for w in (
    "América Andrés José María García Fernández González Rodríguez López Pérez Martínez Sánchez Jesús Príncipe Estación "
    "Policlínica Paraíso Mercadín Gijón Avilés Ramón Bernardo Asunción Concepción Constitución Educación Información "
    "Avenida Fábrica Cámara Teléfonos Ángel Ángeles Pumarín Ciaño Siero Tenderina Cándido Víctor Mª Nicolás Tomás Martín "
    "Fernán Menéndez Álvarez Álvaro Béjar Cuéllar Hernández Jiménez Núñez Ramírez Suárez Vázquez Díaz Gómez Gutiérrez").split()}
_TILDES.pop("mª", None)
_TILDES["huca"] = "HUCA"


def _con_tildes(nombre):
    """«Plaza America» -> «Plaza América»: solo palabras sueltas de la lista, sin tocar las que ya llevan tilde."""
    def f(m):
        w = m.group(0)
        b = _TILDES.get(_sin(w))
        if not b or w != _sin(w).upper() and w != _sin(w) and w != _sin(w).capitalize():
            return w          # no está en la lista, o ya lleva tilde
        return b.upper() if w.isupper() and len(w) > 1 else b if w[0].isupper() else b.lower()
    return re.sub(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]+", f, nombre)


def limpia_parada(nombre):
    """«[AVILÉS]  Cristalería [CTA 04317]» -> («Cristalería», «Avilés»);
    «[PIEDRASBLANCAS|Piedras Blancas]  Eysines [CTA 03075]» -> («Eysines», «Piedras Blancas»)."""
    m = re.match(r"^\[([^\]]*)\]\s*(.*?)\s*(\[CTA[^\]]*\])?\s*$", nombre or "")
    if not m:
        return (nombre or "").strip(), ""
    loc, otro = _localidad(m.group(1))
    if otro:
        ALIAS[loc] = otro
    # «Estación Bus Oviedo IL- Pepe Cosmen» (inicio/final de línea): «Estación Bus Oviedo · Pepe Cosmen»
    nombre = re.sub(r"\s+[IF]L-\s*", " · ", m.group(2).strip())
    return _con_tildes(nombre), loc.strip()


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


def _operador_corto(aid, nombre):
    if aid in OPERADORES:
        return OPERADORES[aid]
    n = re.sub(r",?\s+(S\.?L\.?U?|S\.?A\.?U?|SLU|SAU|UTE|S\.?L\.?L\.?)\.?$", "", (nombre or "").strip(), flags=re.I)
    n = re.sub(r"^(Autos|Autobuses( de)?|Autocares( de)?|Automóviles|Transportes|Empresa|Compañía|Viajes)\s+", "", n, flags=re.I)
    n = n.strip() or aid
    return n if len(n) <= 12 else n.split()[0][:12]


def _via(largo):
    """«Gijón-Oviedo [GO2] [Campus del Cristo] [Consejerías]» -> «GO2 · Campus del Cristo · Consejerías»."""
    return " · ".join(x.strip() for x in re.findall(r"\[([^\]]*)\]", largo or "") if x.strip())


def _localidades_cercanas(paradas, max_km=3.0):
    """Las paradas sin localidad (las de TUA, por ejemplo) toman la de la parada con localidad más cercana."""
    import math
    celda = 0.03
    rejilla = defaultdict(list)
    for pid, p in paradas.items():
        if p[1]:
            rejilla[(int(p[2] / celda), int(p[3] / celda))].append(p)
    for pid, p in paradas.items():
        if p[1]:
            continue
        cx, cy = int(p[2] / celda), int(p[3] / celda)
        mejor, dmin = None, max_km
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in rejilla.get((cx + dx, cy + dy), ()):
                    d = math.hypot((q[2] - p[2]) * 111.2, (q[3] - p[3]) * 111.2 * math.cos(math.radians(p[2])))
                    if d < dmin:
                        mejor, dmin = q, d
        if mejor:
            p[1] = mejor[1]


def _permisos(grupo, horas, n):
    """Dónde se puede subir y hasta dónde se puede bajar en un autobús de verdad.

    El Consorcio parte cada expedición en varios «viajes»: el completo y uno por cada parada desde la
    que se puede subir con reglas propias (subiendo en Oviedo, por ejemplo, no se puede bajar dentro de
    Oviedo). Cada uno es un tramo final del completo. Devuelve, por parada, [primera, última] en la que
    se puede bajar si se sube ahí, o 0 si ahí no se puede subir; None si no hay ninguna restricción."""
    rangos = [None] * n
    largo = [x[1] for x in horas[grupo[0]]]
    for tid in grupo:
        f = horas[tid]
        o = n - len(f)
        if o < 0 or [x[1] for x in f] != largo[o:]:
            continue
        for k, x in enumerate(f):
            if x[3] == "1":                    # aquí no se sube
                continue
            js = [o + m for m in range(k + 1, len(f)) if f[m][4] != "1"]
            if not js:
                continue
            i, r = o + k, rangos[o + k]
            rangos[o + k] = [min(js), max(js)] if r is None else [min(r[0], min(js)), max(r[1], max(js))]
    if all(rangos[i] == [i + 1, n - 1] for i in range(n - 1)):
        return None
    return [r or 0 for r in rangos]


def ruta_cache(red, dia):
    return os.path.join(CACHE, "cta_%s_%s_v%d.json" % (red, dia.strftime("%Y%m%d"), VERSION_CACHE))


def extraer(red, dia, zip_cta=None):
    """La red de un día (ver el formato arriba). Se guarda en caché por día."""
    conf = REDES[red]
    inter = conf.get("tipo") == "interurbano"
    os.makedirs(CACHE, exist_ok=True)
    cache = ruta_cache(red, dia)
    if zip_cta is None and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    z = zipfile.ZipFile(zip_cta or obtener_zip())
    nombres = set(z.namelist())
    ds = dia.strftime("%Y%m%d")

    def mia(aid):
        return aid not in conf["excluir"] if "excluir" in conf else aid in conf["agencias"]
    rutas = {r["route_id"]: r for r in _csv(z, "routes.txt") if mia(r.get("agency_id"))}
    agencias = {a["agency_id"]: a.get("agency_name", "") for a in _csv(z, "agency.txt")} if "agency.txt" in nombres else {}
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
                horas[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], _hora(r["departure_time"] or r["arrival_time"]),
                                            r.get("pickup_type", ""), r.get("drop_off_type", "")))
            except (ValueError, KeyError):
                continue
    for filas in horas.values():
        filas.sort()
    usadas = {x[1] for v in horas.values() for x in v}
    paradas = {}
    for p in _csv(z, "stops.txt"):
        if p["stop_id"] in usadas:
            n, loc = limpia_parada(p["stop_name"])
            if not loc and not inter:     # Oviedo y Mieres no ponen la localidad: la calle, que orienta más
                loc = _bonito(re.sub(r"\s+", " ", p.get("stop_desc") or ""))
            paradas[p["stop_id"]] = [n, loc, round(float(p["stop_lat"]), 6), round(float(p["stop_lon"]), 6)]
    if inter:
        _localidades_cercanas(paradas)

    def cod_de(r):
        if not inter:
            return codigo_linea(r)
        return r["route_id"]
    # cada autobús de verdad: el viaje completo y sus tramos (misma ruta, misma llegada al final)
    grupos = defaultdict(list)
    for tid, filas in horas.items():
        if filas:
            grupos[(viajes[tid]["route_id"], filas[-1][1], round(filas[-1][2], 1))].append(tid)
    variantes, vidx, patrones, pidx, lista, forma_de = [], {}, [], {}, [], {}
    for g in grupos.values():
        g.sort(key=lambda x: (-len(horas[x]), x))
        tid = g[0]
        filas = horas[tid]
        t = viajes[tid]
        cod = cod_de(rutas[t["route_id"]])
        seq = tuple(x[1] for x in filas)
        perm = _permisos(g, horas, len(filas)) if len(g) > 1 or any(x[3] == "1" or x[4] == "1" for x in filas) else None
        clave = (cod, seq, json.dumps(perm))
        if clave not in vidx:
            cab = next((viajes[x].get("trip_headsign") for x in g if viajes[x].get("trip_headsign")), "")
            dest = _bonito(_quita_codigo(cab, cod if not inter else codigo_linea(rutas[t["route_id"]])))
            if not dest or dest.lower() == cod.lower():
                dest = paradas.get(seq[-1], [""])[0]
            vidx[clave] = len(variantes)
            v = {"linea": cod, "destino": dest, "paradas": list(seq), "forma": None}
            if perm:
                v["perm"] = perm
            variantes.append(v)
            forma = next((viajes[x].get("shape_id") for x in g if viajes[x].get("shape_id")), "")
            if forma:
                forma_de[vidx[clave]] = forma
        off = tuple(round(x[2] - filas[0][2], 1) for x in filas)
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
        hechas = {}
        for i, sid in forma_de.items():
            if pts.get(sid):
                if sid not in hechas:
                    hechas[sid] = _simplificar([[la, lo] for _, la, lo in sorted(pts[sid])], conf.get("tolerancia_km", 0.008))
                variantes[i]["forma"] = hechas[sid]
        del pts
    lineas = {}
    usadas_l = {v["linea"] for v in variantes}
    if inter:
        ops = sorted({rutas[c]["agency_id"] for c in usadas_l}, key=lambda a: -sum(1 for x in lista if variantes[x[0]]["linea"] in rutas and rutas[variantes[x[0]]["linea"]]["agency_id"] == a))
        color_op = {a: PALETA[i % len(PALETA)] for i, a in enumerate(ops)}
        for c in sorted(usadas_l, key=lambda c: (_nombre_linea([rutas[c].get("route_long_name", "")]), c)):
            r = rutas[c]
            cc = codigo_linea(r)
            largo = r.get("route_long_name", "")
            propio = bool(re.search(r"\d", cc)) and largo.upper().startswith(cc.upper())
            lineas[c] = {"codigo": cc if propio else _operador_corto(r["agency_id"], agencias.get(r["agency_id"])),
                         "nombre": _nombre_linea([largo], cc), "via": _via(largo),
                         "operador": "ALSA" if r["agency_id"] == "19" else agencias.get(r["agency_id"], ""),
                         "color": ("#" + r["route_color"]) if len(r.get("route_color") or "") == 6 else color_op[r["agency_id"]]}
    else:
        por_linea = defaultdict(list)
        for r in rutas.values():
            por_linea[codigo_linea(r)].append(r.get("route_long_name", ""))
        for i, c in enumerate(sorted(usadas_l, key=_orden)):
            r0 = next((r for r in rutas.values() if codigo_linea(r) == c), {})
            col = r0.get("route_color")
            lineas[c] = {"codigo": NOCTURNOS.get(c.lower(), c),
                         "nombre": _nombre_linea(por_linea[c], c),
                         "color": ("#" + col) if col and len(col) == 6 else PALETA[i % len(PALETA)]}
    datos = {"red": red, "tipo": conf.get("tipo", "urbano"), "nombre": conf["nombre"],
             "titulo": conf.get("titulo", "Autobuses de " + conf["nombre"]),
             "operador": conf.get("operador", ""), "color": conf["color"],
             "fecha": dia.isoformat(), "lineas": lineas, "paradas": paradas,
             "variantes": variantes, "patrones": patrones, "viajes": lista,
             "alias": {l: ALIAS[l] for l in {p[1] for p in paradas.values()} if l in ALIAS}}
    if zip_cta is None:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    return datos


def _orden(c):
    m = re.match(r"[A-Za-z]*(\d+)", c or "")
    return (int(m.group(1)) if m else 999, c)


def cercanas(d, lat, lon, ahora, radio_m=600, n_paradas=2, horizonte=120, max_grupos=4):
    """Paradas de una red a menos de radio_m de un punto, con sus próximos autobuses (según horario).
    `ahora` en minutos desde la medianoche. Devuelve [] si no hay ninguna cerca."""
    import math
    coslat = math.cos(math.radians(lat))
    cerca = []
    for sid, (nom, loc, la, lo) in d["paradas"].items():
        m = math.hypot((lo - lon) * 111.32 * coslat, (la - lat) * 110.57) * 1000
        if m <= radio_m:
            cerca.append((m, sid))
    cerca.sort()
    cerca = cerca[:n_paradas]
    if not cerca:
        return []
    ids = {sid for _, sid in cerca}
    viajes_de = {}
    for vi, sal, p in d["viajes"]:
        viajes_de.setdefault(vi, []).append((sal, p))
    grupos = {sid: {} for sid in ids}
    for vi, v in enumerate(d["variantes"]):
        ps, perm = v["paradas"], v.get("perm")
        for i, s in enumerate(ps):
            if s not in ids:
                continue
            if i == len(ps) - 1 and ps[0] != s:
                continue                      # aquí termina
            if perm and not perm[i]:
                continue                      # aquí solo se baja
            for sal, p in viajes_de.get(vi, ()):
                for base in (0, -1440):
                    t = sal + base + d["patrones"][p][i]
                    if ahora - 0.5 <= t <= ahora + horizonte:
                        g = grupos[s].setdefault((v["linea"], v["destino"]), [])
                        g.append(round(t, 1))
    out = []
    for m, sid in cerca:
        nom, loc, la, lo = d["paradas"][sid]
        gs = []
        for (lin, dest), ts in grupos[sid].items():
            l = d["lineas"].get(lin, {})
            gs.append({"linea": l.get("codigo", lin), "color": l.get("color", "#666"), "destino": dest,
                       "t": sorted(set(ts))[:3]})
        gs.sort(key=lambda g: g["t"][0])
        out.append({"id": sid, "nombre": nom, "loc": loc, "metros": round(m), "salidas": gs[:max_grupos]})
    return out
