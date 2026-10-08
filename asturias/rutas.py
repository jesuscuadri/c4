# -*- coding: utf-8 -*-
"""Rutas por todo el transporte público de Asturias: trenes (con su hora REAL estimada) + autobuses
del Consorcio de Transportes de Asturias (interurbanos y urbanos de Oviedo, Avilés y Mieres, con su
horario oficial) + andar entre paradas y estaciones cercanas. En Gijón, además, el bus urbano de
EMTUSA (en directo) para llegar a la estación o salir de ella, como hacía ya el planificador de trenes.

El cálculo es RAPTOR por rondas (ronda r = viajes con r vehículos): en cada ronda se recorren los
trenes y los autobuses que pasan por las paradas alcanzadas en la ronda anterior, y luego se deja
cambiar andando a otra parada o estación cercana. Se respeta dónde se puede subir y bajar en cada
autobús interurbano (en muchas líneas, al salir de una ciudad no se puede bajar dentro de ella).

Solo biblioteca estándar.
"""
import math
import threading

from .bus.emtusa import andar_min
from .trenes import planificador as P
from .util import distancia_km, hm, hm_salida, normaliza

ANDAR_ACCESO_MAX = 1500       # m: hasta dónde se va andando del origen a una parada o estación
ACCESO_MINIMO = 3             # si no hay nada tan cerca, se miran al menos las N paradas más cercanas
ACCESO_LEJOS_MAX = 6000       # ... siempre que estén a menos de esto
TRANSBORDO_PIE_MAX = 400      # m: cambio andando entre paradas/estaciones distintas
DESVIO_ANDAR = 1.25           # las calles no van en línea recta
MARGEN_TREN = 3.0             # min para cambiar a un tren (como el planificador de trenes)
MARGEN_BUS = 2.0              # min para coger un autobús después de otro vehículo
PENALIZA_TRANSBORDO = 5.0     # un viaje con un transbordo más tiene que ahorrar esto para preferirse
PENALIZA_ANDAR = 0.5          # cada minuto andando «pesa» medio minuto más que ir sentado
MAX_VEHICULOS = 3             # hasta dos transbordos
HORIZONTE = 8 * 60            # min: autobuses que salen como mucho tantas horas después de ahora
CELDA = 0.01                  # grados: rejilla para buscar vecinos (~1 km)


def minutos_andando(metros):
    return andar_min(metros * DESVIO_ANDAR)


