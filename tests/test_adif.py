# -*- coding: utf-8 -*-
import datetime
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from asturias.trenes import adif  # noqa: E402

AVISO = ("09/10/2026 - Se están produciendo retrasos en línea C-4 de Cercanías de Asturias debido a un robo de cable "
         "entre VERIÑA y GIJON-SANZ CRESPO. Se está trabajando para solucionarla lo antes posible.")
PAGINA = ('<table><tr><td>13:27</td><td>Cudillero</td><td>C4</td><td>12</td></tr>'
          '<tr><td><a href="https://www.adif.es/estado-de-la-red" title="Estado de la Red"></a>' + AVISO + '</td><td></td></tr>'
          '<tr><td>13:42</td><td>Avil&eacute;s</td><td>C4</td></tr><tr><td><a></a>' + AVISO + '</td></tr>'
          '<tr><td>05/10/2026 - Aviso de hace días de la línea C-1 que ya no vale para nada</td></tr></table>')
HOY = datetime.date(2026, 10, 9)


class TestAdif(unittest.TestCase):
    def test_saca_el_aviso_una_vez_y_con_su_linea(self):
        r = adif.parsear(PAGINA, HOY)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["lineas"], ["C4"])
        self.assertIn("robo de cable", r[0]["texto"])

    def test_otro_diseno_y_sin_avisos(self):
        self.assertEqual(adif.parsear("<div>13:27 Cudillero C4 12</div>", HOY), [])
        r = adif.parsear("<p>09/10/2026 - Se están produciendo retrasos en línea C-5 por una avería en la vía</p>", HOY)
        self.assertEqual(r[0]["lineas"], ["C5"])

    def test_lineas(self):
        self.assertEqual(adif.lineas_de("retrasos en líneas C-1 y C-4 y C5a"), ["C1", "C4", "C5"])
        self.assertEqual(adif.lineas_de("obras en la estación"), [])

    def test_filtra_por_linea(self):
        a = adif.Adif(leer=lambda u: PAGINA)
        a.avisos = adif.parsear(PAGINA, HOY)
        self.assertEqual(len(a.para_lineas({"C4"})), 1)
        self.assertEqual(a.para_lineas({"C1"}), [])
        a.avisos.append({"texto": "general", "lineas": [], "fecha": "2026-10-09"})
        self.assertEqual(a.para_lineas({"C1"}), ["general"])

    def test_actualiza_con_direcciones_de_reserva_y_aguanta_fallos(self):
        llamadas = []

        def leer(url):
            llamadas.append(url)
            if "oviedo" in url and "15211" in url and "/en/" not in url:
                raise OSError("caído")
            return PAGINA.replace("09/10/2026", datetime.date.today().strftime("%d/%m/%Y"))
        a = adif.Adif(leer=leer)
        a._trabajo()
        self.assertEqual(len(a.avisos), 1)                  # el mismo aviso en dos estaciones cuenta una vez
        self.assertEqual(a.estado["Oviedo"], "ok")
        self.assertEqual(a.error, None)
        # todo falla: se conserva lo último
        a._leer = lambda u: (_ for _ in ()).throw(OSError("sin red"))
        a._trabajo()
        self.assertEqual(len(a.avisos), 1)
        self.assertIn("No se pudo", a.error)

    def test_no_pide_mas_de_una_vez_cada_tres_minutos(self):
        n = []
        a = adif.Adif(leer=lambda u: n.append(u) or "")
        a.actualizar()
        for _ in range(100):
            if not a._ocupado:
                break
            time.sleep(0.01)
        antes = len(n)
        a.actualizar()
        time.sleep(0.05)
        self.assertEqual(len(n), antes)


if __name__ == "__main__":
    unittest.main()
