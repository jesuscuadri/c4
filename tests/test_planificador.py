# -*- coding: utf-8 -*-
"""Pruebas del planificador puerta a puerta (bus urbano + tren C-4), sin red.

    python -m unittest discover tests
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4.emtusa import Emtusa  # noqa: E402
from c4.estimador import Estimador  # noqa: E402
from c4.linea import Linea  # noqa: E402
from c4 import planificador as P  # noqa: E402
from c4.util import CONFIG_DEFECTO  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "horario_C4_20260923.json")


class RTVacio:
    """Tiempo real vacío: solo horario, para pruebas deterministas."""
    pos = {}
    act = {}
    error = None
    avisos = []
    ts_feed = None

    def cuando(self, *a):
        return None


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(CONFIG_DEFECTO)
        with open(DATOS, encoding="utf-8") as f:
            cls.L = Linea(cls.cfg, json.load(f))
        cls.est = Estimador(cls.L, cls.cfg, {})
        cls.bus = Emtusa(activo=False)  # red de disco, sin tiempo real

    def plan(self, o, d, ahora):
        res = self.est.calcular(RTVacio(), ahora)
        po = P.geocodificar(o, self.L, self.bus, con_internet=False)
        pd = P.geocodificar(d, self.L, self.bus, con_internet=False)
        self.assertIsNotNone(po, "no se geocodificó el origen %r" % o)
        self.assertIsNotNone(pd, "no se geocodificó el destino %r" % d)
        return P.planificar(self.L, res, self.bus, po, pd, ahora, en_vivo=False)


class TestRed(Base):
    def test_red_cargada(self):
        self.assertTrue(self.bus.red_ok)
        self.assertGreater(len(self.bus.paradas_d), 500)
        self.assertGreater(len(self.bus.trayectos), 90)
        self.assertGreater(len(self.bus.lineas_d), 20)

    def test_nombres_normalizados(self):
        # ya no deben quedar en MAYÚSCULAS de EMTUSA
        n = self.bus.paradas_d[27]["nombre"]
        self.assertIn("Politecnica", n.replace("é", "e"))
        self.assertNotEqual(n, n.upper())

    def test_buscar_paradas(self):
        r = self.bus.buscar_paradas("politecnica")
        self.assertTrue(any(p["id"] in (27, 41, 192) for p in r))

    def test_resumen_red_contrato(self):
        red = self.bus.resumen_red()
        self.assertIn("lineas", red)
        self.assertIn("paradas", red)
        self.assertIn("trayectos", red)
        self.assertTrue(all("id" in l for l in red["lineas"]))


class TestGeocode(Base):
    def test_lugar_conocido(self):
        p = P.geocodificar("EPI Gijón", self.L, self.bus, con_internet=False)
        self.assertEqual(p["tipo"], "lugar")

    def test_estacion_tren(self):
        p = P.geocodificar("Candás", self.L, self.bus, con_internet=False)
        self.assertEqual(p["tipo"], "estacion")
        self.assertAlmostEqual(p["lat"], 43.5847, places=2)

    def test_parada_por_nombre(self):
        p = P.geocodificar("Hospital de Cabueñes", self.L, self.bus, con_internet=False)
        self.assertIn(p["tipo"], ("lugar", "parada"))


class TestRutasBus(Base):
    def test_directo_epi_a_zona_estacion(self):
        # debe existir algún bus directo desde el campus (27) hacia la zona de la estación
        est_ids = [704, 705, 706, 662, 663, 735]
        rutas = P._rutas_bus(self.bus, [27], est_ids, max_transbordos=1)
        self.assertTrue(rutas, "no encontró bus del campus a la estación")
        self.assertTrue(all("subir" in t and "bajar" in t for it in rutas for t in it))


class TestPlan(Base):
    def test_epi_a_candas_usa_tren(self):
        p = self.plan("EPI Gijón", "Candás", 13 * 60 + 10)
        self.assertTrue(p["ok"])
        tipos = [e["tipo"] for e in p["etapas"]]
        self.assertIn("tren", tipos)
        self.assertIn("bus", tipos)  # el acceso a la estación es en bus
        # la llegada es posterior a la salida
        self.assertGreater(p["llega"], p["sale"])

    def test_tren_llega_despues_de_salir(self):
        p = self.plan("EPI Gijón", "Candás", 13 * 60 + 10)
        tren = next(e for e in p["etapas"] if e["tipo"] == "tren")
        self.assertGreater(tren["llega"], tren["sale"])
        self.assertEqual(tren["desde"], "Gijón-Sanz Crespo")
        self.assertEqual(tren["hasta"], "Candás")

    def test_xivares_a_candas_solo_tren(self):
        p = self.plan("Xivares", "Candás", 7 * 60 + 45)
        self.assertTrue(p["ok"])
        self.assertEqual([e["tipo"] for e in p["etapas"]], ["tren"])

    def test_dentro_de_gijon_sin_tren(self):
        p = self.plan("El Molinón", "Plaza del Humedal", 18 * 60)
        self.assertTrue(p["ok"])
        self.assertTrue(p.get("solo_urbano"))
        self.assertNotIn("tren", [e["tipo"] for e in p["etapas"]])

    def test_reverso_candas_a_epi(self):
        p = self.plan("Candás", "EPI Gijón", 8 * 60 + 30)
        self.assertTrue(p["ok"])
        tipos = [e["tipo"] for e in p["etapas"]]
        self.assertEqual(tipos[0], "tren")          # primero el tren desde Candás
        self.assertIn("bus", tipos)                  # luego bus urbano al campus


if __name__ == "__main__":
    unittest.main(verbosity=2)
