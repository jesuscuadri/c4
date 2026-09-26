# -*- coding: utf-8 -*-
"""Pruebas del modelo con el horario real del 23/09/2026.

    python -m unittest discover tests
"""
import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c4.estimador import Estimador, viajes_entre  # noqa: E402
from c4.linea import Linea  # noqa: E402
from c4.tiemporeal import TiempoReal  # noqa: E402
from c4.util import CONFIG_DEFECTO  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "horario_C4_20260923.json")
MEDIANOCHE = datetime.combine(date.today(), datetime.min.time()).timestamp()


def h(txt):
    hh, mm = txt.split(":")
    return int(hh) * 60 + int(mm)


def ts(minuto):
    return MEDIANOCHE + minuto * 60


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DATOS, encoding="utf-8") as f:
            cls.datos = json.load(f)
        cls.cfg = dict(CONFIG_DEFECTO)
        cls.L = Linea(cls.cfg, cls.datos)

    def tren(self, num):
        return next(v for v in self.L.viajes.values() if v.num == num)

    def lectura(self, rt, ahora, posiciones, retrasos=()):
        """posiciones: [(num, stop, estado)], retrasos: [(num, stop, minutos)]"""
        P = {"header": {"timestamp": str(int(ts(ahora)))},
             "entity": [{"vehicle": {"trip": {"tripId": self.tren(x[0]).id}, "stopId": x[1], "currentStatus": x[2],
                                     "timestamp": str(int(ts(ahora))),
                                     "vehicle": {"label": "C4-%s%s" % (x[0], "-PLATF.(%s)" % x[3] if len(x) > 3 else "")}}}
                        for x in posiciones]}
        U = {"entity": []}
        for n, s, r in retrasos:
            v = self.tren(n)
            U["entity"].append({"tripUpdate": {"trip": {"tripId": v.id}, "delay": int(r * 60),
                                               "stopTimeUpdate": [{"stopId": s, "arrival": {
                                                   "time": str(int(ts(v.sa[v.stop_j[s]] + r)))}}]}})
        rt.cargar(P, U, ahora_ts=ts(ahora))

    def calcular(self, rt, ahora, **cfg):
        c = dict(self.cfg)
        c.update(cfg)
        return Estimador(self.L, c).calcular(rt, ahora)

    def fila(self, res, num, origen, destino):
        o, d = self.L.buscar(origen), self.L.buscar(destino)
        for f in viajes_entre(res, o, d, limite=20):
            if f["tren"]["num"] == num:
                return f
        raise AssertionError("tren %s no encontrado" % num)


class TestLinea(Base):
    def test_estaciones_y_apartaderos(self):
        self.assertEqual(len(self.L.est), 32)
        self.assertEqual(self.L.nombre[0], "Gijón-Sanz Crespo")
        nombres = {self.L.nombre[k] for k in self.L.apartaderos}
        for n in ("Veriña", "Perlora", "Candás", "Trasona", "Piedras Blancas", "Pravia"):
            self.assertIn(n, nombres)
        self.assertNotIn("Xivares", nombres)

    def test_trazado(self):
        self.assertGreater(len(self.L.trazado), 1000)
        self.assertTrue(all(b >= a for a, b in zip(self.L.km, self.L.km[1:])))
        self.assertTrue(50 < self.L.km[-1] < 80, self.L.km[-1])  # Gijón–Cudillero ~ 60 km

    def test_cruce_veriña(self):
        k = self.L.buscar("Veriña")
        pares = {(a.num, b.num) for a, b, kk in self.L.cruces if kk == k}
        self.assertIn(("70210", "70303"), pares)


