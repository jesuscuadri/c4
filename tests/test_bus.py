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
        k = t["_ip"][4]                              # tramo del trazado que sale de la 5ª parada
        a, b = t["_pts"][k], t["_pts"][k + 1]
        v = {"bus": "900", "linea": "1", "linea_id": 1, "destino": t["destino"],
             "lat": a[0] + (b[0] - a[0]) * 0.5, "lon": a[1] + (b[1] - a[1]) * 0.5}
        self.e._situar(v, 1000.0)
        self.assertEqual(v["proxima"]["id"], t["_ids"][5])
        self.assertIsNotNone(v["rumbo"])            # sentido del recorrido aunque aún no se haya movido
        self.assertEqual(v["vel"], 0.0)             # sin dos lecturas no hay velocidad
        # 30 s después está un poco más adelante: ya hay velocidad y camino para seguir moviéndolo
        c = t["_pts"][k + 2] if t["_ip"][5] > k + 1 else b
        v2 = dict(v, lat=(b[0] + c[0]) / 2, lon=(b[1] + c[1]) / 2)
        self.e._situar(v2, 1030.0)
        self.assertGreater(v2["vel"], 0)
        self.assertGreaterEqual(len(v2["camino"]), 2)
        self.assertEqual(v2["quieto_s"], 0)
        # dos lecturas más sin moverse (más de 45 s): parado de verdad
        v3 = dict(v2)
        self.e._situar(v3, 1080.0)
        self.assertEqual(v3["vel"], 0.0)
        self.assertEqual(v3["quieto_s"], 50)

    def test_trazado_por_calles(self):
        # cada recorrido tiene su trazado por las calles y cada parada cae sobre él
        from c4.util import distancia_km
        con = [t for t in self.e.trayectos if t.get("forma")]
        self.assertGreater(len(con), 0.9 * len(self.e.trayectos))
        for t in con:
            self.assertEqual(len(t["_ip"]), len(t["_ids"]))
            for sid, i in zip(t["_ids"], t["_ip"]):
                p = self.e.paradas_d[sid]
                self.assertLess(distancia_km((p["lat"], p["lon"]), t["_pts"][i]), 0.08)

    def test_nombres(self):
        self.assertEqual(_limpia("MUSEL-HOSPITAL DE JOVE-POL. PORCEYO Y ZARRACINA"),
                         "Musel-Hospital de Jove-Pol. Porceyo y Zarracina")
        self.assertEqual(_limpia("LAUREDAL - CAMPUS (VIESQUES)"), "Lauredal - Campus (Viesques)")
        self.assertEqual(_limpia("JOP"), "JOP")


if __name__ == "__main__":
    unittest.main()
