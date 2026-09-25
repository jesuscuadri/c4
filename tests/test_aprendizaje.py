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



class TestRetrasoTipico(unittest.TestCase):
    """Aprender con cuánto retraso sale cada servicio y usarlo para los trenes que aún no han salido."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._hist = historial.HIST
        historial.HIST = self.tmp

    def tearDown(self):
        historial.HIST = self._hist

    def _obs(self, fecha, filas):
        ruta = os.path.join(self.tmp, "obs_%s.csv" % fecha)
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "trip", "stop", "estado", "retraso_min"])
            w.writerows(filas)

    def test_resumir_dia(self):
        # el 70252 está parado en A, sale con +3 hacia B (tarda 4 min), luego hacia C
        t0 = 1_800_000_000
        self._obs("20260920", [
            [t0, "2064X70252C4", "A", "STOPPED_AT", "0.0"],
            [t0 + 60, "2064X70252C4", "A", "STOPPED_AT", "3.0"],
            [t0 + 120, "2064X70252C4", "B", "IN_TRANSIT_TO", "3.0"],
            [t0 + 360, "2064X70252C4", "B", "STOPPED_AT", "3.0"],
            [t0 + 420, "2064X70252C4", "C", "IN_TRANSIT_TO", "3.5"],
        ])
        tramos, salidas = historial.resumir_dia(os.path.join(self.tmp, "obs_20260920.csv"))
        self.assertEqual(salidas, {"70252": 3.0})
        self.assertIn("A|B", tramos)
        # salida y llegada se sitúan a mitad de camino entre lecturas: (t0+90) → (t0+240) = 2,5 min
        self.assertAlmostEqual(tramos["A|B"][0], 2.5, places=1)

    def test_aprender_salidas_mediana(self):
        res = {"tramos": {}, "salidas": {
            "202609%02d" % d: {"70252": r, "70311": 0.0} for d, r in zip(range(10, 15), [3, 4, 3, 2, 5])}}
        s = historial.aprender_salidas(res=res)
        self.assertEqual(s.get("70252"), 3.0)
        self.assertNotIn("70311", s)          # sale en hora: no hay nada que corregir

    def test_pocos_dias_no_basta(self):
        res = {"tramos": {}, "salidas": {"20260910": {"70252": 5.0}, "20260911": {"70252": 6.0}}}
        self.assertEqual(historial.aprender_salidas(res=res), {})

    def test_resumen_sobrevive_sin_obs(self):
        # se guarda el resumen, se borran los obs (reinicio del servidor) y se sigue sabiendo
        res = {"tramos": {"20260910": {"A|B": [4.0] * 6}}, "salidas": {}}
        historial.guardar_resumen(res)
        self.assertAlmostEqual(historial.aprender_tiempos()[("A", "B")], 4.0)


class TestAplicaRetrasoTipico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(CONFIG_DEFECTO)
        with open(DATOS, encoding="utf-8") as f:
            cls.L = Linea(cls.cfg, json.load(f))

    def test_tren_futuro_sale_con_su_retraso_habitual(self):
        ahora = 6 * 60
        v = min((v for v in self.L.viajes.values() if v.sd[0] > ahora + 30), key=lambda v: v.sd[0])
        base = Estimador(self.L, self.cfg).calcular(RTVacio(), ahora=ahora)
        adj = Estimador(self.L, self.cfg, salidas={v.num: 5.0}).calcular(RTVacio(), ahora=ahora)
        tb = next(t for t in base["trenes"] if t["id"] == v.id)
        ta = next(t for t in adj["trenes"] if t["id"] == v.id)
        self.assertAlmostEqual(ta["est_d"][0] - tb["est_d"][0], 5.0, places=1)   # sale 5 min más tarde
        # llega más tarde, aunque no necesariamente 5 min: si iba a esperar en un cruce, esa espera se acorta
        self.assertGreater(ta["est_a"][-1], tb["est_a"][-1])
        self.assertIn("suele salir con +5", ta["situacion"])
        self.assertEqual(ta["retraso"], 0.0)          # la «app oficial» sigue sin retraso (comparación justa)


if __name__ == "__main__":
    unittest.main(verbosity=2)
