# -*- coding: utf-8 -*-
"""Rutas con trenes + autobuses del Consorcio + andar (asturias/rutas.py), con una red pequeña inventada."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asturias import rutas  # noqa: E402
from asturias.trenes import planificador as P  # noqa: E402

# dos estaciones a ~20 km; un pueblo (Villabus) a ~25 km de la estación A, con dos paradas
EST = P.Estaciones([
    {"id": "A", "nombre": "Estación A", "lat": 43.50, "lon": -5.80, "lineas": ["C1"]},
    {"id": "B", "nombre": "Estación B", "lat": 43.50, "lon": -5.55, "lineas": ["C1"]},
])


def tren(id_, sale, llega, j0=0):
    return {"id": id_, "num": id_, "linea": "C1", "destino": "Estación B", "k": [0, 1], "j0": j0,
            "para": [True, True], "est_d": [sale, None], "est_a": [None, llega], "fin": False,
            "cancelado": False, "retraso": 0, "con_datos": True, "motivos": [], "via": None}


def red_bus():
    # S1, S2: Villabus · S3: junto a la estación A (≈150 m) · S9: otro pueblo lejos
    paradas = {
        "S1": ["Plaza", "Villabus", 43.70, -5.80],
        "S2": ["Iglesia", "Villabus", 43.701, -5.801],
        "S3": ["Estación", "Ciudad A", 43.5012, -5.8008],
        "S9": ["Final", "Otro", 43.30, -5.80],
    }
    return {
        "red": "interurbano", "tipo": "interurbano", "nombre": "Interurbanos", "titulo": "Interurbanos de Asturias",
        "color": "#6d28d9", "fecha": "2026-10-08",
        "lineas": {"L1": {"codigo": "L1", "nombre": "Villabus – Ciudad A", "color": "#123456", "operador": "ALSA"},
                   "L2": {"codigo": "L2", "nombre": "Villabus – Otro (sin bajar en Ciudad A)", "color": "#654321"}},
        "paradas": paradas,
        "variantes": [
            {"linea": "L1", "destino": "Ciudad A", "paradas": ["S1", "S2", "S3"], "forma": None},
            # L2 pasa por S3 pero allí no deja bajar a quien sube en Villabus
            {"linea": "L2", "destino": "Otro", "paradas": ["S1", "S3", "S9"], "forma": None,
             "perm": [[2, 2], [2, 2], 0]},
        ],
        "patrones": [[0, 2, 30], [0, 20, 60]],
        # L1 sale a las 10:00 (llega a S3 10:30); L2 sale a las 9:50 (pasaría por S3 a las 10:10)
        "viajes": [[0, 600, 0], [1, 590, 1]],
        "alias": {},
    }


class TestRutas(unittest.TestCase):
    def setUp(self):
        self.g = rutas.Grafo(EST, {"interurbano": red_bus()})

    def test_pueblo_se_encuentra(self):
        p = self.g.buscar_localidad("villa")
        self.assertEqual(p["nombre"], "Villabus")
        self.assertEqual(len(p["nodos"]), 2)
        self.assertTrue(any(s["tipo"] == "localidad" for s in self.g.sugerir("villab")))

    def test_bus_y_tren(self):
        origen = self.g.punto_localidad("Villabus")
        destino = {"lat": 43.5005, "lon": -5.5505, "nombre": "Casa", "tipo": "gps"}
        res = {"trenes": [tren("T1", 640, 670), tren("T0", 625, 655)]}
        plan = rutas.planificar(self.g, res, None, origen, destino, ahora=580)
        self.assertTrue(plan["ok"], plan)
        tipos = [e["tipo"] for e in plan["etapas"]]
        self.assertIn("autobus", tipos)
        self.assertIn("tren", tipos)
        bus = next(e for e in plan["etapas"] if e["tipo"] == "autobus")
        self.assertEqual(bus["linea"], "L1")             # L2 no deja bajar en la estación
        self.assertEqual(bus["sale_hm"], "10:00")
        tr = next(e for e in plan["etapas"] if e["tipo"] == "tren")
        self.assertEqual(tr["num"], "T1")                # el T0 (10:25) sale antes de que llegue el bus (10:30)
        self.assertEqual(plan["llega_hm"], "11:10")
        self.assertTrue(plan["con_bus"])

    def test_sin_nada_hoy(self):
        origen = self.g.punto_localidad("Villabus")
        destino = {"lat": 43.5005, "lon": -5.5505, "nombre": "Casa", "tipo": "gps"}
        plan = rutas.planificar(self.g, {"trenes": []}, None, origen, destino, ahora=1300)
        self.assertFalse(plan["ok"])
        self.assertIn("no quedan", plan["error"])

    def test_cambio_andando(self):
        # S3 y la estación A están a unos 150 m: se puede cambiar andando
        i3, ia = self.g.idx[("S", "S3")], self.g.idx[("T", 0)]
        self.assertTrue(any(j == ia for j, _ in self.g.pie[i3]))


if __name__ == "__main__":
    unittest.main()