class Grafo:
    """Lo fijo del día: paradas de autobús del Consorcio y estaciones de tren como nodos, los cambios
    andando entre ellos, los recorridos de los autobuses y los pueblos (para buscar «Cangas de Onís»)."""

    def __init__(self, est, redes_bus):
        """est: planificador.Estaciones (trenes); redes_bus: {red: datos del día de consorcio.extraer}."""
        self.est = est
        self.nodos = []           # [tipo, ref, nombre, lat, lon, localidad]   tipo 'T' (estación) o 'S' (parada)
        self.idx = {}
        for k in range(len(est.est)):
            self._nodo("T", k, est.nombre[k], est.coord[k][0], est.coord[k][1], "")
        self.lineas = {}          # (red, código) -> info de la línea
        self.rutas = []           # recorridos de autobús: {red, linea, destino, nodos, perm, viajes: [(salida, offsets)]}
        self.alias = {}
        for red, d in redes_bus.items():
            for cod, info in d["lineas"].items():
                self.lineas[(red, cod)] = dict(info, red=red, titulo_red=d.get("titulo") or d.get("nombre", ""))
            for sid, (nom, loc, la, lo) in d["paradas"].items():
                if ("S", sid) not in self.idx:
                    self._nodo("S", sid, nom, la, lo, loc)
            self.alias.update(d.get("alias") or {})
            viajes = {}
            for vi, sal, p in d["viajes"]:
                viajes.setdefault(vi, []).append((sal, d["patrones"][p]))
            for vi, v in enumerate(d["variantes"]):
                if vi not in viajes or len(v["paradas"]) < 2:
                    continue
                self.rutas.append({"red": red, "linea": v["linea"], "destino": v["destino"],
                                   "nodos": [self.idx[("S", s)] for s in v["paradas"]],
                                   "perm": v.get("perm"), "viajes": sorted(viajes[vi])})
        # rejilla para buscar lo que hay cerca de un punto
        self.rejilla = {}
        for i, n in enumerate(self.nodos):
            self.rejilla.setdefault(self._celda(n[3], n[4]), []).append(i)
        # cambios andando entre nodos cercanos (una parada y la estación de al lado, dos paradas
        # a ambos lados de la calle...)
        self.pie = [[] for _ in self.nodos]
        for i, n in enumerate(self.nodos):
            for j, m in self.cerca(n[3], n[4], TRANSBORDO_PIE_MAX):
                if j != i:
                    self.pie[i].append((j, max(1.0, minutos_andando(m))))
        # en qué recorridos está cada nodo (para mirar solo los autobuses que pasan por donde estás)
        self.por_nodo = [[] for _ in self.nodos]
        for ri, r in enumerate(self.rutas):
            for i, nd in enumerate(r["nodos"]):
                self.por_nodo[nd].append((ri, i))
        # pueblos y ciudades: nombre -> nodos
        self.localidades = {}
        for i, n in enumerate(self.nodos):
            if n[0] == "S" and n[5]:
                self.localidades.setdefault(n[5], []).append(i)
        self._aclarar_nombres()

    def _aclarar_nombres(self):
        """Si un pueblo se llama como el principio de otro más grande («Mieres», una aldea de Siero,
        frente a «Mieres del Camín»), el pequeño pasa a llamarse «Mieres (cerca de Pola de Siero)»
        para no confundirlos, y al buscar «Mieres» sale el grande."""
        self.grande_de = {}           # nombre corto -> el pueblo grande que empieza así
        base = {loc: list(ns) for loc, ns in self.localidades.items()}      # foto de antes de renombrar nada
        nombres = list(base)
        nn = {loc: normaliza(loc) for loc in nombres}
        centro = {loc: self._centro_de(ns) for loc, ns in base.items()}
        nuevos = {}
        for loc in nombres:
            mayores = [m for m in nombres if m != loc and nn[m].startswith(nn[loc] + " ")
                       and len(base[m]) >= len(base[loc])]
            if not mayores:
                continue
            grande = max(mayores, key=lambda m: len(base[m]))
            lat, lon = centro[loc]
            otros = [(distancia_km((lat, lon), centro[m]), m) for m in nombres
                     if m not in (loc, grande) and len(base[m]) >= 8]
            ref = min(otros)[1] if otros else None
            nuevo = "%s (cerca de %s)" % (loc, ref) if ref else "%s (pueblo pequeño)" % loc
            self.localidades[nuevo] = self.localidades.pop(loc)
            nuevos[loc] = nuevo
            self.grande_de[nn[loc]] = grande
            if loc in self.alias:
                self.alias[nuevo] = self.alias[loc]
        for k, v in list(self.grande_de.items()):
            self.grande_de[k] = nuevos.get(v, v)

    def _centro_de(self, ns):
        return (sum(self.nodos[i][3] for i in ns) / len(ns), sum(self.nodos[i][4] for i in ns) / len(ns))

    def _centro(self, loc):
        ns = self.localidades[loc]
        return (sum(self.nodos[i][3] for i in ns) / len(ns), sum(self.nodos[i][4] for i in ns) / len(ns))

    def _nodo(self, tipo, ref, nombre, lat, lon, loc):
        self.idx[(tipo, ref)] = len(self.nodos)
        self.nodos.append([tipo, ref, nombre, lat, lon, loc])

    @staticmethod
    def _celda(lat, lon):
        return (int(math.floor(lat / CELDA)), int(math.floor(lon / CELDA)))

    def cerca(self, lat, lon, radio_m):
        """[(nodo, metros)] a menos de radio_m de un punto."""
        r = int(math.ceil(radio_m / 1000.0 / (CELDA * 80))) + 1
        c0, c1 = self._celda(lat, lon)
        out = []
        for a in range(c0 - r, c0 + r + 1):
            for b in range(c1 - r, c1 + r + 1):
                for i in self.rejilla.get((a, b), ()):
                    n = self.nodos[i]
                    m = distancia_km((lat, lon), (n[3], n[4])) * 1000
                    if m <= radio_m:
                        out.append((i, m))
        return out

    def mas_cercanos(self, lat, lon, n, radio_max):
        """Los n nodos más cercanos a un punto (aunque estén lejos), hasta radio_max."""
        for radio in (1500, 3000, radio_max):
            c = sorted(self.cerca(lat, lon, radio), key=lambda x: x[1])
            if len(c) >= n or radio == radio_max:
                return c[:n]
        return []

    # ------------------------------------------------------------------ buscar pueblos y paradas
    def buscar_localidad(self, texto):
        n = normaliza(texto)
        if not n:
            return None
        if n in self.grande_de:                 # «Mieres» es Mieres del Camín
            return self.punto_localidad(self.grande_de[n])
        nombres = list(self.localidades)
        claves = {loc: normaliza(loc + " " + self.alias.get(loc, "")) for loc in nombres}
        for cond in (lambda loc: normaliza(loc) == n or normaliza(self.alias.get(loc, "")) == n,
                     lambda loc: normaliza(loc).startswith(n)):
            hits = [loc for loc in nombres if cond(loc)]
            if hits:
                return self.punto_localidad(max(hits, key=lambda loc: len(self.localidades[loc])))
        hits = [loc for loc in nombres if (" " + n) in (" " + claves[loc])]
        if hits:
            return self.punto_localidad(max(hits, key=lambda loc: len(self.localidades[loc])))
        return None

    def punto_localidad(self, loc):
        ns = self.localidades[loc]
        lat = sum(self.nodos[i][3] for i in ns) / len(ns)
        lon = sum(self.nodos[i][4] for i in ns) / len(ns)
        return {"lat": lat, "lon": lon, "nombre": loc, "tipo": "localidad", "nodos": list(ns)}

    def buscar_parada(self, texto):
        n = normaliza(texto)
        if len(n) < 3:
            return None
        mejor = None
        for i, nd in enumerate(self.nodos):
            if nd[0] != "S":
                continue
            k = normaliza(nd[2])
            r = 0 if k == n else 1 if k.startswith(n) else 2 if (" " + n) in (" " + k) else None
            if r is not None and (mejor is None or (r, len(k)) < mejor[0]):
                mejor = ((r, len(k)), i)
        if mejor is None:
            return None
        nd = self.nodos[mejor[1]]
        return {"lat": nd[3], "lon": nd[4], "nombre": nd[2] + (" · " + nd[5] if nd[5] else ""), "tipo": "parada_cta",
                "nodos": [mejor[1]]}

    def sugerir(self, texto, limite=6):
        """Pueblos y ciudades (los de más paradas primero) y paradas del Consorcio."""
        n = normaliza(texto)
        if len(n) < 2:
            return []
        locs = []
        for loc, ns in self.localidades.items():
            k = normaliza(loc + " " + self.alias.get(loc, ""))
            if (" " + n) in (" " + k):
                locs.append((not normaliza(loc).startswith(n), -len(ns), loc))
        out = []
        for _, _, loc in sorted(locs)[:limite]:
            p = self.punto_localidad(loc)
            out.append({"nombre": loc, "tipo": "localidad", "lat": p["lat"], "lon": p["lon"], "texto": loc})
        if len(out) < limite:
            pars = []
            for i, nd in enumerate(self.nodos):
                if nd[0] != "S":
                    continue
                k = normaliza(nd[2])
                if (" " + n) in (" " + k):
                    pars.append((not k.startswith(n), len(k), i))
            for _, _, i in sorted(pars)[:limite - len(out)]:
                nd = self.nodos[i]
                nom = nd[2] + (" · " + nd[5] if nd[5] else "")
                out.append({"nombre": nom + " (parada de bus)", "tipo": "parada", "lat": nd[3], "lon": nd[4],
                            "texto": nom})
        return out


