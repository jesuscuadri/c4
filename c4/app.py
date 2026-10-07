# -*- coding: utf-8 -*-
"""Bucle de actualización y servidor web local."""
import gzip
import json
import mimetypes
import os
import socket
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gtfs
from . import planificador
from .estimador import Estimador
from .historial import (Precision, aprender_paradas, aprender_salidas, aprender_sesgos, aprender_tiempos,
                        guardar_observaciones, guardar_resumen, resumen)
from .linea import Linea
from .red import Red
from .tiemporeal import TiempoReal
from .emtusa import Emtusa
from .persistencia import Almacen, ciclo_guardado
from .util import RAIZ, WEB, http_get, ahora_min, distancia_km

VERSION = "2.2"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.dia = None
        self.linea = None
        self.estaciones_plan = None
        self.est = None
        self.rt = None
        self.res = None
        self._estado_bytes = None
        self._version = 0
        self._arranque = int(time.time())
        self.linea_json = None
        self.red_json = None
        self.redes = {}
        self.estaciones = []
        self._estado_cache = {}
        self.precision = Precision()
        self.aprendidos = {}
        self.sesgos = {}
        self.salidas = {}
        self.paradas = {}
        self.almacen = Almacen()               # guarda lo aprendido fuera del servidor (GitHub)
        self.ultimo_aprendizaje = 0
        self.errores_seguidos = 0
        self.url_movil = None
        self.error_inicio = None
        self._manana = (None, None)
        try:
            self.bus = Emtusa(cfg.get("bus", True))
            if self.bus.red_ok:
                print("Red de bus EMTUSA: %d líneas · %d paradas" % (
                    len(self.bus.lineas_d), len(self.bus.paradas_d)))
                threading.Thread(target=self.bus.bucle_vivo, daemon=True).start()
        except Exception as e:  # noqa: BLE001
            print("Aviso: no se pudo cargar la red de bus:", e)
            self.bus = None

    def preparar(self):
        hoy = date.today()
        if self.dia == hoy:
            return
        datos = gtfs.extraer_red(self.cfg, hoy)
        self.aprender()
        redes = {}
        for nombre, conf in self.cfg["redes"].items():
            dg = gtfs.separar_por_grupo(datos, set(conf["lineas"]))
            if not dg["viajes"]:
                continue
            cfg_r = dict(self.cfg)
            cfg_r["via_doble_si_coinciden"] = conf.get("via_doble_si_coinciden", 3)
            red = Red(cfg_r, dg)
            est = Estimador(red, cfg_r, self.aprendidos, self.sesgos, self.salidas)
            est.paradas = self.paradas
            redes[nombre] = {"red": red, "est": est, "cfg": cfg_r}
            print("Red %s · %d estaciones · %d trenes · vía doble en %d de %d tramos · cruces en: %s" % (
                nombre, len(red.est), len(red.viajes), len(red.doble), len(red.tramos),
                ", ".join(sorted(red.nombre[k] for k in red.apartaderos))))
        # Estaciones de toda Asturias, una sola vez aunque estén en las dos redes (Gijón, Oviedo...):
        # los trenes se mandan con el índice de esta lista
        estaciones, glob = [], {}
        for nombre, r in redes.items():
            red = r["red"]
            r["glob"] = []
            for k, s in enumerate(red.est):
                if s not in glob:
                    glob[s] = len(estaciones)
                    estaciones.append({"k": glob[s], "id": s, "nombre": red.nombre[k],
                                       "lat": red.coord[k][0], "lon": red.coord[k][1],
                                       "lineas": set(), "cruce": False, "redes": []})
                g = estaciones[glob[s]]
                g["redes"].append(nombre)
                g["cruce"] = g["cruce"] or k in red.apartaderos
                r["glob"].append(glob[s])
            for v in red.viajes.values():
                for k in v.k:
                    estaciones[r["glob"][k]]["lineas"].add(v.linea)
        for e in estaciones:
            e["lineas"] = sorted(e["lineas"], key=_orden_linea)
        with self.lock:
            self.redes = redes
            self.estaciones = estaciones
            self.glob = glob
            # compatibilidad: «la línea» (planificador, mañana...) es la red de la línea principal
            principal = next((r for r in redes.values() if self.cfg["linea"] in r["red"].ejes),
                             next(iter(redes.values())))
            self.linea, self.est = principal["red"], principal["est"]
            self.rt = TiempoReal(self.cfg)
            self.precision = Precision()
            self.dia = hoy
            self.estaciones_plan = planificador.Estaciones(estaciones)
            self.red_json = self._red_json()
            self.linea_json = self._linea_json()
            self._estado_cache = {}
        print("Asturias · %s · %d estaciones · %d trenes hoy" % (
            hoy.strftime("%d/%m/%Y"), len(estaciones), sum(len(r["red"].viajes) for r in redes.values())))

    def _red_json(self):
        """Lo fijo del día para la interfaz: estaciones, líneas (con su eje y los tramos que usan) y
        la geometría de cada tramo (para dibujar las líneas y mover los trenes por la vía)."""
        lineas, tramos = {}, {}
        for nombre, r in self.redes.items():
            red, G = r["red"], r["glob"]
            for (a, b), geo in red.geo_tramo.items():
                if a < b:
                    tramos["%d-%d" % (G[a], G[b])] = [[round(x, 5), round(y, 5)] for x, y in _simplificar(geo, 0.004)]
            for lin, eje in red.ejes.items():
                nodos = eje["nodos"]
                usados = []
                vistos = set()
                for v in red.viajes.values():
                    if v.linea != lin:
                        continue
                    for x, y in zip(v.k, v.k[1:]):
                        c = (min(G[x], G[y]), max(G[x], G[y]))
                        if c not in vistos:
                            vistos.add(c)
                            usados.append("%d-%d" % c)
                lineas[lin] = {
                    "codigo": lin, "red": nombre, "color": self.cfg.get("colores_lineas", {}).get(lin, "#888888"),
                    "nombre": "%s – %s" % (_corto(red.nombre[nodos[0]]), _corto(red.nombre[nodos[-1]])),
                    "eje": [G[k] for k in nodos], "eje_km": [round(x, 3) for x in eje["km"]],
                    "otros": [G[k] for k in eje["otros"]], "tramos": usados,
                    "n_trenes": sum(1 for v in red.viajes.values() if v.linea == lin),
                    "cruces": sorted({G[k] for k in red.apartaderos if k in set(nodos) | set(eje["otros"])}),
                    "doble": sorted("%d-%d" % (min(G[a], G[b]), max(G[a], G[b])) for a, b in red.doble
                                    if (min(G[a], G[b]), max(G[a], G[b])) in vistos),
                }
        return {"version": VERSION, "fecha": self.dia.isoformat(), "linea_defecto": self.cfg["linea"],
                "lineas": dict(sorted(lineas.items(), key=lambda kv: _orden_linea(kv[0]))),
                "estaciones": self.estaciones, "tramos": tramos, "url_movil": self.url_movil}

    def _linea_json(self, codigo=None):
        """Una línea como la veía la app cuando solo había la C-4: estaciones del eje con sus km."""
        codigo = codigo or self.cfg["linea"]
        info = self.red_json["lineas"].get(codigo)
        if not info:
            return None
        r = self.redes[info["red"]]
        red = r["red"]
        eje = red.ejes[codigo]
        kmn = dict(zip(eje["nodos"], eje["km"]))
        # estaciones fuera del eje (ramales): km del punto del eje más cercano
        for k in eje["otros"]:
            kmn[k] = min(eje["nodos"], key=lambda n: distancia_km(red.coord[n], red.coord[k]))
            kmn[k] = dict(zip(eje["nodos"], eje["km"]))[kmn[k]] + distancia_km(red.coord[kmn[k]], red.coord[k])
        ks = list(eje["nodos"]) + list(eje["otros"])
        return {
            "linea": codigo, "version": VERSION, "fecha": self.dia.isoformat(),
            "estaciones": [{"k": r["glob"][k], "id": red.est[k], "nombre": red.nombre[k], "km": round(kmn.get(k, 0.0), 3),
                            "lat": red.coord[k][0], "lon": red.coord[k][1], "cruce": k in red.apartaderos,
                            "cruces_dia": red.cuenta_cruces.get(k, 0)} for k in ks],
            "trazado": self._trazado_eje(codigo),
            "trazado_km": None,
            "n_trenes": info["n_trenes"],
            "url_movil": self.url_movil,
        }

    def _trazado_eje(self, codigo):
        info = self.red_json["lineas"][codigo]
        eje = info["eje"]
        trz = []
        for a, b in zip(eje, eje[1:]):
            g = self.red_json["tramos"].get("%d-%d" % (min(a, b), max(a, b)))
            if not g:
                continue
            g = g if a < b else g[::-1]
            trz.extend(g if not trz else g[1:])
        return trz

    def ciclo(self):
        self.preparar()
        todas = {}
        for r in self.redes.values():
            todas.update(r["red"].viajes)
        rutas = sorted({x for r in self.redes.values() for x in r["red"].rutas})
        paradas = sorted({e["id"] for e in self.estaciones})
        ok = self.rt.consultar(lambda tid: tid in todas, rutas, paradas)
        if ok and self.cfg["guardar_historial"]:
            try:
                guardar_observaciones(self.rt, _Viajes(todas))
            except Exception as e:  # noqa: BLE001
                print("Aviso (historial):", e)
        if time.time() - self.ultimo_aprendizaje > 3600:
            self.aprender()
            for r in self.redes.values():
                r["est"].aprendidos, r["est"].sesgos, r["est"].salidas = self.aprendidos, self.sesgos, self.salidas
                r["est"].paradas = self.paradas
        calidad = self.rt.calidad()
        if calidad == "congelado":  # Renfe no actualiza: mejor el horario que datos viejos
            self.rt.pos, self.rt.act = {}, {}
        self.precision.observar(self.rt, guardar=self.cfg["guardar_historial"])
        trenes, cruces, ahora = [], [], None
        for nombre, r in self.redes.items():
            res_r = r["est"].calcular(self.rt)
            ahora = res_r["ahora"]
            if calidad == "directo":
                self.precision.registrar(res_r, r["red"])
            G = r["glob"]
            for t in res_r["trenes"]:
                t["linea"] = r["red"].viajes[t["id"]].linea
                t["km"] = [round(x, 3) for x in r["red"].viajes[t["id"]].km]
                t["k"] = [G[k] for k in t["k"]]
                for m in t["motivos"]:
                    m["k"] = G[m["k"]]
                trenes.append(t)
            for c in res_r["cruces"]:
                c["k"] = G[c["k"]]
                cruces.append(c)
        cruces.sort(key=lambda c: c["hora"])
        res = {"ahora": ahora, "trenes": trenes, "cruces": cruces}
        res.update({
            "fecha": self.dia.isoformat(),
            "actualizado": datetime.now().strftime("%H:%M:%S"),
            "calidad": calidad,
            "ts_feed": datetime.fromtimestamp(self.rt.ts_feed).strftime("%H:%M:%S") if self.rt.ts_feed else None,
            "error": self.rt.error,
            "avisos": self.rt.avisos,
            "avisos_lineas": [{"texto": a["texto"], "lineas": self._lineas_de_aviso(a)}
                              for a in getattr(self.rt, "avisos_detalle", [])],
            "con_posicion": sum(1 for t in trenes if t["con_datos"] and not t["fin"]),
            "en_circulacion": sum(1 for t in trenes if not t["fin"] and t["j0"] > 0 or t["parado"] and not t["fin"] and t["con_datos"]),
            "tramos_aprendidos": len(self.aprendidos),
            "sesgos_corregidos": len(self.sesgos),
            "salidas_aprendidas": len(self.salidas),
            "paradas_aprendidas": len(self.paradas),
            "modo_cruces": self.cfg["cruces"],
            "ts": round(time.time(), 3),
        })
        self._version += 1
        res["version"] = "%d-%d" % (self._arranque, self._version)   # único aunque el servidor se reinicie
        with self.lock:
            self.res = res
            self._estado_cache = {}

    def _lineas_de_aviso(self, a):
        """A qué líneas afecta un aviso de Renfe (por sus rutas o sus estaciones)."""
        out = set()
        for r in a.get("rutas", []):
            for lin in self.red_json["lineas"]:
                if r.strip().endswith(lin):
                    out.add(lin)
        porid = {e["id"]: e for e in self.estaciones}
        for s in a.get("paradas", []):
            if s in porid:
                out |= set(porid[s]["lineas"])
        return sorted(out, key=_orden_linea)

    def estado_filtrado(self, lineas, gz):
        """El estado para un móvil: solo las líneas que mira y los trenes que importan ahora (los que
        circulan y los que salen en las próximas horas). Se prepara una vez por versión y filtro."""
        with self.lock:
            res = self.res
            clave = (res["version"], lineas)
            c = self._estado_cache.get(clave)
            if c:
                return c
        quiero = set(lineas.split(",")) if lineas and lineas != "todas" else None
        ahora, vent = res["ahora"], self.cfg.get("ventana_estado_min", 240)
        trenes = []
        for t in res["trenes"]:
            if quiero is not None and t["linea"] not in quiero:
                continue
            ini = t["est_d"][0] if t["est_d"][0] is not None else t["prog_d"][0]
            fin = t["est_a"][-1] if t["est_a"][-1] is not None else t["prog_a"][-1]
            if fin < ahora - 15 or ini > ahora + vent:
                continue
            trenes.append(t)
        ids = {t["id"] for t in trenes}
        cruces = [c for c in res["cruces"] if c["ida"]["id"] in ids or c["vuelta"]["id"] in ids]
        out = dict(res, trenes=trenes, cruces=cruces, lineas_pedidas=lineas or "todas")
        if quiero is not None:   # cuentas de la cabecera: solo las líneas pedidas
            sel = [t for t in res["trenes"] if t["linea"] in quiero]
            out["con_posicion"] = sum(1 for t in sel if t["con_datos"] and not t["fin"])
            out["en_circulacion"] = sum(1 for t in sel if not t["fin"] and t["j0"] > 0
                                        or t["parado"] and not t["fin"] and t["con_datos"])
        if quiero is not None:
            out["avisos"] = [a["texto"] for a in res.get("avisos_lineas", []) if not a["lineas"] or set(a["lineas"]) & quiero] \
                if res.get("avisos_lineas") is not None else res["avisos"]
        crudo = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        c = (crudo, gzip.compress(crudo, 5))
        with self.lock:
            if len(self._estado_cache) > 40:
                self._estado_cache = {}
            self._estado_cache[clave] = c
        return c

    def aprender(self):
        """Recalcula todo lo aprendido: tiempos de marcha reales, retraso típico de cada servicio
        y corrección de sesgos a partir de los errores. Deja el resumen en disco (para guardarlo fuera)."""
        try:
            res = guardar_resumen(resumen())
        except Exception as e:  # noqa: BLE001
            print("Aviso (aprendizaje):", e)
            res = None
        self.aprendidos = aprender_tiempos(res=res) if self.cfg["usar_tiempos_aprendidos"] else {}
        self.salidas = aprender_salidas(res=res) if self.cfg.get("usar_retraso_tipico", True) else {}
        self.sesgos = aprender_sesgos() if self.cfg.get("usar_correccion_sesgo") else {}
        self.paradas = aprender_paradas(res=res) if self.cfg["usar_tiempos_aprendidos"] else {}
        self.ultimo_aprendizaje = time.time()

    def manana(self, o_id, d_id):
        """Primeros trenes de mañana entre dos estaciones (para cuando ya no quedan hoy)."""
        dia = date.today() + timedelta(days=1)
        if self._manana[0] != dia:
            self._manana = (dia, gtfs.extraer_red(self.cfg, dia))
        datos = self._manana[1]
        out = []
        for tid, filas in datos["viajes"].items():
            pos = {s: n for n, (s, _, _) in enumerate(filas)}
            jo, jd = pos.get(o_id), pos.get(d_id)
            if jo is not None and jd is not None and jo < jd:
                dig = "".join(c if c.isdigit() else " " for c in tid[5:]).split()
                out.append({"num": dig[0] if dig else tid, "linea": datos["lineas"].get(tid, ""),
                            "sale": round(filas[jo][2], 2), "llega": round(filas[jd][1], 2),
                            "destino": datos["paradas"][filas[-1][0]][0]})
        out.sort(key=lambda x: x["sale"])
        return {"fecha": dia.isoformat(), "trenes": out[:4]}

    def aprendizaje(self):
        """Qué ha aprendido el sistema: tiempos reales de marcha y correcciones por errores."""
        with self.lock:
            L, ap, se = self.linea, self.aprendidos, self.sesgos
        if not L:
            return {"cargando": True}
        nom = {e["id"]: e["nombre"] for e in self.estaciones}
        sesgos = [{"estacion": nom.get(s, s), "min": m}
                  for s, m in sorted(se.items(), key=lambda kv: -abs(kv[1]))]
        tramos = [{"de": nom.get(a, a), "a": nom.get(b, b), "min": round(m, 1)}
                  for (a, b), m in sorted(ap.items(), key=lambda kv: kv[0])]
        sal = sorted(self.salidas.items(), key=lambda kv: -kv[1])
        return {"guardado": self.almacen.estado(),
                "tramos": len(ap), "sesgos_n": len(se), "sesgos": sesgos, "tramos_lista": tramos[:80],
                "salidas_n": len(sal), "salidas": [{"num": n, "min": m} for n, m in sal[:12]],
                "paradas_n": len(self.paradas),
                "paradas": [{"estacion": nom.get(s, s), "min": m}
                            for s, m in sorted(self.paradas.items(), key=lambda kv: -kv[1])[:12]]}

    def geocode(self, q):
        with self.lock:
            linea = self.estaciones_plan
        p = planificador.geocodificar(q, linea, self.bus)
        return p or {"error": "No encontré «%s». Prueba con el nombre de una parada, una estación o un sitio." % q}

    def resolver(self, texto, lat, lon, nombre):
        if lat and lon:
            try:
                return {"lat": float(lat), "lon": float(lon), "nombre": nombre or "Tu ubicación", "tipo": "gps"}
            except ValueError:
                pass
        if texto:
            with self.lock:
                linea = self.estaciones_plan
            return planificador.geocodificar(texto, linea, self.bus)
        return None

    def ir(self, origen, destino):
        if not origen:
            return {"ok": False, "error": "Falta el origen.", "cual": "origen"}
        if not destino:
            return {"ok": False, "error": "Falta el destino.", "cual": "destino"}
        with self.lock:
            linea, res = self.estaciones_plan, self.res
        if linea is None or res is None:
            return {"ok": False, "cargando": True}
        try:
            return planificador.planificar(linea, res, self.bus, origen, destino, ahora_min())
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return {"ok": False, "error": "No pude calcular la ruta (%s)." % e}

    def bucle(self):
        # antes de nada, recuperar lo aprendido (el disco de Render llega vacío tras cada reinicio)
        if self.almacen.activo:
            self.almacen.cargar()
            threading.Thread(target=ciclo_guardado, args=(self.almacen,), daemon=True).start()
        ultimo = 0.0
        while True:
            try:
                # Renfe publica posiciones cada ~20 s. Se le pregunta cada pocos segundos si hay algo
                # nuevo (normalmente contesta «sin cambios» sin mandar nada) y, en cuanto lo hay,
                # se recalcula al momento: así lo que ves va unos segundos por detrás de Renfe, no 30-40.
                # Aunque no haya novedades, se recalcula cada intervalo (el tiempo pasa).
                if self.linea and time.time() - ultimo < self.cfg["intervalo_consulta_s"] \
                        and not self.rt.hay_novedades():
                    time.sleep(self._espera_renfe())
                    continue
                self.ciclo()
                ultimo = time.time()
                self.errores_seguidos = 0
            except Exception as e:  # noqa: BLE001
                self.errores_seguidos += 1
                print("Error en la actualización:", e)
                if self.errores_seguidos == 1:
                    traceback.print_exc()
                with self.lock:
                    self.error_inicio = None if self.linea else str(e)
                    if self.res:
                        self.res["error"] = str(e)
            time.sleep(self._espera_renfe() if self.linea else 10)

    def _espera_renfe(self):
        """Hasta la siguiente consulta a Renfe: justo después de cuando le toca publicar (medio segundo
        de margen); si se retrasa, cada segundo; si aún no se sabe su ritmo, cada intervalo_rapido_s."""
        sig = self.rt.proxima_publicacion() if self.rt is not None else None
        if sig is None:
            return self.cfg.get("intervalo_rapido_s", 2)
        falta = sig + 0.6 - time.time()
        if falta < -0.5:
            return 1.0
        return max(0.3, min(falta, self.cfg.get("intervalo_rapido_s", 2) * 5))


