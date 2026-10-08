# -*- coding: utf-8 -*-
"""Trenes regionales y de larga distancia por la red de Cercanías."""
import io
import os
import sys
import unittest
import zipfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4 import gtfs  # noqa: E402
from c4.tiemporeal import TiempoReal  # noqa: E402
from c4.historial import num_servicio, linea_de  # noqa: E402


def zip_ld():
    f = io.BytesIO()
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("routes.txt", "route_id,agency_id,route_short_name,route_long_name,route_desc,route_type\n"
                                 "R1,1071,REGIONAL,,,2\nR2,1071,AVLO,,,2\nR3,1071,MD,,,2\n")
        z.writestr("calendar.txt", "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                                   "S1,1,1,1,1,1,1,1,20261001,20261031\nS2,1,1,1,1,1,1,1,20261001,20261031\n")
        z.writestr("calendar_dates.txt", "service_id,date,exception_type\nS2,20261008,2\n")
        z.writestr("trips.txt", "route_id,service_id,trip_id,trip_headsign,trip_short_name\n"
                                "R1,S1,7182112026-10-07,,71821\nR1,S2,7182312026-10-07,,71823\n"
                                "R2,S1,0412112026-10-07,,04121\nR3,S1,3712112026-10-07,,37121\n")
        z.writestr("stops.txt", "stop_id,stop_code,stop_name,stop_desc,stop_lat,stop_lon\n"
                                "15211,,Oviedo,,43.36,-5.85\n05509,,El Berrón,,43.38,-5.70\n"
                                "05533,,Infiesto,,43.35,-5.37\n05543,,Arriondas,,43.39,-5.19\n"
                                "05571,,Llanes,,43.42,-4.76\n15410,,Gijón,,43.54,-5.68\n15122,,Pola de Lena,,43.16,-5.83\n")
        z.writestr("stop_times.txt", "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                   "7182112026-10-07,11:10:00,11:10:00,15211,1\n7182112026-10-07,11:34:00,11:35:00,05509,2\n"
                   "7182112026-10-07,12:35:00,12:37:00,05533,3\n7182112026-10-07,13:08:00,13:09:00,05543,4\n"
                   "7182112026-10-07,14:16:00,14:16:00,05571,5\n"
                   "7182312026-10-07,18:39:00,18:39:00,15211,1\n7182312026-10-07,19:03:00,19:05:00,05509,2\n"
                   "0412112026-10-07,14:17:00,14:19:00,15122,1\n0412112026-10-07,15:21:00,15:21:00,15410,2\n"
                   "3712112026-10-07,14:17:00,14:19:00,15122,1\n3712112026-10-07,15:21:00,15:21:00,15410,2\n")
    f.seek(0)
    return f


class TestExtraer(unittest.TestCase):
    def test_solo_la_parte_de_la_red_y_sin_duplicados(self):
        red = {"15211", "05509", "05533", "15410", "15122"}
        d = gtfs.extraer_regionales({}, date(2026, 10, 8), red, zip_ld=zip_ld())
        self.assertIn("7182112026-10-07", d["viajes"])
        self.assertNotIn("7182312026-10-07", d["viajes"])           # quitado ese día (calendar_dates)
        self.assertEqual([s for s, _, _ in d["viajes"]["7182112026-10-07"]], ["15211", "05509", "05533"])
        self.assertEqual(d["tipos"]["7182112026-10-07"][:2], ["REGIONAL", "71821"])
        self.assertEqual(d["tipos"]["7182112026-10-07"][3], "Llanes")   # el destino de verdad
        # el AVLO y el MD de la misma hora son el mismo tren: solo uno
        self.assertEqual(sum(1 for t in d["viajes"] if t[:5] in ("04121", "37121")), 1)


class TestNumeros(unittest.TestCase):
    def test_ids_de_larga_distancia(self):
        self.assertEqual(num_servicio("7182112026-10-07"), "71821")
        self.assertEqual(num_servicio("2078X70208C4"), "70208")
        self.assertEqual(linea_de("7182112026-10-07"), "R")


class TestAlias(unittest.TestCase):
    def test_tiempo_real_de_larga_distancia_a_su_viaje_de_cercanias(self):
        rt = TiempoReal({"datos_viejos_s": 600, "intervalo_consulta_s": 15})
        rt.alias_ld = {"71821": "2079J71821C6"}
        ent = [{"vehicle": {"trip": {"tripId": "7182112026-10-07"}, "stopId": "05509"}},
               {"vehicle": {"trip": {"tripId": "0412112026-10-07"}, "stopId": "15410"}}]
        out = rt._alias(ent, "vehicle")
        self.assertEqual(out[0]["vehicle"]["trip"]["tripId"], "2079J71821C6")
        self.assertEqual(out[1]["vehicle"]["trip"]["tripId"], "0412112026-10-07")
        self.assertEqual(ent[0]["vehicle"]["trip"]["tripId"], "7182112026-10-07")   # sin tocar el original


if __name__ == "__main__":
    unittest.main()
