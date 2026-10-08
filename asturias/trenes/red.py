# -*- coding: utf-8 -*-
"""Modelo de RED ferroviaria: varias líneas que comparten estaciones y vías.

Antes había un modelo por línea (la C-4 como una recta de Gijón a Cudillero). En Asturias las
líneas se cruzan y comparten vía: la C-4 y la C-7 comparten Pravia–Muros, la C-5, C-5a y C-6 el
tronco de El Berrón, la C-1 y la C-3 Oviedo–Villabona... En vía única un tren de la C-7 puede
hacer esperar a uno de la C-4. Por eso se modela la red entera de cada ancho de vía:

  · Las ESTACIONES son nodos y los TRAMOS (entre dos estaciones seguidas) son aristas, con su
    geometría real (del GTFS) y su tipo: vía única o doble (deducido del horario: si el horario
    hace que dos trenes de sentido contrario se crucen a menudo en mitad de un tramo, es doble).
  · Cada tren recorre su camino por la red. Los «km» de un tren son los de SU recorrido.
  · Cruces, órdenes de paso, trenes que van detrás de otro y rotaciones se deducen igual que
    antes, pero entre cualquier par de trenes que compartan tramos de vía única, sean de la
    línea que sean.

La interfaz es la misma que tenía `Linea` (est, nombre, coord, viajes, cruces, ordenes,
seguimientos, rotaciones, apartaderos...), así que el estimador funciona igual.
"""
import heapq
import math
from collections import Counter, defaultdict

from ..util import distancia_km, normaliza


# Regionales y larga distancia: solo se tiene el trozo que va por la red de Cercanías, así que sus
# «cabeceras» aquí no son de verdad (siguen hasta Llanes, Ferrol, León...): no dan la vuelta.
SIN_ROTACION = ("R", "LD", "RE", "RO", "RL")


class Viaje:
    """Un tren del día. Su recorrido cubre todas las estaciones por las que pasa (las que no
    tienen parada comercial se interpolan por distancia)."""

    def __init__(self, tid, linea=""):
        self.id = tid
        digitos = "".join(c if c.isdigit() else " " for c in tid[5:]).split()
        self.num = digitos[0] if digitos else tid
        self.linea = linea
        self.dir = 1          # sentido respecto al eje principal de su línea (para la interfaz)
        self.k = []           # nodos (índices de estación) en orden de marcha
        self.sa = []          # llegada programada (minutos desde medianoche)
        self.sd = []          # salida programada
        self.para = []        # parada comercial
        self.km = []          # km recorridos desde el origen, en cada nodo
        self.pos = {}         # nodo -> posición j
        self.stop_j = {}      # stop_id -> j
        self._geo = None      # polilínea del recorrido (se calcula al pedirla)

    def __repr__(self):
        return "<Tren %s %s>" % (self.linea, self.num)


def _arista(a, b):
    return (a, b) if a < b else (b, a)


