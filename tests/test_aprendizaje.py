# -*- coding: utf-8 -*-
"""Pruebas del aprendizaje a partir de errores: sesgos por estación y su aplicación acotada."""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4 import historial  # noqa: E402
from c4.estimador import Estimador  # noqa: E402
from c4.linea import Linea  # noqa: E402
from c4.util import CONFIG_DEFECTO  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "horario_C4_20260923.json")


class RTVacio:
    pos = {}; act = {}; error = None; avisos = []; ts_feed = None
    def cuando(self, *a): return None


class TestAprenderSesgos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._hist = historial.HIST
        historial.HIST = self.tmp

    def tearDown(self):
        historial.HIST = self._hist

    def _escribir(self, filas):
        ruta = os.path.join(self.tmp, "precision_20260920.csv")
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["fecha", "trip", "stop", "horizonte", "nuestra", "adif", "real"])
            for stop, nuestra, real in filas:
                w.writerow(["2026-09-20", "T1", stop, 10, "%.2f" % nuestra, "%.2f" % (nuestra + 1), "%.2f" % real])

    def test_sesgo_sistematico_detectado(self):
        # en S1 el tren llega siempre ~1.2 min más tarde de lo estimado (8 muestras)
        filas = [("S1", 100.0, 101.2) for _ in range(8)]
        # en S2 el error es pequeño y sin sesgo claro -> se descarta
        filas += [("S2", 100.0, 100.1), ("S2", 100.0, 99.9)] * 4
        # en S3 hay sesgo pero pocas muestras -> se descarta
        filas += [("S3", 100.0, 103.0) for _ in range(3)]
        self._escribir(filas)
        s = historial.aprender_sesgos(dias=30, minimo=6)
        self.assertIn("S1", s)
        self.assertAlmostEqual(s["S1"], 1.2, places=1)
        self.assertNotIn("S2", s)
        self.assertNotIn("S3", s)

    def test_acotado(self):
        filas = [("S9", 100.0, 105.0) for _ in range(10)]   # sesgo grande (5 min, dentro del filtro)
        self._escribir(filas)
        s = historial.aprender_sesgos(dias=30, minimo=6, cap=2.0)
        self.assertLessEqual(abs(s["S9"]), 2.0)             # queda acotado


class TestAplicaSesgo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(CONFIG_DEFECTO)
        with open(DATOS, encoding="utf-8") as f:
            cls.L = Linea(cls.cfg, json.load(f))

    def _est_a_en(self, res, k):
        """Máxima estimación de llegada a la estación índice k entre los trenes que la usan."""
        vals = []
        for t in res["trenes"]:
            if k in t["k"]:
                j = t["k"].index(k)
                if t["est_a"][j] is not None and t["para"][j]:
                    vals.append(t["est_a"][j])
        return vals

    def test_correccion_acotada_y_con_efecto(self):
        k = 5
        sid = self.L.est[k]
        base = Estimador(self.L, self.cfg).calcular(RTVacio(), ahora=6 * 60)
        # sesgo grande en esa estación: debe empujar la llegada, pero acotado por sesgo_cap
        adj = Estimador(self.L, self.cfg, {}, {sid: 5.0}).calcular(RTVacio(), ahora=6 * 60)
        vb, va = self._est_a_en(base, k), self._est_a_en(adj, k)
        self.assertTrue(vb and va)
        # comparamos la media de llegadas a esa estación
        delta = sum(va) / len(va) - sum(vb) / len(vb)
        self.assertGreater(delta, 0.3)                       # tiene efecto
        self.assertLessEqual(delta, self.cfg["sesgo_cap"] + 0.15)  # pero acotado

    def test_sin_sesgos_no_cambia(self):
        a = Estimador(self.L, self.cfg).calcular(RTVacio(), ahora=6 * 60)
        b = Estimador(self.L, self.cfg, {}, {}).calcular(RTVacio(), ahora=6 * 60)
        self.assertEqual(self._est_a_en(a, 5), self._est_a_en(b, 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
