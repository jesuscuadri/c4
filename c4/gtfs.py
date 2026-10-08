# -*- coding: utf-8 -*-
"""Descarga del horario oficial (GTFS de Renfe Cercanías) y extracción de la línea."""
import csv
import io
import json
import os
import time
import zipfile
from collections import defaultdict

from .util import CACHE, http_get

GTFS_URLS = ["https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip"]
# Copia pública diaria del mismo fichero, por si la de Renfe falla
GITHUB_LISTADO = "https://api.github.com/repos/elguardagujas/renfe-gtfs-archive/contents/data"
VERSION_CACHE = 2
VERSION_CACHE_RED = 1


def obtener_zip(forzar=False):
    os.makedirs(CACHE, exist_ok=True)
    ruta = os.path.join(CACHE, "renfe_cercanias_gtfs.zip")
    if not forzar and os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < 20 * 3600:
        return ruta
    errores = []

    def bajar(url):
        datos = http_get(url, timeout=180)
        tmp = ruta + ".tmp"
        with open(tmp, "wb") as f:
            f.write(datos)
        zipfile.ZipFile(tmp).namelist()  # comprueba que es un zip válido
        os.replace(tmp, ruta)

    for url in GTFS_URLS:
        try:
            print("Descargando el horario oficial de Renfe…")
            bajar(url)
            return ruta
        except Exception as e:  # noqa: BLE001
            errores.append("%s: %s" % (url, e))
    try:
        print("Renfe no responde; probando la copia pública de GitHub…")
        lista = json.loads(http_get(GITHUB_LISTADO).decode("utf-8"))
        ultimo = max((x for x in lista if x["name"].startswith("cercanias_")), key=lambda x: x["name"])
        bajar(ultimo["download_url"])
        return ruta
    except Exception as e:  # noqa: BLE001
        errores.append("GitHub: %s" % e)
    if os.path.exists(ruta):
        print("Aviso: no se pudo actualizar el horario; uso el guardado.\n  " + "\n  ".join(errores))
        return ruta
    raise RuntimeError("No se pudo descargar el horario:\n  " + "\n  ".join(errores))


def _csv(z, nombre):
    with z.open(nombre) as f:
        rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace", newline=""))
        cab = [c.strip() for c in next(rd)]
        for fila in rd:
            yield dict(zip(cab, (c.strip() for c in fila)))


def _hora(s):
    h, m, sg = s.split(":")
    return int(h) * 60 + int(m) + int(sg) / 60.0


def extraer(cfg, dia):
    """Horario de la línea para un día:
    {'paradas': {id: [nombre, lat, lon]}, 'viajes': {trip_id: [[stop, llegada, salida], ...]},
     'rutas': [route_id], 'trazado': [[lat, lon], ...]}"""
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, "%s_%s_v%d.json" % (cfg["linea"], dia.strftime("%Y%m%d"), VERSION_CACHE))
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    z = zipfile.ZipFile(obtener_zip())
    print("Preparando el horario de la %s para el %s…" % (cfg["linea"], dia.strftime("%d/%m/%Y")))
    rutas = set()
    for r in _csv(z, "routes.txt"):
        if (r["route_id"].startswith(cfg["prefijo_nucleo"]) and r["route_short_name"] == cfg["linea"]
                and r.get("route_type", "2") == "2"):  # route_type 3 = autobús de sustitución
            rutas.add(r["route_id"])
    ds = dia.strftime("%Y%m%d")
    col = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][dia.weekday()]
    servicios = {c["service_id"] for c in _csv(z, "calendar.txt")
                 if c["start_date"] <= ds <= c["end_date"] and c[col] == "1"}
    viajes_ok, formas = {}, defaultdict(int)
    for t in _csv(z, "trips.txt"):
        if t["route_id"] in rutas and t["service_id"] in servicios:
            viajes_ok[t["trip_id"]] = t.get("shape_id", "")
            formas[t.get("shape_id", "")] += 1
    viajes = defaultdict(list)
    with z.open("stop_times.txt") as f:
        txt = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace")
        next(txt)
        for linea in txt:
            p = linea.split(",")
            tid = p[0].strip()
            if tid in viajes_ok:
                viajes[tid].append((int(p[4]), p[3].strip(), _hora(p[1].strip()), _hora(p[2].strip())))
    usadas = {s for v in viajes.values() for _, s, _, _ in v}
    paradas = {p["stop_id"]: [p["stop_name"], float(p["stop_lat"]), float(p["stop_lon"])]
               for p in _csv(z, "stops.txt") if p["stop_id"] in usadas}
    trazado = []
    forma = max((s for s in formas if s and not s.endswith("_INV")), key=lambda s: formas[s], default="")
    if forma and "shapes.txt" in z.namelist():
        pts = [(int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]))
               for r in _csv(z, "shapes.txt") if r["shape_id"] == forma]
        trazado = [[la, lo] for _, la, lo in sorted(pts)]
    datos = {"paradas": paradas, "rutas": sorted(rutas), "trazado": trazado,
             "viajes": {t: [[s, a, d] for _, s, a, d in sorted(v)] for t, v in viajes.items()}}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    return datos


