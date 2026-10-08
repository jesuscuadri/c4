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


class TestAvisos(unittest.TestCase):
    def setUp(self):
        self.t = 1000.0
        self.enviados = []
        self.codigo = 201

        def falso(clave, contacto, sub, datos, ttl=600):
            self.enviados.append((sub["endpoint"], datos))
            return self.codigo, ""
        self.av = push.Avisos(None, reloj=lambda: self.t, enviar_fn=falso)

    def esperar(self):
        import threading
        import time
        for _ in range(100):
            if threading.active_count() == 1:
                break
            time.sleep(0.01)

    def test_no_avisa_antes_ni_repite(self):
        r = self.av.programar(sub_falsa(), 10, "t", "x", "a")
        self.assertTrue(r["ok"])
        self.av.revisar(); self.esperar()
        self.assertEqual(self.enviados, [])
        self.t += 601
        self.av.revisar(); self.esperar()
        self.assertEqual(len(self.enviados), 1)
        self.av.revisar(); self.esperar()
        self.assertEqual(len(self.enviados), 1)         # enviado una vez y borrado
        self.assertEqual(self.av.avisos, [])

    def test_mismo_aviso_se_reemplaza(self):
        self.av.programar(sub_falsa(), 10, "t", "x", "a")
        self.av.programar(sub_falsa(), 20, "t", "y", "a")
        self.assertEqual(len(self.av.avisos), 1)
        self.assertEqual(self.av.avisos[0]["texto"], "y")

    def test_cancelar_solo_los_propios(self):
        self.av.programar(sub_falsa(1), 10, "t", "x", "a")
        self.av.cancelar("a", sub_falsa(2)["endpoint"])    # otro móvil, mismo id: no borra
        self.assertEqual(len(self.av.avisos), 1)
        self.av.cancelar("a", sub_falsa(1)["endpoint"])
        self.assertEqual(self.av.avisos, [])

    def test_si_llega_tarde_se_descarta(self):
        self.av.programar(sub_falsa(), 1, "t", "x", "a")
        self.t += 3600
        self.av.revisar(); self.esperar()
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.av.avisos, [])

    def test_fallo_temporal_reintenta_y_suscripcion_muerta_se_borra(self):
        self.codigo = 503
        self.av.programar(sub_falsa(), 0, "t", "x", "a")
        self.av.revisar(); self.esperar()
        self.assertEqual(len(self.av.avisos), 1)
        self.t += 50
        self.codigo = 410
        self.av.revisar(); self.esperar()
        self.assertEqual(self.av.avisos, [])

    def test_rechaza_basura(self):
        self.assertFalse(self.av.programar({"endpoint": "http://x"}, 5, "t", "x")["ok"])
        self.assertFalse(self.av.programar(sub_falsa(), 99999, "t", "x")["ok"])
        self.assertFalse(self.av.programar(sub_falsa(), "no", "t", "x")["ok"])


if __name__ == "__main__":
    unittest.main()
