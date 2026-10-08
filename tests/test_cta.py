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


def zip_inter():
    """Como el GTFS del Consorcio: cada expedición partida en el viaje completo y un tramo por cada
    parada con reglas propias (subiendo en Oviedo no se puede bajar en Oviedo, etc.)."""
    f = io.BytesIO()
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("agency.txt", "agency_id,agency_name\n19,Automóviles Luarca SAU\n56,Villa Excursiones SA\n28,Tranvía\n")
        z.writestr("routes.txt", "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                 "4566,19,\"Ovi\",\"Oviedo-Gijón [Paradas]\",3\n78,56,\"L22\",\"L22 - Avilés-Pillarno\",3\n"
                                 "A1,28,L1,L1 - La Luz-Llaranes,3\n")
        z.writestr("calendar_dates.txt", "service_id,date,exception_type\nS,20261008,1\n")
        z.writestr("trips.txt", "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n"
                                "4566,S,X,Gijón,0,SH\n4566,S,X1,Gijón,0,\n4566,S,X2,Gijón,0,\n4566,S,X3,Gijón,0,\n"
                                "78,S,V,Pillarno,0,\nA1,S,U,Llaranes,0,\n")
        z.writestr("stops.txt", "stop_id,stop_name,stop_desc,stop_lat,stop_lon\n"
                                "O1,[OVIEDO/UVIÉU]  Estación Bus Oviedo [CTA 21470],,43.3650,-5.8530\n"
                                "T1,Melquiades Cabal,,43.3672,-5.8510\n"
                                "M1,[LUGONES/LLUGONES]  El Castro [CTA 02313],,43.4010,-5.8110\n"
                                "G1,[GIJÓN/XIXÓN]  Porceyo [CTA 00960],,43.5180,-5.7120\n"
                                "G2,[GIJÓN/XIXÓN]  Estación de autobuses [CTA 00784],,43.5370,-5.6740\n"
                                "A,[AVILÉS]  Plaza [CTA 1],,43.5560,-5.9240\nP,[PILLARNO]  Iglesia [CTA 2],,43.5800,-5.9600\n"
                                "U1,[AVILÉS]  La Luz [CTA 3],,43.55,-5.93\nU2,[AVILÉS]  Llaranes [CTA 4],,43.56,-5.90\n")
        st = "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
        horas = {"O1": "06:45:00", "T1": "06:47:00", "M1": "07:02:00", "G1": "07:37:00", "G2": "07:45:00"}
        def filas(tid, reglas):
            out = ""
            orden = ["O1", "T1", "M1", "G1", "G2"]
            for k, (s_, r) in enumerate(reglas):
                out += "%s,%s,%s,%s,%d,%s,%s\n" % (tid, horas[s_], horas[s_], s_, orden.index(s_) + 1, r[0], r[1])
            return out
        st += filas("X", [("O1", "10"), ("T1", "10"), ("M1", "00"), ("G1", "10"), ("G2", "00")])
        st += filas("X1", [("O1", "00"), ("T1", "11"), ("M1", "10"), ("G1", "10"), ("G2", "10")])
        st += filas("X2", [("T1", "00"), ("M1", "10"), ("G1", "10"), ("G2", "10")])
        st += filas("X3", [("G1", "00"), ("G2", "10")])
        st += "V,10:00:00,10:00:00,A,1,,\nV,10:20:00,10:20:00,P,2,,\nU,09:00:00,09:00:00,U1,1,,\nU,09:10:00,09:10:00,U2,2,,\n"
        z.writestr("stop_times.txt", st)
        z.writestr("shapes.txt", "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nSH,43.365,-5.853,1\nSH,43.45,-5.75,2\nSH,43.537,-5.674,3\n")
    f.seek(0)
    return f


class TestInterurbanos(unittest.TestCase):
    def setUp(self):
        self.d = cta.extraer("interurbano", date(2026, 10, 8), zip_cta=zip_inter())

    def test_un_autobus_por_expedicion(self):
        d = self.d
        self.assertEqual(d["tipo"], "interurbano")
        self.assertEqual(len(d["viajes"]), 2)                       # la expedición partida en 4 cuenta 1
        self.assertNotIn("U1", d["paradas"])                        # el urbano de Avilés, fuera
        v = next(v for v in d["variantes"] if v["linea"] == "4566")
        self.assertEqual(v["paradas"], ["O1", "T1", "M1", "G1", "G2"])
        self.assertEqual(v["destino"], "Gijón")
        self.assertTrue(v["forma"])

    def test_reglas_de_subida_y_bajada(self):
        v = next(v for v in self.d["variantes"] if v["linea"] == "4566")
        # subiendo en Oviedo, a partir de Lugones; en Lugones, a Gijón; en Porceyo, solo a la estación
        self.assertEqual(v["perm"], [[2, 4], [2, 4], [3, 4], [4, 4], 0])
        l22 = next(v for v in self.d["variantes"] if v["linea"] == "78")
        self.assertNotIn("perm", l22)

    def test_lineas_y_localidades(self):
        d = self.d
        self.assertEqual(d["lineas"]["4566"]["codigo"], "ALSA")
        self.assertEqual(d["lineas"]["4566"]["nombre"], "Oviedo – Gijón")
        self.assertEqual(d["lineas"]["4566"]["via"], "Paradas")
        self.assertEqual(d["lineas"]["78"]["codigo"], "L22")
        self.assertEqual(d["lineas"]["78"]["nombre"], "Avilés – Pillarno")
        self.assertEqual(d["paradas"]["O1"][:2], ["Estación Bus Oviedo", "Oviedo"])     # «OVIEDO/UVIÉU»
        self.assertEqual(d["paradas"]["T1"][1], "Oviedo")           # sin localidad: la de al lado
        self.assertEqual(d["paradas"]["G2"][1], "Gijón")


if __name__ == "__main__":
    unittest.main()
