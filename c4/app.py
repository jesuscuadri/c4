# -*- coding: utf-8 -*-
"""Bucle de actualización y servidor web local."""
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
from .estimador import Estimador
from .historial import Precision, aprender_tiempos, guardar_observaciones
from .linea import Linea
from .tiemporeal import TiempoReal
from .util import WEB, http_get

VERSION = "2.1"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.dia = None
        self.linea = None
        self.est = None
        self.rt = None
        self.res = None
        self.linea_json = None
        self.precision = Precision()
        self.aprendidos = {}
        self.ultimo_aprendizaje = 0
        self.errores_seguidos = 0
        self.url_movil = None
        self.error_inicio = None
        self._manana = (None, None)

    def preparar(self):
        hoy = date.today()
        if self.dia == hoy:
            return
        datos = gtfs.extraer(self.cfg, hoy)
        linea = Linea(self.cfg, datos)
        self.aprendidos = aprender_tiempos() if self.cfg["usar_tiempos_aprendidos"] else {}
        self.ultimo_aprendizaje = time.time()
        with self.lock:
            self.linea = linea
            self.est = Estimador(linea, self.cfg, self.aprendidos)
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
        if time.time() - self.ultimo_aprendizaje > 3600 and self.cfg["usar_tiempos_aprendidos"]:
            self.aprendidos = aprender_tiempos()
            self.est.aprendidos = self.aprendidos
            self.ultimo_aprendizaje = time.time()
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
            "con_posicion": sum(1 for t in res["trenes"] if t["fuente"] != "horario" and not t["fin"]),
            "en_circulacion": sum(1 for t in res["trenes"] if not t["fin"] and t["j0"] > 0 or t["parado"] and not t["fin"] and t["con_datos"]),
            "tramos_aprendidos": len(self.aprendidos),
            "modo_cruces": self.cfg["cruces"],
            "ts": round(time.time()),
        })
        with self.lock:
            self.res = res

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

    def bucle(self):
        while True:
            try:
                self.ciclo()
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
            time.sleep(self.cfg["intervalo_consulta_s"] if self.linea else 10)


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

        def _enviar(self, cuerpo, tipo, codigo=200):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, obj):
            self._enviar(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            ruta = self.path.split("?")[0]
            if ruta == "/api/estado":
                with app.lock:
                    return self._json(app.res or {"cargando": True})
            if ruta == "/api/linea":
                with app.lock:
                    if app.linea_json:
                        app.linea_json["url_movil"] = app.url_movil
                    return self._json(app.linea_json or {"cargando": True, "error": app.error_inicio})
            if ruta == "/api/precision":
                return self._json(Precision.estadisticas())
            if ruta == "/api/ping":
                return self._json({"ok": True, "hora": datetime.now().strftime("%H:%M:%S")})
            if ruta == "/api/manana":
                q = parse_qs(urlparse(self.path).query)
                try:
                    return self._json(app.manana(q.get("o", [""])[0], q.get("d", [""])[0]))
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e), "trenes": []})
            if ruta == "/":
                ruta = "/index.html"
            fichero = os.path.normpath(os.path.join(WEB, ruta.lstrip("/")))
            if not fichero.startswith(os.path.normpath(WEB)) or not os.path.isfile(fichero):
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
