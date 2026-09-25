# -*- coding: utf-8 -*-
"""Pruebas de la persistencia en GitHub con un GitHub FALSO en local (no toca internet).

Simula el caso real: el servidor aprende, guarda, Render lo reinicia con el disco vacío
y, al arrancar, lo recupera todo.
"""
import base64
import csv
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4 import historial, persistencia  # noqa: E402


class GitHubFalso:
    """Lo justo de la API de GitHub: ramas, refs y contents (GET/PUT con sha)."""

    def __init__(self):
        self.ramas = {"main": "sha-main"}
        self.archivos = {}      # (rama, ruta) -> (sha, bytes)
        self.puts = 0
        self.forzar_conflicto = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _resp(self, cod, obj):
                b = json.dumps(obj).encode()
                self.send_response(cod)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer TOKEN":
                    return self._resp(401, {"message": "Bad credentials"})
                p = self.path
                if p.startswith("/repos/u/r/branches/"):
                    rama = p.rsplit("/", 1)[1]
                    return self._resp(200 if rama in fake.ramas else 404, {})
                if p == "/repos/u/r":
                    return self._resp(200, {"default_branch": "main"})
                if p.startswith("/repos/u/r/git/ref/heads/"):
                    rama = p.rsplit("/", 1)[1]
                    return self._resp(200, {"object": {"sha": fake.ramas[rama]}})
                if p.startswith("/repos/u/r/contents/"):
                    ruta, rama = p[len("/repos/u/r/contents/"):].split("?ref=")
                    if (rama, ruta) not in fake.archivos:
                        return self._resp(404, {})
                    sha, datos = fake.archivos[(rama, ruta)]
                    return self._resp(200, {"sha": sha, "content": base64.b64encode(datos).decode()})
                self._resp(404, {})

            def do_POST(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/repos/u/r/git/refs":
                    fake.ramas[cuerpo["ref"].split("/")[-1]] = cuerpo["sha"]
                    return self._resp(201, {})
                self._resp(404, {})

            def do_PUT(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                ruta = self.path[len("/repos/u/r/contents/"):]
                clave = (cuerpo["branch"], ruta)
                if cuerpo["branch"] not in fake.ramas:
                    return self._resp(404, {"message": "Branch not found"})
                if fake.forzar_conflicto:
                    fake.forzar_conflicto -= 1
                    return self._resp(409, {"message": "sha mismatch"})
                actual = fake.archivos.get(clave)
                if actual and cuerpo.get("sha") != actual[0]:
                    return self._resp(409, {"message": "sha mismatch"})
                fake.puts += 1
                nuevo = "sha%d" % fake.puts
                fake.archivos[clave] = (nuevo, base64.b64decode(cuerpo["content"]))
                self._resp(200, {"content": {"sha": nuevo}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def parar(self):
        self.srv.shutdown()


class TestPersistencia(unittest.TestCase):
    def setUp(self):
        self.gh = GitHubFalso()
        self._api, self._hist = persistencia.API, historial.HIST
        persistencia.API = self.gh.url
        historial.HIST = tempfile.mkdtemp()

    def tearDown(self):
        persistencia.API, historial.HIST = self._api, self._hist
        self.gh.parar()

    def _aprender_algo(self):
        historial.guardar_resumen({"tramos": {"20260920": {"A|B": [4.0] * 6}},
                                   "salidas": {"202609%02d" % d: {"70252": 3.0} for d in range(15, 20)}})
        ruta = os.path.join(historial.HIST, "precision_20260920.csv")
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(persistencia.CABECERA_PRECISION)
            w.writerow(["2026-09-20", "T1", "S1", 10, "100.00", "101.00", "101.20"])

    def test_inactivo_sin_token(self):
        a = persistencia.Almacen(token="", repo="")
        self.assertFalse(a.activo)
        self.assertFalse(a.guardar())
        self.assertFalse(a.cargar())

    def test_guarda_y_recupera_tras_reinicio(self):
        self._aprender_algo()
        a = persistencia.Almacen(token="TOKEN", repo="u/r")
        self.assertTrue(a.guardar())
        self.assertIn("datos", self.gh.ramas)                        # creó la rama aparte
        self.assertNotIn(("main", persistencia.ARCHIVO), self.gh.archivos)   # nunca toca main
        # «reinicio de Render»: disco vacío
        historial.HIST = tempfile.mkdtemp()
        self.assertEqual(historial.aprender_tiempos(), {})
        b = persistencia.Almacen(token="TOKEN", repo="u/r")
        self.assertTrue(b.cargar())
        self.assertAlmostEqual(historial.aprender_tiempos()[("A", "B")], 4.0)
        self.assertEqual(historial.aprender_salidas().get("70252"), 3.0)
        self.assertTrue(os.path.exists(os.path.join(historial.HIST, "precision_20260920.csv")))
        est = historial.Precision.estadisticas(dias=4000)
        self.assertEqual(est["semana"]["total"]["n"], 1)

    def test_no_duplica_al_recuperar_dos_veces(self):
        self._aprender_algo()
        a = persistencia.Almacen(token="TOKEN", repo="u/r")
        a.guardar()
        a.cargar(); a.cargar()
        with open(os.path.join(historial.HIST, "precision_20260920.csv"), encoding="utf-8") as f:
            self.assertEqual(len(list(csv.reader(f))), 2)            # cabecera + 1 fila, sin duplicar

    def test_no_sube_si_no_ha_cambiado(self):
        self._aprender_algo()
        a = persistencia.Almacen(token="TOKEN", repo="u/r")
        a.guardar(); a.guardar()
        self.assertEqual(self.gh.puts, 1)

    def test_reintenta_si_cambia_el_sha(self):
        self._aprender_algo()
        a = persistencia.Almacen(token="TOKEN", repo="u/r")
        self.gh.forzar_conflicto = 1
        self.assertTrue(a.guardar())
        self.assertEqual(self.gh.puts, 1)

    def test_token_malo_no_rompe_nada(self):
        a = persistencia.Almacen(token="MALO", repo="u/r")
        self.assertFalse(a.cargar())
        self.assertIn("GitHub", a.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