class Red:
    def __init__(self, cfg, datos):
        self.cfg = cfg
        par = datos["paradas"]
        raw = datos["viajes"]
        if not raw:
            raise RuntimeError("El horario no tiene trenes para hoy.")
        self.rutas = datos.get("rutas", [])
        lin_de = datos.get("lineas", {})
        defecto = cfg.get("linea", "")
        self.linea_de = {tid: lin_de.get(tid, defecto) for tid in raw}
        self.nums = datos.get("nums", {})          # número de tren cuando no sale del trip_id (regionales)
        self.regionales = set(datos.get("regionales", []))   # números de regionales (no dan la vuelta aquí)
        # ---- nodos: en el orden en que aparecen en los recorridos más largos (empezando por Gijón)
        largos = sorted(raw.values(), key=len, reverse=True)
        orden, vistos = [], set()
        for filas in largos:
            seq = [s for s, _, _ in filas]
            if "gij" in normaliza(par[seq[-1]][0]) and "gij" not in normaliza(par[seq[0]][0]):
                seq = seq[::-1]
            for s in seq:
                if s not in vistos:
                    vistos.add(s)
                    orden.append(s)
        self.est = orden
        self.nombre = [par[s][0] for s in orden]
        self.coord = [tuple(par[s][1:]) for s in orden]
        self.idx = {s: i for i, s in enumerate(orden)}
        # ---- tramos: las paradas seguidas de cada tren; los que «se saltan» estaciones se expanden
        directas = {}
        for filas in raw.values():
            for (s1, _, _), (s2, _, _) in zip(filas, filas[1:]):
                a, b = self.idx[s1], self.idx[s2]
                if a != b:
                    directas[_arista(a, b)] = distancia_km(self.coord[a], self.coord[b])
        self._expandir(directas)
        self._geometria(datos)
        # ---- trenes
        self.viajes = {}
        for tid, filas in raw.items():
            v = self._construir(tid, filas)
            if v:
                self.viajes[tid] = v
        self._ejes(datos)
        self._tipo_via()
        self._detectar_cruces()
        self._ordenes_en_tramo()
        self._seguimientos()
        self._rotaciones()

    # ================================================================ estructura
    def _expandir(self, directas):
        """Un tren que no para en todas las estaciones va de A a C, pero la vía pasa por B (otros
        trenes paran en B). Se busca el camino por otras paradas de longitud casi igual a la
        distancia directa: si existe, el tramo A–C se recorre como A–B–C."""
        vec = defaultdict(dict)
        for (a, b), d in directas.items():
            vec[a][b] = d
            vec[b][a] = d
        self._camino = {}
        for (a, b), d in sorted(directas.items(), key=lambda x: x[1]):
            camino = self._dijkstra(vec, a, b, prohibida=(a, b), tope=1.15 * d + 0.3)
            if camino and len(camino) > 2:
                self._camino[(a, b)] = camino
                self._camino[(b, a)] = camino[::-1]
        # tramos finos (los que no se pueden expandir)
        self.tramos = {}
        for (a, b), d in directas.items():
            if (a, b) not in self._camino:
                self.tramos[(a, b)] = d
        self.vecinos = defaultdict(set)
        for a, b in self.tramos:
            self.vecinos[a].add(b)
            self.vecinos[b].add(a)

    @staticmethod
    def _dijkstra(vec, a, b, prohibida, tope):
        dist, prev, cola = {a: 0.0}, {}, [(0.0, a)]
        while cola:
            d, n = heapq.heappop(cola)
            if n == b:
                break
            if d > dist.get(n, 1e18) or d > tope:
                continue
            for m, w in vec[n].items():
                if _arista(n, m) == _arista(*prohibida):
                    continue
                nd = d + w
                if nd < dist.get(m, 1e18) and nd <= tope:
                    dist[m], prev[m] = nd, n
                    heapq.heappush(cola, (nd, m))
        if b not in dist:
            return None
        cam = [b]
        while cam[-1] != a:
            cam.append(prev[cam[-1]])
        return cam[::-1]

    def camino(self, a, b):
        """Nodos de a a b por los tramos finos (incluidos los extremos)."""
        if _arista(a, b) in self.tramos or a == b:
            return [a, b] if a != b else [a]
        c = self._camino.get((a, b))
        if not c:
            return [a, b]
        out = [c[0]]
        for x, y in zip(c, c[1:]):
            out.extend(self.camino(x, y)[1:])
        return out

    def _geometria(self, datos):
        """Polilínea real de cada tramo: el trozo de alguna forma del GTFS que une sus dos estaciones
        (si no la hay, línea recta)."""
        formas = list((datos.get("formas") or {}).values())
        if datos.get("trazado"):
            formas.append(datos["trazado"])
        prep = []
        for pts in formas:
            if len(pts) < 2:
                continue
            acum = [0.0]
            for p, q in zip(pts, pts[1:]):
                acum.append(acum[-1] + distancia_km(p, q))
            prep.append((pts, acum))
        self.geo_tramo, self.largo = {}, {}
        for (a, b), recto in self.tramos.items():
            mejor = None
            for pts, acum in prep:
                ia, da = _mas_cerca(pts, self.coord[a])
                ib, db = _mas_cerca(pts, self.coord[b])
                if da > 0.25 or db > 0.25 or ia == ib:
                    continue
                largo = abs(acum[ib] - acum[ia])
                if largo > 1.8 * recto + 0.5:
                    continue
                if mejor is None or da + db < mejor[0]:
                    tro = pts[ia:ib + 1] if ia < ib else pts[ib:ia + 1][::-1]
                    mejor = (da + db, [list(self.coord[a])] + [list(p) for p in tro[1:-1]] + [list(self.coord[b])])
            geo = mejor[1] if mejor else [list(self.coord[a]), list(self.coord[b])]
            self.geo_tramo[(a, b)] = geo
            self.geo_tramo[(b, a)] = geo[::-1]
            lg = sum(distancia_km(p, q) for p, q in zip(geo, geo[1:]))
            self.largo[(a, b)] = self.largo[(b, a)] = max(lg, recto)

    def _construir(self, tid, filas):
        if len(filas) < 2:
            return None
        v = Viaje(tid, self.linea_de.get(tid, ""))
        if tid in self.nums:
            v.num = self.nums[tid]
        for n, (s, a, d) in enumerate(filas):
            k = self.idx[s]
            if n > 0:
                k0, d0, km0 = v.k[-1], v.sd[-1], v.km[-1]
                cam = self.camino(k0, k)
                total = sum(self.largo.get((x, y), distancia_km(self.coord[x], self.coord[y]))
                            for x, y in zip(cam, cam[1:]))
                acc = 0.0
                for x, y in zip(cam, cam[1:-1]):
                    acc += self.largo.get((x, y), distancia_km(self.coord[x], self.coord[y]))
                    t = d0 + (a - d0) * acc / max(1e-6, total)
                    v.k.append(y); v.sa.append(t); v.sd.append(t); v.para.append(False); v.km.append(km0 + acc)
                km = km0 + total
            else:
                km = 0.0
            v.k.append(k); v.sa.append(a); v.sd.append(d); v.para.append(True); v.km.append(km)
            v.stop_j[s] = len(v.k) - 1
        v.pos = {}
        for j, k in enumerate(v.k):
            v.pos.setdefault(k, j)
        return v

    def _ejes(self, datos):
        """Eje principal de cada línea (su recorrido más largo, empezando por Gijón u Oviedo): sirve
        para la interfaz (horarios por estación, gráfico de malla, sentido «ida»/«vuelta»)."""
        self.ejes = {}
        por_linea = defaultdict(list)
        for v in self.viajes.values():
            por_linea[v.linea].append(v)
        for lin, vs in por_linea.items():
            # el eje es el camino más largo de la línea; los ramales se añaden al final
            largo = max(vs, key=lambda v: v.km[-1])
            eje = list(largo.k)
            ini = normaliza(self.nombre[eje[0]])
            fin = normaliza(self.nombre[eje[-1]])
            if ("gij" in fin and "gij" not in ini) or ("oviedo" in fin and "gij" not in ini and "oviedo" not in ini):
                eje = eje[::-1]
            pos = {k: i for i, k in enumerate(eje)}
            for v in vs:
                comunes = [k for k in v.k if k in pos]
                if len(comunes) >= 2:
                    v.dir = 1 if pos[comunes[-1]] > pos[comunes[0]] else -1
                else:
                    v.dir = 1
            km = [0.0]
            for x, y in zip(eje, eje[1:]):
                km.append(km[-1] + self.largo.get((x, y), distancia_km(self.coord[x], self.coord[y])))
            ramales = sorted({k for v in vs for k in v.k} - set(eje))
            self.ejes[lin] = {"nodos": eje, "km": km, "otros": ramales}
        # compatibilidad con el modelo de una línea: km «de la línea» = km del eje de la primera
        lin0 = self.cfg.get("linea") if self.cfg.get("linea") in self.ejes else next(iter(self.ejes))
        e = self.ejes[lin0]
        kmn = dict(zip(e["nodos"], e["km"]))
        self.km = [kmn.get(k, 0.0) for k in range(len(self.est))]
        pts, acum = [list(self.coord[e["nodos"][0]])], [0.0]
        for (x, y), base in zip(zip(e["nodos"], e["nodos"][1:]), e["km"]):
            g = self.geo_tramo.get((x, y)) or [list(self.coord[x]), list(self.coord[y])]
            d = 0.0
            for p, q in zip(g, g[1:]):
                d += distancia_km(p, q)
                pts.append(list(q))
                acum.append(base + d)
        self.trazado, self.trazado_km = pts, acum

    def proyectar(self, lat, lon, km_min=None, km_max=None):
        """Km sobre el eje principal (compatibilidad con el modelo de una línea)."""
        if lat is None or lon is None or len(self.trazado) < 2:
            return None
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        pts, acum = self.trazado, self.trazado_km
        km_min = -1e9 if km_min is None else km_min
        km_max = 1e9 if km_max is None else km_max
        coslat = math.cos(math.radians(lat))
        mejor = None
        for i in range(len(pts) - 1):
            if acum[i + 1] < km_min or acum[i] > km_max:
                continue
            ax, ay = (pts[i][1] - lon) * 111.32 * coslat, (pts[i][0] - lat) * 110.57
            bx, by = (pts[i + 1][1] - lon) * 111.32 * coslat, (pts[i + 1][0] - lat) * 110.57
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if ll <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ll))
            dd = math.hypot(ax + t * dx, ay + t * dy)
            if mejor is None or dd < mejor[1]:
                mejor = (acum[i] + t * (acum[i + 1] - acum[i]), dd)
        if mejor is None:
            return None
        return (max(km_min, min(km_max, mejor[0])), mejor[1])

    def _tipo_via(self):
        """Vía doble o única en cada tramo, por el horario: en vía única el horario nunca hace que
        dos trenes de sentido contrario coincidan en mitad del tramo (salvo algún error suelto);
        en vía doble pasa continuamente."""
        recorridos = defaultdict(list)     # tramo -> [(t_entra, t_sale, sentido, tren)]
        for v in self.viajes.values():
            for j in range(len(v.k) - 1):
                a, b = v.k[j], v.k[j + 1]
                recorridos[_arista(a, b)].append((v.sd[j], v.sa[j + 1], 1 if a < b else -1, v))
        self.doble = set()
        minimo = self.cfg.get("via_doble_si_coinciden", 3)
        for t, rs in recorridos.items():
            ida = sorted((r for r in rs if r[2] == 1), key=lambda r: r[0])
            vta = sorted((r for r in rs if r[2] == -1), key=lambda r: r[0])
            n = 0
            for r in ida:
                for s in vta:
                    if s[0] >= r[1]:
                        break
                    if r[0] < s[1] - 0.01 and s[0] < r[1] - 0.01:
                        n += 1
            if n >= minimo:
                self.doble.add(t)
        for x in self.cfg.get("tramos_via_doble", []):          # [["Oviedo", "Lugones"], ...]
            try:
                a, b = self.buscar(x[0]), self.buscar(x[1])
                for p, q in zip(self.camino(a, b), self.camino(a, b)[1:]):
                    self.doble.add(_arista(p, q))
            except KeyError:
                pass
        self.recorridos = recorridos

    def unica(self, a, b):
        return _arista(a, b) not in self.doble

    # ================================================================ cruces
    def _corredor(self, a, b):
        """Tramos de vía única que a y b recorren en sentido contrario: listas de nodos (en el
        orden de a) contiguos en ambos recorridos."""
        runs, actual = [], []
        for j in range(len(a.k) - 1):
            x, y = a.k[j], a.k[j + 1]
            jx, jy = b.pos.get(x), b.pos.get(y)
            ok = jx is not None and jy is not None and jy == jx - 1 and self.unica(x, y)
            if ok:
                if not actual:
                    actual = [x]
                actual.append(y)
            elif actual:
                runs.append(actual)
                actual = []
        if actual:
            runs.append(actual)
        return runs

    def _detectar_cruces(self):
        """Del horario: en qué estación se cruzan dos trenes de sentido contrario en vía única.
        Las estaciones donde eso pasa más de una vez tienen vía de cruce (apartadero)."""
        vs = sorted(self.viajes.values(), key=lambda v: v.sd[0])
        candidatos, cuenta, eps = [], Counter(), 0.01
        self.corredores = {}
        for i, a in enumerate(vs):
            for b in vs[i + 1:]:
                if b.sd[0] > a.sa[-1]:
                    break
                if a.sa[0] > b.sd[-1] or b.sa[0] > a.sd[-1] or a is b:
                    continue
                x, y = (b, a) if (a.dir == -1 and b.dir == 1) else (a, b)   # como antes: x «de ida»
                runs = self._corredor(x, y)
                for run in runs:
                    ks = [k for k in run if x.sa[x.pos[k]] <= y.sd[y.pos[k]] + eps
                          and y.sa[y.pos[k]] <= x.sd[x.pos[k]] + eps]
                    candidatos.append((x, y, ks, run))
                    cuenta.update(ks)
        # nodos de bifurcación y cabeceras: siempre tienen más de una vía
        extremos = {v.k[0] for v in self.viajes.values()} | {v.k[-1] for v in self.viajes.values()}
        bifurcaciones = {k for k, vec in self.vecinos.items() if len(vec) >= 3}
        extra = {normaliza(x) for x in self.cfg.get("estaciones_cruce_extra", [])}
        excl = {normaliza(x) for x in self.cfg.get("estaciones_cruce_excluir", [])}
        self.apartaderos = {k for k in range(len(self.est))
                            if (cuenta[k] >= 2 or k in extremos or k in bifurcaciones
                                or normaliza(self.nombre[k]) in extra)
                            and normaliza(self.nombre[k]) not in excl}
        self.cruces = []  # (tren a, tren b, estación programada)
        for a, b, ks, run in candidatos:
            ks = [k for k in ks if k in self.apartaderos]
            if ks:
                self.cruces.append((a, b, max(ks, key=lambda k: cuenta[k])))
                self.corredores[(a.id, b.id)] = [k for k in run if k in self.apartaderos]
                continue
            # Coinciden ENTRE dos estaciones (p. ej. El Parador–Soto del Barco el 26/09): en vía única
            # uno espera al otro en el apartadero más cercano (si queda lejos, es que hay doble vía).
            for x, y in zip(run, run[1:]):
                ja, jb = a.pos[x], b.pos[y]
                ia = (a.sd[ja], a.sa[ja + 1])          # a va de x a y
                ib = (b.sd[jb], b.sa[jb + 1])          # b va de y a x
                if not (ia[0] < ib[1] and ib[0] < ia[1]):
                    continue
                atras = [k for k in run[:run.index(x) + 1] if k in self.apartaderos]
                delante = [k for k in run[run.index(y):] if k in self.apartaderos]
                opciones = []
                if atras:
                    ka = atras[-1]
                    opciones.append((b.sa[b.pos[ka]] - a.sd[a.pos[ka]], ka))
                if delante:
                    kb = delante[0]
                    opciones.append((a.sa[a.pos[kb]] - b.sd[b.pos[kb]], kb))
                if opciones and min(opciones)[0] <= self.cfg.get("cruce_en_tramo_max_min", 5):
                    self.cruces.append((a, b, min(opciones)[1]))
                    self.corredores[(a.id, b.id)] = [k for k in run if k in self.apartaderos]
                break
        self.cuenta_cruces = cuenta

    def _ordenes_en_tramo(self):
        """Vía única sin cruce «visible» en el horario: el tren que sale de un apartadero hacia un
        tramo tiene que esperar a que haya llegado el último tren que venía de frente por ese tramo."""
        ya = {(a.id, b.id, k) for a, b, k in self.cruces} | {(b.id, a.id, k) for a, b, k in self.cruces}
        llegan = defaultdict(list)       # (nodo, viene_de) -> [(hora, tren)]
        for a in self.viajes.values():
            for j in range(1, len(a.k)):
                llegan[(a.k[j], a.k[j - 1])].append((a.sa[j], a))
        self.ordenes = []
        for b in self.viajes.values():
            for j in range(len(b.k) - 1):
                L, vecino = b.k[j], b.k[j + 1]
                if L not in self.apartaderos or not self.unica(L, vecino):
                    continue
                mejor = None
                for ta, a in llegan.get((L, vecino), ()):
                    if a is b:
                        continue
                    if b.sd[j] - 90 <= ta <= b.sd[j] + 0.01 and (mejor is None or ta > mejor[0]):
                        mejor = (ta, a)
                if mejor and (mejor[1].id, b.id, L) not in ya:
                    self.ordenes.append((b, mejor[1], L))

    def _seguimientos(self):
        """Tren detrás de otro en el mismo sentido por vía única: no entra en el tramo hasta que el
        de delante ha llegado al siguiente apartadero."""
        self.seguimientos = []
        salen = defaultdict(list)      # (apartadero, siguiente nodo) -> trenes
        for v in self.viajes.values():
            for j in range(len(v.k) - 1):
                if v.k[j] in self.apartaderos and self.unica(v.k[j], v.k[j + 1]):
                    salen[(v.k[j], v.k[j + 1])].append(v)
        for (L, n), vs in salen.items():
            vs.sort(key=lambda v: v.sd[v.pos[L]])
            for lead, fol in zip(vs, vs[1:]):
                jl = lead.pos[L]
                sig = [lead.k[j] for j in range(jl + 1, len(lead.k)) if lead.k[j] in self.apartaderos]
                k2 = sig[0] if sig else lead.k[-1]
                if k2 not in fol.pos or fol.pos[k2] <= fol.pos[L]:
                    continue
                if fol.sd[fol.pos[L]] >= lead.sa[lead.pos[k2]] - 0.01:   # el horario lo respeta
                    self.seguimientos.append((lead, fol, L, k2))

    def _rotaciones(self):
        """Qué tren da la vuelta en cabecera para hacer el siguiente servicio de su línea (deducido)."""
        self.rotaciones = []
        if not self.cfg.get("usar_rotaciones", True):
            return
        finales = sorted(self.viajes.values(), key=lambda v: v.sa[-1])
        usados = set()
        for sig in sorted(self.viajes.values(), key=lambda v: v.sd[0]):
            opciones = [a for a in finales if a.id not in usados and a.k[-1] == sig.k[0] and a is not sig
                        and a.linea == sig.linea and 2 <= sig.sd[0] - a.sa[-1] <= 90
                        and sig.linea not in SIN_ROTACION
                        and sig.num not in self.regionales and a.num not in self.regionales]
            if opciones:
                ant = opciones[0]
                usados.add(ant.id)
                margen = min(self.cfg.get("vuelta_minima_min", 4), sig.sd[0] - ant.sa[-1])
                self.rotaciones.append((ant, sig, margen))

    # ================================================================ geometría de un tren
    def geo_viaje(self, v):
        """Polilínea de todo el recorrido del tren y km acumulados en cada punto."""
        if v._geo is None:
            pts, acum = [list(self.coord[v.k[0]])], [0.0]
            for j in range(len(v.k) - 1):
                g = self.geo_tramo.get((v.k[j], v.k[j + 1])) or [list(self.coord[v.k[j]]), list(self.coord[v.k[j + 1]])]
                base = v.km[j]
                tramo = [0.0]
                for p, q in zip(g, g[1:]):
                    tramo.append(tramo[-1] + distancia_km(p, q))
                esc = (v.km[j + 1] - base) / tramo[-1] if tramo[-1] > 0 else 0.0
                for p, d in zip(g[1:], tramo[1:]):
                    pts.append(list(p))
                    acum.append(base + d * esc)
            v._geo = (pts, acum)
        return v._geo

    def proyectar_viaje(self, v, lat, lon, km_min=None, km_max=None):
        """Km del recorrido del tren más cercanos al punto (lat, lon) y la distancia a la vía,
        buscando solo entre km_min y km_max."""
        if lat is None or lon is None:
            return None
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        pts, acum = self.geo_viaje(v)
        km_min = -1e9 if km_min is None else km_min
        km_max = 1e9 if km_max is None else km_max
        coslat = math.cos(math.radians(lat))
        mejor = None
        for i in range(len(pts) - 1):
            if acum[i + 1] < km_min or acum[i] > km_max:
                continue
            ax, ay = (pts[i][1] - lon) * 111.32 * coslat, (pts[i][0] - lat) * 110.57
            bx, by = (pts[i + 1][1] - lon) * 111.32 * coslat, (pts[i + 1][0] - lat) * 110.57
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if ll <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ll))
            d = math.hypot(ax + t * dx, ay + t * dy)
            if mejor is None or d < mejor[1]:
                mejor = (acum[i] + t * (acum[i + 1] - acum[i]), d)
        if mejor is None:
            return None
        return (max(km_min, min(km_max, mejor[0])), mejor[1])

    # ================================================================ ayudas
    def buscar(self, texto):
        n = normaliza(texto)
        for k, nom in enumerate(self.nombre):
            if normaliza(nom) == n:
                return k
        for k, nom in enumerate(self.nombre):
            if n in normaliza(nom):
                return k
        raise KeyError(texto)


def _mas_cerca(pts, c):
    mejor, di = 0, 1e9
    for i, p in enumerate(pts):
        d = distancia_km(p, c)
        if d < di:
            mejor, di = i, d
    return mejor, di
