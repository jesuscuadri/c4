# -*- coding: utf-8 -*-
"""Historial: guarda lo observado, aprende tiempos reales y mide la precisión de las estimaciones."""
import csv
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import date, timedelta

from .util import HIST

HORIZONTES = (5, 10, 20, 30)  # minutos de antelación a los que se evalúa la estimación


def _fichero(prefijo, dia=None):
    os.makedirs(HIST, exist_ok=True)
    return os.path.join(HIST, "%s_%s.csv" % (prefijo, (dia or date.today()).strftime("%Y%m%d")))


def _anexar(ruta, cabecera, filas):
    nuevo = not os.path.exists(ruta)
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(cabecera)
        w.writerows(filas)


def guardar_observaciones(rt, linea):
    filas = [[p["ts"], tid, p["stop"], p["estado"], "%.1f" % rt.act.get(tid, {}).get("retraso", 0)]
             for tid, p in rt.pos.items() if tid in linea.viajes]
    if filas:
        _anexar(_fichero("obs"), ["ts", "trip", "stop", "estado", "retraso_min"], filas)


# --------------------------------------------------------------------------------------------
# Resumen de lo aprendido. Las observaciones en bruto (obs_*.csv, ~1 MB al día) solo se usan para
# sacar de cada día unas pocas cifras: cuánto tardan de verdad los trenes entre dos estaciones y con
# cuánto retraso sale cada servicio. Esas cifras se guardan en «aprendizaje.json», que es pequeño y
# se puede conservar fuera del servidor (ver persistencia.py), así el aprendizaje no se pierde nunca.
RESUMEN = "aprendizaje.json"
# Desde este día el modelo interpreta bien el «IN_TRANSIT_TO» de Renfe y usa el GPS: los
# errores medidos antes eran de otro modelo y no sirven para corregir sesgos del actual.
MODELO_DESDE = "20260927"


def num_servicio(tid):
    """70208 a partir de «2064X70208C4» (el número del servicio se repite cada día).
    Los regionales y de larga distancia van como «7182112026-10-07»: número + 1 + fecha."""
    if len(tid) == 16 and tid[5] == "1" and tid[10] == "-" and tid[:5].isdigit():
        return tid[:5]
    dig = "".join(c if c.isdigit() else " " for c in tid[5:]).split()
    return dig[0] if dig else tid


