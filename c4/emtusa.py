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
                                       "color": "#" + color.lstrip("#"), "nombre": l.get("desc") or ""}
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
            return {"id": id_parada, "nombre": _limpia(j.get("descripcion", "")) or (base or {}).get("nombre"),
                    "lat": float(j["latitud"]) if j.get("latitud") else (base or {}).get("lat"),
                    "lon": float(j["longitud"]) if j.get("longitud") else (base or {}).get("lon"),
                    "llegadas": lls}
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

    def enlace(self, lat, lon, radio_m=500, por_parada=3):
        """Paradas cercanas a un punto con sus próximas llegadas (panel tren+bus)."""
        salida = []
        for p in self.cercanas(lat, lon, radio_m):
            info = self.llegadas(p["id"])
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
    out = []
    for i, w in enumerate(txt.split()):
        b = w.strip(".")
        if w.upper() in _SIGLAS or (len(b) <= 4 and b.isupper() and not any(v in b.lower() for v in "aeiou")):
            out.append(w)
        elif i > 0 and w.lower() in _MINUS:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _hoy_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
