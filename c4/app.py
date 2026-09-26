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
from .tiemporeal import TiempoReal
from .emtusa import Emtusa
from .persistencia import Almacen, ciclo_guardado
from .util import RAIZ, WEB, http_get, ahora_min

VERSION = "2.2"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.dia = None
        self.linea = None
        self.est = None
        self.rt = None
        self.res = None
        self._estado_bytes = None
        self._version = 0
        self._arranque = int(time.time())
        self.linea_json = None
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
        except Exception as e:  # noqa: BLE001
            print("Aviso: no se pudo cargar la red de bus:", e)
            self.bus = None

    def preparar(self):
        hoy = date.today()
        if self.dia == hoy:
            return
        datos = gtfs.extraer(self.cfg, hoy)
        linea = Linea(self.cfg, datos)
        self.aprender()
        with self.lock:
            self.linea = linea
            self.est = Estimador(linea, self.cfg, self.aprendidos, self.sesgos, self.salidas)
            self.est.paradas = self.paradas
            self.rt = TiempoReal(self.cfg)
            self.precision = Precision()
            self.dia = hoy
            self.linea_json = self._linea_json()
        print("Línea %s · %s · %d estaciones · %d trenes hoy" % (
            self.cfg["linea"], hoy.strftime("%d/%m/%Y"), len(linea.est), len(linea.viajes)))
        print("Estaciones con cruce: " + ", ".join(linea.nombre[k] for k in sorted(linea.apartaderos)))

    def _linea_json(self):
        L = self.linea
        return {
            "linea": self.cfg["linea"], "version": VERSION, "fecha": self.dia.isoformat(),
            "estaciones": [{"k": k, "id": L.est[k], "nombre": L.nombre[k], "km": round(L.km[k], 3),
                            "lat": L.coord[k][0], "lon": L.coord[k][1], "cruce": k in L.apartaderos,
                            "cruces_dia": L.cuenta_cruces.get(k, 0)}
                           for k in range(len(L.est))],
            "trazado": [[round(a, 6), round(b, 6)] for a, b in L.trazado],
            "trazado_km": [round(x, 4) for x in L.trazado_km],
            "n_trenes": len(L.viajes),
            "url_movil": self.url_movil,
        }

    def ciclo(self):
        self.preparar()
        L = self.linea
        ok = self.rt.consultar(lambda tid: tid in L.viajes, L.rutas, L.est)
        if ok and self.cfg["guardar_historial"]:
            try:
                guardar_observaciones(self.rt, L)
            except Exception as e:  # noqa: BLE001
                print("Aviso (historial):", e)
        if time.time() - self.ultimo_aprendizaje > 3600:
            self.aprender()
            self.est.aprendidos, self.est.sesgos, self.est.salidas = self.aprendidos, self.sesgos, self.salidas
            self.est.paradas = self.paradas
        calidad = self.rt.calidad()
        if calidad == "congelado":  # Renfe no actualiza: mejor el horario que datos viejos
            self.rt.pos, self.rt.act = {}, {}
        res = self.est.calcular(self.rt)
        self.precision.observar(self.rt, guardar=self.cfg["guardar_historial"])
        if calidad == "directo":
            self.precision.registrar(res, L)
        res.update({
            "fecha": self.dia.isoformat(),
            "actualizado": datetime.now().strftime("%H:%M:%S"),
            "calidad": calidad,
            "ts_feed": datetime.fromtimestamp(self.rt.ts_feed).strftime("%H:%M:%S") if self.rt.ts_feed else None,
            "error": self.rt.error,
            "avisos": self.rt.avisos,
            "con_posicion": sum(1 for t in res["trenes"] if t["con_datos"] and not t["fin"]),
            "en_circulacion": sum(1 for t in res["trenes"] if not t["fin"] and t["j0"] > 0 or t["parado"] and not t["fin"] and t["con_datos"]),
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
            self._manana = (dia, Linea(self.cfg, gtfs.extraer(self.cfg, dia)))
        L = self._manana[1]
        out = []
        for v in L.viajes.values():
            jo, jd = v.stop_j.get(o_id), v.stop_j.get(d_id)
            if jo is not None and jd is not None and jo < jd:
                out.append({"num": v.num, "sale": round(v.sd[jo], 2), "llega": round(v.sa[jd], 2),
                            "destino": L.nombre[v.k[-1]]})
        out.sort(key=lambda x: x["sale"])
        return {"fecha": dia.isoformat(), "trenes": out[:4]}

    def aprendizaje(self):
        """Qué ha aprendido el sistema: tiempos reales de marcha y correcciones por errores."""
        with self.lock:
            L, ap, se = self.linea, self.aprendidos, self.sesgos
        if not L:
            return {"cargando": True}
        nom = {L.est[k]: L.nombre[k] for k in range(len(L.est))}
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
            linea = self.linea
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
                linea = self.linea
            return planificador.geocodificar(texto, linea, self.bus)
        return None

    def ir(self, origen, destino):
        if not origen:
            return {"ok": False, "error": "Falta el origen.", "cual": "origen"}
        if not destino:
            return {"ok": False, "error": "Falta el destino.", "cual": "destino"}
        with self.lock:
            linea, res = self.linea, self.res
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
                    time.sleep(self.cfg.get("intervalo_rapido_s", 3))
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
            time.sleep(self.cfg.get("intervalo_rapido_s", 3) if self.linea else 10)


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
                    # nuevo se contesta con unos bytes; si lo hay, el estado entero (ya comprimido)
                    if q.get("v", [""])[0] == str(res.get("version")):
                        return self._json({"sin_cambios": True, "version": res.get("version")})
                    cache = app._estado_bytes
                    if not cache or cache[0] is not res:
                        crudo = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        cache = app._estado_bytes = (res, crudo, gzip.compress(crudo, 5))
                return self._enviar(cache[1], "application/json; charset=utf-8", gz=cache[2])
            if ruta == "/api/linea":
                with app.lock:
                    if app.linea_json:
                        app.linea_json["url_movil"] = app.url_movil
                    return self._json(app.linea_json or {"cargando": True, "error": app.error_inicio})
            if ruta == "/api/precision":
                return self._json(Precision.estadisticas())
            if ruta == "/api/aprendizaje":
                return self._json(app.aprendizaje())
            if ruta == "/api/ping":
                return self._json({"ok": True, "hora": datetime.now().strftime("%H:%M:%S")})
            if ruta == "/api/bus/red":
                return self._json(app.bus.resumen_red() if app.bus else {"error": "sin datos de bus"})
            if ruta == "/api/bus/coordenadas":
                return self._json(app.bus.vehiculos() if app.bus else {"disponible": False, "vehiculos": []})
            if ruta == "/api/bus/cercanas":
                q = parse_qs(urlparse(self.path).query)
                try:
                    return self._json({"paradas": app.bus.cercanas(float(q["lat"][0]), float(q["lon"][0]))})
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e), "paradas": []})
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
                    linea = app.linea
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
