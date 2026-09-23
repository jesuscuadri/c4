# -*- coding: utf-8 -*-
"""Modelo estático de la línea: estaciones, trenes del día, cruces, seguimientos y rotaciones."""
from collections import Counter

from .util import distancia_km, normaliza


class Viaje:
    """Un tren del día. Su recorrido cubre todas las estaciones entre origen y destino
    (las que no tienen parada comercial se interpolan)."""

    def __init__(self, tid):
        self.id = tid
        digitos = "".join(c if c.isdigit() else " " for c in tid[5:]).split()
        self.num = digitos[0] if digitos else tid
        self.dir = 1          # +1 hacia Cudillero / Avilés, -1 hacia Gijón
        self.k = []           # índices de estación en la línea, en orden de marcha
        self.sa = []          # llegada programada (minutos desde medianoche)
        self.sd = []          # salida programada
        self.para = []        # parada comercial
        self.pos = {}         # índice de estación -> posición j en el recorrido
        self.stop_j = {}      # stop_id -> j

    def __repr__(self):
        return "<Tren %s>" % self.num


class Linea:
    def __init__(self, cfg, datos):
        self.cfg = cfg
        par = datos["paradas"]
        raw = datos["viajes"]
        if not raw:
            raise RuntimeError("El horario no tiene trenes de la %s para hoy." % cfg["linea"])
        self.rutas = datos.get("rutas", [])
        largo = max(raw.values(), key=len)
        orden = [s for s, _, _ in largo]
        if "gij" not in normaliza(par[orden[0]][0]) and "gij" in normaliza(par[orden[-1]][0]):
            orden.reverse()
        faltan = {s for v in raw.values() for s, _, _ in v} - set(orden)
        for s in sorted(faltan):
            cerca = min(range(len(orden)), key=lambda i: distancia_km(par[s][1:], par[orden[i]][1:]))
            orden.insert(cerca + 1, s)
        self.est = orden
        self.nombre = [par[s][0] for s in orden]
        self.coord = [par[s][1:] for s in orden]
        self.idx = {s: i for i, s in enumerate(orden)}
        self._trazado(datos.get("trazado") or [])
        self.viajes = {}
        for tid, filas in raw.items():
            v = self._construir(tid, filas)
            if v:
                self.viajes[tid] = v
        self._detectar_cruces()
        self._ordenes_en_tramo()
        self._seguimientos()
        self._rotaciones()

    # ---------------------------------------------------------- geometría
    def _trazado(self, pts):
        """Coloca cada estación sobre el trazado real de la vía (km desde Gijón)."""
        if len(pts) > 10:
            if distancia_km(pts[0], self.coord[0]) > distancia_km(pts[-1], self.coord[0]):
                pts = pts[::-1]
            acum = [0.0]
            for a, b in zip(pts, pts[1:]):
                acum.append(acum[-1] + distancia_km(a, b))
            km, desde = [], 0
            for c in self.coord:
                ventana = range(desde, len(pts))
                i = min(ventana, key=lambda n: distancia_km(pts[n], c))
                km.append(acum[i])
                desde = i
            if all(b >= a for a, b in zip(km, km[1:])):
                self.trazado, self.trazado_km, self.km = pts, acum, km
                return
        self.trazado, self.trazado_km = [list(c) for c in self.coord], None
        self.km = [0.0]
        for a, b in zip(self.coord, self.coord[1:]):
            self.km.append(self.km[-1] + distancia_km(a, b))
        self.trazado_km = list(self.km)

    def _construir(self, tid, filas):
        if len(filas) < 2:
            return None
        v = Viaje(tid)
        ks = [self.idx[s] for s, _, _ in filas]
        v.dir = 1 if ks[-1] > ks[0] else -1
        for n, (s, a, d) in enumerate(filas):
            k = self.idx[s]
            if n > 0:  # estaciones intermedias sin parada: hora interpolada por distancia
                k0, d0 = v.k[-1], v.sd[-1]
                for m in range(k0 + v.dir, k, v.dir):
                    fr = abs(self.km[m] - self.km[k0]) / max(1e-6, abs(self.km[k] - self.km[k0]))
                    t = d0 + (a - d0) * fr
                    v.k.append(m); v.sa.append(t); v.sd.append(t); v.para.append(False)
            v.k.append(k); v.sa.append(a); v.sd.append(d); v.para.append(True)
            v.stop_j[s] = len(v.k) - 1
        v.pos = {k: j for j, k in enumerate(v.k)}
        return v

    # ---------------------------------------------------------- cruces
    def _detectar_cruces(self):
        """Del horario: en qué estación coinciden dos trenes de sentido contrario.
        Las estaciones donde eso pasa más de una vez tienen vía de cruce (apartadero)."""
        ida = [v for v in self.viajes.values() if v.dir == 1]
        vuelta = [v for v in self.viajes.values() if v.dir == -1]
        candidatos, cuenta, eps = [], Counter(), 0.01
        for a in ida:
            for b in vuelta:
                if a.sa[0] > b.sd[-1] or b.sa[0] > a.sd[-1]:
                    continue
                ks = [k for k in set(a.pos) & set(b.pos)
                      if a.sa[a.pos[k]] <= b.sd[b.pos[k]] + eps and b.sa[b.pos[k]] <= a.sd[a.pos[k]] + eps]
                if ks:
                    candidatos.append((a, b, ks))
                    cuenta.update(ks)
        extremos = {v.k[0] for v in self.viajes.values()} | {v.k[-1] for v in self.viajes.values()}
        extra = {normaliza(x) for x in self.cfg["estaciones_cruce_extra"]}
        excl = {normaliza(x) for x in self.cfg["estaciones_cruce_excluir"]}
        self.apartaderos = {k for k in range(len(self.est))
                            if (cuenta[k] >= 2 or k in extremos or normaliza(self.nombre[k]) in extra)
                            and normaliza(self.nombre[k]) not in excl}
        self.cruces = []  # (tren hacia Cudillero, tren hacia Gijón, estación programada)
        for a, b, ks in candidatos:
            ks = [k for k in ks if k in self.apartaderos]
            if ks:
                self.cruces.append((a, b, max(ks, key=lambda k: cuenta[k])))
        self.cuenta_cruces = cuenta

    def _ordenes_en_tramo(self):
        """Vía única sin cruce "visible" en el horario: el tren que sale de un apartadero
        hacia un tramo tiene que esperar a que haya llegado el último tren que venía de frente
        por ese mismo tramo (típico en cabeceras: Cudillero, Pravia, Avilés, Candás…)."""
        ya = {(a.id, b.id, k) for a, b, k in self.cruces}
        self.ordenes = []  # (tren que sale, tren que debe haber llegado, estación)
        for b in self.viajes.values():
            for L in (self.apartaderos & set(b.pos)):
                jb = b.pos[L]
                if jb == len(b.k) - 1:
                    continue
                vecino = L + b.dir  # estación hacia la que sale b
                mejor = None
                for a in self.viajes.values():
                    if a.dir == b.dir or L not in a.pos or vecino not in a.pos or a.pos[L] == 0:
                        continue
                    ta = a.sa[a.pos[L]]
                    if b.sd[jb] - 90 <= ta <= b.sd[jb] + 0.01 and (mejor is None or ta > mejor[1]):
                        mejor = (a, ta)
                if not mejor:
                    continue
                a = mejor[0]
                par = (a.id, b.id, L) if a.dir == 1 else (b.id, a.id, L)
                if par not in ya:
                    self.ordenes.append((b, a, L))

    def _seguimientos(self):
        """Tren detrás de otro en el mismo sentido: no entra en un tramo hasta que el de
        delante ha llegado al siguiente apartadero."""
        self.seguimientos = []
        for sentido in (1, -1):
            vs = [v for v in self.viajes.values() if v.dir == sentido]
            for L in self.apartaderos:
                pasan = sorted((v for v in vs if L in v.pos and v.pos[L] < len(v.k) - 1),
                               key=lambda v: v.sd[v.pos[L]])
                for lead, fol in zip(pasan, pasan[1:]):
                    jl = lead.pos[L]
                    sig = [lead.k[j] for j in range(jl + 1, len(lead.k)) if lead.k[j] in self.apartaderos]
                    k2 = sig[0] if sig else lead.k[-1]
                    if fol.sd[fol.pos[L]] >= lead.sa[lead.pos[k2]] - 0.01:  # el horario lo respeta
                        self.seguimientos.append((lead, fol, L, k2))

    def _rotaciones(self):
        """Qué tren da la vuelta en cabecera para hacer el siguiente servicio (deducido)."""
        self.rotaciones = []
        if not self.cfg["usar_rotaciones"]:
            return
        finales = sorted(self.viajes.values(), key=lambda v: v.sa[-1])
        usados = set()
        for sig in sorted(self.viajes.values(), key=lambda v: v.sd[0]):
            opciones = [a for a in finales if a.id not in usados and a.k[-1] == sig.k[0]
                        and a.dir != sig.dir and 2 <= sig.sd[0] - a.sa[-1] <= 90]
            if opciones:
                ant = opciones[0]
                usados.add(ant.id)
                margen = min(self.cfg["vuelta_minima_min"], sig.sd[0] - ant.sa[-1])
                self.rotaciones.append((ant, sig, margen))

    # ---------------------------------------------------------- ayudas
    def buscar(self, texto):
        n = normaliza(texto)
        for k, nom in enumerate(self.nombre):
            if normaliza(nom) == n:
                return k
        for k, nom in enumerate(self.nombre):
            if n in normaliza(nom):
                return k
        raise KeyError(texto)