def ruta_cache_red(cfg, dia):
    return os.path.join(CACHE, "red%s_%s_v%d.json" % (cfg["prefijo_nucleo"], dia.strftime("%Y%m%d"), VERSION_CACHE_RED))


def ruta_cache_reg(dia):
    return os.path.join(CACHE, "regionales_%s_v%d.json" % (dia.strftime("%Y%m%d"), VERSION_CACHE_REG))


def extraer_red(cfg, dia):
    """Horario de TODAS las líneas de Cercanías del núcleo (Asturias) para un día:
    {'paradas': {id: [nombre, lat, lon]}, 'viajes': {trip_id: [[stop, llegada, salida], ...]},
     'lineas': {trip_id: 'C4'}, 'formas': {shape_id: [[lat, lon], ...]}, 'rutas': [route_id]}"""
    os.makedirs(CACHE, exist_ok=True)
    cache = ruta_cache_red(cfg, dia)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    z = zipfile.ZipFile(obtener_zip())
    print("Preparando el horario de toda la red para el %s…" % dia.strftime("%d/%m/%Y"))
    rutas = {}
    for r in _csv(z, "routes.txt"):
        if r["route_id"].startswith(cfg["prefijo_nucleo"]) and r.get("route_type", "2") == "2":
            rutas[r["route_id"]] = r["route_short_name"].strip()   # route_type 3 = autobús de sustitución
    ds = dia.strftime("%Y%m%d")
    col = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][dia.weekday()]
    servicios = {c["service_id"] for c in _csv(z, "calendar.txt")
                 if c["start_date"] <= ds <= c["end_date"] and c[col] == "1"}
    viajes_ok, lineas, forma_de = {}, {}, {}
    for t in _csv(z, "trips.txt"):
        if t["route_id"] in rutas and t["service_id"] in servicios:
            viajes_ok[t["trip_id"]] = True
            lineas[t["trip_id"]] = rutas[t["route_id"]]
            if t.get("shape_id"):
                forma_de[t["trip_id"]] = t["shape_id"]
    viajes = defaultdict(list)
    with z.open("stop_times.txt") as f:
        txt = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace")
        next(txt)
        for linea in txt:
            p = linea.split(",")
            tid = p[0].strip()
            if tid in viajes_ok:
                viajes[tid].append((int(p[4]), p[3].strip(), _hora(p[1].strip()), _hora(p[2].strip())))
    usadas = {s for v in viajes.values() for _, s, _, _ in v}
    paradas = {p["stop_id"]: [p["stop_name"], float(p["stop_lat"]), float(p["stop_lon"])]
               for p in _csv(z, "stops.txt") if p["stop_id"] in usadas}
    formas = defaultdict(list)
    quiero = set(forma_de.values())
    if quiero and "shapes.txt" in z.namelist():
        for r in _csv(z, "shapes.txt"):
            if r["shape_id"] in quiero:
                formas[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    datos = {"paradas": paradas, "rutas": sorted(rutas),
             "viajes": {t: [[s, a, d] for _, s, a, d in sorted(v)] for t, v in viajes.items()},
             "lineas": {t: lineas[t] for t in viajes},
             "formas": {k: [[round(a, 6), round(b, 6)] for _, a, b in sorted(v)] for k, v in formas.items()},
             "forma_de": {t: forma_de[t] for t in viajes if t in forma_de}}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    return datos


def separar_por_grupo(datos, lineas_grupo):
    """Los viajes (y formas) de un grupo de líneas (un ancho de vía)."""
    vi = {t: v for t, v in datos["viajes"].items() if datos["lineas"].get(t) in lineas_grupo}
    formas = {datos["forma_de"][t] for t in vi if t in datos.get("forma_de", {})}
    usadas = {s for v in vi.values() for s, _, _ in v}
    return {"paradas": {s: p for s, p in datos["paradas"].items() if s in usadas},
            "viajes": vi, "lineas": {t: datos["lineas"][t] for t in vi},
            "formas": {k: datos["formas"][k] for k in formas if k in datos["formas"]},
            "rutas": datos.get("rutas", [])}


# --------------------------------------------------------------------------------------------
# Trenes regionales y de larga distancia (FEVE Oviedo–Santander/Llanes, Oviedo–Ferrol, Gijón–León,
# Alvia, AVE…). No son Cercanías, pero usan las mismas vías: en vía única los Cercanías tienen que
# cruzarse con ellos (visto el 08/10: los C-6 hacia Infiesto llegaban 7-8 min tarde de lo calculado
# porque esperaban a los regionales de Llanes, que no estaban en el horario de Cercanías).
LD_URLS = ["https://ssl.renfe.com/gtransit/Fichero_AV_LD/google_transit.zip"]
VERSION_CACHE_REG = 1
TIPOS_REGIONAL = {"REGIONAL", "MD", "REG.EXP.", "R. EXPRES"}


def obtener_zip_ld(forzar=False):
    os.makedirs(CACHE, exist_ok=True)
    ruta = os.path.join(CACHE, "renfe_ld_gtfs.zip")
    if not forzar and os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < 20 * 3600:
        return ruta
    errores = []
    for url in LD_URLS:
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
    raise RuntimeError("No se pudo descargar el horario de regionales: " + "; ".join(errores))


def _servicios_del_dia(z, dia):
    ds = dia.strftime("%Y%m%d")
    col = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][dia.weekday()]
    act = set()
    if "calendar.txt" in z.namelist():
        act = {c["service_id"] for c in _csv(z, "calendar.txt")
               if c.get("start_date", "") <= ds <= c.get("end_date", "") and c.get(col) == "1"}
    if "calendar_dates.txt" in z.namelist():
        for c in _csv(z, "calendar_dates.txt"):
            if c.get("date") == ds:
                if c.get("exception_type") == "2":
                    act.discard(c["service_id"])
                else:
                    act.add(c["service_id"])
    return act