class TestEscenarios(Base):
    def test_sin_tiempo_real_coincide_con_horario(self):
        rt = TiempoReal(self.cfg)
        res = self.calcular(rt, h("05:00"))
        # Sin datos en directo todo va según el horario, salvo donde el propio horario es imposible
        # en vía única: los hace coincidir entre El Parador y Soto del Barco, y uno tiene que esperar
        # al otro en Soto (así se vio en directo el 26/09). Esas esperas tienen que estar explicadas.
        for t in res["trenes"]:
            con_motivo = {m["j"] for m in t["motivos"]}
            for j in range(len(t["k"]) - 1):
                if abs(t["est_d"][j] - t["prog_d"][j]) > 0.55:   # (esperas de medio minuto no se explican)
                    self.assertTrue(any(i <= j for i in con_motivo), (t["num"], j))
        for t in res["trenes"]:
            for m in t["motivos"]:      # solo cruces que el horario hace imposibles, y cortos
                self.assertEqual(m["tipo"], "cruce")
                self.assertLessEqual(m["min"], 5)
        # llegadas: nunca más tarde que el horario (salvo tras esas esperas); antes sí, donde el
        # horario tiene holgura (el tren llega antes y espera a su hora de salida)
        dev = max(t["est_a"][j] - t["prog_a"][j] for t in res["trenes"] for j in range(len(t["k"])) if not t["motivos"])
        self.assertLessEqual(dev, 0.55)     # (tramos de 0 min y esperas de medio minuto)

    def test_ejemplo_xivares_gijon(self):
        """El 70303 (hacia Gijón) debe esperar en Veriña al 70210, que sale 7 min tarde de Gijón.
        (A las 10:03 el 70210, que salía a las 09:56, sigue parado en Gijón. El «retraso» que da Renfe
        para un tren en su estación de origen no sirve: se mide por el tiempo que lleva allí.)"""
        rt = TiempoReal(self.cfg)
        self.lectura(rt, h("10:03"), [("70303", "05208", "STOPPED_AT"), ("70210", "15410", "STOPPED_AT")],
                     [("70303", "05208", 0)])
        res = self.calcular(rt, h("10:03"))
        f = self.fila(res, "70303", "Xivares", "Gijón")
        t, jd = f["tren"], f["jd"]
        self.assertAlmostEqual(t["adif_a"][jd], h("10:18"), delta=0.6)   # lo que diría la app
        self.assertTrue(any("Veriña" in m["texto"] and m["con"] == "70210" for m in f["motivos"]))
        # la espera se nota: llega más tarde que si el 70210 hubiera salido a su hora
        rt2 = TiempoReal(self.cfg)
        self.lectura(rt2, h("10:03"), [("70303", "05208", "STOPPED_AT"), ("70210", "05208", "IN_TRANSIT_TO")],
                     [("70303", "05208", 0)])
        sin_espera = self.fila(self.calcular(rt2, h("10:03")), "70303", "Xivares", "Gijón")["tren"]["est_a"][jd]
        self.assertGreater(t["est_a"][jd], sin_espera + 1)

    def test_tren_contrario_retrasado_retiene_al_que_sale(self):
        rt = TiempoReal(self.cfg)
        self.lectura(rt, h("09:59"), [("70303", "05210", "IN_TRANSIT_TO"), ("70210", "15410", "STOPPED_AT")],
                     [("70303", "05209", 10), ("70210", "15410", 0)])
        res = self.calcular(rt, h("09:59"))
        f = self.fila(res, "70210", "Gijón", "Avilés")
        self.assertTrue(any(m["tipo"] == "cruce" and m["con"] == "70303" for m in f["motivos"]))
        # la espera por el cruce retrasa su salida de ese apartadero respecto al horario
        m = next(m for m in f["motivos"] if m["tipo"] == "cruce" and m["con"] == "70303")
        t = f["tren"]
        self.assertGreater(t["est_d"][m["j"]], t["prog_d"][m["j"]] + 1)

    def test_cruce_trasladado(self):
        """Si uno ya pasó el apartadero del cruce programado, el cruce se hace en el siguiente."""
        rt = TiempoReal(self.cfg)
        self.lectura(rt, h("10:10"), [("70303", "05209", "STOPPED_AT"), ("70210", "05208", "IN_TRANSIT_TO")],
                     [("70303", "05209", 12), ("70210", "05208", 0)])
        res = self.calcular(rt, h("10:10"))
        c = [x for x in res["cruces"] if x["ida"]["num"] == "70210" and x["vuelta"]["num"] == "70303"]
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["estacion"], "Perlora")
        self.assertEqual(c[0]["info"], "movido")

    def test_retraso_por_posicion_sin_datos_de_retraso(self):
        """Renfe muchas veces no da retraso para la C-4: se calcula por la posición."""
        rt = TiempoReal(self.cfg)
        self.lectura(rt, h("08:05"), [("70251", "05209", "STOPPED_AT")])           # Perlora
        # Renfe: «IN_TRANSIT_TO» + la parada de la que acaba de salir
        self.lectura(rt, h("08:07"), [("70251", "05209", "IN_TRANSIT_TO")])        # sale hacia Xivares
        res = self.calcular(rt, h("08:07"))
        t = next(t for t in res["trenes"] if t["num"] == "70251")
        jx = t["k"].index(self.L.buscar("Xivares"))
        self.assertAlmostEqual(t["est_a"][jx], h("08:07") + 4, delta=0.6)       # 4 min de marcha
        self.assertGreater(t["retraso"], 5)

    def test_datos_viejos_se_ignoran(self):
        rt = TiempoReal(self.cfg)
        P = {"header": {"timestamp": str(int(ts(h("08:13"))))},
             "entity": [{"vehicle": {"trip": {"tripId": self.tren("70251").id}, "stopId": "05208",
                                     "currentStatus": "STOPPED_AT", "timestamp": str(int(ts(h("08:13"))))}}]}
        rt.cargar(P, {"entity": []}, ahora_ts=ts(h("16:00")))
        self.assertEqual(rt.pos, {})
        self.assertEqual(rt.calidad(ahora_ts=ts(h("16:00"))), "congelado")

    def test_foto_real_0813(self):
        """Posiciones reales del feed de Renfe a las 08:13:07 del 23/09/2026."""
        foto = [("70208", "05203", "STOPPED_AT"), ("70306", "15410", "STOPPED_AT", "12"),
                ("70303", "05245", "STOPPED_AT"), ("70302", "05243", "IN_TRANSIT_TO"),
                ("70203", "15410", "INCOMING_AT", "12"), ("70251", "05208", "STOPPED_AT"),
                ("70250", "05227", "IN_TRANSIT_TO"), ("70301", "05232", "IN_TRANSIT_TO")]
        ahora = h("08:13") + 7 / 60
        rt = TiempoReal(self.cfg)
        self.lectura(rt, ahora, foto)
        res = self.calcular(rt, ahora)
        por_num = {t["num"]: t for t in res["trenes"]}
        for x in foto:
            n = x[0]
            t = por_num[n]
            self.assertNotEqual(t["fuente"], "horario", n)
            futuros = [x for x in t["est_a"][t["j0"]:] if x is not None]
            self.assertTrue(all(x >= ahora - 0.01 for x in futuros), n)
            self.assertTrue(all(b >= a - 1e-6 for a, b in zip(futuros, futuros[1:])), n)
        # el 70251 va ~12 min tarde en Xivares; el 70208 (hacia Avilés) se cruza con él en Veriña
        self.assertGreater(por_num["70251"]["retraso"], 10)
        m = [x for x in por_num["70208"]["motivos"] if x["tipo"] == "cruce"]
        self.assertTrue(any(x["con"] == "70251" for x in m), por_num["70208"]["motivos"])
        # el 70306 espera en la vía 12 de Gijón al 70203, que entra en esa misma vía
        rot = [x for x in por_num["70306"]["motivos"] if x["tipo"] == "rotacion"]
        self.assertTrue(all(x["con"] == "70203" for x in rot))
        # en Cudillero, el 70303 no puede salir hasta que llegue el 70302 (vía única)
        m = por_num["70303"]["motivos"]
        self.assertTrue(any(x["con"] == "70302" and x["tipo"] in ("tramo", "cruce") for x in m), m)

    def test_tramo_en_cabecera(self):
        """Sin datos: en Cudillero el horario ya separa llegada y salida, no debe añadir espera."""
        k = self.L.buscar("Cudillero")
        self.assertTrue(any(b.num == "70303" and a.num == "70302" and kk == k for b, a, kk in self.L.ordenes))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRetrocesos(Base):
    def test_renfe_hace_saltar_el_tren_atras(self):
        """Visto en directo: Renfe alterna «parado en Veriña» y «parado en Tremañes» cada 15 s.
        El tren no puede ir marcha atrás: se mantiene en Veriña."""
        v = self.tren("70210")                     # Gijón → Avilés
        ver, tre = self.L.est[self.L.buscar("Veriña")], self.L.est[self.L.buscar("Tremañes")]
        rt = TiempoReal(self.cfg)
        est = Estimador(self.L, dict(self.cfg))
        t0 = v.sa[v.stop_j[ver]] + 1
        self.lectura(rt, t0, [("70210", ver, "STOPPED_AT")])
        est.calcular(rt, t0)
        self.lectura(rt, t0 + 0.25, [("70210", tre, "STOPPED_AT")])
        res = est.calcular(rt, t0 + 0.25)
        t = next(x for x in res["trenes"] if x["num"] == "70210")
        self.assertIn("Veriña", t["situacion"])


