# -*- coding: utf-8 -*-
"""Autobús urbano de Gijón (EMTUSA): red (líneas, paradas, recorridos) y tiempo real.

Dos fuentes de datos, separadas a propósito:

  · La RED (qué líneas hay, por qué paradas pasan y en qué orden) se guarda en
    disco, en ``c4/datos/red_emtusa.json``. Así el buscador de paradas, el mapa y
    el planificador de viajes funcionan al instante y sin depender de la red. Ese
    fichero se generó leyendo la API pública de EMTUSA y el servidor lo puede
    refrescar de vez en cuando (``refrescar_red``) cuando tiene salida a internet.

  · El TIEMPO REAL (¿en cuántos minutos llega el próximo bus a esta parada?) sí se
    pide en directo a la API de EMTUSA, la misma que usan su app y su web oficial.
    Son credenciales públicas de la aplicación (visibles en el código de su web);
    se pueden cambiar con variables de entorno por si EMTUSA las modifica.

Todo con biblioteca estándar. Si la API de tiempo real no responde, la red sigue
disponible: se ven líneas, paradas y se puede planificar; solo faltan los minutos.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from .util import distancia_km, normaliza

BASE = os.environ.get("C4_BUS_BASE", "https://emtusasiri.pub.gijon.es/emtusasiri/")
USER = os.environ.get("C4_BUS_USER", "info@vitesia.com")
PASS = os.environ.get("C4_BUS_PASS", "vitesia130")
BASIC = os.environ.get("C4_BUS_BASIC", "Basic YXBpOmFwaQ==")   # api:api (credencial pública de la app)

RED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "red_emtusa.json")

VEL_ANDAR_KMH = 4.8          # velocidad de caminar (para tiempos de acceso)
MIN_POR_PARADA = 1.5         # tiempo medio de bus entre dos paradas urbanas (marcha + parada)


def andar_min(metros):
    return metros / 1000.0 / VEL_ANDAR_KMH * 60.0


class Emtusa:
    def __init__(self, activo=True, red_path=RED_PATH):
        self.activo = activo
        self.lock = threading.Lock()
        self._token = None
        self._token_ts = 0
        self.disponible = None      # None = sin probar; True/False según la última llamada de tiempo real
        self.error = None
        self._veh = None
        self._veh_ts = 0
        self._hist = {}             # bus -> {"pos", "t_cambio", "hdg", "tray"}: para rumbo, «parado» y próxima parada
        self._lleg_cache = {}       # parada -> (ts, respuesta): muchos móviles miran la misma parada
        self.red_generada = None
        # red (estructura fija en disco)
        self.paradas_d = {}         # id(int) -> {id, nombre, lat, lon, lineas:[codigos]}
        self.lineas_d = {}          # idlinea(str) -> {id, codigo, color, nombre}
        self.trayectos = []         # [{linea, codigo, color, destino, direccion, paradas:[ids]}]
        self.por_parada = {}        # id(int) -> [(idx_trayecto, posicion)]
        self._cargar_red(red_path)

    # ================================================================= RED (disco)
    def _cargar_red(self, ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                net = json.load(f)
        except Exception as e:  # noqa: BLE001
            print("Aviso: no se pudo cargar la red de bus (%s)" % e)
            return
        self.red_generada = net.get("generado")
        self.lineas_d = {}
        for lid, l in net["lineas"].items():
            color = l.get("color") or "888888"
            self.lineas_d[str(lid)] = {"id": int(lid), "codigo": l.get("codigo") or str(lid),
                                       "color": "#" + color.lstrip("#"), "nombre": _limpia(l.get("desc") or "")}
        self.paradas_d = {}
        for pid, p in net["paradas"].items():
            self.paradas_d[int(pid)] = {"id": int(pid), "lat": p[0], "lon": p[1],
                                        "nombre": _limpia(p[2]), "lineas": []}
        self.trayectos = []
        self.por_parada = {}
        for t in net["trayectos"]:
            idlinea, idtray, direccion, destino, seq = t
            info = self.lineas_d.get(str(idlinea), {})
            idx = len(self.trayectos)
            self.trayectos.append({"linea": idlinea, "codigo": info.get("codigo", str(idlinea)),
                                   "color": info.get("color", "#888"), "destino": _limpia(destino),
                                   "direccion": direccion, "paradas": seq})
            for pos, sid in enumerate(seq):
                self.por_parada.setdefault(sid, []).append((idx, pos))
                pd = self.paradas_d.get(sid)
                if pd and info.get("codigo") and info["codigo"] not in pd["lineas"]:
                    pd["lineas"].append(info["codigo"])
            # geometría (parada a parada) para situar cada bus sobre su recorrido
            pts = [(self.paradas_d[p]["lat"], self.paradas_d[p]["lon"]) for p in seq if p in self.paradas_d]
            self.trayectos[-1]["_pts"] = pts
            self.trayectos[-1]["_ids"] = [p for p in seq if p in self.paradas_d]
            acc = [0.0]
            for a, b in zip(pts, pts[1:]):
                acc.append(acc[-1] + distancia_km(a, b))
            self.trayectos[-1]["_acc"] = acc

    @property
    def red_ok(self):
        return bool(self.paradas_d)

    def refrescar_red(self, guardar=True):
        """Vuelve a leer toda la estructura (líneas → recorridos → paradas) de la API de
        EMTUSA y la guarda en disco. Solo tiene sentido en el servidor (con internet)."""
        lineas = self._get("lineas/lineas", autenticado=True).get("lineas", {})
        net = {"lineas": {}, "paradas": {}, "trayectos": [], "generado": _hoy_iso()}
        for lid in lineas:
            try:
                L = self._get("lineas/lineas/%s" % lid)
            except Exception:  # noqa: BLE001
                continue
            L = L[0] if isinstance(L, list) else L
            if not L:
                continue
            net["lineas"][str(lid)] = {"codigo": L.get("codigo"), "color": L.get("colorhex"),
                                       "desc": _limpia(L.get("descripcion"))}
            for t in L.get("trayectos", []):
                try:
                    one = self._get("trayectos/trayectos/%s/%s" % (lid, t["idtrayecto"]))
                except Exception:  # noqa: BLE001
                    continue
                seq = []
                for p in one.get("paradas", []):
                    sid = p["idParada"]
                    if str(sid) not in net["paradas"]:
                        net["paradas"][str(sid)] = [float(p["latitud"]), float(p["longitud"]),
                                                    _limpia(p["descripcion"])]
                    seq.append(sid)
                net["trayectos"].append([int(lid), t["idtrayecto"], t.get("direccion"),
                                         _limpia(t.get("destino")), seq])
        if guardar and net["trayectos"]:
            os.makedirs(os.path.dirname(RED_PATH), exist_ok=True)
            with open(RED_PATH, "w", encoding="utf-8") as f:
                json.dump(net, f, ensure_ascii=False, separators=(",", ":"))
            self._cargar_red(RED_PATH)
        return {"lineas": len(net["lineas"]), "paradas": len(net["paradas"]),
                "trayectos": len(net["trayectos"])}

    # ----------------------------------------------------------------- consultas de red
    def paradas(self):
        return list(self.paradas_d.values())

    def resumen_red(self):
        return {
            "hay_tiempo_real": bool(self.activo),
            "generado": self.red_generada,
            "lineas": sorted(self.lineas_d.values(), key=lambda l: l["id"]),
            "paradas": list(self.paradas_d.values()),
            "trayectos": {str(i): {"linea": t["linea"], "codigo": t["codigo"], "color": t["color"],
                                   "destino": t["destino"], "direccion": t["direccion"],
                                   "paradas": t["paradas"]}
                          for i, t in enumerate(self.trayectos)},
        }

    def cercanas(self, lat, lon, radio_m=500, limite=6):
        """Paradas a menos de radio_m metros de un punto, ordenadas por distancia."""
        out = []
        for p in self.paradas_d.values():
            d = distancia_km((lat, lon), (p["lat"], p["lon"])) * 1000
            if d <= radio_m:
                out.append(dict(p, metros=round(d)))
        out.sort(key=lambda p: p["metros"])
        return out[:limite]

    def buscar_paradas(self, q, limite=12):
        n = normaliza(q)
        if len(n) < 2:
            return []
        exactas, contiene = [], []
        for p in self.paradas_d.values():
            nn = normaliza(p["nombre"])
            if nn == n or nn.startswith(n):
                exactas.append(p)
            elif n in nn:
                contiene.append(p)
        return (exactas + contiene)[:limite]

    def lineas_de(self, id_parada):
        return self.paradas_d.get(int(id_parada), {}).get("lineas", [])

    # ================================================================= TIEMPO REAL (red)
    def _get(self, ruta, autenticado=True, timeout=12):
        req = urllib.request.Request(BASE + ruta)
        req.add_header("Accept", "application/json")
        if autenticado:
            req.add_header("Authorization", "Bearer " + self._asegura_token())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _asegura_token(self):
        if self._token and time.time() - self._token_ts < 1800:
            return self._token
        datos = urllib.parse.urlencode({"grant_type": "password", "username": USER, "password": PASS})
        req = urllib.request.Request(BASE + "login?" + datos, data=b"", method="POST")
        req.add_header("Authorization", BASIC)
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        tok = j.get("access_token") or j.get("token") or (j if isinstance(j, str) else None)
        if not tok:
            raise RuntimeError("Respuesta de login sin token")
        self._token, self._token_ts = tok, time.time()
        return tok

    def llegadas(self, id_parada):
        """Autobuses por llegar a una parada, en minutos, de la API en directo."""
        if not self.activo:
            return {"id": id_parada, "error": "bus desactivado", "llegadas": []}
        base = self.paradas_d.get(int(id_parada)) if str(id_parada).isdigit() else None
        c = self._lleg_cache.get(str(id_parada))
        if c and time.time() - c[0] < 10:
            return c[1]
        try:
            j = self._get("paradas/parada/%s" % id_parada)
            lls = []
            for x in j.get("llegadas", []) or []:
                ln = x.get("linea") or {}
                tr = x.get("trayecto") or {}
                lls.append({"linea": ln.get("codigo") or ln.get("descripcion", ""),
                            "linea_id": ln.get("idlinea"),
                            "nombre_linea": _limpia(ln.get("descripcion", "")),
                            "color": "#" + (ln.get("colorhex") or "666666").lstrip("#"),
                            "destino": _limpia(tr.get("destino") or ""),
                            "minutos": x.get("minutos"), "distancia": x.get("distancia"),
                            "actualizado": x.get("horaActualizacion")})
            lls.sort(key=lambda l: (l["minutos"] is None, l["minutos"]))
            self.disponible, self.error = True, None
            res = {"id": id_parada, "nombre": _limpia(j.get("descripcion", "")) or (base or {}).get("nombre"),
                   "lat": float(j["latitud"]) if j.get("latitud") else (base or {}).get("lat"),
                   "lon": float(j["longitud"]) if j.get("longitud") else (base or {}).get("lon"),
                   "ts": int(time.time()), "llegadas": lls}
            self._lleg_cache[str(id_parada)] = (time.time(), res)
            if len(self._lleg_cache) > 600:
                self._lleg_cache.clear()
            return res
        except Exception as e:  # noqa: BLE001
            self.disponible, self.error = False, str(e)
            return {"id": id_parada, "nombre": (base or {}).get("nombre"), "error": str(e), "llegadas": []}

    def proximos_por_linea(self, id_parada):
        """{codigo_linea: minutos del próximo bus} en una parada, según el tiempo real."""
        info = self.llegadas(id_parada)
        out = {}
        for l in info.get("llegadas", []):
            m = l.get("minutos")
            if m is None:
                continue
            c = l["linea"]
            if c not in out or m < out[c]:
                out[c] = m
        return out

    def vehiculos(self):
        """Todos los autobuses en circulación ahora mismo, con su posición (para el mapa en vivo).

        Fuente: autobuses/coordenadas de EMTUSA. Se cachea unos segundos porque muchos
        navegadores pueden pedirlo a la vez. Además de la posición se calcula, con el
        recorrido de su línea: hacia dónde va (rumbo), cuál es su próxima parada y si
        lleva un rato sin moverse (EMTUSA solo da la coordenada)."""
        if not self.activo:
            return {"disponible": False, "vehiculos": []}
        with self.lock:
            if self._veh is not None and time.time() - self._veh_ts < 4:
                return self._veh
        try:
            arr = self._get("autobuses/coordenadas")
            ahora = time.time()
            out = []
            vistos = set()
            for b in arr:
                lat, lon = b.get("latitud"), b.get("longitud")
                if lat is None or lon is None:
                    continue
                v = {"bus": str(b.get("numBus") or ""), "linea": b.get("codigo") or str(b.get("idlinea")),
                     "linea_id": b.get("idlinea"),
                     "color": "#" + (b.get("colorhex") or "666666").lstrip("#"),
                     "destino": _limpia(b.get("destino")), "origen": _limpia(b.get("origen")),
                     "nombre_linea": _limpia(b.get("nombreLinea")),
                     "lat": float(lat), "lon": float(lon)}
                self._situar(v, ahora)
                vistos.add(v["bus"])
                out.append(v)
            for k in list(self._hist):
                if k not in vistos and ahora - self._hist[k]["t_visto"] > 1800:
                    del self._hist[k]
            res = {"disponible": True, "ts": int(ahora), "vehiculos": out,
                   "lineas_activas": len({v["linea"] for v in out})}
            with self.lock:
                self._veh, self._veh_ts = res, time.time()
            self.disponible, self.error = True, None
            return res
        except Exception as e:  # noqa: BLE001
            self.disponible, self.error = False, str(e)
            with self.lock:
                if self._veh is not None and time.time() - self._veh_ts < 60:
                    # un fallo puntual de EMTUSA: mejor las posiciones de hace unos segundos que nada
                    return dict(self._veh, viejo=True)
            return {"disponible": False, "error": str(e), "vehiculos": []}

    def _situar(self, v, ahora):
        """Rumbo, próxima parada y tiempo parado de un bus (añade campos a v)."""
        pos = (v["lat"], v["lon"])
        h = self._hist.get(v["bus"])
        if h is None or h.get("linea") != v["linea_id"]:
            h = self._hist[v["bus"]] = {"pos": pos, "t_cambio": ahora, "hdg": None, "tray": None,
                                        "linea": v["linea_id"], "t_visto": ahora}
        movido = False
        if h is not None and h.get("linea") == v["linea_id"] and distancia_km(h["pos"], pos) > 0.008:
            # se ha movido (más de 8 m). EMTUSA renueva la posición cada ~30 s (medido el 26/09)
            h["hdg"] = _rumbo(h["pos"], pos)
            h["pos"], h["t_cambio"] = pos, ahora
            movido = True
        h["t_visto"] = ahora
        # recorrido: los de su línea hacia su destino; el más cercano (y el mismo que antes si vale)
        dn = normaliza(v["destino"])
        cands = [i for i, t in enumerate(self.trayectos) if t["linea"] == v["linea_id"] and len(t["_pts"]) > 1]
        mismos = [i for i in cands if normaliza(self.trayectos[i]["destino"]) == dn]
        mejor = None
        for i in (mismos or cands):
            pr = _proyectar(self.trayectos[i]["_pts"], pos)
            if pr is None:
                continue
            d = pr[1] - (0.03 if i == h["tray"] else 0)
            if mejor is None or d < mejor[0]:
                mejor = (d, i, pr)
        if mejor and mejor[2][1] < 0.35:
            _, i, (seg, dist, frac, rumbo_via) = mejor
            h["tray"] = i
            t = self.trayectos[i]
            ids = t["_ids"]
            # en la parada (a menos de ~35 m) o camino de la siguiente
            p_a, p_b = self.paradas_d[ids[seg]], self.paradas_d[ids[seg + 1]]
            en = None
            if distancia_km(pos, (p_a["lat"], p_a["lon"])) < 0.035:
                en = p_a
            elif distancia_km(pos, (p_b["lat"], p_b["lon"])) < 0.035:
                en = p_b
            sig = p_b if en is not p_b else (self.paradas_d[ids[seg + 2]] if seg + 2 < len(ids) else None)
            v["en_parada"] = {"id": en["id"], "nombre": en["nombre"]} if en else None
            v["proxima"] = {"id": sig["id"], "nombre": sig["nombre"]} if sig else None
            if h["hdg"] is None:
                h["hdg"] = rumbo_via                      # recién visto: el sentido de su recorrido
            # Velocidad a lo largo del recorrido (para que el mapa lo mueva entre dos lecturas)
            acc = t["_acc"]
            s_km = acc[seg] + frac * (acc[seg + 1] - acc[seg])
            if movido and h.get("tray_s") == i and h.get("t_s"):
                dt = ahora - h["t_s"]
                ds = s_km - h["s"]
                if dt > 3 and -0.05 < ds < 1.5:
                    vel = max(0.0, min(0.8, ds / dt * 60.0))          # km/min (0,8 = 48 km/h)
                    h["vel"] = vel if h.get("vel") is None else 0.5 * h["vel"] + 0.5 * vel
            if movido or h.get("tray_s") != i:
                h["s"], h["t_s"], h["tray_s"] = s_km, ahora, i
            # camino hasta la parada siguiente a la próxima: en 30 s rara vez pasa de ahí (la velocidad
            # media ya incluye lo que pierde en paradas y semáforos)
            fin = min(ids.index(sig["id"], seg + 1) + 1, len(ids) - 1) if sig else seg + 1
            px = (t["_pts"][seg][0] + (t["_pts"][seg + 1][0] - t["_pts"][seg][0]) * frac,
                  t["_pts"][seg][1] + (t["_pts"][seg + 1][1] - t["_pts"][seg][1]) * frac)
            v["camino"] = [[round(px[0], 6), round(px[1], 6)]] + [
                [round(q[0], 6), round(q[1], 6)] for q in t["_pts"][seg + 1:fin + 1]]
        quieto = int(ahora - h["t_cambio"])
        v["rumbo"] = None if h["hdg"] is None else round(h["hdg"])
        v["quieto_s"] = quieto
        # más de ~45 s sin moverse = dos lecturas iguales: está parado de verdad (semáforo, parada…)
        v["vel"] = round(h.get("vel") or 0.0, 3) if quieto < 45 and v.get("camino") else 0.0

    def llegadas_de(self, ids):
        """Llegadas de varias paradas a la vez (en paralelo; EMTUSA tarda ~0,3 s por parada)."""
        from concurrent.futures import ThreadPoolExecutor
        ids = [i for i in ids if str(i).isdigit()][:10]
        with ThreadPoolExecutor(max_workers=6) as ex:
            return dict(zip(ids, ex.map(self.llegadas, ids)))

    def enlace(self, lat, lon, radio_m=500, por_parada=3):
        """Paradas cercanas a un punto con sus próximas llegadas (panel tren+bus)."""
        salida = []
        cerca = self.cercanas(lat, lon, radio_m)
        todas = self.llegadas_de([p["id"] for p in cerca])
        for p in cerca:
            info = todas.get(p["id"], {})
            salida.append({**p, "llegadas": info.get("llegadas", [])[:por_parada], "error": info.get("error")})
        return {"disponible": self.disponible, "error": self.error, "paradas": salida}


# --------------------------------------------------------------------- utilidades de texto
_MINUS = {"de", "del", "la", "las", "el", "los", "y", "e", "o", "a", "con",
          "por", "en", "al", "the"}
_SIGLAS = {"JOP", "APTA", "BYG", "INEM", "FIDMA", "URB", "POL", "CTRA", "AS",
           "GJ", "AVDA", "C", "Nº", "II", "III", "IV", "DRT"}


def _limpia(txt):
    """MAYÚSCULAS de EMTUSA a Formato Normal (respeta 'de/la…' y siglas conocidas)."""
    txt = (txt or "").replace("�", "ñ").strip()
    if not txt:
        return ""
    import re

    def cap(w):
        # «JOVE-POL.» -> «Jove-Pol.», «(VIESQUES)» -> «(Viesques)»
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), w.lower(), count=0)
    out = []
    for i, w in enumerate(txt.split()):
        b = w.strip(".()")
        if w.upper() in _SIGLAS or (2 <= len(b) <= 4 and b.isupper() and not any(v in b.lower() for v in "aeiouáéíóú")):
            out.append(w)
        elif i > 0 and w.lower() in _MINUS:
            out.append(w.lower())
        else:
            out.append(cap(w))
    return " ".join(out)


def _rumbo(a, b):
    """Rumbo en grados (0 = norte) de a hacia b."""
    import math
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dlo = math.radians(b[1] - a[1])
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _proyectar(pts, p):
    """(tramo, distancia km, fracción, rumbo del tramo) del punto más cercano de la polilínea."""
    import math
    coslat = math.cos(math.radians(p[0]))
    mejor = None
    for i in range(len(pts) - 1):
        ax, ay = (pts[i][1] - p[1]) * 111.32 * coslat, (pts[i][0] - p[0]) * 110.57
        bx, by = (pts[i + 1][1] - p[1]) * 111.32 * coslat, (pts[i + 1][0] - p[0]) * 110.57
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0.0 if ll <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ll))
        d = math.hypot(ax + t * dx, ay + t * dy)
        if mejor is None or d < mejor[1]:
            mejor = (i, d, t)
    if mejor is None:
        return None
    i = mejor[0]
    return mejor[0], mejor[1], mejor[2], _rumbo(pts[i], pts[i + 1])


def _hoy_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
