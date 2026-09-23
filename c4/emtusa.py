# -*- coding: utf-8 -*-
"""Autobús urbano de Gijón (EMTUSA) en tiempo real, para enlazar con el tren.

Usa la API pública de EMTUSA (la misma que su app y su web oficial, www.emtusa.es):
un servicio SIRI en https://emtusasiri.pub.gijon.es que devuelve las paradas con
coordenadas y, para cada parada, los autobuses que están por llegar y en cuántos
minutos. No son datos de ningún usuario: son las credenciales públicas de la
aplicación, visibles en el código de su web. Se pueden cambiar con variables de
entorno (C4_BUS_USER, C4_BUS_PASS, C4_BUS_BASIC) por si EMTUSA las modifica.

Todo con la biblioteca estándar. Si la API no responde, el resto del programa
(el tren) sigue funcionando: el bus simplemente no aparece.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from .util import distancia_km

BASE = os.environ.get("C4_BUS_BASE", "https://emtusasiri.pub.gijon.es/emtusasiri/")
USER = os.environ.get("C4_BUS_USER", "info@vitesia.com")
PASS = os.environ.get("C4_BUS_PASS", "vitesia130")
BASIC = os.environ.get("C4_BUS_BASIC", "Basic YXBpOmFwaQ==")   # api:api (credencial pública de la app)


class Emtusa:
    def __init__(self, activo=True):
        self.activo = activo
        self.lock = threading.Lock()
        self._token = None
        self._token_ts = 0
        self._paradas = None
        self._paradas_ts = 0
        self.disponible = None      # None = sin probar, True/False según última llamada
        self.error = None

    # ------------------------------------------------------------------ red
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
        tok = j.get("access_token") or j.get("token") or j.get("api_token") or (j if isinstance(j, str) else None)
        if not tok:
            raise RuntimeError("Respuesta de login sin token")
        self._token, self._token_ts = tok, time.time()
        return tok

    # ------------------------------------------------------------------ paradas (se cachean a diario)
    def paradas(self):
        if not self.activo:
            return []
        with self.lock:
            if self._paradas is not None and time.time() - self._paradas_ts < 20 * 3600:
                return self._paradas
        try:
            crudo = self._get("paradas/todasParadas")
            paradas = [{"id": p["idparada"], "nombre": _bonito(p["descripcion"]),
                        "lat": float(p["latitud"]), "lon": float(p["longitud"])}
                       for p in crudo if p.get("latitud") and p.get("longitud")]
            with self.lock:
                self._paradas, self._paradas_ts = paradas, time.time()
            self.disponible, self.error = True, None
            return paradas
        except Exception as e:  # noqa: BLE001
            self.disponible, self.error = False, "No se pudo leer las paradas de EMTUSA (%s)" % e
            return self._paradas or []

    def cercanas(self, lat, lon, radio_m=500, limite=6):
        """Paradas a menos de radio_m metros de un punto, ordenadas por distancia."""
        out = []
        for p in self.paradas():
            d = distancia_km((lat, lon), (p["lat"], p["lon"])) * 1000
            if d <= radio_m:
                out.append(dict(p, metros=round(d)))
        out.sort(key=lambda p: p["metros"])
        return out[:limite]

    # ------------------------------------------------------------------ llegadas en tiempo real
    def llegadas(self, id_parada):
        """Autobuses por llegar a una parada: [{linea, codigo, color, destino, minutos, distancia}]."""
        if not self.activo:
            return {"error": "bus desactivado", "llegadas": []}
        try:
            j = self._get("paradas/parada/%s" % id_parada)
            lls = []
            for x in j.get("llegadas", []) or []:
                ln = x.get("linea") or {}
                tr = x.get("trayecto") or {}
                lls.append({"linea": ln.get("codigo") or ln.get("descripcion", ""),
                            "nombre_linea": _bonito(ln.get("descripcion", "")),
                            "color": "#" + (ln.get("colorhex") or "666666"),
                            "destino": _bonito(tr.get("destino") or ""),
                            "minutos": x.get("minutos"), "distancia": x.get("distancia"),
                            "actualizado": x.get("horaActualizacion")})
            lls.sort(key=lambda l: (l["minutos"] is None, l["minutos"]))
            self.disponible, self.error = True, None
            return {"id": id_parada, "nombre": _bonito(j.get("descripcion", "")),
                    "lat": float(j["latitud"]) if j.get("latitud") else None,
                    "lon": float(j["longitud"]) if j.get("longitud") else None,
                    "llegadas": lls}
        except Exception as e:  # noqa: BLE001
            self.disponible, self.error = False, str(e)
            return {"id": id_parada, "error": str(e), "llegadas": []}

    def enlace(self, lat, lon, radio_m=500, por_parada=3):
        """Para un punto (p. ej. la estación de tren): paradas cercanas con sus próximas llegadas."""
        paradas = self.cercanas(lat, lon, radio_m)
        salida = []
        for p in paradas:
            info = self.llegadas(p["id"])
            salida.append({**p, "llegadas": info.get("llegadas", [])[:por_parada], "error": info.get("error")})
        return {"disponible": self.disponible, "error": self.error, "paradas": salida}


_MINUS = {"de", "del", "la", "las", "el", "los", "y", "e", "o", "a", "con",
          "por", "en", "al", "the"}
_SIGLAS = {"JOP", "APTA", "BYG", "INEM", "FIDMA", "URB", "POL", "CTRA", "AS",
          "GJ", "AVDA", "C", "Nº", "II", "III", "IV"}


def _bonito(txt):
    """MAYÚSCULAS de EMTUSA a Formato Normal (respeta 'de/la…' y siglas conocidas)."""
    txt = (txt or "").strip()
    if not txt:
        return ""
    out = []
    for i, w in enumerate(txt.split()):
        b = w.strip(".")
        if w.upper() in _SIGLAS or (len(b) <= 4 and b.isupper() and not any(v in b.lower() for v in "aeiou")):
            out.append(w)                       # siglas: JOP, BYG, CTRA…
        elif i > 0 and w.lower() in _MINUS:
            out.append(w.lower())               # de, la, el… salvo al principio
        else:
            out.append(w.capitalize())
    return " ".join(out)
