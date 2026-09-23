# -*- coding: utf-8 -*-
"""Pruebas del enlace con el autobús (EMTUSA), sin tocar la red."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from c4.emtusa import Emtusa, _bonito

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


class FakeBus(Emtusa):
    def _get(self, ruta, autenticado=True, timeout=12):
        if ruta == "paradas/todasParadas":
            return json.load(open(os.path.join(D, "emtusa_paradas.json"), encoding="utf-8"))
        if ruta.startswith("paradas/parada/"):
            idp = ruta.rsplit("/", 1)[-1]
            return {"idparada": int(idp), "descripcion": "ESTACIÓN FERROCARRIL", "latitud": "43.5375", "longitud": "-5.6752",
                    "llegadas": [
                        {"linea": {"codigo": "1", "descripcion": "CERILLERO - HOSPITAL DE CABUEÑES", "colorhex": "00A0E1"},
                         "trayecto": {"destino": "HOSPITAL DE CABUEÑES", "direccion": 1}, "minutos": 4, "distancia": 900,
                         "horaActualizacion": "21:45:00"},
                        {"linea": {"codigo": "16", "descripcion": "ESTACIÓN FERROCARRIL - VEGA", "colorhex": "5C4033"},
                         "trayecto": {"destino": "VEGA", "direccion": 2}, "minutos": 12, "distancia": 3200,
                         "horaActualizacion": "21:45:00"}]}
        raise AssertionError(ruta)


class Test(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()

    def test_paradas(self):
        p = self.bus.paradas()
        self.assertEqual(len(p), 8)
        self.assertTrue(all("lat" in x and "lon" in x for x in p))

    def test_cercanas_a_la_estacion(self):
        # Gijón-Sanz Crespo (estación de tren)
        cerca = self.bus.cercanas(43.5377, -5.6760, radio_m=550)
        nombres = [c["nombre"] for c in cerca]
        self.assertTrue(nombres[0].startswith("Estación Ferroc"))  # la 706/662, la más cercana
        self.assertLess(cerca[0]["metros"], 200)
        # Muselín está lejos, no debe salir
        self.assertNotIn("Muselín", nombres)

    def test_llegadas_en_directo(self):
        info = self.bus.llegadas(706)
        self.assertEqual([l["minutos"] for l in info["llegadas"]], [4, 12])
        self.assertEqual(info["llegadas"][0]["destino"], "Hospital de Cabueñes")
        self.assertEqual(info["llegadas"][0]["color"], "#00A0E1")

    def test_enlace(self):
        e = self.bus.enlace(43.5377, -5.6760, radio_m=550)
        self.assertTrue(e["disponible"])
        self.assertTrue(e["paradas"])
        self.assertTrue(e["paradas"][0]["llegadas"])
        self.assertIn("metros", e["paradas"][0])

    def test_bonito(self):
        self.assertEqual(_bonito("HOSPITAL DE CABUEÑES"), "Hospital de Cabueñes")
        self.assertEqual(_bonito("JOP"), "JOP")

    def test_desactivado(self):
        b = FakeBus(activo=False)
        self.assertEqual(b.paradas(), [])
        self.assertEqual(b.enlace(43.5, -5.6)["paradas"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