# ---------------------------------------------------------------------- el cálculo
def _accesos(g, punto, bus, en_vivo, ida):
    """Cómo se llega del punto (origen) a cada nodo, o de cada nodo al punto (destino).
    Devuelve {nodo: (etapas, minutos)}."""
    out = {}
    if punto.get("nodos"):                         # un pueblo: se sale (o llega) de cualquiera de sus paradas
        for i in punto["nodos"]:
            out[i] = ([], 0.0)
    c = g.cerca(punto["lat"], punto["lon"], ANDAR_ACCESO_MAX)
    if len(c) < ACCESO_MINIMO:
        vistos = {i for i, _ in c}
        c += [x for x in g.mas_cercanos(punto["lat"], punto["lon"], ACCESO_MINIMO, ACCESO_LEJOS_MAX) if x[0] not in vistos]
    for i, m in c:
        if i in out:
            continue
        nd = g.nodos[i]
        w = minutos_andando(m)
        if m <= 90:
            out[i] = ([], 0.0)
        else:
            de, a = (punto["nombre"], nd[2]) if ida else (nd[2], punto["nombre"])
            out[i] = ([{"tipo": "andar", "desde": de, "hasta": a, "metros": round(m * DESVIO_ANDAR),
                        "min": round(w, 1)}], w)
    # a las estaciones de tren, en Gijón, también en el bus urbano (EMTUSA, en directo)
    if bus is not None and getattr(bus, "red_ok", False):
        for k in P._candidatas(g.est, punto):
            i = g.idx[("T", k)]
            pt = {"lat": g.est.coord[k][0], "lon": g.est.coord[k][1], "nombre": g.est.nombre[k], "k": k}
            r = P._acceso(bus, punto, pt, en_vivo) if ida else P._salida(bus, pt, punto, en_vivo)
            if r and (i not in out or r[1] < out[i][1] - 0.5):
                out[i] = r
    return out


