# -*- coding: utf-8 -*-
"""Planificador puerta a puerta: combina el autobús urbano de Gijón (EMTUSA) con el
tren de Cercanías C-4 y andar, y cuadra la conexión para "salir ahora".

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

from .emtusa import MIN_POR_PARADA, andar_min
from .util import distancia_km, hm, hm_salida, normaliza

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


def geocodificar(texto, linea, bus, con_internet=True):
    """Convierte un texto libre en un punto {lat, lon, nombre, tipo}.

    Orden: sitios conocidos → estación del tren → parada de bus → Nominatim (OSM)."""
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

    # 2) estación de tren de la C-4
    if linea is not None:
        try:
            k = linea.buscar(texto)
            return {"lat": linea.coord[k][0], "lon": linea.coord[k][1],
                    "nombre": linea.nombre[k] + " (estación)", "tipo": "estacion", "k": k}
        except KeyError:
            pass

    # 3) parada de autobús por nombre
    if bus is not None and bus.red_ok:
        ps = bus.buscar_paradas(texto, limite=1)
        if ps:
            p = ps[0]
            return {"lat": p["lat"], "lon": p["lon"], "nombre": p["nombre"] + " (parada)",
                    "tipo": "parada", "parada": p["id"]}

    # 4) Nominatim (solo si el servidor tiene internet)
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


def sugerir(texto, linea, bus, limite=8):
    """Sugerencias mientras se escribe: sitios conocidos, estaciones de la C-4 y paradas de bus."""
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
    if bus is not None and getattr(bus, "red_ok", False):
        for p in bus.buscar_paradas(texto, limite=30):
            pn = " " + normaliza(p["nombre"]).replace("(", " ")
            if (" " + n) not in pn:
                continue                    # solo si alguna palabra empieza así («can» no es «Vaticano»)
            poner(p["nombre"] + " (parada de bus)", "parada", p["lat"], p["lon"], p["nombre"])
    return out[:limite]


def _nominatim(texto):
    q = urllib.parse.urlencode({
        "q": texto + ", Gijón, Asturias, España", "format": "json", "limit": 1,
        "countrycodes": "es",
        "viewbox": "-5.80,43.60,-5.55,43.45", "bounded": 0,
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


def _andar_etapa(p_desde, p_hasta, nombre_desde, nombre_hasta):
    m = distancia_km((p_desde["lat"], p_desde["lon"]), (p_hasta["lat"], p_hasta["lon"])) * 1000
    return {"tipo": "andar", "desde": nombre_desde, "hasta": nombre_hasta,
            "metros": round(m), "min": round(andar_min(m), 1)}


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


def _estacion_mas_cerca(linea, lat, lon):
    ks = range(len(linea.est))
    k = min(ks, key=lambda i: distancia_km((lat, lon), linea.coord[i]))
    return k, distancia_km((lat, lon), linea.coord[k]) * 1000


def planificar(linea, res, bus, origen, destino, ahora, en_vivo=True):
    """Devuelve un plan multimodal salir-ahora entre dos puntos ya geocodificados."""
    from .estimador import viajes_entre

    plan = {"origen": origen, "destino": destino, "ahora": round(ahora, 2), "hora_ahora": hm(ahora),
            "ok": False, "etapas": [], "avisos": []}

    kb, db = _estacion_mas_cerca(linea, origen["lat"], origen["lon"])
    ka, da = _estacion_mas_cerca(linea, destino["lat"], destino["lon"])
    dist_od = distancia_km((origen["lat"], origen["lon"]), (destino["lat"], destino["lon"])) * 1000

    if dist_od < 120:
        plan["error"] = "El origen y el destino son el mismo sitio."
        return plan
    usar_tren = kb != ka and dist_od > 1500
    if not usar_tren:
        # todo dentro de la misma zona: bus/andar directo, sin tren
        return _plan_sin_tren(bus, origen, destino, ahora, en_vivo, plan)

    # 1) acceso del origen a la estación de subida
    est_sub = {"lat": linea.coord[kb][0], "lon": linea.coord[kb][1], "nombre": linea.nombre[kb], "k": kb}
    etapas_acc, t_acc = _acceso(bus, origen, est_sub, en_vivo)
    listo_en_estacion = ahora + t_acc + (MARGEN_ENLACE if etapas_acc else 0)

    # 2) tren C-4 (llegada REAL con cruces)
    filas = viajes_entre(res, kb, ka, limite=8) if res else []
    fila = next((f for f in filas if f["tren"]["est_d"][f["jo"]] is not None
                 and f["tren"]["est_d"][f["jo"]] >= listo_en_estacion - 0.1), None)
    if fila is None:
        plan["etapas"] = etapas_acc
        plan["avisos"].append("No quedan trenes de la C-4 hoy que enlacen a tiempo entre %s y %s."
                              % (linea.nombre[kb], linea.nombre[ka]))
        plan["sin_tren_hoy"] = {"o": linea.est[kb], "d": linea.est[ka]}
        return plan

    tren = fila["tren"]
    jo, jd = fila["jo"], fila["jd"]
    sale, llega = tren["est_d"][jo], tren["est_a"][jd]
    # si al tren se llega andando, no hace falta salir ya: se puede salir justo a tiempo
    solo_andando = all(e["tipo"] == "andar" for e in etapas_acc)
    salir = ahora
    if solo_andando:
        salir = max(ahora, sale - t_acc - (MARGEN_ENLACE if etapas_acc else 1.0))

    etapa_tren = {
        "tipo": "tren", "num": tren["num"], "linea": "C-4",
        "desde": linea.nombre[kb], "hasta": linea.nombre[ka],
        "sale": round(sale, 2), "llega": round(llega, 2),
        "sale_hm": hm_salida(sale), "llega_hm": hm(llega),
        "retraso": tren["retraso"], "destino": tren["destino"], "via": tren.get("via"),
        "espera_estacion": round(max(0.0, sale - (salir + t_acc)), 1),
        "motivos": [{"texto": m["texto"], "min": m["min"], "estacion": linea.nombre[m["k"]]}
                    for m in fila["motivos"]],
    }

    # 3) salida de la estación de bajada al destino
    est_baj = {"lat": linea.coord[ka][0], "lon": linea.coord[ka][1], "nombre": linea.nombre[ka], "k": ka}
    etapas_sal, t_sal = _salida(bus, est_baj, destino, en_vivo)

    plan["etapas"] = etapas_acc + [etapa_tren] + etapas_sal
    plan["ok"] = True
    plan["sale"] = round(salir, 2)
    plan["sale_hm"] = hm_salida(salir)
    plan["sale_en"] = round(max(0.0, salir - ahora), 1)
    plan["sale_estacion"] = round(sale, 2)
    plan["llega"] = round(llega + t_sal, 2)
    plan["llega_hm"] = hm(llega + t_sal)
    plan["duracion"] = round(llega + t_sal - salir, 1)
    # otras opciones: los trenes siguientes que también enlazan
    alt = []
    for f in filas:
        tt = f["tren"]
        s_ = tt["est_d"][f["jo"]]
        if tt["id"] == tren["id"] or s_ is None or s_ < listo_en_estacion - 0.1:
            continue
        l_ = tt["est_a"][f["jd"]]
        alt.append({"num": tt["num"], "destino": tt["destino"], "sale": round(s_, 2), "sale_hm": hm_salida(s_),
                    "llega": round(l_ + t_sal, 2), "llega_hm": hm(l_ + t_sal),
                    "salir_hm": hm_salida(s_ - t_acc - (MARGEN_ENLACE if etapas_acc else 1.0)) if solo_andando else None,
                    "retraso": tt["retraso"], "con_datos": tt["con_datos"]})
        if len(alt) == 2:
            break
    plan["alternativas"] = alt
    plan["estacion_sub"] = linea.nombre[kb]
    plan["estacion_baj"] = linea.nombre[ka]
    if da > ANDAR_DIRECTO_MAX and not (bus and bus.red_ok and bus.cercanas(destino["lat"], destino["lon"], 600)):
        plan["avisos"].append("El destino queda a %d min andando de la estación de %s (sin bus urbano cerca)."
                              % (round(andar_min(da)), linea.nombre[ka]))
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
