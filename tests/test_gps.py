# -*- coding: utf-8 -*-
"""Pruebas de la posición GPS: cuánto le queda al tren según dónde está sobre la vía."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4.estimador import Estimador  # noqa: E402
from c4.linea import Linea  # noqa: E402
from c4.util import CONFIG_DEFECTO  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "horario_C4_20260923.json")


class RT:
    def __init__(self, pos):
        self.pos = pos; self.act = {}; self.error = None; self.avisos = []; self.ts_feed = None
    def cuando(self, *a): return None


def punto_en_km(L, km):
    """Coordenada sobre el trazado a ese km."""
    acum, pts = L.trazado_km, L.trazado
    for i in range(len(pts) - 1):
        if acum[i] <= km <= acum[i + 1]:
            f = (km - acum[i]) / max(1e-9, acum[i + 1] - acum[i])
            return (pts[i][0] + f * (pts[i + 1][0] - pts[i][0]), pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))
    return tuple(pts[-1])


class TestGPS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(CONFIG_DEFECTO)
        with open(DATOS, encoding="utf-8") as f:
            cls.L = Linea(cls.cfg, json.load(f))
        L = cls.L
        # un tren y un tramo largo entre dos paradas comerciales
        for v in L.viajes.values():
            js = [j for j in range(len(v.k)) if v.para[j]]
            for a, b in zip(js, js[1:]):
                if b == a + 1 and abs(L.km[v.k[b]] - L.km[v.k[a]]) > 2.0 and a > 0:
                    cls.v, cls.jp, cls.j = v, a, b
                    return
        raise unittest.SkipTest("sin tramo largo")

    def _estado(self, frac, desviado_km=0.0, est=None):
        L, v = self.L, self.v
        ka, kb = L.km[v.k[self.jp]], L.km[v.k[self.j]]
        lat, lon = punto_en_km(L, ka + frac * (kb - ka))
        lat += desviado_km / 110.57
        # así lo da Renfe: «IN_TRANSIT_TO» con la parada de la que acaba de salir
        p = {"stop": L.est[v.k[self.jp]], "estado": "IN_TRANSIT_TO", "ts": None, "via": None,
             "lat": lat, "lon": lon, "coord_desde": None}
        est = est or Estimador(L, self.cfg)
        ahora = v.sd[self.jp] + 1
        return est._estado_inicial(v, RT({v.id: p}), ahora), ahora, est

    def test_proyeccion_sobre_la_via(self):
        L = self.L
        lat, lon = punto_en_km(L, 5.0)
        km, d = L.proyectar(lat, lon)
        self.assertAlmostEqual(km, 5.0, delta=0.05)
        self.assertLess(d, 0.02)

    def test_mas_avanzado_llega_antes(self):
        e1, ahora, _ = self._estado(0.2)
        e2, _, _ = self._estado(0.8)
        self.assertEqual(e1["fuente"], "GPS")
        self.assertGreater(e1["llegada0"], e2["llegada0"])
        marcha = Estimador(self.L, self.cfg)._recorrido_teorico(self.v, self.jp, self.j)
        self.assertLessEqual(e1["llegada0"] - ahora, marcha + 1e-6)
        self.assertAlmostEqual(e2["progreso"], 0.8, delta=0.05)

    def test_perfil_arranque_y_frenada(self):
        # a mitad de camino le queda la mitad del tiempo (perfil simétrico)
        e, ahora, _ = self._estado(0.5)
        marcha = Estimador(self.L, self.cfg)._recorrido_teorico(self.v, self.jp, self.j)
        self.assertAlmostEqual(e["llegada0"] - ahora, marcha / 2, delta=0.05)
        # recién arrancado le queda casi todo
        e0, ahora, _ = self._estado(0.06)
        self.assertEqual(e0["fuente"], "GPS")
        self.assertGreater(e0["llegada0"] - ahora, marcha * 0.8)

    def test_lejos_de_la_via_no_se_usa(self):
        e, _, _ = self._estado(0.5, desviado_km=2.0)
        self.assertNotEqual(e["fuente"], "GPS")

    def test_no_retrocede_si_renfe_da_una_coordenada_vieja(self):
        e1, _, est = self._estado(0.7)
        e2, _, _ = self._estado(0.1, est=est)
        self.assertAlmostEqual(e2["llegada0"], e1["llegada0"], delta=0.01)

    def test_desactivable(self):
        cfg = dict(self.cfg, usar_gps=False)
        e, _, _ = self._estado(0.5, est=Estimador(self.L, cfg))
        self.assertEqual(e["fuente"], "posición (aprox.)")

    def test_coordenada_pegada_a_una_estacion_no_se_usa(self):
        # Renfe a veces pone la coordenada de una estación en vez de la del tren
        L, v = self.L, self.v
        lat, lon = L.coord[v.k[self.j]]
        p = {"stop": L.est[v.k[self.jp]], "estado": "IN_TRANSIT_TO", "ts": None, "via": None,
             "lat": lat, "lon": lon, "coord_desde": None}
        e = Estimador(L, self.cfg)._estado_inicial(v, RT({v.id: p}), v.sd[self.jp] + 1)
        self.assertNotEqual(e["fuente"], "GPS")
        self.assertEqual(e["j0"], self.j)          # va hacia la SIGUIENTE parada


if __name__ == "__main__":
    unittest.main(verbosity=2)
