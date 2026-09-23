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
