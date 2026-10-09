# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from asturias import app as A  # noqa: E402


def t(ret, con=True, fin=False, canc=False, linea="C4"):
    return {"linea": linea, "retraso": ret, "con_datos": con, "fin": fin, "cancelado": canc}


class TestPerturbacion(unittest.TestCase):
    def test_un_tren_muy_retrasado_en_marcha_avisa(self):
        self.assertIsNotNone(A.perturbacion({"trenes": [t(0), t(17)]}))

    def test_solo_cuentan_las_lineas_del_viaje(self):
        res = {"trenes": [t(17, linea="RO"), t(0)]}
        self.assertIsNone(A.perturbacion(res, {"C4"}))
        self.assertIn("RO", A.perturbacion(res, {"RO", "C4"}))

    def test_no_avisa_si_no_es_real(self):
        self.assertIsNone(A.perturbacion({"trenes": [t(0), t(5), t(17, con=False), t(20, fin=True), t(20, canc=True)]}))
        self.assertIsNone(A.perturbacion(None))
        self.assertIsNone(A.perturbacion({"trenes": []}))


class TestSinTiempoReal(unittest.TestCase):
    def test_todo_por_horario(self):
        en_marcha = {"linea": "C4", "con_datos": False, "fin": False, "j0": 3}
        self.assertTrue(A.sin_tiempo_real({"trenes": [en_marcha]}, {"C4"}))
        self.assertFalse(A.sin_tiempo_real({"trenes": [en_marcha, dict(en_marcha, con_datos=True)]}, {"C4"}))
        self.assertFalse(A.sin_tiempo_real({"trenes": [dict(en_marcha, j0=0)]}, {"C4"}))     # aún no ha salido nadie
        self.assertFalse(A.sin_tiempo_real({"trenes": [en_marcha]}, {"C1"}))                  # otra línea
        self.assertFalse(A.sin_tiempo_real(None, {"C4"}))


if __name__ == "__main__":
    unittest.main()
