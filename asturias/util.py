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
    # Redes: cada ancho de vía es una red (las líneas de un mismo ancho comparten vías y cruces).
    # «via_doble_si_coinciden»: cuántas veces tiene que hacer coincidir el horario a dos trenes de
    # sentido contrario en mitad de un tramo para darlo por vía doble. En ancho ibérico el horario no
    # tiene esas rarezas (1 basta); en el métrico la C-4 tiene 1-2 al día en vía única (El Parador–Soto).
    "redes": {
        "metrico": {"lineas": ["C4", "C5", "C5a", "C6", "C7", "C8"], "otras": ["RE", "RO"], "via_doble_si_coinciden": 3},
        "iberico": {"lineas": ["C1", "C2", "C3"], "otras": ["RL", "LD"], "via_doble_si_coinciden": 1},
    },
    # Lo que no es Cercanías, en sus apartados: regionales (FEVE y Renfe) y AVE / larga distancia
    "categorias": [["cercanias", "Cercanías", ["C1", "C2", "C3", "C4", "C5", "C5a", "C6", "C7", "C8"]],
                   ["regional", "Regionales", ["RE", "RO", "RL"]],
                   ["larga", "AVE y larga distancia", ["LD"]]],
    "nombres_lineas": {"RE": "Oviedo – Infiesto · a Llanes y Santander",
                       "RO": "Oviedo – Cudillero · a Ferrol",
                       "RL": "Gijón – Pola de Lena · a León",
                       "LD": "AVE, Alvia y Avlo · Gijón – Oviedo – Lena"},
    "colores_lineas": {"C1": "#e2231a", "C2": "#1d70b8", "C3": "#00965e", "C4": "#e93cac",
                       "C5": "#f39200", "C5a": "#c77d00", "C6": "#7b3f98", "C7": "#00a3e0",
                       "C8": "#8a6d3b", "RE": "#0f766e", "RO": "#0369a1", "RL": "#4d7c0f", "LD": "#6d28d9"},
    "ventana_estado_min": 240,          # trenes que se mandan al móvil: los que circulan y los de las próximas 4 h
    "puerto": 8765,
    "intervalo_consulta_s": 20,         # recalcular al menos cada 20 s
    "intervalo_rapido_s": 2,            # preguntar a Renfe si hay datos nuevos cada 2 s (contesta «sin cambios» casi siempre)
    "margen_cruce_min": 1.0,            # desde que entra el tren contrario hasta que sale el que espera
    "margen_seguimiento_min": 0.5,
    "usar_rotaciones": True,
    "vuelta_minima_min": 4,             # tiempo mínimo para dar la vuelta en cabecera
    "rotacion_espera_max_min": 10,      # si el material llega muy tarde, suponemos que ponen otro tren
    "parada_minima_min": 1.0,           # parada mínima cuando el tren va tarde y recorta (medido 25-26/09: ~1 min)
    # En las paradas intermedias el tren sale ~0,7 min después de su hora (puertas, la hora del horario es
    # el minuto entero; medido con el GPS en las grabaciones del 25-26/09). La hora que se enseña se
    # redondea hacia abajo, así que sigue siendo el minuto del horario: nunca te hace llegar tarde.
    "salida_tras_hora_min": 0.7,
    "cruces": "fijos",                  # "fijos" | "dinamicos"
    "umbral_cambio_cruce_min": 8,
    "estaciones_cruce_extra": ["Soto del Barco", "Regueral"],   # tienen vía de cruce aunque el horario casi no las use (visto 26/09)
    "cruce_mover_si_espera_min": 8,    # si un cruce haría esperar más que esto, se adelanta al siguiente apartadero
    "estaciones_cruce_excluir": [],
    "llegada_supuesta_tras_min": 5,     # en marcha y debería haber llegado hace más de esto: ya está en la estación
    "retroceso_max_min": 25,            # cuánto tiempo se ignora a Renfe si «hace retroceder» a un tren
    "recuperacion": 0.1,                # con retraso va ~10 % más rápido que el horario (medido 25/09)
    "usar_tiempos_aprendidos": True,
    "usar_correccion_sesgo": False,     # (antigua) corrección por estación: la sustituye la calibración
    "usar_calibracion": True,           # aprende de los fallos: estación+sentido, tren, salida/llegada, antelación
    "fantasma_origen_min": 30,          # «parado en origen» tanto después de su hora: viaje fantasma de Renfe
    "fantasma_min": 180,                # lo mismo en cualquier estación
    "detenido_tras_min": 3,             # parado más de lo normal sin causa: incidencia
    "usar_retraso_tipico": True,        # los trenes que aún no han salido llevan su retraso habitual
    "renfe_en_marcha_desde": True,      # IN_TRANSIT_TO X de Renfe = acaba de salir de X
    "usar_gps": True,                   # posición GPS del tren sobre la vía para saber cuánto le queda
    "gps_vel_kmh": 60,                  # velocidad normal en marcha (para no fiarse de la holgura del horario)
    "arranque_frenada_min": 0.6,        # lo que se pierde arrancando y frenando en cada tramo
    "mezcla_oficial": True,             # lejos (>5 min) se mezcla con horario+retraso: medido, reduce el error
    "mezcla_peso_min": 0.8,
    "holgura_ratio": 2.0,               # si el horario da más del doble de lo que permite la vía, es holgura
    "gps_max_km": 0.4,                  # más lejos de la vía que esto: posición no fiable
    "parada_real_min": 1.0,             # medido 25/09: con retraso, entre «entra» y «sale» pasa ~1 min o más
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


def hm_salida(minutos):
    """Hora de salida redondeada hacia abajo (si sale a las 18:25:40, decir 18:26 haría perder el tren)."""
    if minutos is None:
        return "--:--"
    m = int(math.floor(minutos + 1e-6))
    return "%02d:%02d" % ((m // 60) % 24, m % 60)


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