def _trenes(res):
    """Los trenes que aún tienen paradas por delante, en el formato del cálculo."""
    out = []
    for t in (res or {}).get("trenes", ()):
        if t.get("fin") or t.get("cancelado"):
            continue
        out.append(t)
    return out


def raptor(g, trenes, listo, llegadas, ahora, max_veh=MAX_VEHICULOS, horizonte=HORIZONTE):
    """listo: {nodo: hora a la que se está allí}; llegadas: {nodo: minutos de allí al destino}.
    Devuelve el mejor viaje como lista de tramos, o None."""
    INF = 1e18
    mejor = dict(listo)
    rondas, padres = [dict(listo)], [{k: None for k in listo}]
    marcados = set(listo)
    tope = INF
    for r in range(1, max_veh + 1):
        prev, cur, par = rondas[-1], {}, {}
        if not marcados:
            break

        def mejora(nodo, t, p):
            if t < mejor.get(nodo, INF) - 1e-6 and t < tope:
                cur[nodo], par[nodo], mejor[nodo] = t, p, t

        # trenes
        m_tren = 0.0 if r == 1 else MARGEN_TREN
        for t in trenes:
            ks, n = t["k"], len(t["k"])
            sube = None
            for j in range(max(0, t["j0"]), n):
                if not t["para"][j]:
                    continue
                nodo = g.idx[("T", ks[j])]
                if sube is not None:
                    a = t["est_a"][j]
                    if a is not None:
                        mejora(nodo, a, ("tren", t, sube[0], j, sube[1]))
                if sube is None and j < n - 1 and nodo in prev:
                    d = t["est_d"][j]
                    if d is not None and d >= prev[nodo] + m_tren - 0.05:
                        sube = (j, nodo)
        # autobuses: solo los recorridos que pasan por una parada a la que se acaba de llegar
        m_bus = 0.0 if r == 1 else MARGEN_BUS
        tocados = {}
        for nodo in marcados:
            if nodo in prev:
                for ri, i in g.por_nodo[nodo]:
                    if i < tocados.get(ri, 10 ** 9):
                        tocados[ri] = i
        for ri, i0 in tocados.items():
            ruta = g.rutas[ri]
            ns, perm, n = ruta["nodos"], ruta["perm"], len(ruta["nodos"])
            for sal, off in ruta["viajes"]:
                for base in (0, -1440):
                    t0 = sal + base
                    if t0 + off[-1] < ahora or t0 + off[0] > ahora + horizonte:
                        continue
                    for i in range(i0, n - 1):
                        nodo = ns[i]
                        if nodo not in prev or (perm and not perm[i]):
                            continue
                        if t0 + off[i] < prev[nodo] + m_bus - 0.05:
                            continue
                        j_ini, j_fin = (perm[i][0], perm[i][1]) if perm else (i + 1, n - 1)
                        for j in range(max(j_ini, i + 1), j_fin + 1):
                            mejora(ns[j], t0 + off[j], ("bus", ri, t0, i, j, nodo))
                        if not perm:
                            break          # sin restricciones, subir antes siempre es igual o mejor
        # cambiar andando a otro nodo cercano (una vez)
        for nodo, a in list(cur.items()):
            for n2, w in g.pie[nodo]:
                if a + w < mejor.get(n2, INF) - 1e-6 and a + w < tope:
                    cur[n2], par[n2], mejor[n2] = a + w, ("pie", nodo, w), a + w
        for nodo, extra in llegadas.items():
            if nodo in cur:
                tope = min(tope, cur[nodo] + extra)
        rondas.append(cur)
        padres.append(par)
        marcados = set(cur)
    # el mejor: llegar antes, penalizando transbordos y andar
    elegido = None
    for r in range(1, len(rondas)):
        for nodo, extra in llegadas.items():
            if nodo in rondas[r]:
                total = rondas[r][nodo] + extra
                nota = total + PENALIZA_TRANSBORDO * (r - 1) + PENALIZA_ANDAR * extra
                if elegido is None or nota < elegido[0] - 1e-6:
                    elegido = (nota, r, nodo, total)
    if elegido is None:
        return None
    _, r, nodo, total = elegido
    tramos, n_fin = [], nodo
    while r > 0:
        p = padres[r][nodo]
        if p[0] == "pie":
            tramos.append({"pie": True, "de": p[1], "a": nodo, "min": p[2]})
            nodo = p[1]
            p = padres[r][nodo]
        if p[0] == "tren":
            _, t, jb, ja, nb = p
            tramos.append({"tren": t, "jb": jb, "ja": ja, "de": nb, "a": nodo})
            nodo = nb
        else:
            _, ri, t0, i, j, nb = p
            tramos.append({"bus": ri, "t0": t0, "i": i, "j": j, "de": nb, "a": nodo})
            nodo = nb
        r -= 1
    tramos.reverse()
    return {"llega": total, "nodo_final": n_fin, "nodo_inicial": nodo, "tramos": tramos}


