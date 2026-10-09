# -*- coding: utf-8 -*-
"""Avisos al móvil: cifrado Web Push (contra una librería de referencia, si está) y programación."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from asturias import push  # noqa: E402

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    REF = True
except ImportError:      # pragma: no cover
    REF = False


def sub_falsa(n=1):
    return {"endpoint": "https://push.example/%d" % n, "keys": {"p256dh": push.b64u(push.publica(12345 + n)), "auth": push.b64u(b"0123456789abcdef")}}


@unittest.skipUnless(REF, "sin cryptography no hay referencia")
class TestCripto(unittest.TestCase):
    def test_aes_gcm(self):
        for n in (0, 1, 16, 17, 90):
            k, iv, t = os.urandom(16), os.urandom(12), os.urandom(n)
            self.assertEqual(push._gcm_cifrar(k, iv, t), AESGCM(k).encrypt(iv, t, None))

    def test_clave_publica(self):
        d = int.from_bytes(os.urandom(32), "big") % (push._N - 1) + 1
        ref = ec.derive_private_key(d, ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.assertEqual(push.publica(d), ref)

    def test_firma_vapid(self):
        c = push.Clave("semilla")
        h = c.cabecera("https://web.push.apple.com/x", "https://c4.example")
        jwt = h.split("t=")[1].split(",")[0]
        a, b, s = jwt.split(".")
        firma = push.desb64u(s)
        pk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), c.pub)
        pk.verify(encode_dss_signature(int.from_bytes(firma[:32], "big"), int.from_bytes(firma[32:], "big")),
                  (a + "." + b).encode(), ec.ECDSA(hashes.SHA256()))
        self.assertIn('"aud":"https://web.push.apple.com"', push.desb64u(b).decode())

    def test_mensaje_descifrable(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        msg = "🚌 Tu L1 sale en 15 min (08:14) ñ".encode()
        cuerpo = push.cifrar(msg, push.b64u(ua_pub), push.b64u(auth))
        sal, idlen = cuerpo[:16], cuerpo[20]
        as_pub, ct = cuerpo[21:21 + idlen], cuerpo[21 + idlen:]
        sh = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\0" + ua_pub + as_pub).derive(sh)
        cek = HKDF(hashes.SHA256(), 16, sal, b"Content-Encoding: aes128gcm\0").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, sal, b"Content-Encoding: nonce\0").derive(ikm)
        self.assertEqual(AESGCM(cek).decrypt(nonce, ct, None), msg + b"\x02")


def tren(sale, ret=0, ident="T1", linea="C4", desde="Candás"):
    return {"tipo": "tren", "linea": linea, "desde": desde, "sale": sale, "retraso": ret, "id": ident, "llega": sale + 40}


def plan(*etapas, llega=None):
    return {"ok": True, "etapas": list(etapas), "llega": llega if llega is not None else etapas[-1]["sale"] + 40}


def datos(ahora_ts, sale_min_dia=None, en=20, **kw):
    d = {"en": en, "andar": 5, "margen": 10, "retraso": 0, "linea": "C4", "desde": "Candás", "tipo": "tren", "trip": "T1",
         "llega": 500, "q": "origen=a&destino=b"}
    d.update(kw)
    return d


class TestAvisos(unittest.TestCase):
    def setUp(self):
        import datetime
        self.t = datetime.datetime(2026, 10, 12, 7, 0).timestamp()      # un lunes a las 07:00
        self.enviados = []
        self.codigo = 201
        self.plan = None
        self.cancelados = set()
        self.alertas = []
        self.pedidos = []

        def falso(clave, contacto, sub, d, ttl=600):
            self.enviados.append((sub["endpoint"], d))
            return self.codigo, ""

        def plan_fn(q, hora):
            self.pedidos.append((q, hora))
            return self.plan
        self.av = push.Avisos(None, reloj=lambda: self.t, enviar_fn=falso, plan_fn=plan_fn,
                              cancelado_fn=lambda i: i in self.cancelados, alertas_fn=lambda linea: self.alertas)

    def esperar(self):
        import threading
        import time
        time.sleep(0.02)
        for _ in range(200):
            if threading.active_count() == 1:
                break
            time.sleep(0.01)

    def paso(self, seg=0):
        self.t += seg
        self.av.revisar()
        self.esperar()

    def test_avisa_a_su_hora_una_sola_vez(self):
        r = self.av.programar(sub_falsa(), datos(self.t, en=30), "a")          # sale en 30 min; 5 andando + 10 de margen
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["cuando"] - self.t, 15 * 60, delta=1)
        self.av.avisos[0]["q"] = None                                          # sin seguimiento
        self.paso(); self.assertEqual(self.enviados, [])
        self.paso(15 * 60 + 1)
        self.assertEqual(len(self.enviados), 1)
        d = self.enviados[0][1]
        self.assertIn("Tu C4 sale en 15 min (07:30)", d["titulo"])
        self.assertIn("5 min andando hasta Candás", d["texto"])
        self.paso(5); self.assertEqual(len(self.enviados), 1)
        self.paso(20 * 60)                                                     # ya salió: se borra
        self.assertEqual(self.av.avisos, [])

    def test_mismo_aviso_se_reemplaza_y_cancelar_solo_los_propios(self):
        self.av.programar(sub_falsa(1), datos(self.t), "a")
        self.av.programar(sub_falsa(1), datos(self.t, en=40), "a")
        self.assertEqual(len(self.av.avisos), 1)
        self.av.cancelar("a", sub_falsa(2)["endpoint"])
        self.assertEqual(len(self.av.avisos), 1)
        self.av.cancelar("a", sub_falsa(1)["endpoint"])
        self.assertEqual(self.av.avisos, [])

    def test_si_llega_tarde_se_descarta(self):
        self.av.programar(sub_falsa(), datos(self.t, en=16), "a")
        self.av.avisos[0]["q"] = None
        self.paso(3600)
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.av.avisos, [])

    def test_fallo_temporal_reintenta_y_suscripcion_muerta_se_borra(self):
        self.codigo = 503
        self.av.programar(sub_falsa(), datos(self.t, en=15), "a")
        self.av.avisos[0]["q"] = None
        self.paso(1)
        self.assertEqual(len(self.av.avisos), 1)
        self.codigo = 410
        self.paso(50)
        self.assertEqual(self.av.avisos, [])

    def test_rechaza_basura(self):
        self.assertFalse(self.av.programar({"endpoint": "http://x"}, datos(self.t))["ok"])
        self.assertFalse(self.av.programar(sub_falsa(), datos(self.t, en=99999))["ok"])
        self.assertFalse(self.av.programar(sub_falsa(), datos(self.t, en="no"))["ok"])
        self.assertFalse(self.av.programar(sub_falsa(), datos(self.t, linea=""))["ok"])

    def test_retraso_mueve_la_hora_antes_de_avisar(self):
        self.av.programar(sub_falsa(), datos(self.t, en=30), "a")              # sale 07:30
        self.plan = plan(tren(7 * 60 + 38, ret=8))                             # lleva 8 min de retraso
        self.paso(10 * 60)                                                     # 07:10: se mira el tiempo real
        a = self.av.avisos[0]
        self.assertAlmostEqual(a["dep"], self.t - 10 * 60 + 38 * 60, delta=1)
        self.assertAlmostEqual(a["cuando"] - (self.t - 10 * 60), 23 * 60, delta=1)
        self.paso(13 * 60 + 5)                                                 # 07:23: ahora sí
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("+8 min de retraso", self.enviados[0][1]["titulo"])
        self.assertIn("07:38", self.enviados[0][1]["titulo"])

    def test_si_se_retrasa_despues_de_avisar_lo_dice(self):
        self.av.programar(sub_falsa(), datos(self.t, en=16), "a")              # sale 07:16, avisa ya
        self.plan = plan(tren(7 * 60 + 16))
        self.paso(1); self.paso(61)
        self.assertEqual(len(self.enviados), 1)
        self.plan = plan(tren(7 * 60 + 25, ret=9))
        self.paso(61)
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("lleva +9 min", self.enviados[1][1]["titulo"])
        self.assertIn("07:25", self.enviados[1][1]["texto"])
        self.paso(61)                                                          # sin más cambios, no repite
        self.assertEqual(len(self.enviados), 2)

    def test_cancelado_avisa_y_sigue_la_alternativa(self):
        self.av.programar(sub_falsa(), datos(self.t, en=30), "a")
        self.cancelados.add("T1")
        self.plan = plan(tren(7 * 60 + 50, ident="T2"), llega=7 * 60 + 90)
        self.paso(10 * 60)
        self.assertEqual(len(self.enviados), 1)
        d = self.enviados[0][1]
        self.assertIn("Cancelado", d["titulo"])
        self.assertIn("07:50", d["texto"])
        self.assertEqual(self.av.avisos[0]["ref"]["id"], "T2")
        self.assertFalse(self.av.avisos[0]["enviado"])

    def test_cambio_de_plan_solo_si_de_verdad_cambia(self):
        self.av.programar(sub_falsa(), datos(self.t, en=30, llega=7 * 60 + 70), "a")
        self.plan = plan(tren(7 * 60 + 20, ident="T9"), llega=7 * 60 + 68)      # otra opción igual de buena
        self.paso(10 * 60); self.paso(61)
        self.assertEqual(self.enviados, [])
        self.plan = plan(tren(7 * 60 + 55, ident="T9"), llega=7 * 60 + 110)      # la nuestra ya no es la mejor y todo llega mucho después
        self.paso(61); self.paso(61)
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("Cambio en tu viaje", self.enviados[0][1]["titulo"])

    def test_alerta_de_renfe_una_vez(self):
        self.av.programar(sub_falsa(), datos(self.t, en=30), "a")
        self.plan = plan(tren(7 * 60 + 30))
        self.alertas = ["Obras entre Oviedo y Gijón"]
        self.paso(10 * 60); self.paso(61)
        self.assertEqual([e[1]["titulo"] for e in self.enviados], ["⚠️ Aviso de Renfe"])

    def test_aviso_fijo_se_prepara_cada_dia_laborable(self):
        sub = sub_falsa()
        self.assertTrue(self.av.programar_fijo(sub, {"q": "origen=a&destino=b", "hora": "07:30", "dias": [0, 1, 2, 3, 4], "margen": 10,
                                                      "etiqueta": "Casa → Oviedo"}, "f")["ok"])
        self.plan = plan(tren(7 * 60 + 36))
        self.paso()                                                            # 07:00, lunes: ya está dentro de la ventana (90 min)
        self.assertEqual(len(self.av.avisos), 1)
        self.assertEqual(self.av.avisos[0]["fijo"], self.av.fijos[0]["id"])
        self.assertEqual(self.pedidos[-1][1], 7 * 60 + 30)                     # pide salir a partir de las 07:30
        self.paso(61)
        self.assertEqual(len(self.av.avisos), 1)                                # hoy no se repite
        self.av.cancelar("f", sub["endpoint"])                                  # quitar el fijo quita también el de hoy
        self.assertEqual((self.av.avisos, self.av.fijos), ([], []))

    def test_aviso_fijo_no_sale_en_fin_de_semana(self):
        import datetime
        self.t = datetime.datetime(2026, 10, 10, 7, 0).timestamp()              # sábado
        self.av.programar_fijo(sub_falsa(), {"q": "x=1", "hora": "07:30", "dias": [0, 1, 2, 3, 4], "margen": 10}, "f")
        self.plan = plan(tren(7 * 60 + 36))
        self.paso()
        self.assertEqual(self.av.avisos, [])

    def test_fijo_no_valido(self):
        self.assertFalse(self.av.programar_fijo(sub_falsa(), {"q": "x", "hora": "25:00", "dias": [0]})["ok"])
        self.assertFalse(self.av.programar_fijo(sub_falsa(), {"q": "x", "hora": "07:00", "dias": []})["ok"])


class TestPlan(unittest.TestCase):
    def test_primer_vehiculo_cuenta_lo_que_se_anda(self):
        p = {"ok": True, "llega": 500, "etapas": [{"tipo": "andar", "min": 4}, {"tipo": "andar", "min": 2.5}, dict(tren(450, 3), tipo="tren")]}
        v = push.primer_vehiculo(p, 440)
        self.assertEqual((v["linea"], v["andar"], v["prog"], v["id"]), ("C4", 6.5, 447, "T1"))
        self.assertIsNone(push.primer_vehiculo({"ok": False}, 0))

    def test_bus_en_directo(self):
        p = {"ok": True, "etapas": [{"tipo": "bus", "linea": "1", "subir": "Parada", "sale_en": 6}], "llega": 500}
        self.assertEqual(push.primer_vehiculo(p, 600)["sale"], 606)


if __name__ == "__main__":
    unittest.main()
