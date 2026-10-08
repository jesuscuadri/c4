# -*- coding: utf-8 -*-
"""Planificador puerta a puerta: combina el autobús urbano de Gijón (EMTUSA) con los trenes
de Cercanías Asturias (todas las líneas, con transbordos) y andar, y cuadra la conexión para
"salir ahora".

La idea: le dices de dónde sales (tu ubicación GPS, o un sitio como "EPI Gijón",
o una parada/estación) y a dónde vas ("Candás", una dirección, otra parada), y te
dice cómo llegar antes: qué bus coger y cuándo pasa, en qué tren enlazar (con la
hora de llegada REAL, contando las esperas de cruce en la vía única) y cuánto hay
que andar. El tren es la parte exacta; el bus urbano lleva la mejor estimación
posible con los minutos en directo de EMTUSA.

Solo biblioteca estándar. La geocodificación de sitios usa Nominatim (OpenStreetMap)
cuando el servidor tiene internet; sin ella, se resuelve por nombre de parada/estación
y por un pequeño diccionario de sitios conocidos de Gijón.
"""
import json
import urllib.parse
import urllib.request

from ..bus.emtusa import MIN_POR_PARADA, andar_min
from ..util import distancia_km, hm, hm_salida, normaliza

# umbrales (metros / minutos)
ANDAR_DIRECTO_MAX = 1300     # si la estación está más cerca que esto, se va andando (sin bus)
RADIO_PARADA_ORIGEN = 550    # buscar paradas de bus cerca del origen
RADIO_PARADA_ESTACION = 500  # buscar paradas de bus cerca de la estación de tren
CERCA_ESTACION = 280         # si el origen ya está pegado a la estación, no hay acceso
MARGEN_ENLACE = 2.0          # minutos de colchón para no perder el tren por los pelos
ESPERA_BUS_DEF = 6.0         # espera media si no hay minutos en directo (media frecuencia)
ESPERA_TRANSBORDO_DEF = 8.0  # espera media al hacer transbordo de bus

# sitios conocidos de Gijón / corredor (por si no hay geocodificador): normalizado -> (lat, lon, nombre)
LUGARES = {
    "epi": (43.5235, -5.6343, "EPI · Escuela Politécnica de Ingeniería (Campus)"),
    "politecnica": (43.5235, -5.6343, "Escuela Politécnica de Ingeniería (Campus)"),
    "escuela politecnica de ingenieria": (43.5235, -5.6343, "Escuela Politécnica de Ingeniería (Campus)"),
    "campus": (43.5231, -5.6231, "Campus de Viesques"),
    "campus de viesques": (43.5231, -5.6231, "Campus de Viesques"),
    "uni": (43.5235, -5.6343, "Campus de Viesques (Universidad)"),
    "universidad": (43.5235, -5.6343, "Campus de Viesques (Universidad)"),
    "universidad laboral": (43.5240, -5.6131, "Universidad Laboral"),
    "laboral": (43.5240, -5.6131, "Universidad Laboral"),
    "hospital de cabuenes": (43.5249, -5.6079, "Hospital de Cabueñes"),
    "cabuenes": (43.5249, -5.6079, "Hospital de Cabueñes"),
    "hospital de jove": (43.5498, -5.7001, "Hospital de Jove"),
    "molinon": (43.5341, -5.6358, "El Molinón"),
    "el molinon": (43.5341, -5.6358, "El Molinón"),
    "plaza del humedal": (43.5390, -5.6660, "Plaza del Humedal"),
    "humedal": (43.5390, -5.6660, "Plaza del Humedal"),
    "plaza mayor": (43.5419, -5.6635, "Plaza Mayor"),
    "puerto deportivo": (43.5445, -5.6620, "Puerto Deportivo"),
    "cimavilla": (43.5451, -5.6620, "Cimavilla"),
    "cimadevilla": (43.5451, -5.6620, "Cimavilla"),
    "feria de muestras": (43.5352, -5.6300, "Feria de Muestras"),
    "estacion de autobuses": (43.5383, -5.6673, "Estación de Autobuses"),
    "el corte ingles": (43.5360, -5.6560, "El Corte Inglés"),
}