def _sale(g, x):
    if "tren" in x:
        return x["tren"]["est_d"][x["jb"]]
    return x["t0"] + _offsets(g, x)[x["i"]]


def _offsets(g, x):
    ruta = g.rutas[x["bus"]]
    for s, o in ruta["viajes"]:
        if abs(s - x["t0"]) < 1e-6 or abs(s - 1440 - x["t0"]) < 1e-6:
            return o
    return None


def _llega(g, x):
    if "tren" in x:
        return x["tren"]["est_a"][x["ja"]]
    return x["t0"] + _offsets(g, x)[x["j"]]


def _etapa_bus(g, x, espera):
    ruta = g.rutas[x["bus"]]
    off = _offsets(g, x)
    L = g.lineas.get((ruta["red"], ruta["linea"]), {})
    sale, llega = x["t0"] + off[x["i"]], x["t0"] + off[x["j"]]
    nsub, nbaj = g.nodos[ruta["nodos"][x["i"]]], g.nodos[ruta["nodos"][x["j"]]]
    return {"tipo": "autobus", "red": ruta["red"], "linea": L.get("codigo", ruta["linea"]),
            "color": L.get("color", "#6d28d9"), "nombre_linea": L.get("nombre", ""), "via": L.get("via", ""),
            "operador": L.get("operador", ""), "destino": ruta["destino"],
            "subir": nsub[2], "subir_loc": nsub[5], "bajar": nbaj[2], "bajar_loc": nbaj[5],
            "sale": round(sale, 2), "llega": round(llega, 2), "sale_hm": hm_salida(sale), "llega_hm": hm(llega),
            "paradas": x["j"] - x["i"], "espera": round(max(0.0, espera), 1), "horario": True}