class TestSinDatosRenfe(Base):
    def test_si_renfe_deja_de_mandar_un_tren_sigue_con_su_ultimo_retraso(self):
        """Sábado 26/09: el tiempo real de Renfe quedó vacío en toda España. Un tren que iba con
        retraso no pasa de golpe a «en hora»: sigue con el último retraso visto."""
        v = self.tren("70251")
        rt = TiempoReal(self.cfg)
        est = Estimador(self.L, dict(self.cfg))
        self.lectura(rt, h("08:05"), [("70251", "05209", "STOPPED_AT")])     # Perlora con ~12 min
        r1 = est.calcular(rt, h("08:05"))
        t1 = next(x for x in r1["trenes"] if x["num"] == "70251")
        self.assertGreater(t1["retraso"], 5)
        P = {"header": {"timestamp": str(int(ts(h("08:07"))))}}             # fichero vacío
        rt.cargar(P, {"entity": []}, ahora_ts=ts(h("08:07")))
        self.assertEqual(rt.calidad(ahora_ts=ts(h("08:07"))), "sin_posiciones")
        r2 = est.calcular(rt, h("08:07"))
        t2 = next(x for x in r2["trenes"] if x["num"] == "70251")
        self.assertFalse(t2["con_datos"])
        self.assertEqual(t2["ultimo_dato"], 2)
        self.assertAlmostEqual(t2["retraso"], t1["retraso"], delta=0.01)
        jg = t2["k"].index(self.L.buscar("Gijón"))
        self.assertGreater(t2["est_a"][jg], v.sa[v.stop_j[self.L.est[self.L.buscar("Gijón")]]] + 5)


class TestRetrasoRenfeEnOrigen(Base):
    def test_contador_de_renfe_en_origen_no_retrasa_la_salida(self):
        """25/09: el 70254 esperaba en Gijón para salir a su hora y Renfe decía +7 (su «retraso» es un
        contador que sube mientras el tren está en el andén). Salió con +1,4. La app oficial dice +7;
        nosotros, a su hora."""
        v = self.tren("70210")
        rt = TiempoReal(self.cfg)
        antes = v.sd[0] - 1
        self.lectura(rt, antes, [("70210", "15410", "STOPPED_AT")], [("70210", "15410", 7)])
        res = self.calcular(rt, antes)
        t = next(x for x in res["trenes"] if x["num"] == "70210")
        self.assertAlmostEqual(t["est_d"][0], v.sd[0], delta=0.2)
        self.assertAlmostEqual(t["adif_d"][0], v.sd[0] + 7, delta=0.2)   # la columna «app oficial» sigue siendo la de Renfe
