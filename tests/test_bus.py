# -*- coding: utf-8 -*-
"""Autobuses: situar cada bus en su recorrido (rumbo, próxima parada, velocidad) y nombres."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4.emtusa import Emtusa, _limpia  # noqa: E402


class TestBus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Emtusa()

    def _tray(self, linea):
        return next(t for t in self.e.trayectos if t["linea"] == linea and len(t["_pts"]) > 10)

    def test_situar_en_su_recorrido(self):
        t = self._tray(1)
        a, b = t["_pts"][4], t["_pts"][5]
        v = {"bus": "900", "linea": "1", "linea_id": 1, "destino": t["destino"],
             "lat": a[0] + (b[0] - a[0]) * 0.3, "lon": a[1] + (b[1] - a[1]) * 0.3}
        self.e._situar(v, 1000.0)
        self.assertEqual(v["proxima"]["id"], t["_ids"][5])
        self.assertIsNotNone(v["rumbo"])            # sentido del recorrido aunque aún no se haya movido
        self.assertEqual(v["vel"], 0.0)             # sin dos lecturas no hay velocidad
        # 30 s después está un poco más adelante: ya hay velocidad y camino para seguir moviéndolo
        v2 = dict(v, lat=a[0] + (b[0] - a[0]) * 0.8, lon=a[1] + (b[1] - a[1]) * 0.8)
        self.e._situar(v2, 1030.0)
        self.assertGreater(v2["vel"], 0)
        self.assertGreaterEqual(len(v2["camino"]), 2)
        self.assertEqual(v2["quieto_s"], 0)
        # dos lecturas más sin moverse (más de 45 s): parado de verdad
        v3 = dict(v2)
        self.e._situar(v3, 1080.0)
        self.assertEqual(v3["vel"], 0.0)
        self.assertEqual(v3["quieto_s"], 50)

    def test_nombres(self):
        self.assertEqual(_limpia("MUSEL-HOSPITAL DE JOVE-POL. PORCEYO Y ZARRACINA"),
                         "Musel-Hospital de Jove-Pol. Porceyo y Zarracina")
        self.assertEqual(_limpia("LAUREDAL - CAMPUS (VIESQUES)"), "Lauredal - Campus (Viesques)")
        self.assertEqual(_limpia("JOP"), "JOP")


if __name__ == "__main__":
    unittest.main()
