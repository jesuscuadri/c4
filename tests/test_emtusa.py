# -*- coding: utf-8 -*-
"""Pruebas del módulo de autobús (EMTUSA): red de disco + tiempo real simulado."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from c4.emtusa import Emtusa, _limpia  # noqa: E402


class FakeBus(Emtusa):
    """Como Emtusa pero con las llegadas en directo simuladas (no toca la red)."""
    def _get(self, ruta, autenticado=True, timeout=12):
        if ruta.startswith("paradas/parada/"):
            idp = ruta.rsplit("/", 1)[-1]
            return {"idparada": int(idp), "descripcion": "ESTACIÓN FERROCARRIL",
                    "latitud": "43.5375", "longitud": "-5.6752",
                    "llegadas": [
                        {"linea": {"idlinea": 1, "codigo": "1", "descripcion": "CERILLERO - HOSPITAL DE CABUEÑES",
                                   "colorhex": "00A0E1"},
                         "trayecto": {"destino": "HOSPITAL DE CABUEÑES", "direccion": 1},
                         "minutos": 4, "distancia": 900, "horaActualizacion": "21:45:00"},
                        {"linea": {"idlinea": 18, "codigo": "18", "descripcion": "NUEVO GIJÓN - HOSPITAL DE CABUEÑES",
                                   "colorhex": "5C4033"},
                         "trayecto": {"destino": "VEGA", "direccion": 2},
                         "minutos": 12, "distancia": 3200, "horaActualizacion": "21:45:00"}]}
        raise AssertionError(ruta)


class TestRedDisco(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bus = Emtusa(activo=False)  # red de disco, sin tiempo real

    def test_red_cargada(self):
        self.assertTrue(self.bus.red_ok)
        self.assertGreater(len(self.bus.paradas()), 500)

    def test_cercanas_a_la_estacion(self):
        cerca = self.bus.cercanas(43.5377, -5.6760, radio_m=550)
        self.assertTrue(cerca)
        self.assertLess(cerca[0]["metros"], 300)
        nombres = " ".join(c["nombre"] for c in cerca).lower()
        self.assertTrue("estación" in nombres or "estacion" in nombres or "sanz crespo" in nombres or "juzgados" in nombres)

    def test_buscar_paradas(self):
        r = self.bus.buscar_paradas("cabueñes")
        self.assertTrue(any("cabue" in p["nombre"].lower() for p in r))

    def test_paradas_conocen_sus_lineas(self):
        # la parada del campus (EPI) debe listar alguna línea
        self.assertIn(27, self.bus.paradas_d)
        self.assertTrue(self.bus.paradas_d[27]["lineas"])


class TestTiempoReal(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus(activo=True)

    def test_llegadas_en_directo(self):
        info = self.bus.llegadas(706)
        self.assertEqual([l["minutos"] for l in info["llegadas"]], [4, 12])
        self.assertEqual(info["llegadas"][0]["destino"], "Hospital de Cabueñes")
        self.assertEqual(info["llegadas"][0]["color"], "#00A0E1")

    def test_proximos_por_linea(self):
        prox = self.bus.proximos_por_linea(706)
        self.assertEqual(prox.get("1"), 4)
        self.assertEqual(prox.get("18"), 12)

    def test_enlace(self):
        e = self.bus.enlace(43.5377, -5.6760, radio_m=550)
        self.assertTrue(e["disponible"])
        self.assertTrue(e["paradas"])
        self.assertTrue(e["paradas"][0]["llegadas"])

    def test_desactivado_no_da_llegadas(self):
        b = FakeBus(activo=False)
        self.assertTrue(b.red_ok)                       # la red sigue disponible
        self.assertIn("error", b.llegadas(706))          # pero sin tiempo real


class TestLimpia(unittest.TestCase):
    def test_mayusculas(self):
        self.assertEqual(_limpia("HOSPITAL DE CABUEÑES"), "Hospital de Cabueñes")

    def test_siglas(self):
        self.assertEqual(_limpia("JOP"), "JOP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
