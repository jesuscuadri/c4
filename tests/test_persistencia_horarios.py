import base64
import unittest

from c4.persistencia import Almacen


class Falso(Almacen):
    def __init__(self):
        super().__init__(token="t", repo="u/r")
        self.archivos = {"horarios/viejo.json.gz": b"x", "horarios/hoy.json.gz": b"y"}
        self.llamadas = []

    def _api(self, metodo, ruta, cuerpo=None):
        self.llamadas.append((metodo, ruta))
        if "/branches/" in ruta:
            return 200, {}
        if "/contents/horarios?" in ruta:
            return 200, [{"path": k, "sha": "s"} for k in self.archivos]
        if "/contents/" in ruta:
            nombre = ruta.split("/contents/")[1].split("?")[0]
            if metodo == "GET":
                if nombre in self.archivos:
                    return 200, {"sha": "s", "content": base64.b64encode(self.archivos[nombre]).decode()}
                return 404, None
            if metodo == "PUT":
                self.archivos[nombre] = base64.b64decode(cuerpo["content"])
                return 201, {"content": {"sha": "n"}}
            if metodo == "DELETE":
                self.archivos.pop(nombre)
                return 200, {}
        return 404, None


class TestHorarios(unittest.TestCase):
    def test_sube_y_baja(self):
        a = Falso()
        self.assertTrue(a.subir_archivo("horarios/nuevo.json.gz", b"datos"))
        self.assertEqual(a.bajar_archivo("horarios/nuevo.json.gz"), b"datos")
        self.assertIsNone(a.bajar_archivo("horarios/no.json.gz"))

    def test_borra_lo_viejo_y_conserva_lo_pedido(self):
        a = Falso()
        a.subir_archivo("horarios/nuevo.json.gz", b"d", borrar_prefijo="horarios", conservar={"horarios/hoy.json.gz"})
        self.assertEqual(sorted(a.archivos), ["horarios/hoy.json.gz", "horarios/nuevo.json.gz"])

    def test_sin_token_no_hace_nada(self):
        a = Almacen(token="", repo="")
        self.assertFalse(a.subir_archivo("horarios/x", b"1"))
        self.assertIsNone(a.bajar_archivo("horarios/x"))


if __name__ == "__main__":
    unittest.main()
