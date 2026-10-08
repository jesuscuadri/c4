# -*- coding: utf-8 -*-
"""Avisos al móvil («tu bus sale en N minutos»), sin ninguna librería externa.

Es el «Web Push» estándar (RFC 8030 + cifrado RFC 8291 + identificación VAPID RFC 8292), escrito con la
biblioteca estándar de Python para que Render siga sin instalar nada. Las cuentas son pequeñas (un aviso
son unos 300 bytes), así que la criptografía en Python puro sobra de rápida.

    C4_URL   (opcional) dirección pública de la app; sirve de identificación ante Apple/Google.

La clave VAPID del servidor sale del token de GitHub (que ya es secreto y siempre es el mismo), así no hay
que guardar ni pegar nada más: si no hay token (uso local), se crea una clave nueva en cada arranque.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

# ---------------------------------------------------------------------------------------------- curva P-256
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
      0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _sumar(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0]:
        if (a[1] + b[1]) % _P == 0:
            return None
        m = (3 * a[0] * a[0] + _A) * pow(2 * a[1], -1, _P) % _P
    else:
        m = (b[1] - a[1]) * pow(b[0] - a[0], -1, _P) % _P
    x = (m * m - a[0] - b[0]) % _P
    return (x, (m * (a[0] - x) - a[1]) % _P)


def _multiplicar(k, punto):
    res = None
    while k:
        if k & 1:
            res = _sumar(res, punto)
        punto = _sumar(punto, punto)
        k >>= 1
    return res


def publica(priv):
    """Clave pública (65 bytes, formato sin comprimir) de un entero privado."""
    x, y = _multiplicar(priv, _G)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def _punto(pub):
    if len(pub) != 65 or pub[0] != 4:
        raise ValueError("clave pública no válida")
    p = (int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big"))
    if (p[1] * p[1] - (p[0] ** 3 + _A * p[0] + 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B)) % _P:
        raise ValueError("el punto no está en la curva")
    return p


def _ecdh(priv, pub):
    return _multiplicar(priv, _punto(pub))[0].to_bytes(32, "big")


def _firmar(priv, mensaje):
    """ECDSA P-256 + SHA-256; devuelve r||s (64 bytes), el formato de los JWT ES256."""
    z = int.from_bytes(hashlib.sha256(mensaje).digest(), "big")
    while True:
        k = secrets.randbelow(_N - 1) + 1
        r = _multiplicar(k, _G)[0] % _N
        if not r:
            continue
        s = pow(k, -1, _N) * (z + r * priv) % _N
        if s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---------------------------------------------------------------------------------------------- AES-128-GCM
def _sbox():
    # caja de sustitución de AES calculada (inverso en GF(2^8) + transformación afín)
    s = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        s[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    s[0] = 0x63
    return s


_SB = _sbox()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expandir(clave):
    w = [list(clave[i:i + 4]) for i in range(0, 16, 4)]
    rc = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SB[b] for b in t]
            t[0] ^= rc
            rc = _xt(rc)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]


def _bloque(rk, b):
    s = [b[i] ^ rk[0][i] for i in range(16)]
    for r in range(1, 11):
        s = [_SB[x] for x in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]       # ShiftRows (columnas)
        if r < 10:
            n = []
            for c in range(0, 16, 4):
                a0, a1, a2, a3 = s[c:c + 4]
                n += [_xt(a0) ^ _xt(a1) ^ a1 ^ a2 ^ a3, a0 ^ _xt(a1) ^ _xt(a2) ^ a2 ^ a3,
                      a0 ^ a1 ^ _xt(a2) ^ _xt(a3) ^ a3, _xt(a0) ^ a0 ^ a1 ^ a2 ^ _xt(a3)]
            s = n
        s = [s[i] ^ rk[r][i] for i in range(16)]
    return bytes(s)


def _mult_gf(x, y):
    z, v = 0, y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def _ghash(h, datos):
    y = 0
    for i in range(0, len(datos), 16):
        y = _mult_gf(y ^ int.from_bytes(datos[i:i + 16].ljust(16, b"\0"), "big"), h)
    return y


def _gcm_cifrar(clave, nonce, texto):
    rk = _expandir(clave)
    h = int.from_bytes(_bloque(rk, bytes(16)), "big")
    cifrado = bytearray()
    for i in range(0, len(texto), 16):
        ks = _bloque(rk, nonce + (2 + i // 16).to_bytes(4, "big"))
        cifrado += bytes(a ^ b for a, b in zip(texto[i:i + 16], ks))
    cifrado = bytes(cifrado)
    lon = (0).to_bytes(8, "big") + (len(cifrado) * 8).to_bytes(8, "big")
    g = _ghash(h, cifrado + b"\0" * (-len(cifrado) % 16) + lon)
    etiqueta = g ^ int.from_bytes(_bloque(rk, nonce + (1).to_bytes(4, "big")), "big")
    return cifrado + etiqueta.to_bytes(16, "big")


# ---------------------------------------------------------------------------------------------- Web Push
def _hkdf(sal, ikm, info, n):
    prk = hmac.new(sal, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def desb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def cifrar(mensaje, p256dh, auth, _priv_efimera=None, _sal=None):
    """Cuerpo cifrado (RFC 8291, aes128gcm) para la suscripción del navegador."""
    ua = desb64u(p256dh)
    priv = _priv_efimera or (secrets.randbelow(_N - 1) + 1)
    mia = publica(priv)
    sal = _sal or secrets.token_bytes(16)
    ikm = _hkdf(desb64u(auth), _ecdh(priv, ua), b"WebPush: info\0" + ua + mia, 32)
    cek = _hkdf(sal, ikm, b"Content-Encoding: aes128gcm\0", 16)
    nonce = _hkdf(sal, ikm, b"Content-Encoding: nonce\0", 12)
    cuerpo = _gcm_cifrar(cek, nonce, mensaje + b"\x02")
    return sal + (4096).to_bytes(4, "big") + bytes([len(mia)]) + mia + cuerpo


class Clave:
    """Clave VAPID del servidor."""

    def __init__(self, semilla=None):
        if semilla:
            self.priv = int.from_bytes(hmac.new(semilla.encode("utf-8"), b"c4-vapid-v1", hashlib.sha256).digest(), "big") % (_N - 1) + 1
        else:
            self.priv = secrets.randbelow(_N - 1) + 1
        self.pub = publica(self.priv)
        self.pub_b64 = b64u(self.pub)

    def cabecera(self, endpoint, contacto, ahora=None):
        u = urlparse(endpoint)
        cab = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        cuerpo = b64u(json.dumps({"aud": "%s://%s" % (u.scheme, u.netloc), "exp": int((ahora or time.time()) + 12 * 3600),
                                  "sub": contacto}, separators=(",", ":")).encode())
        firma = _firmar(self.priv, ("%s.%s" % (cab, cuerpo)).encode("ascii"))
        return "vapid t=%s.%s.%s, k=%s" % (cab, cuerpo, b64u(firma), self.pub_b64)


def enviar(clave, contacto, sub, datos, ttl=600):
    """Manda una notificación. Devuelve (código HTTP, texto). 404/410 = la suscripción ya no existe."""
    k = sub.get("keys") or {}
    cuerpo = cifrar(json.dumps(datos, ensure_ascii=False).encode("utf-8"), k["p256dh"], k["auth"])
    req = urllib.request.Request(sub["endpoint"], data=cuerpo, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
        "Urgency": "high", "Authorization": clave.cabecera(sub["endpoint"], contacto)})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            txt = ""
        return e.code, txt
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


# ---------------------------------------------------------------------------------------------- avisos programados
ARCHIVO = "push.json"
MAX_AVISOS = 300
MAX_POR_DISPOSITIVO = 12
MAX_ESPERA_S = 48 * 3600        # no se programa nada a más de dos días
TOLERANCIA_S = 20 * 60          # si el aviso llega a destiempo (servidor dormido) más de esto, ya no sirve


class Avisos:
    def __init__(self, almacen=None, reloj=time.time, enviar_fn=enviar):
        token = getattr(almacen, "token", "") if almacen is not None else ""
        self.clave = Clave(token or None)
        self.contacto = os.environ.get("C4_URL", "https://c4-1-lgfj.onrender.com").strip() or "https://c4-1-lgfj.onrender.com"
        self.almacen = almacen
        self.reloj = reloj
        self._enviar = enviar_fn
        self.lock = threading.Lock()
        self.avisos = []
        self._sucio = False

    # -- persistencia (en la rama «datos» de GitHub, como el resto)
    def cargar(self):
        if not (self.almacen and self.almacen.activo):
            return
        crudo = self.almacen.bajar_archivo(ARCHIVO)
        if not crudo:
            return
        try:
            lista = json.loads(crudo.decode("utf-8")).get("avisos", [])
        except Exception:  # noqa: BLE001
            return
        with self.lock:
            ids = {a["id"] for a in self.avisos}
            self.avisos += [a for a in lista if a.get("id") not in ids]
        print("Avisos al móvil recuperados: %d" % len(lista))

    def guardar_si_hace_falta(self):
        with self.lock:
            if not self._sucio:
                return
            self._sucio = False
            copia = json.dumps({"avisos": self.avisos}, ensure_ascii=False).encode("utf-8")
        if self.almacen and self.almacen.activo:
            threading.Thread(target=self.almacen.subir_archivo, args=(ARCHIVO, copia), daemon=True).start()

    # -- API
    def programar(self, sub, en_min, titulo, texto, ident=None):
        if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") \
                or not (sub.get("keys") or {}).get("p256dh") or not (sub.get("keys") or {}).get("auth"):
            return {"ok": False, "error": "Suscripción no válida."}
        try:
            en_s = float(en_min) * 60
        except (TypeError, ValueError):
            return {"ok": False, "error": "Hora no válida."}
        if en_s > MAX_ESPERA_S:
            return {"ok": False, "error": "Solo puedo avisarte con menos de dos días de antelación."}
        cuando = self.reloj() + max(0.0, en_s)
        ident = self.ident(sub["endpoint"], ident)
        with self.lock:
            self.avisos = [a for a in self.avisos if a["id"] != ident]
            propios = [a for a in self.avisos if a["sub"]["endpoint"] == sub["endpoint"]]
            if len(propios) >= MAX_POR_DISPOSITIVO:
                masviejo = min(propios, key=lambda a: a["cuando"])
                self.avisos.remove(masviejo)
            if len(self.avisos) >= MAX_AVISOS:
                return {"ok": False, "error": "Hay demasiados avisos pendientes. Prueba más tarde."}
            self.avisos.append({"id": ident, "sub": {"endpoint": sub["endpoint"], "keys": {
                "p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}},
                "cuando": cuando, "titulo": str(titulo)[:80], "texto": str(texto)[:200], "intentos": 0})
            self._sucio = True
        self.guardar_si_hace_falta()
        return {"ok": True, "id": ident, "cuando": cuando}

    @staticmethod
    def ident(endpoint, propio=None):
        """Identificador del aviso: el que da el móvil, atado a su suscripción (nadie puede borrar los de otro)."""
        if not propio:
            return secrets.token_hex(7)
        return hashlib.sha1((endpoint + "|" + str(propio)[:80]).encode("utf-8")).hexdigest()[:14]

    def cancelar(self, ident, endpoint=None):
        if endpoint:
            ident = self.ident(endpoint, ident)
        with self.lock:
            n = len(self.avisos)
            self.avisos = [a for a in self.avisos if a["id"] != ident]
            if len(self.avisos) != n:
                self._sucio = True
        self.guardar_si_hace_falta()
        return {"ok": True}

    def probar(self, sub):
        cod, txt = self._enviar(self.clave, self.contacto, sub,
                                {"titulo": "🔔 Avisos activados", "texto": "Así te avisaré cuando tengas que salir.", "etiqueta": "prueba"})
        return {"ok": 200 <= cod < 300, "codigo": cod, "detalle": txt}

    def pendientes(self, endpoint):
        with self.lock:
            return [{"id": a["id"], "cuando": a["cuando"], "texto": a["texto"]} for a in self.avisos if a["sub"]["endpoint"] == endpoint]

    # -- reloj: se llama muy a menudo desde el bucle principal
    def revisar(self):
        ahora = self.reloj()
        with self.lock:
            vencidos = [a for a in self.avisos if a["cuando"] <= ahora and a.get("proximo", 0) <= ahora]
            for a in vencidos:
                a["proximo"] = ahora + 45          # no repetir mientras se envía
        for a in vencidos:
            threading.Thread(target=self._mandar, args=(a,), daemon=True).start()

    def _mandar(self, a):
        ahora = self.reloj()
        if ahora - a["cuando"] > TOLERANCIA_S:
            return self.cancelar(a["id"])          # llegó tarde: avisar ya no sirve de nada
        cod, txt = self._enviar(self.clave, self.contacto, a["sub"],
                                {"titulo": a["titulo"], "texto": a["texto"], "etiqueta": a["id"]})
        if 200 <= cod < 300 or cod in (400, 401, 403, 404, 410, 413):
            if not 200 <= cod < 300:
                print("Aviso al móvil descartado (%s %s)" % (cod, txt))
            return self.cancelar(a["id"])
        a["intentos"] = a.get("intentos", 0) + 1
        if a["intentos"] >= 4:
            return self.cancelar(a["id"])
        print("Aviso al móvil: fallo %s %s; se reintenta" % (cod, txt))
