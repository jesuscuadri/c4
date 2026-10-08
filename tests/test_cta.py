# -*- coding: utf-8 -*-
"""Autobuses del Consorcio (urbano de Avilés): lectura del GTFS."""
import io
import os
import sys
import unittest
import zipfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4 import cta  # noqa: E402


def zip_cta():
    f = io.BytesIO()
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("routes.txt", "route_id,agency_id,route_short_name,route_long_name,route_type,route_color\n"
                                 "A1,28,L1,L1 - La Luz-Llaranes,3,\nA2,28,Pie,Pie - Avilés-Avilés [Circular],3,\n"
                                 "X9,51,A,A - Oviedo-Oviedo,3,\n")
        z.writestr("calendar_dates.txt", "service_id,date,exception_type\nS1,20261008,1\nS2,20261009,1\nS3,20261008,1\n")
        z.writestr("trips.txt", "route_id,service_id,trip_id,trip_headsign,shape_id\n"
                                "A1,S1,T1,L1 - Llaranes,SH1\nA1,S1,T2,L1 - Llaranes,SH1\nA1,S2,T3,L1 - Llaranes,SH1\n"
                                "A2,S1,T4,,\nX9,S3,T5,Oviedo,\n")
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n"
                                "P1,[AVILÉS]  La Luz [CTA 04001],43.55,-5.93\n"
                                "P2,[AVILÉS]  Cristalería [CTA 04317],43.56,-5.92\n"
                                "P3,[LLARANES|Llaranes]  Plaza [CTA 04400],43.57,-5.90\n"
                                "P9,[OVIEDO]  Uría [CTA 01000],43.36,-5.85\n")
        z.writestr("stop_times.txt", "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                   "T1,07:00:00,07:00:00,P1,1\nT1,07:05:00,07:05:00,P2,2\nT1,07:12:00,07:12:00,P3,3\n"
                   "T2,08:00:00,08:00:00,P1,1\nT2,08:05:00,08:05:00,P2,2\nT2,08:12:00,08:12:00,P3,3\n"
                   "T3,09:00:00,09:00:00,P1,1\nT3,09:05:00,09:05:00,P2,2\n"
                   "T4,24:30:00,24:30:00,P2,1\nT4,24:40:00,24:40:00,P1,2\n"
                   "T5,10:00:00,10:00:00,P9,1\n")
        z.writestr("shapes.txt", "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                   "SH1,43.55,-5.93,1\nSH1,43.555,-5.925,2\nSH1,43.56,-5.92,3\nSH1,43.57,-5.90,4\n")
    f.seek(0)
    return f


class TestParadas(unittest.TestCase):
    def test_nombres(self):
        self.assertEqual(cta.limpia_parada("[AVILÉS]  Cristalería [CTA 04317]"), ("Cristalería", "Avilés"))
        self.assertEqual(cta.limpia_parada("[PIEDRASBLANCAS|Piedras Blancas]  Eysines [CTA 03075]"),
                         ("Eysines", "Piedras Blancas"))
        self.assertEqual(cta.limpia_parada("Sin formato"), ("Sin formato", ""))


class TestExtraer(unittest.TestCase):
    def setUp(self):
        self.d = cta.extraer("aviles", date(2026, 10, 8), zip_cta=zip_cta())

    def test_solo_avilés_y_el_dia(self):
        d = self.d
        self.assertEqual(set(d["lineas"]), {"L1", "Pie"})
        self.assertNotIn("P9", d["paradas"])                       # Oviedo es de otra agencia
        self.assertEqual(len(d["viajes"]), 3)                      # T3 es de otro día
        self.assertEqual(d["paradas"]["P3"][:2], ["Plaza", "Llaranes"])

    def test_variantes_patrones_y_horas(self):
        d = self.d
        l1 = [v for v in d["variantes"] if v["linea"] == "L1"]
        self.assertEqual(len(l1), 1)
        self.assertEqual(l1[0]["destino"], "Llaranes")
        self.assertEqual(l1[0]["paradas"], ["P1", "P2", "P3"])
        self.assertTrue(l1[0]["forma"])
        self.assertEqual(len(d["patrones"]), 2)                    # T1 y T2 comparten patrón
        salidas = [v[1] for v in d["viajes"]]
        self.assertEqual(salidas, sorted(salidas))
        self.assertIn(24 * 60 + 30, salidas)                       # pasada la medianoche
        buho = next(v for v in d["variantes"] if v["linea"] == "Pie")
        self.assertEqual(buho["destino"], "La Luz")                # sin rótulo: la última parada
        self.assertEqual(d["lineas"]["Pie"]["codigo"], "Búho")
        self.assertTrue(d["lineas"]["L1"]["color"].startswith("#"))


DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


class TestOviedo(unittest.TestCase):
    """Muestra real del GTFS del Consorcio (TUA, líneas A y C, 08/10/2026)."""

    def test_nombres_en_limpio(self):
        d = cta.extraer("oviedo", date(2026, 10, 8), zip_cta=os.path.join(DATOS, "cta_oviedo_muestra.zip"))
        self.assertEqual(d["lineas"]["A"]["nombre"], "Centro Asturiano – Llamaquique")
        self.assertEqual(d["lineas"]["C"]["nombre"], "Facultades – Lugones")
        destinos = {(v["linea"], v["destino"]) for v in d["variantes"]}
        self.assertIn(("A", "Llamaquique"), destinos)
        self.assertIn(("C", "Lugones"), destinos)
        self.assertNotIn("BUH", d["lineas"])                       # el Búho no sale los jueves
        self.assertEqual(d["paradas"]["2051"][:2], ["Oviedo 41 Avda", "Avda. de Oviedo"])   # la calle
        self.assertEqual(len(d["viajes"]), 24)


class TestCodigos(unittest.TestCase):
    def test_codigos_recortados_del_consorcio(self):
        self.assertEqual(cta.codigo_linea({"route_short_name": "L1.", "route_long_name": "L1.1 San Andrés-La Hueria"}), "L1.1")
        self.assertEqual(cta.codigo_linea({"route_short_name": "Mie", "route_long_name": "Mieres-San Andrés [Curuxa]"}), "Curuxa")
        self.assertEqual(cta.codigo_linea({"route_short_name": "L2 ", "route_long_name": "L2 Mieres-Cenera"}), "L2")
        self.assertEqual(cta.codigo_linea({"route_short_name": "BUH", "route_long_name": "BUHO SAN CLAUDIO-BUHO CUATRO CAÑOS"}), "BUH")
        self.assertEqual(cta._nombre_linea(["BUHO SAN CLAUDIO-BUHO CUATRO CAÑOS"], "BUH"), "San Claudio – Cuatro Caños")
        self.assertEqual(cta._bonito(cta._quita_codigo("L2 Mieres", "L2")), "Mieres")
        self.assertEqual(cta._bonito(cta._quita_codigo("A2-CENTRO ASTURIANO", "A")), "Centro Asturiano")
        self.assertEqual(cta._titulo("MIERES DEL CAMÍN"), "Mieres del Camín")


if __name__ == "__main__":
    unittest.main()
