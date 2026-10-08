# -*- coding: utf-8 -*-
"""Aprender de los errores: calibración por estación y sentido, salidas medidas, trenes fantasma."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asturias.trenes import historial as H  # noqa: E402


def fila(dia, trip, stop, h, nuestra, real, tipo=None, bruta=None):
    r = [dia, trip, stop, str(h), "%.2f" % nuestra, "%.2f" % nuestra, "%.2f" % real]
    if tipo:
        r += [tipo, H.linea_de(trip), str(int(H.num_servicio(trip)) % 2), "%.2f" % (bruta if bruta is not None else nuestra)]
    return r


class TestFilas(unittest.TestCase):
    def test_formato_viejo_y_nuevo(self):
        a = H.fila_precision(fila("2026-10-01", "2072J70237C4", "05209", 5, 600, 601))
        self.assertEqual((a["tipo"], a["linea"], a["par"]), ("a", "C4", "1"))
        b = H.fila_precision(fila("2026-10-01", "2072J70238C5a", "05209", 10, 600, 601, "d", 599.5))
        self.assertEqual((b["tipo"], b["linea"], b["par"], b["bruta"]), ("d", "C5a", "0", 599.5))
        self.assertIsNone(H.fila_precision(["fecha", "trip"]))

    def test_medianoche(self):
        # visto en producción el 01/10: 23:36 frente a 00:14 del día siguiente
        x = H.fila_precision(["2026-10-01", "2072J70237C4", "05209", "5", "13.41", "1416.00", "14.65"])
        self.assertAlmostEqual(x["adif"], -24.0, places=1)


class TestCalibracion(unittest.TestCase):
    def test_sentidos_opuestos_no_se_anulan(self):
        filas = []
        for d in range(10):
            for h in H.HORIZ_CAL:
                # Pravia: los impares llegan 1,6 min más tarde de lo estimado; los pares, 1,6 antes
                filas.append(H.fila_precision(fila("2026-10-0%d" % d, "2070M70301C4", "05237", h, 600, 601.6)))
                filas.append(H.fila_precision(fila("2026-10-0%d" % d, "2070M70302C4", "05237", h, 600, 598.4)))
        cal = H.aprender_calibracion(filas=filas)
        impar = H.correccion(cal, "a", "C4", "05237", "1", 10)
        par = H.correccion(cal, "a", "C4", "05237", "0", 10)
        self.assertGreater(impar, 0.6)
        self.assertLess(par, -0.6)
        # y el propio tren, que lo hace siempre, aún más
        self.assertGreater(H.correccion(cal, "a", "C4", "05237", "1", 10, "70301"), 1.0)
        self.assertAlmostEqual(H.correccion(cal, "a", "C4", "05237", "1", 0.0), 0.0)   # llegando: no se toca
        self.assertEqual(H.correccion(None, "a", "C4", "05237", "1", 10), 0.0)

    def test_pocos_datos_van_a_la_media_de_la_linea(self):
        filas = [H.fila_precision(fila("2026-10-01", "2070M70301C4", "05237", 5, 600, 603))]
        cal = H.aprender_calibracion(filas=filas)
        self.assertNotIn("a|C4|05237|1", cal["k"])
        self.assertLess(abs(H.correccion(cal, "a", "C4", "05237", "1", 5)), 1.0)

    def test_incidencias_no_ensenan(self):
        filas = [H.fila_precision(fila("2026-10-0%d" % d, "2070M70301C4", "05237", 5, 600, 655)) for d in range(9)]
        cal = H.aprender_calibracion(filas=filas)
        self.assertEqual(H.correccion(cal, "a", "C4", "05237", "1", 5), 0.0)


class RT:
    def __init__(self):
        self.parados, self.salida_vista = {}, {}

    def cuando(self, tid, stop, parado):
        return self.parados.get((tid, stop))


class Lin:
    est = ["A", "B", "C"]


class TestPrecisionSalidas(unittest.TestCase):
    def test_mide_salidas_y_llegadas(self):
        tmp = tempfile.mkdtemp()
        viejo = H.HIST
        H.HIST = tmp
        try:
            p = H.Precision()
            t = {"id": "2078X70201C4", "num": "70201", "linea": "C4", "fin": False, "con_datos": True,
                 "j0": 1, "parado": False, "k": [0, 1, 2], "para": [True, True, True],
                 "est_a": [None, 604.0, 610.0], "est_d": [600.0, 605.0, None],
                 "adif_a": [600, 603, 609], "adif_d": [600, 603, None], "_bruta_a": [None, 603.5, 609.5],
                 "_bruta_d": [600.0, 604.5, None]}
            p.registrar({"ahora": 600.0, "trenes": [t]}, Lin())
            rt = RT()
            rt.parados[("2078X70201C4", "B")] = (604.5, True)
            rt.salida_vista[("2078X70201C4", "B")] = (605.6, True)
            filas = p.observar(rt)
            tipos = sorted((f[2], f[7]) for f in filas)
            self.assertIn(("B", "a"), tipos)
            self.assertIn(("B", "d"), tipos)
            llegada = next(f for f in filas if f[2] == "B" and f[7] == "a")
            self.assertEqual(llegada[10], "603.50")          # se guarda la estimación sin calibrar
            est = H.Precision.estadisticas(dias=1, desde="")
            self.assertIn("C4", est["lineas"])
            self.assertIsNotNone(est["salidas"]["total"])
        finally:
            H.HIST = viejo


class TestSalidaVista(unittest.TestCase):
    def test_detecta_salida(self):
        from asturias.trenes.tiemporeal import TiempoReal
        cfg = {"datos_viejos_s": 600, "intervalo_consulta_s": 15}
        rt = TiempoReal(cfg)
        t0 = 1791400000

        def lectura(ts, stop, estado):
            pos = {"header": {"timestamp": str(ts)}, "entity": [{"vehicle": {
                "trip": {"tripId": "2078X70201C4"}, "stopId": stop, "currentStatus": estado, "timestamp": str(ts)}}]}
            rt.cargar(pos, {"entity": []}, ahora_ts=ts)
        lectura(t0, "B", "INCOMING_AT")
        lectura(t0 + 20, "B", "STOPPED_AT")
        lectura(t0 + 40, "B", "INCOMING_AT")           # Renfe a veces vuelve a «entrando»: no es salir
        self.assertNotIn(("2078X70201C4", "B"), rt.salida_vista)
        lectura(t0 + 60, "B", "STOPPED_AT")
        lectura(t0 + 80, "B", "IN_TRANSIT_TO")
        m, fiable = rt.salida_vista[("2078X70201C4", "B")]
        self.assertTrue(fiable)
        from asturias.util import ahora_min
        self.assertAlmostEqual(m, ahora_min(t0 + 70), places=2)


if __name__ == "__main__":
    unittest.main()