def geocodificar(texto, linea, bus, con_internet=True, grafo=None):
    """Convierte un texto libre en un punto {lat, lon, nombre, tipo}.

    Orden: sitios conocidos → estación del tren → pueblo o ciudad (paradas del Consorcio) →
    parada de bus de Gijón → parada del Consorcio → Nominatim (OSM)."""
    if texto is None:
        return None
    texto = texto.strip()
    if not texto:
        return None
    n = normaliza(texto)

    # 1) diccionario de sitios conocidos
    if n in LUGARES:
        lat, lon, nom = LUGARES[n]
        return {"lat": lat, "lon": lon, "nombre": nom, "tipo": "lugar"}
    clave = _lugar_en(n)
    if clave:
        lat, lon, nom = LUGARES[clave]
        return {"lat": lat, "lon": lon, "nombre": nom, "tipo": "lugar"}

    # un pueblo pequeño con estación («Candás»): todo el pueblo, estación incluida
    if grafo is not None:
        p = grafo.localidad_pequena_exacta(texto) or grafo.ciudad_exacta(texto)
        if p:
            return p

    # 2) estación de tren (cualquier línea de Cercanías Asturias)
    if linea is not None:
        try:
            k = linea.buscar(texto)
            return {"lat": linea.coord[k][0], "lon": linea.coord[k][1],
                    "nombre": linea.nombre[k] + " (estación)", "tipo": "estacion", "k": k}
        except KeyError:
            pass

    # 3) pueblo o ciudad (todas sus paradas del Consorcio)
    if grafo is not None:
        p = grafo.buscar_localidad(texto)
        if p:
            return p

    # 3b) un concejo o comarca que sale en varias paradas del Consorcio («Cabrales», «Somiedo»)
    if grafo is not None:
        p = grafo.buscar_concejo(texto)
        if p:
            return p

    # 4) parada de autobús por nombre: Gijón y, si no, del Consorcio
    if bus is not None and bus.red_ok:
        ps = bus.buscar_paradas(texto, limite=1)
        if ps:
            p = ps[0]
            return {"lat": p["lat"], "lon": p["lon"], "nombre": p["nombre"] + " (parada)",
                    "tipo": "parada", "parada": p["id"]}
    if grafo is not None:
        p = grafo.buscar_parada(texto)
        if p:
            return p

    # 5) Nominatim (solo si el servidor tiene internet)
    if con_internet:
        p = _nominatim(texto)
        if p:
            return p
    return None


def _lugar_en(n):
    """Sitio conocido cuyo nombre aparece como palabras completas en el texto (el más largo gana).
    Así «la universidad laboral» es la Laboral (no el campus por contener «uni») y «Pepito»
    no es la EPI."""
    palabras = " %s " % n
    mejor = None
    for clave in LUGARES:
        if " %s " % clave in palabras and (mejor is None or len(clave) > len(mejor)):
            mejor = clave
    return mejor