def resumir_dia(ruta, con_paradas=False):
    """De un obs_AAAAMMDD.csv: ({"A|B": [min, ...]}, {num: retraso_al_salir}) y, si se pide,
    también {stop: [minutos parado, ...]} (lo que dura de verdad cada parada)."""
    por_viaje = defaultdict(list)
    try:
        with open(ruta, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    ret = float(r.get("retraso_min") or 0)
                except ValueError:
                    ret = 0.0
                por_viaje[r["trip"]].append((int(r["ts"]), r["stop"], r["estado"], ret))
    except Exception:  # noqa: BLE001
        return ({}, {}, {}) if con_paradas else ({}, {})
    tramos, salidas, paradas = defaultdict(list), {}, defaultdict(list)
    for tid, obs in por_viaje.items():
        obs = sorted(set(obs))
        salida, llegada = {}, {}
        for (t0, s0, e0, _), (t1, s1, e1, _) in zip(obs, obs[1:]):
            if e0 == "STOPPED_AT" and (s1 != s0 or e1 != "STOPPED_AT"):
                salida.setdefault(s0, (t0 + t1) / 2)
            if e1 == "STOPPED_AT" and (s0 != s1 or e0 != "STOPPED_AT"):
                llegada.setdefault(s1, (t0 + t1) / 2)
        for st, t_ll in llegada.items():
            if st in salida and 0 < salida[st] - t_ll < 600:
                paradas[st].append(round((salida[st] - t_ll) / 60.0, 2))
        # orden de paso por las estaciones; si Renfe hace «saltar» el tren a la estación anterior
        # (pasa: Veriña ↔ Tremañes), cada estación cuenta solo la primera vez
        orden = []
        for _, st, _, _ in obs:
            if st not in orden:
                orden.append(st)
        for a_, b_ in zip(orden, orden[1:]):
            if a_ in salida and b_ in llegada and llegada[b_] > salida[a_]:
                tramos["%s|%s" % (a_, b_)].append(round((llegada[b_] - salida[a_]) / 60.0, 2))
        # retraso con el que salió: el que daba Renfe justo al dejar la primera estación vista
        primera = obs[0][1]
        movido = next((o for o in obs if o[1] != primera), None)
        if movido is not None:
            salidas[num_servicio(tid)] = round(movido[3], 1)
    if con_paradas:
        return dict(tramos), salidas, dict(paradas)
    return dict(tramos), salidas


def _leer_resumen():
    ruta = os.path.join(HIST, RESUMEN)
    try:
        with open(ruta, encoding="utf-8") as f:
            j = json.load(f)
        return {"tramos": j.get("tramos", {}), "salidas": j.get("salidas", {}), "paradas": j.get("paradas", {})}
    except Exception:  # noqa: BLE001
        return {"tramos": {}, "salidas": {}, "paradas": {}}


# Cada arranque del servidor es una «sesión». Lo observado en esta sesión se guarda con la clave
# AAAAMMDD_sesión, para no pisar lo guardado de sesiones anteriores del mismo día (en Render el
# disco se vacía al reiniciar o redesplegar: antes, un reinicio a media tarde borraba lo aprendido
# esa mañana).
SESION = "%d" % time.time()


def _por_dias(dic, dias):
    """Entradas (clave, valor) de los últimos `dias` días distintos (la clave empieza por AAAAMMDD)."""
    fechas = sorted({k[:8] for k in dic})[-dias:]
    return [(k, v) for k, v in sorted(dic.items()) if k[:8] in fechas]


def resumen(dias=45):
    """Lo aprendido por día: lo guardado (días y sesiones anteriores, aunque el servidor se haya
    reiniciado) más lo que se saque de las observaciones de esta sesión que haya en disco."""
    res = _leer_resumen()
    if os.path.isdir(HIST):
        for fn in sorted(f for f in os.listdir(HIST) if f.startswith("obs_"))[-dias:]:
            clave = "%s_%s" % (fn[4:12], SESION)
            tr, sa, pa = resumir_dia(os.path.join(HIST, fn), con_paradas=True)
            if tr or sa:
                res["tramos"][clave], res["salidas"][clave] = tr, sa
                res["paradas"][clave] = pa
    for c in ("tramos", "salidas", "paradas"):
        res[c] = dict(_por_dias(res[c], dias))
    return res


def guardar_resumen(res=None):
    os.makedirs(HIST, exist_ok=True)
    res = res or resumen()
    tmp = os.path.join(HIST, RESUMEN + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, separators=(",", ":"))
    os.replace(tmp, os.path.join(HIST, RESUMEN))
    return res


def aprender_tiempos(dias=30, res=None):
    """Mediana del tiempo real de marcha entre paradas consecutivas (necesita 5 muestras)."""
    res = res or resumen()
    muestras = defaultdict(list)
    for fecha, tramos in _por_dias(res["tramos"], dias):
        for clave, vals in tramos.items():
            a_, b_ = clave.split("|", 1)
            muestras[(a_, b_)].extend(vals)
    return {k: statistics.median(v) for k, v in muestras.items() if len(v) >= 5}


def aprender_paradas(dias=30, minimo=5, res=None):
    """Cuánto dura como mínimo cada parada (cuartil bajo, en minutos): {stop: min}.
    El horario suele poner llegada = salida; la realidad son 20-60 s que hay que sumar
    cuando se usan tiempos de marcha aprendidos (que no incluyen la parada)."""
    res = res or resumen()
    muestras = defaultdict(list)
    for fecha, pa in _por_dias(res.get("paradas") or {}, dias):
        for stop, vals in pa.items():
            muestras[stop].extend(x for x in vals if 0 < x <= 5)
    out = {}
    for k, v in muestras.items():
        if len(v) >= minimo:
            # cuartil bajo, no la mediana: muchas paradas largas son el tren esperando a su
            # hora de salida (va adelantado) o a un cruce; lo que interesa es lo mínimo que tarda
            v = sorted(v)
            out[k] = round(v[int(0.25 * (len(v) - 1))], 2)
    return out


def aprender_salidas(dias=21, minimo=4, res=None):
    """Retraso típico con el que sale cada servicio (número de tren), si se repite: {num: minutos}.
    Solo se guardan los servicios que suelen salir con al menos 1 minuto de retraso."""
    res = res or resumen()
    por_num = defaultdict(list)
    vistos = set()     # (día, tren): un valor por día aunque haya varias sesiones
    for fecha, sal in _por_dias(res["salidas"], dias):
        for num, r in sal.items():
            if (fecha[:8], num) in vistos:
                continue
            vistos.add((fecha[:8], num))
            if -5 <= r <= 45:
                por_num[num].append(r)
    out = {}
    for num, rs in por_num.items():
        if len(rs) >= minimo:
            m = statistics.median(rs)
            if m >= 1:
                out[num] = round(min(m, 15.0), 1)
    return out


def aprender_sesgos(dias=14, minimo=6, cap=2.0, desde=None):
    """Aprende de los fallos. Lee el historial de precisión (lo que el programa dijo que
    llegaría un tren frente a lo que llegó de verdad) y calcula, por estación, el sesgo
    sistemático: si en una estación siempre nos quedamos cortos o largos, se guarda esa
    diferencia (acotada) para corregir las próximas estimaciones.

    Devuelve {stop_id: minutos}, donde un valor positivo significa que los trenes suelen
    llegar MÁS TARDE de lo que estimábamos (hay que sumar tiempo) y uno negativo, antes."""
    if not os.path.isdir(HIST):
        return {}
    desde = MODELO_DESDE if desde is None else desde
    err = defaultdict(list)
    ficheros = sorted(f for f in os.listdir(HIST) if f.startswith("precision_") and f[10:18] >= desde)[-dias:]
    for fn in ficheros:
        try:
            with open(os.path.join(HIST, fn), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        e = float(r["real"]) - float(r["nuestra"])
                    except (ValueError, KeyError):
                        continue
                    if abs(e) <= 8:  # descarta valores absurdos (datos corruptos, trenes raros)
                        err[r["stop"]].append(e)
        except Exception:  # noqa: BLE001
            continue
    out = {}
    for stop, es in err.items():
        if len(es) >= minimo:
            m = statistics.median(es)
            if abs(m) >= 0.5:                       # solo si el sesgo es apreciable
                out[stop] = round(max(-cap, min(cap, m)), 2)
    return out


CABECERA_PRECISION = ["fecha", "trip", "stop", "horizonte", "nuestra", "adif", "real",
                      "tipo", "linea", "par", "bruta"]


def linea_de(tid):
    """«C4» a partir de «2078X70208C4» (los regionales, «R»)."""
    if len(tid) == 16 and tid[10] == "-":
        return "R"
    i = tid.rfind("C")
    return tid[i:] if i > 4 else ""


def fila_precision(r):
    """Una medición (lista del CSV, formato viejo de 7 columnas o nuevo de 11) como diccionario,
    con las horas corregidas si pasan de medianoche. None si no vale."""
    if not r or r[0] == "fecha" or len(r) < 7:
        return None
    try:
        nuestra, adif, real = float(r[4]), float(r[5]), float(r[6])
        bruta = float(r[10]) if len(r) > 10 and r[10] not in ("", None) else nuestra
        h = int(r[3])
    except (ValueError, IndexError):
        return None

    def junto(x):          # misma referencia de día que «real» (23:58 frente a 00:03)
        if x - real > 720:
            return x - 1440
        if real - x > 720:
            return x + 1440
        return x
    tid = r[1]
    return {"fecha": r[0], "trip": tid, "stop": r[2], "h": h, "nuestra": junto(nuestra), "adif": junto(adif),
            "real": real, "bruta": junto(bruta), "tipo": (r[7] if len(r) > 7 and r[7] else "a"),
            "linea": (r[8] if len(r) > 8 and r[8] else linea_de(tid)),
            "par": (r[9] if len(r) > 9 and r[9] != "" else str(int(num_servicio(tid) or 0) % 2)
                    if num_servicio(tid).isdigit() else "0")}


def leer_precision(dias=14, desde=None):
    """Mediciones de los últimos días (las más antiguas primero)."""
    if not os.path.isdir(HIST):
        return []
    desde = MODELO_DESDE if desde is None else desde
    out = []
    for fn in sorted(f for f in os.listdir(HIST) if f.startswith("precision_") and f[10:18] >= desde)[-dias:]:
        try:
            with open(os.path.join(HIST, fn), encoding="utf-8") as f:
                for r in csv.reader(f):
                    x = fila_precision(r)
                    if x:
                        out.append(x)
        except Exception:  # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------------------------
# Calibración aprendida de los errores. Medido en producción (C-4, 25/09–07/10, ~25 000 llegadas):
# el error tenía un sesgo claro por estación Y SENTIDO (en Pravia, por ejemplo, +1,6 min en un
# sentido y −1,6 en el otro), que la corrección vieja por estación anulaba al mezclar los dos
# sentidos, y un sesgo general que depende de la antelación (+0,5 min a 5 min vista, ~0 a 30).
# Ahora se aprende el error de la estimación BRUTA (sin calibrar, así no se persigue la cola) por
# estación, sentido, línea, llegada/salida y antelación, con «encogimiento» hacia la media de la
# línea cuando hay pocos datos.
HORIZ_CAL = (5, 10, 20, 30)
CAL_K = 10.0          # peso de la media de arriba (equivale a 10 mediciones)
CAL_K_TREN = 8.0      # lo mismo para el nivel «este tren en esta estación»
CAL_MAX = 3.0         # tope de corrección (min)


def _mediana(v):
    return statistics.median(v) if v else 0.0


def aprender_calibracion(dias=14, minimo=4, filas=None, minimo_tren=3):
    filas = leer_precision(dias) if filas is None else filas
    grupos = defaultdict(list)
    for x in filas:
        e = x["real"] - x["bruta"]
        if abs(e) > 8:            # incidencias (un tren parado una hora) no enseñan nada
            continue
        t, lin, h = x["tipo"], x["linea"], x["h"]
        grupos[("g", t, h)].append(e)
        grupos[("l", t, lin, h)].append(e)
        grupos[("k", t, lin, x["stop"], x["par"], h)].append(e)
        grupos[("t", t, num_servicio(x["trip"]), x["stop"], h)].append(e)

    def encoge(vals, padre):
        n = len(vals)
        return (n * _mediana(vals) + CAL_K * padre) / (n + CAL_K) if n else padre
    g = {}
    for t in ("a", "d"):
        g[t] = [round(encoge(grupos.get(("g", t, h), []), 0.0), 2) for h in HORIZ_CAL]
    lineas, claves, trenes = {}, {}, {}
    lin_de_tren = {}
    for x in filas:
        lin_de_tren[num_servicio(x["trip"])] = (x["linea"], x["par"])
    for c in grupos:
        if c[0] == "l":
            lineas.setdefault("%s|%s" % (c[1], c[2]), None)
        elif c[0] == "k":
            claves.setdefault("%s|%s|%s|%s" % c[1:5], None)
        elif c[0] == "t":
            trenes.setdefault("%s|%s|%s" % c[1:4], None)
    for c in list(lineas):
        t, lin = c.split("|")
        lineas[c] = [round(encoge(grupos.get(("l", t, lin, h), []), g[t][i]), 2) for i, h in enumerate(HORIZ_CAL)]
    for c in list(claves):
        t, lin, stop, par = c.split("|")
        base = lineas.get("%s|%s" % (t, lin), g[t])
        vals = []
        for i, h in enumerate(HORIZ_CAL):
            v = grupos.get(("k", t, lin, stop, par, h), [])
            vals.append(round(encoge(v, base[i]) if len(v) >= minimo else base[i], 2))
        if any(abs(a - b) >= 0.15 for a, b in zip(vals, base)):
            claves[c] = vals
        else:
            del claves[c]
    for c in list(trenes):
        t, num, stop = c.split("|")
        lin, par = lin_de_tren.get(num, ("", "0"))
        base = claves.get("%s|%s|%s|%s" % (t, lin, stop, par)) or lineas.get("%s|%s" % (t, lin), g[t])
        vals = []
        for i, h in enumerate(HORIZ_CAL):
            v = grupos.get(("t", t, num, stop, h), [])
            vals.append(round((len(v) * _mediana(v) + CAL_K_TREN * base[i]) / (len(v) + CAL_K_TREN)
                              if len(v) >= minimo_tren else base[i], 2))
        if any(abs(a - b) >= 0.15 for a, b in zip(vals, base)):
            trenes[c] = vals
        else:
            del trenes[c]
    return {"h": list(HORIZ_CAL), "g": g, "l": lineas, "k": claves, "t": trenes, "n": len(filas)}


def correccion(cal, tipo, linea, stop, par, falta, num=None):
    """Minutos a sumar a una hora estimada que falta `falta` minutos para pasar."""
    if not cal or falta is None or falta < 0:
        return 0.0
    vals = (num and cal.get("t", {}).get("%s|%s|%s" % (tipo, num, stop))) \
        or cal["k"].get("%s|%s|%s|%s" % (tipo, linea, stop, par)) \
        or cal["l"].get("%s|%s" % (tipo, linea)) or cal["g"].get(tipo)
    if not vals:
        return 0.0
    H = cal["h"]
    if falta <= 2:                 # a punto de llegar: lo observado manda, la corrección se apaga
        v = vals[0] * falta / 2.0
    elif falta >= H[-1]:
        v = vals[-1]
    elif falta <= H[0]:
        v = vals[0]
    else:
        i = next(i for i in range(len(H) - 1) if H[i] <= falta <= H[i + 1])
        f = (falta - H[i]) / float(H[i + 1] - H[i])
        v = vals[i] + (vals[i + 1] - vals[i]) * f
    return max(-CAL_MAX, min(CAL_MAX, v))


class Precision:
    """Compara, cuando el tren llega (o sale) de verdad, lo que dijimos antes con lo que diría Adif."""

    def __init__(self):
        self.pendientes = {}  # (trip, stop, tipo) -> {horizonte: (nuestra, adif, cuando, bruta)}
        self.hechos = set()
        self.extra = {}       # (trip, stop, tipo) -> (linea, par)

    def registrar(self, res, linea):
        ahora = res["ahora"]
        for t in res["trenes"]:
            if t["fin"] or not t["con_datos"]:
                continue
            lin = t.get("linea") or linea_de(t["id"])
            num = num_servicio(t["id"])
            par = str(int(num) % 2) if num.isdigit() else "0"
            n = len(t["k"])
            for j in range(t["j0"], n):
                if not t["para"][j]:
                    continue
                stop = linea.est[t["k"][j]]
                for tipo, est, adif, bruta in (
                        ("a", t["est_a"], t["adif_a"], t.get("_bruta_a") or t["est_a"]),
                        ("d", t["est_d"], t.get("adif_d") or t["adif_a"], t.get("_bruta_d") or t["est_d"])):
                    if tipo == "a" and j == 0 or tipo == "d" and j == n - 1 or est[j] is None:
                        continue
                    if tipo == "a" and t["parado"] and j == t["j0"]:
                        continue                       # ya está ahí
                    clave = (t["id"], stop, tipo)
                    if clave in self.hechos:
                        continue
                    falta = est[j] - ahora
                    reg = self.pendientes.setdefault(clave, {})
                    self.extra[clave] = (lin, par)
                    for h in HORIZONTES:
                        if h not in reg and h - 3 < falta <= h:
                            reg[h] = (est[j], adif[j], ahora, bruta[j] if bruta[j] is not None else est[j])

    def observar(self, rt, guardar=True):
        filas = []
        salidas = getattr(rt, "salida_vista", {})
        for (tid, stop, tipo), reg in list(self.pendientes.items()):
            visto = rt.cuando(tid, stop, True) if tipo == "a" else salidas.get((tid, stop))
            if not visto:
                continue
            real, fiable = visto
            del self.pendientes[(tid, stop, tipo)]
            lin, par = self.extra.pop((tid, stop, tipo), ("", "0"))
            self.hechos.add((tid, stop, tipo))
            if not fiable:
                continue  # ya estaba así al empezar: no sabemos cuándo pasó
            for h, (nuestra, adif, cuando, bruta) in reg.items():
                if real < cuando - 0.5:
                    continue
                filas.append([date.today().isoformat(), tid, stop, h, "%.2f" % nuestra, "%.2f" % adif, "%.2f" % real,
                              tipo, lin, par, "%.2f" % bruta])
        if filas and guardar:
            _anexar(_fichero("precision"), CABECERA_PRECISION, filas)
        return filas

    @staticmethod
    def estadisticas(dias=7, desde=None, linea=None):
        """Acierto de los últimos días: total, por antelación, por línea y en las salidas. Solo cuenta
        desde que se estrenó el modelo actual (MODELO_DESDE)."""
        desde = MODELO_DESDE if desde is None else desde
        hoy = date.today()
        dias_ok = [hoy - timedelta(days=n) for n in range(dias)]
        lineas = set(linea.split(",")) if linea else None
        if not any(d.strftime("%Y%m%d") >= desde and os.path.exists(_fichero("precision", d)) for d in dias_ok):
            desde, anterior = "", True
        else:
            anterior = False
        grupos = {"hoy": defaultdict(list), "semana": defaultdict(list)}
        por_linea, salidas = defaultdict(list), defaultdict(list)
        for n in range(dias):
            d = hoy - timedelta(days=n)
            if d.strftime("%Y%m%d") < desde:
                continue
            ruta = _fichero("precision", d)
            if not os.path.exists(ruta):
                continue
            with open(ruta, encoding="utf-8") as f:
                for r in csv.reader(f):
                    x = fila_precision(r)
                    if not x:
                        continue
                    fila = (x["nuestra"] - x["real"], x["adif"] - x["real"])
                    if x["tipo"] == "a":
                        por_linea[x["linea"]].append(fila)       # la comparativa siempre con todas
                    if lineas is not None and x["linea"] not in lineas:
                        continue
                    if x["tipo"] == "d":
                        salidas[x["h"]].append(fila)
                        continue
                    grupos["semana"][x["h"]].append(fila)
                    if n == 0:
                        grupos["hoy"][x["h"]].append(fila)

        def resumen(filas):
            if not filas:
                return None
            en = [abs(a) for a, _ in filas]
            ea = [abs(b) for _, b in filas]
            return {"n": len(filas),
                    "error_nuestro": round(statistics.mean(en), 2),
                    "error_adif": round(statistics.mean(ea), 2),
                    "acierto_nuestro": round(100.0 * sum(e <= 1 for e in en) / len(en)),
                    "acierto_adif": round(100.0 * sum(e <= 1 for e in ea) / len(ea)),
                    "sesgo_nuestro": round(statistics.mean(a for a, _ in filas), 2)}

        out = {}
        for periodo, g in grupos.items():
            out[periodo] = {str(h): resumen(g.get(h, [])) for h in HORIZONTES}
            todas = [x for h in HORIZONTES for x in g.get(h, [])]
            out[periodo]["total"] = resumen(todas)
        out["lineas"] = {lin: resumen(v) for lin, v in sorted(por_linea.items()) if lin}
        out["salidas"] = {str(h): resumen(salidas.get(h, [])) for h in HORIZONTES}
        out["salidas"]["total"] = resumen([x for h in HORIZONTES for x in salidas.get(h, [])])
        out["linea"] = linea
        out["modelo_desde"] = MODELO_DESDE
        out["incluye_modelo_anterior"] = anterior
        return out
