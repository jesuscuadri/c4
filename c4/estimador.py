# -*- coding: utf-8 -*-
"""Simulación de la línea en vía única: calcula a qué hora llegará cada tren a cada estación."""
from collections import defaultdict

from .util import ahora_min

MOTIVO_MIN = 0.5  # por debajo de esto no merece la pena explicar la espera


class Estimador:
    def __init__(self, linea, cfg, aprendidos=None, sesgos=None):
        self.L = linea
        self.cfg = cfg
        self.aprendidos = aprendidos or {}
        self.sesgos = sesgos or {}                 # {stop_id: min} corrección aprendida de errores
        self.sesgo_damp = cfg.get("sesgo_damp", 0.5)
        self.sesgo_cap = cfg.get("sesgo_cap", 1.0)
        self.cruces_activos = []

    # ------------------------------------------------------------------ estado actual
    def _recorrido_teorico(self, v, j0, j1):
        """Minutos entre la salida de j0 y la llegada a j1 (con tiempos aprendidos si los hay)."""
        t = 0.0
        for j in range(j0, j1):
            t += self._recorrido(v, j, False)
        # las paradas intermedias sin parada comercial no suman
        return t

    def _estado_inicial(self, v, rt, ahora):
        """Dónde está el tren ahora: próxima estación j0, hora de llegada a ella y retraso actual."""
        L = self.L
        p = rt.pos.get(v.id)
        u = rt.act.get(v.id)
        n = len(v.k)
        e = {"j0": 0, "llegada0": v.sa[0], "parado": False, "fin": False, "cancelado": False,
             "retraso": (u["retraso"] if u else 0.0), "con_datos": bool(p or u), "situacion": "",
             "fuente": "horario"}
        if u and u.get("cancelado"):
            e.update(fin=True, cancelado=True, situacion="Cancelado")
            return e
        if p and p["stop"] in v.stop_j:
            j = v.stop_j[p["stop"]]
            nombre = L.nombre[v.k[j]]
            if p["estado"] == "STOPPED_AT" or j == 0:
                visto = rt.cuando(v.id, p["stop"], True) if p["estado"] == "STOPPED_AT" else None
                llegada = min(ahora, visto[0]) if visto else ahora
                if j == 0:
                    llegada = min(ahora, v.sa[0])
                e.update(j0=j, llegada0=llegada, parado=True, fuente="posición",
                         situacion=("En " if j == 0 else "Parado en ") + nombre)
                e["retraso"] = u["retraso"] if u else max(0.0, llegada - v.sa[j], ahora - v.sd[j])
                if j == n - 1:
                    e.update(fin=True, situacion="Llegado a " + nombre)
                return e
            # en marcha hacia j: ¿cuándo salió de la parada anterior?
            jp = max((i for i in range(j) if v.para[i]), default=0)
            marcha = self._recorrido_teorico(v, jp, j)
            visto = rt.cuando(v.id, p["stop"], False)
            if visto and visto[1]:          # vimos el momento en que arrancó
                est = visto[0] + marcha
                e["fuente"] = "posición"
            elif u and u["stop"] == p["stop"] and u["hora"]:
                est = u["hora"]
                e["fuente"] = "Renfe"
            else:                           # no sabemos cuándo salió: suponemos media marcha hecha
                est = max(ahora + marcha * 0.5, v.sa[j] - 0.5)
                e["fuente"] = "posición (aprox.)"
            if p["estado"] == "INCOMING_AT":
                est = min(est, ahora + 1.0)
            llegada = max(ahora + 0.2, est)
            e.update(j0=j, llegada0=llegada,
                     situacion=("Entrando en " if p["estado"] == "INCOMING_AT" else "Hacia ") + nombre)
            e["retraso"] = u["retraso"] if u else max(0.0, llegada - v.sa[j])
            return e
        if u and u["stop"] in v.stop_j:
            j = v.stop_j[u["stop"]]
            e.update(j0=j, llegada0=max(ahora, u["hora"] or v.sa[j] + e["retraso"]), fuente="Renfe",
                     situacion="Hacia " + L.nombre[v.k[j]] + " (sin posición)")
            if j == 0:
                e["parado"] = True
            return e
        r = e["retraso"]
        if v.sd[0] + r > ahora - 1:
            e.update(situacion="Aún no ha salido")
            return e
        if v.sa[-1] + r < ahora - 2:
            e.update(fin=True, j0=n - 1, situacion="Terminado")
            return e
        j = next((j for j in range(n) if v.sa[j] + r >= ahora), n - 1)
        e.update(j0=j, llegada0=max(ahora, v.sa[j] + r), situacion="Sin datos en tiempo real: se supone en hora")
        return e

    def _rotaciones_por_via(self, rt):
        """Renfe indica la vía: si un tren espera en la vía 12 de Gijón y otro está entrando
        en esa misma vía, el que entra es el que hará el servicio."""
        L = self.L
        out = []
        for sig in L.viajes.values():
            p = rt.pos.get(sig.id)
            if not p or not p.get("via") or p["stop"] != L.est[sig.k[0]] or p["estado"] != "STOPPED_AT":
                continue
            for ant in L.viajes.values():
                q = rt.pos.get(ant.id)
                if (ant is not sig and q and q.get("via") == p["via"] and q["stop"] == p["stop"]
                        and q["estado"] != "STOPPED_AT" and ant.k[-1] == sig.k[0]):
                    out.append((ant, sig))
        return out

    # ------------------------------------------------------------------ marcha
    def _recorrido(self, v, j, tarde):
        base = max(0.3, v.sa[j + 1] - v.sd[j])
        if self.aprendidos and v.para[j] and v.para[j + 1]:
            clave = (self.L.est[v.k[j]], self.L.est[v.k[j + 1]])
            if clave in self.aprendidos:
                base = min(max(self.aprendidos[clave], base * 0.6), base * 1.8)
        if tarde and self.cfg["recuperacion"] > 0:
            base *= 1 - self.cfg["recuperacion"]
        # corrección aprendida de los fallos: si en la estación de llegada solemos
        # equivocarnos siempre en el mismo sentido, ajustamos (amortiguado y acotado)
        if self.sesgos:
            c = self.sesgos.get(self.L.est[v.k[j + 1]])
            if c is not None:
                base = max(0.2, base + max(-self.sesgo_cap, min(self.sesgo_cap, self.sesgo_damp * c)))
        return base

    def _resolver(self, viajes, estados, deps, ahora):
        A = {v.id: [None] * len(v.k) for v in viajes}
        D = {v.id: [None] * len(v.k) for v in viajes}
        por_que = {v.id: {} for v in viajes}
        for v in viajes:  # lo ya recorrido: horario + retraso, nunca después de ahora
            e = estados[v.id]
            r = e["retraso"] or 0.0
            hasta = len(v.k) if e["fin"] else e["j0"]
            for j in range(hasta):
                A[v.id][j] = min(v.sa[j] + r, ahora)
                D[v.id][j] = min(v.sd[j] + r, ahora)
            if e["fin"] and e["parado"]:
                A[v.id][-1] = D[v.id][-1] = e["llegada0"]
        orden = sorted(viajes, key=lambda v: v.sd[0])
        pmin = self.cfg["parada_minima_min"]
        for _ in range(300):
            cambio = False
            for v in orden:
                e = estados[v.id]
                if e["fin"]:
                    continue
                a_v, d_v = A[v.id], D[v.id]
                for j in range(e["j0"], len(v.k)):
                    if j == e["j0"]:
                        a = e["llegada0"]
                    else:
                        a = d_v[j - 1] + self._recorrido(v, j - 1, d_v[j - 1] > v.sd[j - 1] + 0.5)
                    if a_v[j] is None or abs(a - a_v[j]) > 1e-6:
                        cambio = True
                    a_v[j] = a
                    if j == len(v.k) - 1:
                        d_v[j] = a
                        break
                    # la parada programada ya va en el horario; si va tarde puede acortarla
                    parada = min(v.sd[j] - v.sa[j], pmin) if v.para[j] else 0.0
                    base = max(v.sd[j], a + max(0.0, parada))
                    if j == e["j0"]:
                        base = max(base, ahora + (0.1 if e["parado"] else 0.0))
                        if j == 0 and e["retraso"] and e["con_datos"]:
                            base = max(base, v.sd[0] + e["retraso"])
                    d, motivo = base, None
                    for tipo, otro, oj, margen, info, tope in deps.get((v.id, j), ()):
                        val = A[otro.id][oj]
                        if val is None:
                            continue
                        val += margen
                        if tope is not None:
                            val = min(val, tope)
                        if val > d + 1e-6:
                            d, motivo = val, (tipo, otro, oj, info)
                    d = min(d, max(ahora, v.sd[j]) + 240)  # tope de seguridad
                    if d_v[j] is None or abs(d - d_v[j]) > 1e-6:
                        cambio = True
                    d_v[j] = d
                    if motivo and d - base > MOTIVO_MIN:
                        por_que[v.id][j] = (motivo, d - base)
                    else:
                        por_que[v.id].pop(j, None)
            if not cambio:
                break
        return A, D, por_que

    # ------------------------------------------------------------------ cálculo completo
    def calcular(self, rt, ahora=None):
        L, cfg = self.L, self.cfg
        ahora = ahora_min() if ahora is None else ahora
        viajes = list(L.viajes.values())
        estados = {v.id: self._estado_inicial(v, rt, ahora) for v in viajes}

        def pasado(v, k):
            e = estados[v.id]
            return e["fin"] or v.pos[k] < e["j0"]

        deps = defaultdict(list)

        def dep(v, j, tipo, otro, oj, margen, info=None, tope=None):
            deps[(v.id, j)].append((tipo, otro, oj, margen, info, tope))

        for lead, fol, k, k2 in L.seguimientos:
            dep(fol, fol.pos[k], "seguimiento", lead, lead.pos[k2], cfg["margen_seguimiento_min"], k2)
        m_cruce = cfg["margen_cruce_min"]
        for b, a, k in L.ordenes:  # vía única: el que sale espera al último que venía de frente
            if not pasado(b, k):
                jb, ja = b.pos[k], a.pos[k]
                dep(b, jb, "tramo", a, ja, max(0.0, min(m_cruce, b.sd[jb] - a.sa[ja])))
        # rotaciones de material en cabecera
        rot_dinamicas = self._rotaciones_por_via(rt)
        con_via = {sig.id for _, sig in rot_dinamicas}
        for ant, sig in rot_dinamicas:
            dep(sig, 0, "rotacion", ant, len(ant.k) - 1, cfg["vuelta_minima_min"],
                tope=sig.sd[0] + 3 * cfg["rotacion_espera_max_min"])
        for ant, sig, margen in L.rotaciones:
            # si el tren ya aparece en origen, el material está ahí (salvo que la vía diga otra cosa)
            if sig.id not in con_via and not estados[sig.id]["con_datos"]:
                dep(sig, 0, "rotacion", ant, len(ant.k) - 1, margen,
                    tope=sig.sd[0] + cfg["rotacion_espera_max_min"])

        # 1ª pasada sin cruces: hora a la que llegaría cada tren si nadie le cortase el paso
        A0, _, _ = self._resolver(viajes, estados, deps, ahora)

        def poner_cruce(a, b, k, info, programado):
            m = cfg["margen_cruce_min"]
            ja, jb = a.pos[k], b.pos[k]
            ma = max(0.0, min(m, a.sd[ja] - b.sa[jb])) if programado else m
            mb = max(0.0, min(m, b.sd[jb] - a.sa[ja])) if programado else m
            if k != b.k[0] and ja < len(a.k) - 1 and not pasado(a, k):
                dep(a, ja, "cruce", b, jb, ma, info)
            if k != a.k[0] and jb < len(b.k) - 1 and not pasado(b, k):
                dep(b, jb, "cruce", a, ja, mb, info)

        self.cruces_activos = []
        for a, b, k in L.cruces:  # a va hacia Cudillero/Avilés, b hacia Gijón
            pa, pb = pasado(a, k), pasado(b, k)
            if pa and pb:
                continue
            elegido, info = k, "programado"
            if pa != pb or cfg["cruces"] == "dinamicos":
                cands = [c for c in sorted(L.apartaderos)
                         if c in a.pos and c in b.pos and not pasado(a, c) and not pasado(b, c)]
                if not cands:
                    continue
                coste = {c: abs(A0[a.id][a.pos[c]] - A0[b.id][b.pos[c]]) for c in cands}
                if pa != pb:
                    elegido, info = min(cands, key=lambda c: coste[c]), "movido"
                elif k in cands and coste[k] > cfg["umbral_cambio_cruce_min"]:
                    mejor = min(cands, key=lambda c: coste[c] + (0 if c == k else 1))
                    if coste[k] - coste[mejor] > 2:
                        elegido, info = mejor, "movido"
            poner_cruce(a, b, elegido, info, elegido == k)
            self.cruces_activos.append((a, b, elegido, info, k))

        A, D, por_que = self._resolver(viajes, estados, deps, ahora)
        return self._salida(viajes, estados, A, D, por_que, ahora, rt)

    # ------------------------------------------------------------------ resultado
    def _salida(self, viajes, estados, A, D, por_que, ahora, rt):
        L = self.L
        trenes = []
        for v in viajes:
            e = estados[v.id]
            motivos = []
            for j, ((tipo, otro, oj, info), espera) in sorted(por_que[v.id].items()):
                sitio = L.nombre[v.k[j]]
                if tipo == "cruce":
                    txt = "Espera en %s a que llegue el %s (hacia %s)" % (sitio, otro.num, L.nombre[otro.k[-1]])
                    if info == "movido":
                        txt += " · cruce trasladado"
                elif tipo == "tramo":
                    txt = "Espera en %s a que llegue el %s, que viene de frente por la vía única" % (
                        sitio, otro.num)
                elif tipo == "seguimiento":
                    txt = "Va detrás del %s: no sale de %s hasta que este llegue a %s" % (
                        otro.num, sitio, L.nombre[info])
                else:
                    txt = "Espera a que llegue el %s, que es el tren que hace este servicio" % otro.num
                motivos.append({"j": j, "k": v.k[j], "texto": txt, "min": round(espera, 1), "tipo": tipo,
                                "con": otro.num})
            p = rt.pos.get(v.id)
            r = e["retraso"] or 0.0
            j0 = e["j0"]
            trenes.append({
                "id": v.id, "num": v.num, "dir": v.dir,
                "origen": L.nombre[v.k[0]], "destino": L.nombre[v.k[-1]],
                "situacion": e["situacion"], "fin": e["fin"], "cancelado": e["cancelado"],
                "j0": j0, "parado": e["parado"], "con_datos": e["con_datos"], "fuente": e["fuente"],
                "retraso": round(r, 1),
                "k": v.k, "para": v.para,
                "prog_a": [round(x, 2) for x in v.sa], "prog_d": [round(x, 2) for x in v.sd],
                "est_a": [None if x is None else round(x, 3) for x in A[v.id]],
                "est_d": [None if x is None else round(x, 3) for x in D[v.id]],
                # lo que calcularía la app oficial: horario + retraso actual, igual en todo el recorrido
                "adif_a": [round(x + r, 2) for x in v.sa],
                "adif_d": [round(x + r, 2) for x in v.sd],
                "motivos": motivos,
                "via": p.get("via") if p else None,
            })
        cruces = []
        for a, b, k, info, k0 in self.cruces_activos:
            ja, jb = a.pos[k], b.pos[k]
            aa, ab = A[a.id][ja], A[b.id][jb]
            if aa is None or ab is None:
                continue
            da, db = D[a.id][ja], D[b.id][jb]
            espera_a = (da - aa) if da is not None else 0
            espera_b = (db - ab) if db is not None else 0
            extra = {}
            for t, jj in ((a, ja), (b, jb)):
                m = por_que[t.id].get(jj)
                extra[t.num] = round(m[1], 1) if m and m[0][0] == "cruce" else 0
            cruces.append({"k": k, "estacion": L.nombre[k], "programado": L.nombre[k0], "info": info,
                           "hora": round(max(aa, ab), 2),
                           "ida": {"num": a.num, "id": a.id, "llega": round(aa, 2),
                                   "sale": None if da is None else round(da, 2),
                                   "espera": round(espera_a, 1), "retraso_extra": extra[a.num]},
                           "vuelta": {"num": b.num, "id": b.id, "llega": round(ab, 2),
                                      "sale": None if db is None else round(db, 2),
                                      "espera": round(espera_b, 1), "retraso_extra": extra[b.num]}})
        cruces.sort(key=lambda c: c["hora"])
        return {"ahora": round(ahora, 3), "trenes": trenes, "cruces": cruces}


def viajes_entre(res, origen, destino, limite=6):
    """Próximos trenes que paran en origen y después en destino."""
    ahora = res["ahora"]
    filas = []
    for t in res["trenes"]:
        if origen not in t["k"] or destino not in t["k"] or t["fin"]:
            continue
        jo, jd = t["k"].index(origen), t["k"].index(destino)
        if jo >= jd or not t["para"][jo] or not t["para"][jd] or t["j0"] > jo:
            continue
        sal = t["est_d"][jo]
        if sal is None or sal < ahora - 0.5:
            continue
        filas.append({"tren": t, "jo": jo, "jd": jd,
                      "motivos": [m for m in t["motivos"] if jo <= m["j"] < jd],
                      "motivos_antes": [m for m in t["motivos"] if m["j"] < jo]})
    filas.sort(key=lambda f: f["tren"]["est_d"][f["jo"]])
    return filas[:limite]