def sugerir(texto, linea, bus, limite=8, grafo=None):
    """Sugerencias mientras se escribe: sitios conocidos, estaciones, pueblos y paradas de bus."""
    n = normaliza(texto or "")
    if len(n) < 2:
        return []
    out, vistos = [], set()

    def poner(nombre, tipo, lat, lon, texto_busqueda):
        if nombre in vistos:
            return
        vistos.add(nombre)
        out.append({"nombre": nombre, "tipo": tipo, "lat": lat, "lon": lon, "texto": texto_busqueda})

    for clave, (lat, lon, nom) in sorted(LUGARES.items(), key=lambda kv: (not kv[0].startswith(n), len(kv[0]))):
        if clave.startswith(n) or (" " + n) in (" " + clave):
            poner(nom, "lugar", lat, lon, nom)
    if linea is not None:
        ests = []
        for k, nom in enumerate(linea.nombre):
            nn = normaliza(nom)
            if nn.startswith(n) or (" " + n) in (" " + nn) or ("-" + n) in nn:
                ests.append((not nn.startswith(n), len(nn), k))
        for _, _, k in sorted(ests):
            poner(linea.nombre[k] + " (estación)", "estacion", linea.coord[k][0], linea.coord[k][1], linea.nombre[k])
    cta = grafo.sugerir(texto, limite) if grafo is not None else []
    for x in cta:
        if x["tipo"] == "localidad":
            poner(x["nombre"], "localidad", x["lat"], x["lon"], x["texto"])
    if grafo is not None and not any(x["tipo"] == "localidad" for x in cta):
        c = grafo.buscar_concejo(texto) or grafo.concejo_por_prefijo(texto)
        if c:
            poner(c["nombre"], "localidad", c["lat"], c["lon"], c["nombre"])
    if bus is not None and getattr(bus, "red_ok", False):
        for p in bus.buscar_paradas(texto, limite=30):
            pn = " " + normaliza(p["nombre"]).replace("(", " ")
            if (" " + n) not in pn:
                continue                    # solo si alguna palabra empieza así («can» no es «Vaticano»)
            poner(p["nombre"] + " (parada de bus)", "parada", p["lat"], p["lon"], p["nombre"])
    for x in cta:
        if x["tipo"] == "parada":
            poner(x["nombre"], "parada", x["lat"], x["lon"], x["texto"])
    return out[:limite]


