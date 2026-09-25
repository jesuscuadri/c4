# -*- coding: utf-8 -*-
"""Lectura del tiempo real de Renfe (GTFS-Realtime en JSON)."""
import json
import time

from .util import ahora_min, http_get

RT_POSICIONES = "https://gtfsrt.renfe.com/vehicle_positions.json"
RT_ACTUALIZACIONES = "https://gtfsrt.renfe.com/trip_updates.json"
RT_AVISOS = "https://gtfsrt.renfe.com/alerts.json"


class TiempoReal:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pos = {}           # trip_id -> {stop, estado, ts, lat, lon, via}
        self.act = {}           # trip_id -> {retraso, stop, hora, cancelado}
        self.avisos = []
        self.ts_consulta = None  # cuándo leímos nosotros
        self.ts_feed = None      # marca de tiempo que pone Renfe en el fichero
        self.error = None
        self.primera = {}        # (trip, stop, estado) -> minuto en que se vio por primera vez
        self.ultimo_estado = {}  # trip -> (stop, estado)
        self.ts_lectura = {}     # trip -> marca de tiempo de la última lectura
        self.coord_vista = {}    # trip -> ((lat, lon), minuto en que apareció esa coordenada)

    # ------------------------------------------------------------------
    def consultar(self, filtro, rutas=(), paradas=()):
        try:
            p = json.loads(http_get(RT_POSICIONES, 20).decode("utf-8"))
            u = json.loads(http_get(RT_ACTUALIZACIONES, 20).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.error = "No se pudo leer el tiempo real de Renfe (%s)" % e
            return False
        try:
            a = json.loads(http_get(RT_AVISOS, 20).decode("utf-8"))
        except Exception:  # noqa: BLE001  (los avisos son opcionales)
            a = None
        self.cargar(p, u, filtro, a, rutas, paradas)
        return True

    def cargar(self, posiciones, actualizaciones, filtro=None, avisos=None, rutas=(), paradas=(), ahora_ts=None):
        ahora_ts = ahora_ts or time.time()
        try:
            self.ts_feed = int((posiciones.get("header") or {}).get("timestamp") or 0) or None
        except (TypeError, ValueError):
            self.ts_feed = None
        pos, act = {}, {}
        for e in posiciones.get("entity", []):
            vh = e.get("vehicle") or {}
            tid = (vh.get("trip") or {}).get("tripId", "")
            if filtro and not filtro(tid):
                continue
            ts = int(vh.get("timestamp", 0) or 0) or int(ahora_ts)
            if ahora_ts - ts > self.cfg["datos_viejos_s"]:
                continue  # posición antigua: no nos fiamos
            stop = vh.get("stopId")
            if not stop or stop == "00000":
                continue
            estado = vh.get("currentStatus", "IN_TRANSIT_TO")
            etiqueta = (vh.get("vehicle") or {}).get("label", "")
            via = etiqueta.split("PLATF.(")[1].rstrip(")") if "PLATF.(" in etiqueta else None
            lat = (vh.get("position") or {}).get("latitude")
            lon = (vh.get("position") or {}).get("longitude")
            # Renfe renueva la marca de tiempo en cada lectura aunque la coordenada no haya
            # cambiado: apuntamos desde cuándo es la coordenada para saber su antigüedad real.
            desde = None
            if lat is not None and lon is not None:
                c = (round(float(lat), 5), round(float(lon), 5))
                previo = self.coord_vista.get(tid)
                if previo and previo[0] == c:
                    desde = previo[1]
                else:   # coordenada nueva (en la primera lectura no sabemos desde cuándo está)
                    desde = ahora_min(ts) if previo else None
                    self.coord_vista[tid] = (c, desde)
            pos[tid] = {"stop": stop, "estado": estado, "ts": ts, "via": via,
                        "lat": lat, "lon": lon, "coord_desde": desde}
            clave = (tid, stop, "STOPPED_AT" if estado == "STOPPED_AT" else "MARCHA")
            if self.ultimo_estado.get(tid) != (stop, clave[2]):
                # cambio de estado observado ahora mismo: pasó entre la lectura anterior y esta,
                # así que lo más probable es que fuera a mitad de camino entre ambas
                previa = self.ts_lectura.get(tid)
                cuando = ts if not previa or not 0 < ts - previa <= 90 else (ts + previa) / 2.0
                self.primera.setdefault(clave, (ahora_min(cuando), tid in self.ultimo_estado))
                self.ultimo_estado[tid] = (stop, clave[2])
            self.ts_lectura[tid] = ts
        for e in actualizaciones.get("entity", []):
            tu = e.get("tripUpdate") or {}
            tid = (tu.get("trip") or {}).get("tripId", "")
            if filtro and not filtro(tid):
                continue
            stu = (tu.get("stopTimeUpdate") or [{}])[0]
            hora = (stu.get("arrival") or stu.get("departure") or {}).get("time")
            act[tid] = {"retraso": float(tu.get("delay", 0) or 0) / 60.0, "stop": stu.get("stopId"),
                        "hora": ahora_min(int(hora)) if hora else None,
                        "cancelado": (tu.get("trip") or {}).get("scheduleRelationship") == "CANCELED"}
        self.pos, self.act = pos, act
        self.avisos = self._avisos(avisos, rutas, paradas, ahora_ts) if avisos else []
        self.ts_consulta = ahora_ts
        self.error = None

    @staticmethod
    def _avisos(avisos, rutas, paradas, ahora_ts):
        rutas = {r.strip() for r in rutas}
        paradas = set(paradas)
        out = []
        for e in avisos.get("entity", []):
            al = e.get("alert") or {}
            afecta = False
            for ie in al.get("informedEntity", []) or []:
                if (ie.get("routeId") or "").strip() in rutas or ie.get("stopId") in paradas:
                    afecta = True
            if not afecta:
                continue
            activo = False
            for per in al.get("activePeriod", []) or [{}]:
                ini = int(per.get("start", 0) or 0)
                fin = int(per.get("end", 0) or 0)
                if ini <= ahora_ts and (not fin or ahora_ts <= fin):
                    activo = True
            if not activo:
                continue
            textos = (al.get("descriptionText") or al.get("headerText") or {}).get("translation", [])
            txt = next((t.get("text") for t in textos if t.get("language", "es").startswith("es")), None)
            if txt is None and textos:
                txt = textos[0].get("text")
            if txt:
                out.append(txt.strip())
        return out

    # ------------------------------------------------------------------
    def cuando(self, tid, stop, parado):
        """(minuto, fiable): cuándo se vio por primera vez al tren en ese estado.
        fiable=False si ya estaba así en la primera lectura (no sabemos desde cuándo)."""
        return self.primera.get((tid, stop, "STOPPED_AT" if parado else "MARCHA"))

    def calidad(self, ahora_ts=None):
        """'directo', 'congelado' (Renfe no actualiza) o 'sin_conexion'."""
        ahora_ts = ahora_ts or time.time()
        if self.ts_consulta is None or ahora_ts - self.ts_consulta > 3 * self.cfg["intervalo_consulta_s"] + 60:
            return "sin_conexion"
        if self.ts_feed and ahora_ts - self.ts_feed > self.cfg["datos_viejos_s"]:
            return "congelado"
        return "directo"
