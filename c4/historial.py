# -*- coding: utf-8 -*-
"""Historial: guarda lo observado, aprende tiempos reales y mide la precisión de las estimaciones."""
import csv
import json
import os
import statistics
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


def num_servicio(tid):
    """70208 a partir de «2064X70208C4» (el número del servicio se repite cada día)."""
    dig = "".join(c if c.isdigit() else " " for c in tid[5:]).split()
    return dig[0] if dig else tid


def resumir_dia(ruta):
    """De un obs_AAAAMMDD.csv: ({"A|B": [min, ...]}, {num: retraso_al_salir})."""
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
        return {}, {}
    tramos, salidas = defaultdict(list), {}
    for tid, obs in por_viaje.items():
        obs = sorted(set(obs))
        salida, llegada = {}, {}
        for (t0, s0, e0, _), (t1, s1, e1, _) in zip(obs, obs[1:]):
            if e0 == "STOPPED_AT" and (s1 != s0 or e1 != "STOPPED_AT"):
                salida.setdefault(s0, (t0 + t1) / 2)
            if e1 == "STOPPED_AT" and (s0 != s1 or e0 != "STOPPED_AT"):
                llegada.setdefault(s1, (t0 + t1) / 2)
        orden = []
        for _, st, _, _ in obs:
            if not orden or orden[-1] != st:
                orden.append(st)
        for a_, b_ in zip(orden, orden[1:]):
            if a_ in salida and b_ in llegada and llegada[b_] > salida[a_]:
                tramos["%s|%s" % (a_, b_)].append(round((llegada[b_] - salida[a_]) / 60.0, 2))
        # retraso con el que salió: el que daba Renfe justo al dejar la primera estación vista
        primera = obs[0][1]
        movido = next((o for o in obs if o[1] != primera), None)
        if movido is not None:
            salidas[num_servicio(tid)] = round(movido[3], 1)
    return dict(tramos), salidas


def _leer_resumen():
    ruta = os.path.join(HIST, RESUMEN)
    try:
        with open(ruta, encoding="utf-8") as f:
            j = json.load(f)
        return {"tramos": j.get("tramos", {}), "salidas": j.get("salidas", {})}
    except Exception:  # noqa: BLE001
        return {"tramos": {}, "salidas": {}}


def resumen(dias=45):
    """Lo aprendido por día: lo guardado (días anteriores, aunque el servidor se haya reiniciado)
    más lo que se saque de las observaciones que haya en disco (el día de hoy manda)."""
    res = _leer_resumen()
    if os.path.isdir(HIST):
        for fn in sorted(f for f in os.listdir(HIST) if f.startswith("obs_"))[-dias:]:
            fecha = fn[4:12]
            tr, sa = resumir_dia(os.path.join(HIST, fn))
            if tr or sa:
                res["tramos"][fecha], res["salidas"][fecha] = tr, sa
    for clave in ("tramos", "salidas"):
        res[clave] = {f: v for f, v in sorted(res[clave].items())[-dias:]}
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
    for fecha, tramos in sorted(res["tramos"].items())[-dias:]:
        for clave, vals in tramos.items():
            a_, b_ = clave.split("|", 1)
            muestras[(a_, b_)].extend(vals)
    return {k: statistics.median(v) for k, v in muestras.items() if len(v) >= 5}


def aprender_salidas(dias=21, minimo=4, res=None):
    """Retraso típico con el que sale cada servicio (número de tren), si se repite: {num: minutos}.
    Solo se guardan los servicios que suelen salir con al menos 1 minuto de retraso."""
    res = res or resumen()
    por_num = defaultdict(list)
    for fecha, sal in sorted(res["salidas"].items())[-dias:]:
        for num, r in sal.items():
            if -5 <= r <= 45:
                por_num[num].append(r)
    out = {}
    for num, rs in por_num.items():
        if len(rs) >= minimo:
            m = statistics.median(rs)
            if m >= 1:
                out[num] = round(min(m, 15.0), 1)
    return out


def aprender_sesgos(dias=14, minimo=6, cap=2.0):
    """Aprende de los fallos. Lee el historial de precisión (lo que el programa dijo que
    llegaría un tren frente a lo que llegó de verdad) y calcula, por estación, el sesgo
    sistemático: si en una estación siempre nos quedamos cortos o largos, se guarda esa
    diferencia (acotada) para corregir las próximas estimaciones.

    Devuelve {stop_id: minutos}, donde un valor positivo significa que los trenes suelen
    llegar MÁS TARDE de lo que estimábamos (hay que sumar tiempo) y uno negativo, antes."""
    if not os.path.isdir(HIST):
        return {}
    err = defaultdict(list)
    ficheros = sorted(f for f in os.listdir(HIST) if f.startswith("precision_"))[-dias:]
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


class Precision:
    """Compara, cuando el tren llega de verdad, lo que dijimos antes con lo que diría Adif."""

    def __init__(self):
        self.pendientes = {}  # (trip, stop) -> {horizonte: (nuestra, adif, cuando)}
        self.hechos = set()

    def registrar(self, res, linea):
        ahora = res["ahora"]
        for t in res["trenes"]:
            if t["fin"] or not t["con_datos"]:
                continue
            for j in range(t["j0"], len(t["k"])):
                if not t["para"][j] or t["est_a"][j] is None:
                    continue
                clave = (t["id"], linea.est[t["k"][j]])
                if clave in self.hechos:
                    continue
                falta = t["est_a"][j] - ahora
                reg = self.pendientes.setdefault(clave, {})
                for h in HORIZONTES:
                    if h not in reg and h - 3 < falta <= h:
                        reg[h] = (t["est_a"][j], t["adif_a"][j], ahora)

    def observar(self, rt, guardar=True):
        filas = []
        for (tid, stop), reg in list(self.pendientes.items()):
            visto = rt.cuando(tid, stop, True)
            if not visto:
                continue
            real, fiable = visto
            del self.pendientes[(tid, stop)]
            self.hechos.add((tid, stop))
            if not fiable:
                continue  # ya estaba parado al empezar: no sabemos cuándo llegó
            for h, (nuestra, adif, cuando) in reg.items():
                if real < cuando - 0.5:
                    continue
                filas.append([date.today().isoformat(), tid, stop, h, "%.2f" % nuestra, "%.2f" % adif, "%.2f" % real])
        if filas and guardar:
            _anexar(_fichero("precision"), ["fecha", "trip", "stop", "horizonte", "nuestra", "adif", "real"], filas)
        return filas

    @staticmethod
    def estadisticas(dias=7):
        hoy = date.today()
        grupos = {"hoy": defaultdict(list), "semana": defaultdict(list)}
        for n in range(dias):
            d = hoy - timedelta(days=n)
            ruta = _fichero("precision", d)
            if not os.path.exists(ruta):
                continue
            with open(ruta, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        fila = (float(r["nuestra"]) - float(r["real"]), float(r["adif"]) - float(r["real"]))
                    except ValueError:
                        continue
                    grupos["semana"][int(r["horizonte"])].append(fila)
                    if n == 0:
                        grupos["hoy"][int(r["horizonte"])].append(fila)

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
        return out