def extraer_regionales(cfg, dia, paradas_red, zip_ld=None):
    """Trenes no Cercanías que pasan por las vías de la red (solo el trozo dentro de ella):
    {'viajes': {trip_id: [[stop, llegada, salida], ...]},
     'tipos': {trip_id: [tipo, número, origen, destino]}}"""
    os.makedirs(CACHE, exist_ok=True)
    cache = ruta_cache_reg(dia)
    if os.path.exists(cache) and zip_ld is None:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    z = zipfile.ZipFile(zip_ld or obtener_zip_ld())
    tipo_ruta = {r["route_id"]: r.get("route_short_name", "").strip().upper()
                 for r in _csv(z, "routes.txt") if r.get("route_type", "2") in ("2", "101", "102", "103", "106")}
    act = _servicios_del_dia(z, dia)
    info = {}
    for t in _csv(z, "trips.txt"):
        if t["service_id"] in act and t["route_id"] in tipo_ruta:
            info[t["trip_id"]] = (tipo_ruta[t["route_id"]], t.get("trip_short_name") or t["trip_id"][:5])
    filas = defaultdict(list)
    for r in _csv(z, "stop_times.txt"):
        tid = r["trip_id"]
        if tid in info:
            try:
                filas[tid].append((int(r["stop_sequence"]), r["stop_id"], _hora(r["arrival_time"]), _hora(r["departure_time"])))
            except (ValueError, KeyError):
                continue
    nombres = {p["stop_id"]: p["stop_name"] for p in _csv(z, "stops.txt")}
    viajes, tipos, vistos = {}, {}, set()
    for tid, fs in filas.items():
        fs.sort()
        # el tramo más largo seguido dentro de la red (si sale y vuelve a entrar, se queda el mayor)
        bloques, cur = [], []
        for _, s, a, d in fs:
            if s in paradas_red:
                cur.append([s, round(a, 2), round(d, 2)])
            elif cur:
                bloques.append(cur)
                cur = []
        if cur:
            bloques.append(cur)
        if not bloques:
            continue
        mejor = max(bloques, key=len)
        if len(mejor) < 2:
            continue
        firma = json.dumps(mejor)
        if firma in vistos:          # el mismo tren con dos códigos (MD y AVLO a la misma hora)
            continue
        vistos.add(firma)
        viajes[tid] = mejor
        tipos[tid] = [info[tid][0], info[tid][1], nombres.get(fs[0][1], ""), nombres.get(fs[-1][1], "")]
    datos = {"viajes": viajes, "tipos": tipos}
    if zip_ld is None:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
    return datos
