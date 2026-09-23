# -*- coding: utf-8 -*-
"""Utilidades comunes y configuración."""
import json
import math
import os
import time
import unicodedata
import urllib.request
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(RAIZ, "cache")
HIST = os.path.join(RAIZ, "historial")
WEB = os.path.join(RAIZ, "web")

CONFIG_DEFECTO = {
    "linea": "C4",
    "prefijo_nucleo": "20T",            # 20 = núcleo de Asturias en el GTFS de Renfe
    "puerto": 8765,
    "intervalo_consulta_s": 20,
    "margen_cruce_min": 0.5,            # desde que entra el tren contrario hasta que sale el que espera
    "margen_seguimiento_min": 0.5,
    "usar_rotaciones": True,
    "vuelta_minima_min": 4,             # tiempo mínimo para dar la vuelta en cabecera
    "rotacion_espera_max_min": 10,      # si el material llega muy tarde, suponemos que ponen otro tren
    "parada_minima_min": 0.4,           # parada mínima cuando el tren va tarde y recorta
    "cruces": "fijos",                  # "fijos" | "dinamicos"
    "umbral_cambio_cruce_min": 8,
    "estaciones_cruce_extra": [],
    "estaciones_cruce_excluir": [],
    "recuperacion": 0.0,                # fracción de marcha recuperable si va con retraso
    "usar_tiempos_aprendidos": True,
    "guardar_historial": True,
    "bus": True,                        # enlace con el autobus urbano de Gijon (EMTUSA)
    "bus_radio_m": 550,                 # radio para buscar paradas cerca de una estacion
    "datos_viejos_s": 300,              # tiempo real más antiguo que esto se ignora
}


def cargar_config():
    cfg = dict(CONFIG_DEFECTO)
    ruta = os.path.join(RAIZ, "config.json")
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:  # noqa: BLE001
            print("Aviso: config.json no válido (%s); uso valores por defecto" % e)
    return cfg


def normaliza(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def hm(minutos):
    if minutos is None:
        return "--:--"
    m = int(round(minutos))
    return "%02d:%02d" % ((m // 60) % 24, m % 60)


def ahora_min(ts=None):
    """Minutos desde la medianoche local (el ordenador debe estar en hora de Madrid)."""
    d = datetime.fromtimestamp(ts if ts is not None else time.time())
    return d.hour * 60 + d.minute + d.second / 60.0


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (c4-tiempo-real)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def distancia_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, h)))
