# -*- coding: utf-8 -*-
"""Los autobuses urbanos de Gijón (EMTUSA) como una red más del planificador.

EMTUSA no publica su horario: solo tenemos el recorrido de cada línea (paradas, trazado) y los minutos
en directo de cada parada. Para poder planificar viajes que usan el bus urbano (a otra hora, mañana, o en
mitad de un viaje largo) se inventa un horario por FRECUENCIA: un bus cada N minutos a lo largo del día,
con el tiempo entre paradas sacado de la distancia real del recorrido. Las horas son aproximadas y la
interfaz lo dice («cada ~12 min»). El formato es el mismo que el de las redes del Consorcio.
"""
import re

# (desde, hasta, cada cuántos minutos), en minutos desde medianoche
LABORABLE = [(6 * 60 + 30, 7 * 60 + 30, 20), (7 * 60 + 30, 21 * 60, 12), (21 * 60, 22 * 60 + 45, 20)]
FESTIVO = [(7 * 60 + 30, 22 * 60 + 30, 20)]
POCO_FRECUENTE = 30               # líneas de barrio/rural (M1-M4, E71): mucho menos que las principales
KMH = 17.0                        # velocidad media urbana, sin contar las paradas
PARADA_MIN = 0.3                  # lo que se tarda en cada parada


def _escala(codigo):
    return POCO_FRECUENTE / 12.0 if re.match(r"^(M\d|E?71)", codigo or "") else 1.0


def _offsets(t):
    n = len(t["paradas"])
    ids, ip, acc = t.get("_ids") or [], t.get("_ip") or [], t.get("_acc") or []
    if len(ids) == n and len(ip) == n and acc:
        off = [acc[ip[i]] * 60.0 / KMH + PARADA_MIN * i for i in range(n)]
    else:
        off = [1.4 * i for i in range(n)]
    for i in range(1, n):                                # siempre crecen
        off[i] = max(off[i], off[i - 1] + 0.3)
    return [round(o, 1) for o in off]


def _salidas(codigo, laborable, k):
    """Minutos del día a los que sale un bus de esa línea (k = cuántos recorridos distintos se reparten la frecuencia)."""
    out = []
    fase = (sum(ord(c) for c in codigo) * 3) % 11
    for ini, fin, cada in (LABORABLE if laborable else FESTIVO):
        paso = max(5.0, cada * _escala(codigo) * k)
        t = ini + fase % max(1, int(paso))
        while t < fin:
            out.append(round(float(t), 1))
            t += paso
    return out


def como_red(emtusa, dia):
    """La red de EMTUSA con horario por frecuencia para un día (date). None si no hay red cargada."""
    if not emtusa or not getattr(emtusa, "red_ok", False):
        return None
    laborable = dia.weekday() < 5
    lineas, paradas, variantes, patrones, viajes = {}, {}, [], [], []
    for cod, l in emtusa.lineas_d.items():
        if (l.get("nombre") or "").upper().startswith("BUHO") or (l.get("nombre") or "").upper().startswith("BÚHO"):
            continue                                       # los búhos (nocturnos) no entran
        lineas[l["codigo"]] = {"codigo": l["codigo"], "nombre": l["nombre"].title() if l["nombre"].isupper() else l["nombre"],
                               "color": l["color"], "operador": "EMTUSA", "frecuencia": True}
    # recorridos de cada línea y sentido; los que son un trozo de otro más largo no cuentan aparte
    grupos = {}
    for t in emtusa.trayectos:
        if t["codigo"] not in lineas or len(t["paradas"]) < 2:
            continue
        grupos.setdefault((t["codigo"], t["direccion"]), []).append(t)
    for (cod, _), ts in grupos.items():
        ts = sorted(ts, key=lambda t: -len(t["paradas"]))
        elegidos = []
        for t in ts:
            if not any(set(t["paradas"]) <= set(e["paradas"]) for e in elegidos):
                elegidos.append(t)
        for t in elegidos:
            ids = []
            for sid in t["paradas"]:
                p = emtusa.paradas_d.get(sid)
                if not p:
                    continue
                k = "E%d" % sid
                paradas.setdefault(k, [p["nombre"], "", p["lat"], p["lon"]])
                ids.append(k)
            if len(ids) != len(t["paradas"]):
                continue
            vi = len(variantes)
            variantes.append({"linea": cod, "destino": t["destino"].title() if t["destino"].isupper() else t["destino"],
                              "paradas": ids, "forma": None})
            patrones.append(_offsets(t))
            for s in _salidas(cod, laborable, len(elegidos)):
                viajes.append([vi, s, vi])
    return {"red": "emtusa", "tipo": "urbano", "nombre": "Gijón", "titulo": "Autobuses urbanos de Gijón", "operador": "EMTUSA",
            "color": "#169cd8", "fecha": dia.isoformat(), "lineas": lineas, "paradas": paradas, "variantes": variantes,
            "patrones": patrones, "viajes": viajes, "alias": {}}
