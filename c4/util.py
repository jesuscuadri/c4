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
# Carpeta de datos persistentes (historial de precisión y tiempos aprendidos).
# En Render el disco normal es TEMPORAL: se borra en cada despliegue o reinicio, y por eso
# la precisión "se reiniciaba". Si montas un disco persistente (p. ej. en /var/data) y pones
# la variable de entorno C4_DATOS=/var/data, el historial se conserva entre actualizaciones.
DATOS = os.environ.get("C4_DATOS")
CACHE = os.path.join(DATOS or RAIZ, "cache")
HIST = os.path.join(DATOS or RAIZ, "historial")
WEB = os.path.join(RAIZ, "web")
if DATOS:
    try:
        os.makedirs(HIST, exist_ok=True)
        os.makedirs(CACHE, exist_ok=True)
    except OSError:
        pass

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
    "usar_correccion_sesgo": True,      # aprende de los fallos: corrige sesgos por estación
    "usar_retraso_tipico": True,        # los trenes que aún no han salido llevan su retraso habitual
    "renfe_en_marcha_desde": True,      # IN_TRANSIT_TO X de Renfe = acaba de salir de X
    "usar_gps": True,                   # posición GPS del tren sobre la vía para saber cuánto le queda
    "gps_vel_kmh": 60,                  # velocidad normal en marcha (para no fiarse de la holgura del horario)
    "arranque_frenada_min": 0.6,        # lo que se pierde arrancando y frenando en cada tramo
    "mezcla_oficial": True,             # lejos (>5 min) se mezcla con horario+retraso: medido, reduce el error
    "mezcla_peso_min": 0.6,
    "gps_max_km": 0.4,                  # más lejos de la vía que esto: posición no fiable
    "adelanto_llegada_min": 0.5,        # Renfe marca «parado» al entrar en la estación: llegada algo antes
    "parada_defecto_min": 1.5,          # parada real (de «entra» a «sale») si aún no se ha aprendido
    "sesgo_damp": 0.5,                  # cuánto se aplica del sesgo aprendido (0-1)
    "sesgo_cap": 1.0,                   # tope de la corrección por estación (min)
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