def _nominatim(texto):
    q = urllib.parse.urlencode({
        "q": texto + ", Asturias, España", "format": "json", "limit": 1,
        "countrycodes": "es",
        "viewbox": "-7.20,43.70,-4.50,42.85", "bounded": 1,
    })
    url = "https://nominatim.openstreetmap.org/search?" + q
    req = urllib.request.Request(url, headers={"User-Agent": "c4-tiempo-real/1.0 (planificador local)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            arr = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not arr:
        return None
    d = arr[0]
    nombre = d.get("display_name", texto).split(",")[0]
    return {"lat": float(d["lat"]), "lon": float(d["lon"]), "nombre": nombre, "tipo": "direccion"}


# --------------------------------------------------------------------- enrutado de bus
def _rutas_bus(bus, orig_ids, dest_ids, max_transbordos=1, limite=6):
    """Itinerarios de bus entre un grupo de paradas de origen y otro de destino.

    Devuelve lista de itinerarios; cada uno es una lista de tramos:
        {linea, codigo, color, destino, subir, bajar, hops}
    Con ≤1 transbordo (mismo punto de parada)."""
    T = bus.trayectos
    porp = bus.por_parada
    orig_ids = list(dict.fromkeys(orig_ids))
    dest_set = set(dest_ids)
    itinerarios = []
    vistos = set()

    def tramo(idx, i0, i1):
        t = T[idx]
        return {"linea": t["codigo"], "codigo": t["codigo"], "color": t["color"], "destino": t["destino"],
                "subir": t["paradas"][i0], "bajar": t["paradas"][i1], "hops": i1 - i0, "traj": idx}

    # directo
    for a in orig_ids:
        for idx, pa in porp.get(a, ()):
            seq = T[idx]["paradas"]
            for i1 in range(pa + 1, len(seq)):
                if seq[i1] in dest_set:
                    clave = (T[idx]["codigo"], a, seq[i1])
                    if clave not in vistos:
                        vistos.add(clave)
                        itinerarios.append([tramo(idx, pa, i1)])
                    break
    directos = len(itinerarios)

    # un transbordo (en una parada común)
    if max_transbordos >= 1 and directos < limite:
        for a in orig_ids:
            for idx1, pa in porp.get(a, ()):
                seq1 = T[idx1]["paradas"]
                pos1 = {sid: i for i, sid in enumerate(seq1)}
                cola1 = set(seq1[pa + 1:])
                for m in seq1[pa + 1:]:                      # posible parada de transbordo
                    for idx2, pm in porp.get(m, ()):
                        if idx2 == idx1:
                            continue
                        seq2 = T[idx2]["paradas"]
                        for i2 in range(pm + 1, len(seq2)):
                            if seq2[i2] in dest_set:
                                clave = (T[idx1]["codigo"], a, m, T[idx2]["codigo"], seq2[i2])
                                if clave in vistos:
                                    break
                                vistos.add(clave)
                                itinerarios.append([tramo(idx1, pa, pos1[m]), tramo(idx2, pm, i2)])
                                break
    # coste estructural: andar-equivalente por hops + penalización por transbordo
    itinerarios.sort(key=lambda it: sum(tr["hops"] for tr in it) + 6 * (len(it) - 1))
    return itinerarios[:limite]


def _acceso(bus, origen, est_pt, en_vivo):
    """Cómo ir del origen a la estación de tren: andar, o bus + andar. Devuelve
    (etapas, minutos_totales) o None si no encuentra forma en bus y está lejos."""
    d_directo = distancia_km((origen["lat"], origen["lon"]), (est_pt["lat"], est_pt["lon"])) * 1000
    andar_directo = {"tipo": "andar", "desde": origen["nombre"], "hasta": est_pt["nombre"],
                     "metros": round(d_directo), "min": round(andar_min(d_directo), 1)}
    if d_directo <= CERCA_ESTACION:
        return [], 0.0
    if d_directo <= ANDAR_DIRECTO_MAX or not (bus and bus.red_ok):
        return [andar_directo], andar_directo["min"]

    orig_paradas = bus.cercanas(origen["lat"], origen["lon"], RADIO_PARADA_ORIGEN, limite=8)
    est_paradas = bus.cercanas(est_pt["lat"], est_pt["lon"], RADIO_PARADA_ESTACION, limite=8)
    if not orig_paradas or not est_paradas:
        return [andar_directo], andar_directo["min"]
    pmeta = {p["id"]: p for p in orig_paradas + est_paradas}
    rutas = _rutas_bus(bus, [p["id"] for p in orig_paradas], [p["id"] for p in est_paradas])

    mejor = None
    for it in rutas[:4]:
        subir = pmeta.get(it[0]["subir"]) or bus.paradas_d.get(it[0]["subir"])
        bajar = pmeta.get(it[-1]["bajar"]) or bus.paradas_d.get(it[-1]["bajar"])
        if not subir or not bajar:
            continue
        w_ini = andar_min(distancia_km((origen["lat"], origen["lon"]), (subir["lat"], subir["lon"])) * 1000)
        w_fin = distancia_km((bajar["lat"], bajar["lon"]), (est_pt["lat"], est_pt["lon"])) * 1000
        w_fin_min = andar_min(w_fin)
        ride = sum(tr["hops"] for tr in it) * MIN_POR_PARADA
        espera = ESPERA_BUS_DEF + (ESPERA_TRANSBORDO_DEF if len(it) > 1 else 0)
        total = w_ini + espera + ride + w_fin_min
        if mejor is None or total < mejor[0]:
            mejor = (total, it, subir, bajar, w_ini, w_fin, w_fin_min, ride)

    if mejor is None or mejor[0] >= andar_directo["min"]:
        return [andar_directo], andar_directo["min"]

    total, it, subir, bajar, w_ini, w_fin, w_fin_min, ride = mejor
    etapas, t = [], 0.0
    if distancia_km((origen["lat"], origen["lon"]), (subir["lat"], subir["lon"])) * 1000 > 90:
        etapas.append({"tipo": "andar", "desde": origen["nombre"], "hasta": subir["nombre"],
                       "metros": round(distancia_km((origen["lat"], origen["lon"]),
                                                     (subir["lat"], subir["lon"])) * 1000),
                       "min": round(w_ini, 1)})
        t += w_ini
    # esperas en directo para el primer bus (y transbordo)
    for i, tr in enumerate(it):
        sale_en = None
        if en_vivo:
            sale_en = bus.proximos_por_linea(tr["subir"]).get(tr["codigo"])
        if sale_en is not None and sale_en < t - 0.5:
            # ese autobús pasa antes de que llegues a la parada: toca esperar al siguiente
            sale_en = None
        defecto = ESPERA_BUS_DEF if i == 0 else ESPERA_TRANSBORDO_DEF
        espera = max(0.0, sale_en - t) if sale_en is not None else defecto
        t += espera
        ride_i = tr["hops"] * MIN_POR_PARADA
        t += ride_i
        etapas.append({"tipo": "bus", "linea": tr["codigo"], "color": tr["color"], "destino": tr["destino"],
                       "subir": bus.paradas_d.get(tr["subir"], {}).get("nombre", ""),
                       "bajar": bus.paradas_d.get(tr["bajar"], {}).get("nombre", ""),
                       "paradas": tr["hops"], "min": round(ride_i, 1),
                       "sale_en": None if sale_en is None else round(sale_en),
                       "aprox": sale_en is None})
    if w_fin > 90:
        etapas.append({"tipo": "andar", "desde": bajar["nombre"], "hasta": est_pt["nombre"],
                       "metros": round(w_fin), "min": round(w_fin_min, 1)})
        t += w_fin_min
    return etapas, t


class Estaciones:
    """Todas las estaciones de Cercanías Asturias (las de las dos redes juntas), con lo que el
    planificador necesita: nombre, coordenadas, código y búsqueda por texto."""

    def __init__(self, lista):
        self.est = [e["id"] for e in lista]
        self.nombre = [e["nombre"] for e in lista]
        self.coord = [(e["lat"], e["lon"]) for e in lista]
        self.lineas = [list(e.get("lineas", [])) for e in lista]
        # transbordos andando entre estaciones cercanas que no son la misma (p. ej. dos andenes con
        # código distinto en el mismo pueblo): (otra, minutos)
        self.a_pie = {k: [] for k in range(len(lista))}
        for i in range(len(lista)):
            for j in range(i + 1, len(lista)):
                m = distancia_km(self.coord[i], self.coord[j]) * 1000
                if m <= TRANSBORDO_ANDANDO_MAX:
                    w = andar_min(m * 1.3) + 1.0
                    self.a_pie[i].append((j, w))
                    self.a_pie[j].append((i, w))

    def buscar(self, texto):
        n = normaliza(texto)
        nn = [normaliza(x) for x in self.nombre]
        for cond in (lambda x: x == n, lambda x: x.startswith(n), lambda x: (" " + n) in (" " + x) or ("-" + n) in x,
                     lambda x: n in x):
            hits = [k for k, x in enumerate(nn) if cond(x)]
            if hits:
                return min(hits, key=lambda k: len(nn[k]))
        raise KeyError(texto)


TRANSBORDO_MIN = 3.0          # minutos mínimos para cambiar de tren en la misma estación
TRANSBORDO_ANDANDO_MAX = 450  # metros: estaciones distintas entre las que se puede cambiar andando
PENALIZA_TRANSBORDO = 4.0     # un viaje con transbordo tiene que ahorrar esto para preferirse
MAX_TRENES = 3                # hasta dos transbordos


def _estacion_mas_cerca(linea, lat, lon):
    ks = range(len(linea.est))
    k = min(ks, key=lambda i: distancia_km((lat, lon), linea.coord[i]))
    return k, distancia_km((lat, lon), linea.coord[k]) * 1000


def _candidatas(est, punto, n=3, radio=3000):
    """Estaciones a considerar para subir (o bajar): la más cercana y otras próximas (quizá con
    mejores trenes: p. ej. Renfe y FEVE en el mismo sitio, o una línea más directa)."""
    d = sorted((distancia_km((punto["lat"], punto["lon"]), est.coord[k]) * 1000, k) for k in range(len(est.est)))
    if punto.get("tipo") == "estacion" and punto.get("k") is not None:
        k0 = punto["k"]
        return [k0] + [k for m, k in d if k != k0 and m <= TRANSBORDO_ANDANDO_MAX][:n - 1]
    out = [d[0][1]]
    for m, k in d[1:]:
        if len(out) >= n or m > max(radio, d[0][0] * 1.6):
            break
        out.append(k)
    return out


def buscar_viaje(res, est, salidas, llegadas, max_trenes=MAX_TRENES):
    """El viaje en tren que llega antes, con transbordos si hace falta (algoritmo RAPTOR por rondas:
    ronda r = viajes de r trenes). salidas: {estación: hora a la que se está allí listo};
    llegadas: {estación: minutos desde ella hasta el destino}. Las horas de los trenes son las
    REALES estimadas (con cruces y retrasos), así que un transbordo se cuenta con la hora a la que
    llegará de verdad el primer tren.
    Devuelve {"llega", "tramos": [{tren, jb, ja}], "pie": [...]} o None."""
    INF = 1e18
    trenes = [t for t in res["trenes"] if not t["fin"] and not t.get("cancelado")]
    mejor = dict(salidas)                     # mejor hora conocida en cada estación (poda)
    rondas = [dict(salidas)]
    padres = [{k: None for k in salidas}]
    tope = INF                                # mejor llegada al destino ya encontrada
    for r in range(1, max_trenes + 1):
        prev, cur, par = rondas[-1], {}, {}
        if not prev:
            break
        margen = 0.0 if r == 1 else TRANSBORDO_MIN
        for t in trenes:
            ks, n = t["k"], len(t["k"])
            sube = None
            for j in range(max(0, t["j0"]), n):
                k = ks[j]
                if sube is not None and t["para"][j]:
                    a = t["est_a"][j]
                    if a is not None and a < mejor.get(k, INF) - 1e-6 and a < tope:
                        cur[k], par[k] = a, ("tren", t, sube[0], j, sube[1])
                        mejor[k] = a
                if sube is None and j < n - 1 and t["para"][j] and k in prev:
                    d = t["est_d"][j]
                    if d is not None and d >= prev[k] + margen - 0.05:
                        sube = (j, k)
        # cambiar de estación andando (solo una vez seguida)
        for k, a in list(cur.items()):
            for k2, w in getattr(est, "a_pie", {}).get(k, ()):
                if a + w < mejor.get(k2, INF) - 1e-6:
                    cur[k2], par[k2] = a + w, ("pie", k, w)
                    mejor[k2] = a + w
        for k, extra in llegadas.items():
            if k in cur:
                tope = min(tope, cur[k] + extra)
        rondas.append(cur)
        padres.append(par)
    # el mejor: la llegada más temprana, penalizando cada transbordo
    elegido = None
    for r in range(1, len(rondas)):
        for k, extra in llegadas.items():
            if k in rondas[r]:
                total = rondas[r][k] + extra
                nota = total + PENALIZA_TRANSBORDO * (r - 1)
                if elegido is None or nota < elegido[0] - 1e-6:
                    elegido = (nota, r, k, total)
    if elegido is None:
        return None
    _, r, k, total = elegido
    tramos, k_fin = [], k
    while r > 0:
        p = padres[r][k]
        if p[0] == "pie":
            tramos.append({"pie": True, "de": p[1], "a": k, "min": p[2]})
            k = p[1]
            p = padres[r][k]
        _, t, jb, ja, kb = p
        tramos.append({"tren": t, "jb": jb, "ja": ja})
        k, r = kb, r - 1
    tramos.reverse()
    return {"llega": total, "estacion_final": k_fin, "estacion_inicial": k, "tramos": tramos}


def _etapa_tren(est, t, jb, ja, espera=0.0):
    return {
        "tipo": "tren", "num": t["num"], "linea": t.get("linea", "C4"),
        "desde": est.nombre[t["k"][jb]], "hasta": est.nombre[t["k"][ja]],
        "sale": round(t["est_d"][jb], 2), "llega": round(t["est_a"][ja], 2),
        "sale_hm": hm_salida(t["est_d"][jb]), "llega_hm": hm(t["est_a"][ja]),
        "retraso": t["retraso"], "destino": t["destino"], "via": t.get("via"), "con_datos": t["con_datos"],
        "espera_estacion": round(max(0.0, espera), 1), "id": t["id"],
        "motivos": [{"texto": m["texto"], "min": m["min"], "estacion": est.nombre[m["k"]]}
                    for m in t["motivos"] if jb <= m["j"] < ja],
    }


def planificar(est, res, bus, origen, destino, ahora, en_vivo=True):
    """Plan puerta a puerta para salir ahora, entre dos puntos ya geocodificados, con cualquier
    línea de Cercanías Asturias y transbordos entre ellas (Gijón, Oviedo, El Berrón, Calzada...)."""
    plan = {"origen": origen, "destino": destino, "ahora": round(ahora, 2), "hora_ahora": hm(ahora),
            "ok": False, "etapas": [], "avisos": []}
    kb, db = _estacion_mas_cerca(est, origen["lat"], origen["lon"])
    ka, da = _estacion_mas_cerca(est, destino["lat"], destino["lon"])
    dist_od = distancia_km((origen["lat"], origen["lon"]), (destino["lat"], destino["lon"])) * 1000
    if dist_od < 120:
        plan["error"] = "El origen y el destino son el mismo sitio."
        return plan
    if kb == ka or dist_od <= 1500:
        # todo dentro de la misma zona: bus/andar directo, sin tren
        return _plan_sin_tren(bus, origen, destino, ahora, en_vivo, plan)

    # 1) cómo llegar a cada estación candidata de subida, y desde cada una de bajada al destino
    acceso, salida = {}, {}
    for k in _candidatas(est, origen):
        pt = {"lat": est.coord[k][0], "lon": est.coord[k][1], "nombre": est.nombre[k], "k": k}
        acceso[k] = _acceso(bus, origen, pt, en_vivo)
    for k in _candidatas(est, destino):
        if k in acceso and len(acceso) == 1:
            continue
        pt = {"lat": est.coord[k][0], "lon": est.coord[k][1], "nombre": est.nombre[k], "k": k}
        salida[k] = _salida(bus, pt, destino, en_vivo)
    listo = {k: ahora + t + (MARGEN_ENLACE if et else 0) for k, (et, t) in acceso.items()}
    llegadas = {k: t for k, (et, t) in salida.items()}
    v = buscar_viaje(res, est, listo, llegadas) if res and llegadas else None
    if v is None:
        plan["etapas"] = acceso[kb][0]
        plan["avisos"].append("No quedan trenes hoy que enlacen a tiempo entre %s y %s."
                              % (est.nombre[kb], est.nombre[ka]))
        plan["sin_tren_hoy"] = {"o": est.est[kb], "d": est.est[ka]}
        return plan
    return _montar(plan, est, bus, v, acceso, salida, listo, llegadas, res, ahora, en_vivo, da, ka)


def _montar(plan, est, bus, v, acceso, salida, listo, llegadas, res, ahora, en_vivo, da, ka):
    k0, kf = v["estacion_inicial"], v["estacion_final"]
    etapas_acc, t_acc = acceso[k0]
    etapas_sal, t_sal = salida[kf]
    trenes = [x for x in v["tramos"] if not x.get("pie")]
    primero = trenes[0]
    sale = primero["tren"]["est_d"][primero["jb"]]
    # si al tren se llega andando, no hace falta salir ya: se puede salir justo a tiempo
    solo_andando = all(e["tipo"] == "andar" for e in etapas_acc)
    salir = ahora
    if solo_andando:
        salir = max(ahora, sale - t_acc - (MARGEN_ENLACE if etapas_acc else 1.0))
    etapas, previo = list(etapas_acc), None
    for x in v["tramos"]:
        if x.get("pie"):
            etapas.append({"tipo": "andar", "desde": est.nombre[x["de"]], "hasta": est.nombre[x["a"]],
                           "metros": round(distancia_km(est.coord[x["de"]], est.coord[x["a"]]) * 1000),
                           "min": round(x["min"], 1), "transbordo": True})
            previo = (previo or 0) + x["min"]
            continue
        t, jb, ja = x["tren"], x["jb"], x["ja"]
        if previo is None:
            espera = sale - (salir + t_acc)
        else:
            espera = t["est_d"][jb] - previo
            etapas.append({"tipo": "transbordo", "estacion": est.nombre[t["k"][jb]], "linea": t.get("linea"),
                           "espera": round(max(0.0, espera), 1), "sale_hm": hm_salida(t["est_d"][jb])})
        etapas.append(_etapa_tren(est, t, jb, ja, espera if previo is None else 0.0))
        previo = t["est_a"][ja]
    etapas += etapas_sal
    llega = trenes[-1]["tren"]["est_a"][trenes[-1]["ja"]]
    plan.update({
        "etapas": etapas, "ok": True, "sale": round(salir, 2), "sale_hm": hm_salida(salir),
        "sale_en": round(max(0.0, salir - ahora), 1), "sale_estacion": round(sale, 2),
        "llega": round(llega + t_sal, 2), "llega_hm": hm(llega + t_sal),
        "duracion": round(llega + t_sal - salir, 1), "transbordos": len(trenes) - 1,
        "lineas": [x["tren"].get("linea") for x in trenes],
        "estacion_sub": est.nombre[k0], "estacion_baj": est.nombre[kf],
    })
    # otras opciones: los viajes siguientes (saliendo después del elegido)
    alt, desde, usados = [], sale, {primero["tren"]["id"]}
    for _ in range(6):
        if len(alt) == 2:
            break
        listo2 = {k: max(h, desde + 0.5) for k, h in listo.items()}
        w = buscar_viaje(res, est, listo2, llegadas)
        if w is None:
            break
        tr = [x for x in w["tramos"] if not x.get("pie")]
        s_ = tr[0]["tren"]["est_d"][tr[0]["jb"]]
        if s_ <= desde:
            break
        if tr[0]["tren"]["id"] in usados:     # el mismo tren cogido en otra estación cercana
            desde = s_
            continue
        usados.add(tr[0]["tren"]["id"])
        ka2 = w["estacion_inicial"]
        et_a, t_a = acceso[ka2]
        l_ = tr[-1]["tren"]["est_a"][tr[-1]["ja"]] + llegadas[w["estacion_final"]]
        t0 = tr[0]["tren"]
        alt.append({"num": t0["num"], "destino": t0["destino"], "sale": round(s_, 2), "sale_hm": hm_salida(s_),
                    "llega": round(l_, 2), "llega_hm": hm(l_), "desde": est.nombre[ka2],
                    "lineas": [x["tren"].get("linea") for x in tr], "transbordos": len(tr) - 1,
                    "salir_hm": hm_salida(s_ - t_a - (MARGEN_ENLACE if et_a else 1.0))
                    if all(e["tipo"] == "andar" for e in et_a) else None,
                    "retraso": t0["retraso"], "con_datos": t0["con_datos"]})
        desde = s_
    plan["alternativas"] = alt
    if da > ANDAR_DIRECTO_MAX and not (bus and bus.red_ok and bus.cercanas(plan["destino"]["lat"], plan["destino"]["lon"], 600)):
        plan["avisos"].append("El destino queda a %d min andando de la estación de %s (sin bus urbano cerca)."
                              % (round(andar_min(da)), est.nombre[ka]))
    return plan


def _salida(bus, est_pt, destino, en_vivo):
    """De la estación de bajada al destino (simétrico al acceso)."""
    return _acceso(bus, {"lat": est_pt["lat"], "lon": est_pt["lon"], "nombre": est_pt["nombre"]},
                   destino, en_vivo)


def _plan_sin_tren(bus, origen, destino, ahora, en_vivo, plan):
    """Origen y destino en la misma zona (típicamente dentro de Gijón): solo bus/andar."""
    etapas, t = _acceso(bus, origen, destino, en_vivo)
    plan["etapas"] = etapas
    plan["ok"] = True
    plan["solo_urbano"] = True
    plan["sale"] = round(ahora, 2)
    plan["llega"] = round(ahora + t, 2)
    plan["llega_hm"] = hm(ahora + t)
    plan["duracion"] = round(t, 1)
    return plan
