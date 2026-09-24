# -*- coding: utf-8 -*-
"""Historial: guarda lo observado, aprende tiempos reales y mide la precisión de las estimaciones."""
import csv
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


def aprender_tiempos(dias=30):
    """Mediana del tiempo real de marcha entre paradas consecutivas (necesita 5 muestras)."""
    muestras = defaultdict(list)
    if not os.path.isdir(HIST):
        return {}
    ficheros = sorted(f for f in os.listdir(HIST) if f.startswith("obs_"))[-dias:]
    for fn in ficheros:
        por_viaje = defaultdict(list)
        try:
            with open(os.path.join(HIST, fn), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    por_viaje[r["trip"]].append((int(r["ts"]), r["stop"], r["estado"]))
        except Exception:  # noqa: BLE001
            continue
        for obs in por_viaje.values():
            obs = sorted(set(obs))
            salida, llegada = {}, {}
            for (t0, s0, e0), (t1, s1, e1) in zip(obs, obs[1:]):
                if e0 == "STOPPED_AT" and (s1 != s0 or e1 != "STOPPED_AT"):
                    salida.setdefault(s0, (t0 + t1) / 2)
                if e1 == "STOPPED_AT" and (s0 != s1 or e0 != "STOPPED_AT"):
                    llegada.setdefault(s1, (t0 + t1) / 2)
            orden = []
            for _, s, _ in obs:
                if not orden or orden[-1] != s:
                    orden.append(s)
            for a, b in zip(orden, orden[1:]):
                if a in salida and b in llegada and llegada[b] > salida[a]:
                    muestras[(a, b)].append((llegada[b] - salida[a]) / 60.0)
    return {k: statistics.median(v) for k, v in muestras.items() if len(v) >= 5}


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