def _montar(g, plan, v, acc, sal, ahora, est):
    n0, nf = v["nodo_inicial"], v["nodo_final"]
    et_acc, t_acc = acc[n0]
    et_sal, t_sal = sal[nf]
    veh = [x for x in v["tramos"] if not x.get("pie")]
    primero = veh[0]
    sale = _sale(g, primero)
    solo_andando = all(e["tipo"] == "andar" for e in et_acc)
    salir = ahora
    if solo_andando:
        salir = max(ahora, sale - t_acc - (P.MARGEN_ENLACE if et_acc else 1.0))
    etapas, previo, ant = list(et_acc), None, None
    for x in v["tramos"]:
        if x.get("pie"):
            a, b = g.nodos[x["de"]], g.nodos[x["a"]]
            etapas.append({"tipo": "andar", "desde": a[2], "hasta": b[2],
                           "metros": round(distancia_km((a[3], a[4]), (b[3], b[4])) * 1000 * DESVIO_ANDAR),
                           "min": round(x["min"], 1), "transbordo": True})
            previo = (previo or 0) + x["min"]
            continue
        s = _sale(g, x)
        espera = s - (salir + t_acc) if previo is None else s - previo
        if "tren" in x:
            t, jb, ja = x["tren"], x["jb"], x["ja"]
            if ant is not None and "tren" in ant and x["de"] == ant["a"]:      # cambio de tren en la misma estación
                etapas.append({"tipo": "transbordo", "estacion": est.nombre[t["k"][jb]], "linea": t.get("linea"),
                               "espera": round(max(0.0, espera), 1), "sale_hm": hm_salida(t["est_d"][jb])})
                etapas.append(P._etapa_tren(est, t, jb, ja, 0.0))
            else:
                etapas.append(P._etapa_tren(est, t, jb, ja, espera))
        else:
            etapas.append(_etapa_bus(g, x, espera))
        previo, ant = _llega(g, x), x
    etapas += et_sal
    llega = _llega(g, veh[-1])
    sube_n, baja_n = g.nodos[veh[0]["de"]], g.nodos[veh[-1]["a"]]
    plan.update({
        "etapas": etapas, "ok": True, "sale": round(salir, 2), "sale_hm": hm_salida(salir),
        "sale_en": round(max(0.0, salir - ahora), 1), "sale_estacion": round(sale, 2),
        "llega": round(llega + t_sal, 2), "llega_hm": hm(llega + t_sal),
        "duracion": round(llega + t_sal - salir, 1), "transbordos": len(veh) - 1,
        "lineas": [x["tren"].get("linea") if "tren" in x else None for x in veh],
        "modos": ["tren" if "tren" in x else "bus" for x in veh],
        "estacion_sub": sube_n[2], "estacion_baj": baja_n[2],
        "con_bus": any("bus" in x for x in veh),
    })
    return sale