class _Viajes:
    def __init__(self, viajes):
        self.viajes = viajes


def _orden_linea(c):
    """C1 < C2 < … < C5 < C5a < C6 …"""
    import re
    m = re.match(r"[A-Za-z]*(\d+)(.*)", c or "")
    return (int(m.group(1)), m.group(2)) if m else (999, c)


def _simplificar(pts, tol_km):
    """Douglas-Peucker: quita puntos que se desvían menos de tol_km de la recta (la vía se ve igual)."""
    if len(pts) < 3:
        return list(pts)
    a, b = pts[0], pts[-1]
    coslat = __import__("math").cos(__import__("math").radians(a[0]))
    ax, ay = a[1] * 111.32 * coslat, a[0] * 110.57
    bx, by = b[1] * 111.32 * coslat, b[0] * 110.57
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    mx, mi = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i][1] * 111.32 * coslat, pts[i][0] * 110.57
        t = 0.0 if ll <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        d = ((ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2) ** 0.5
        if d > mx:
            mx, mi = d, i
    if mx <= tol_km:
        return [a, b]
    return _simplificar(pts[:mi + 1], tol_km)[:-1] + _simplificar(pts[mi:], tol_km)


def _corto(nombre):
    return (nombre or "").replace("Gijón-Sanz Crespo", "Gijón").replace(" Apeadero", "").replace("-Apeadero", "")


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def servir(app, abrir=True, en_red=False, publico=False):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, cuerpo, tipo, codigo=200, gz=None):
            # comprimido si el navegador lo acepta (el estado pasa de ~200 KB a ~25 KB: llega antes)
            if gz is None and len(cuerpo) > 2048 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                gz = gzip.compress(cuerpo, 5)
            usar_gz = gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or "")
            if usar_gz:
                cuerpo = gz
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            if usar_gz:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "no-store")
            # hora del servidor: el móvil la usa para corregir si su reloj va adelantado o atrasado
            self.send_header("X-Hora-Servidor", "%.3f" % time.time())
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, obj):
            self._enviar(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            ruta = self.path.split("?")[0]
            if ruta == "/api/estado":
                q = parse_qs(urlparse(self.path).query)
                with app.lock:
                    res = app.res
                    if not res:
                        return self._json({"cargando": True})
                    # el móvil pregunta cada pocos segundos con la versión que tiene: si no hay nada
                    # nuevo se contesta con unos bytes; si lo hay, el estado (solo de las líneas que mira)
                    # cuándo volver a preguntar: justo después de la próxima publicación de Renfe
                    sig = app.rt.proxima_publicacion() if app.rt is not None else None
                    sig_s = round(max(0.5, sig + 1.6 - time.time()), 1) if sig else None
                    if q.get("v", [""])[0] == str(res.get("version")):
                        return self._json({"sin_cambios": True, "version": res.get("version"), "sig_s": sig_s})
                lineas = q.get("lineas", [""])[0] or "todas"
                crudo, gz = app.estado_filtrado(lineas, True)
                return self._enviar(crudo, "application/json; charset=utf-8", gz=gz)
            if ruta == "/api/red":
                with app.lock:
                    if app.red_json:
                        app.red_json["url_movil"] = app.url_movil
                    return self._json(app.red_json or {"cargando": True, "error": app.error_inicio})
            if ruta == "/api/linea":
                q = parse_qs(urlparse(self.path).query)
                with app.lock:
                    lj = app._linea_json(q.get("linea", [""])[0] or None) if app.red_json else None
                    if lj:
                        lj["url_movil"] = app.url_movil
                    return self._json(lj or {"cargando": True, "error": app.error_inicio})
            if ruta == "/api/precision":
                return self._json(Precision.estadisticas())
            if ruta == "/api/aprendizaje":
                return self._json(app.aprendizaje())
            if ruta == "/api/ping":
                return self._json({"ok": True, "hora": datetime.now().strftime("%H:%M:%S")})
            if ruta == "/api/bus/red":
                return self._json(app.bus.resumen_red() if app.bus else {"error": "sin datos de bus"})
            if ruta == "/api/bus/coordenadas":
                if not app.bus:
                    return self._json({"disponible": False, "vehiculos": []})
                # el móvil pregunta cada 2 s con la versión que tiene: si nada ha cambiado, unos bytes
                q = parse_qs(urlparse(self.path).query)
                crudo, gz = app.bus.vehiculos_bytes(True)
                if q.get("v", [""])[0] and q["v"][0] == app.bus.version:
                    return self._json({"sin_cambios": True, "version": app.bus.version})
                return self._enviar(crudo, "application/json; charset=utf-8", gz=gz)
            if ruta == "/api/bus/cercanas":
                q = parse_qs(urlparse(self.path).query)
                try:
                    return self._json({"paradas": app.bus.cercanas(float(q["lat"][0]), float(q["lon"][0]))})
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e), "paradas": []})
            if ruta == "/api/bus/llegadas":
                # varias paradas a la vez (favoritas, cerca de mí): ?ids=51,76
                q = parse_qs(urlparse(self.path).query)
                ids = [x for x in q.get("ids", [""])[0].split(",") if x.strip().isdigit()]
                if not app.bus:
                    return self._json({"paradas": {}})
                return self._json({"paradas": app.bus.llegadas_de(ids), "disponible": app.bus.disponible})
            if ruta == "/api/bus/buscar":
                q = parse_qs(urlparse(self.path).query)
                return self._json({"paradas": app.bus.buscar_paradas(q.get("q", [""])[0]) if app.bus else []})
            if ruta.startswith("/api/bus/parada/"):
                try:
                    return self._json(app.bus.llegadas(ruta.rsplit("/", 1)[1]))
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e), "llegadas": []})
            if ruta == "/bus" or ruta == "/bus/":
                ruta = "/bus/index.html"
            if ruta == "/api/bus/enlace":
                q = parse_qs(urlparse(self.path).query)
                try:
                    lat = float(q.get("lat", [""])[0]); lon = float(q.get("lon", [""])[0])
                    return self._json(app.bus.enlace(lat, lon, app.cfg.get("bus_radio_m", 550)))
                except Exception as e:  # noqa: BLE001
                    return self._json({"disponible": False, "error": str(e), "paradas": []})
            if ruta.startswith("/api/bus/parada/"):
                return self._json(app.bus.llegadas(ruta.rsplit("/", 1)[-1]))
            if ruta == "/api/sugerir":
                q = parse_qs(urlparse(self.path).query)
                with app.lock:
                    linea = getattr(app, "estaciones_plan", None)
                return self._json({"sugerencias": planificador.sugerir(q.get("q", [""])[0], linea, app.bus)})
            if ruta == "/api/geocode":
                q = parse_qs(urlparse(self.path).query)
                return self._json(app.geocode(q.get("q", [""])[0]))
            if ruta == "/api/ir":
                q = parse_qs(urlparse(self.path).query)
                g = lambda k: q.get(k, [""])[0]  # noqa: E731
                origen = app.resolver(g("origen"), g("olat"), g("olon"), g("oname"))
                destino = app.resolver(g("destino"), g("dlat"), g("dlon"), g("dname"))
                if g("origen") and not origen:
                    return self._json({"ok": False, "error": "No encontré el origen «%s»." % g("origen"), "cual": "origen"})
                if g("destino") and not destino:
                    return self._json({"ok": False, "error": "No encontré el destino «%s»." % g("destino"), "cual": "destino"})
                return self._json(app.ir(origen, destino))
            if ruta == "/api/manana":
                q = parse_qs(urlparse(self.path).query)
                try:
                    return self._json(app.manana(q.get("o", [""])[0], q.get("d", [""])[0]))
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e), "trenes": []})
            if ruta == "/":
                ruta = "/index.html"
            if ruta.startswith("/bus/"):
                raiz = os.path.join(RAIZ, "web-bus")
                fichero = os.path.normpath(os.path.join(raiz, ruta[len("/bus/"):]))
                base = os.path.normpath(raiz)
            else:
                fichero = os.path.normpath(os.path.join(WEB, ruta.lstrip("/")))
                base = os.path.normpath(WEB)
            if not fichero.startswith(base) or not os.path.isfile(fichero):
                return self._enviar(b"No encontrado", "text/plain; charset=utf-8", 404)
            tipo = mimetypes.guess_type(fichero)[0] or "application/octet-stream"
            if tipo.startswith("text/") or tipo.endswith("javascript"):
                tipo += "; charset=utf-8"
            with open(fichero, "rb") as f:
                self._enviar(f.read(), tipo)

    host = "0.0.0.0" if en_red else "127.0.0.1"
    srv = ThreadingHTTPServer((host, app.cfg["puerto"]), Manejador)
    url = "http://localhost:%d" % app.cfg["puerto"]
    if publico:
        print("Servidor escuchando en el puerto %d" % app.cfg["puerto"])
    else:
        print("\nAbierto en %s   (Ctrl+C para cerrar)" % url)
    if en_red and not publico:
        ip = ip_local()
        if ip:
            app.url_movil = "http://%s:%d" % (ip, app.cfg["puerto"])
            print("\nPara el iPhone (conectado a la misma wifi):  %s" % app.url_movil)
            print("En la web del ordenador tienes un botón «iPhone» con un código QR para abrirla con la cámara.")
            print("Si Windows pregunta por el cortafuegos, pulsa «Permitir» (redes privadas).")
        else:
            print("No se ha podido averiguar la IP de este ordenador en la wifi.")
    if abrir:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    externa = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("C4_URL_PUBLICA")
    if publico and externa:
        threading.Thread(target=mantener_despierto, args=(externa,), daemon=True).start()
    srv.serve_forever()


def mantener_despierto(url):
    """En el plan gratis de Render el servidor se duerme tras 15 min sin visitas y tarda
    ~1 min en despertar. Mientras circulan trenes (5:00-0:45) se visita a sí mismo cada
    10 min para estar siempre listo. Fuera de ese horario se deja dormir (ahorra horas)."""
    print("Manteniendo despierto %s en horario de trenes" % url)
    while True:
        time.sleep(600)
        ahora = datetime.now()
        minuto = ahora.hour * 60 + ahora.minute
        if minuto >= 5 * 60 or minuto <= 45:
            try:
                http_get(url.rstrip("/") + "/api/ping", timeout=30)
            except Exception as e:  # noqa: BLE001
                print("Aviso (despertar):", e)