def _resumen_alt(g, w, acc, llegadas):
    veh = [x for x in w["tramos"] if not x.get("pie")]
    s_ = _sale(g, veh[0])
    et_a, t_a = acc[w["nodo_inicial"]]
    l_ = _llega(g, veh[-1]) + llegadas[w["nodo_final"]]
    x0 = veh[0]
    if "tren" in x0:
        t0 = x0["tren"]
        num, dest, ret, cd = t0["num"], t0["destino"], t0["retraso"], t0["con_datos"]
    else:
        ruta = g.rutas[x0["bus"]]
        num, dest, ret, cd = g.lineas.get((ruta["red"], ruta["linea"]), {}).get("codigo", ruta["linea"]), ruta["destino"], 0, False
    chips = []
    for x in veh:
        if "tren" in x:
            chips.append({"tipo": "tren", "linea": x["tren"].get("linea")})
        else:
            ruta = g.rutas[x["bus"]]
            L = g.lineas.get((ruta["red"], ruta["linea"]), {})
            chips.append({"tipo": "bus", "linea": L.get("codigo", ruta["linea"]), "color": L.get("color", "#6d28d9")})
    return {"num": num, "destino": dest, "sale": round(s_, 2), "sale_hm": hm_salida(s_),
            "llega": round(l_, 2), "llega_hm": hm(l_), "desde": g.nodos[w["nodo_inicial"]][2],
            "lineas": [c["linea"] for c in chips if c["tipo"] == "tren"], "vehiculos": chips,
            "transbordos": len(veh) - 1,
            "salir_hm": hm_salida(s_ - t_a - (P.MARGEN_ENLACE if et_a else 1.0))
            if all(e["tipo"] == "andar" for e in et_a) else None,
            "retraso": ret, "con_datos": cd}


def planificar(g, res, bus, origen, destino, ahora, en_vivo=True, horizonte=HORIZONTE):
    """Plan para salir ahora entre dos puntos (ya geocodificados), con trenes y autobuses."""
    est = g.est
    plan = {"origen": origen, "destino": destino, "ahora": round(ahora, 2), "hora_ahora": hm(ahora),
            "ok": False, "etapas": [], "avisos": []}
    dist_od = distancia_km((origen["lat"], origen["lon"]), (destino["lat"], destino["lon"])) * 1000
    if dist_od < 120 or (origen.get("nodos") and origen.get("nodos") == destino.get("nodos")):
        plan["error"] = "El origen y el destino son el mismo sitio."
        return plan
    if dist_od <= 1500 and not origen.get("nodos") and not destino.get("nodos"):
        return P._plan_sin_tren(bus, origen, destino, ahora, en_vivo, plan)
    acc = _accesos(g, origen, bus, en_vivo, True)
    sal = _accesos(g, destino, bus, en_vivo, False)
    for i in set(acc) & set(sal):          # un nodo a la vez cerca del origen y del destino no sirve de parada
        if not acc[i][0] and not sal[i][0]:
            sal.pop(i)
    if not acc or not sal:
        plan["error"] = "No hay paradas ni estaciones cerca del %s." % ("origen" if not acc else "destino")
        return plan
    listo = {i: ahora + t + (P.MARGEN_ENLACE if et else 0.0) for i, (et, t) in acc.items()}
    llegadas = {i: t for i, (et, t) in sal.items()}
    trenes = _trenes(res)
    v = raptor(g, trenes, listo, llegadas, ahora, horizonte=horizonte)
    if v is None:
        if dist_od <= 4000:
            return P._plan_sin_tren(bus, origen, destino, ahora, en_vivo, plan)
        plan["error"] = "Hoy ya no quedan trenes ni autobuses que te lleven de %s a %s." % (
            _corto(origen["nombre"]), _corto(destino["nombre"]))
        plan["sin_servicio_hoy"] = True
        return plan
    sale = _montar(g, plan, v, acc, sal, ahora, est)
    # si andando se llega antes que con todo esto (sitios cercanos), mejor andar
    andando = minutos_andando(dist_od)
    if dist_od <= 3000 and ahora + andando <= plan["llega"]:
        limpio = {k: plan[k] for k in ("origen", "destino", "ahora", "hora_ahora")}
        limpio.update({"ok": False, "etapas": [], "avisos": []})
        return P._plan_sin_tren(bus, origen, destino, ahora, en_vivo, limpio)
    # otras opciones: los viajes siguientes
    alt, desde, usados = [], sale, set()
    primero = next(x for x in v["tramos"] if not x.get("pie"))
    usados.add(_clave(primero))
    for _ in range(8):
        if len(alt) == 2:
            break
        listo2 = {k: max(h, desde + 0.5) for k, h in listo.items()}
        w = raptor(g, trenes, listo2, llegadas, ahora, horizonte=horizonte)
        if w is None:
            break
        p1 = next(x for x in w["tramos"] if not x.get("pie"))
        s_ = _sale(g, p1)
        if s_ <= desde:
            break
        desde = s_
        if _clave(p1) in usados:
            continue
        usados.add(_clave(p1))
        alt.append(_resumen_alt(g, w, acc, llegadas))
    plan["alternativas"] = alt
    return plan


def trenes_de_horario(est, datos):
    """Convierte el horario de un día (gtfs.extraer_red) en trenes con el formato del cálculo."""
    pos = {e: k for k, e in enumerate(est.est)}
    out = []
    for tid, filas in (datos or {}).get("viajes", {}).items():
        k, d, a = [], [], []
        for s, llega, sale in filas:
            if s in pos:
                k.append(pos[s])
                a.append(llega)
                d.append(sale)
        if len(k) < 2:
            continue
        n = len(k)
        out.append({"id": tid, "num": tid, "linea": (datos.get("lineas") or {}).get(tid, ""), "destino": "",
                    "k": k, "j0": 0, "para": [True] * n, "est_d": d, "est_a": a, "fin": False,
                    "cancelado": False, "retraso": 0, "con_datos": False, "motivos": [], "via": None})
    return out


def primero_manana(g, trenes_manana, origen, destino):
    """El primer viaje de mañana (desde las 0:00) entre dos puntos, o None. g: grafo de mañana. Sin datos en vivo."""
    plan = planificar(g, {"trenes": trenes_manana}, None, origen, destino, 0.0, en_vivo=False,
                      horizonte=24 * 60)
    if not plan.get("ok") or not plan.get("etapas"):
        return None
    primero = next((e for e in plan["etapas"] if e.get("sale_hm") and e["tipo"] in ("tren", "autobus", "bus")), None)
    return {"sale_hm": primero["sale_hm"] if primero else plan["sale_hm"], "llega_hm": plan["llega_hm"],
            "duracion": plan.get("duracion"), "transbordos": plan.get("transbordos", 0)}


def _clave(x):
    return ("t", x["tren"]["id"]) if "tren" in x else ("b", x["bus"], x["t0"])


def _corto(nombre):
    return (nombre or "").replace(" (estación)", "").replace(" (parada)", "").replace(" (parada de bus)", "")


# ---------------------------------------------------------------------- un grafo por día, compartido
class Cache:
    """Guarda el grafo del día y lo rehace solo cuando cambian los datos (otro día, otra red...)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clave = None
        self.grafo = None

    def obtener(self, est, redes_bus):
        clave = (id(est), tuple(sorted((r, id(d)) for r, d in redes_bus.items())))
        with self.lock:
            if clave != self.clave:
                self.grafo = Grafo(est, redes_bus)
                self.clave = clave
            return self.grafo
