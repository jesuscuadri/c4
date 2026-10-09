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
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
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
MAX_FIJOS = 8
MAX_ESPERA_S = 48 * 3600        # no se programa nada a más de dos días
TOLERANCIA_S = 20 * 60          # si el aviso llega a destiempo (servidor dormido) más de esto, ya no sirve
VIGILA_ANTES_S = 25 * 60        # se empieza a mirar el tiempo real 25 min antes de avisar
VENTANA_FIJO_ANTES = 90         # un aviso fijo se prepara hasta 90 min antes de la hora de salir


def _min_dia(ts):
    d = _dt.datetime.fromtimestamp(ts)
    return d.hour * 60 + d.minute + d.second / 60.0


def _hm_ts(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _hm_min(m):
    m = int(m + 1e-6) % 1440
    return "%02d:%02d" % (m // 60, m % 60)


def _dep_abs(sale, ahora_ts):
    """Instante (epoch) de una salida dada en minutos del día."""
    dif = sale - _min_dia(ahora_ts)
    if dif < -720:
        dif += 1440
    return ahora_ts + dif * 60


def primer_vehiculo(plan, ahora_m):
    """El primer tren/autobús de un plan: lo que hay que coger y cuánto se anda antes."""
    if not plan or not plan.get("ok"):
        return None
    andar = 0.0
    for e in plan.get("etapas") or []:
        t = e.get("tipo")
        if t == "andar":
            andar += e.get("min") or 0
            continue
        if t == "tren":
            sale, ret, desde, linea, ident = e.get("sale"), e.get("retraso") or 0, e.get("desde"), e.get("linea") or "C4", e.get("id")
        elif t == "autobus":
            sale, ret, desde, linea, ident = e.get("sale"), 0, e.get("subir"), e.get("linea"), None
        elif t == "bus":
            sale = ahora_m + e["sale_en"] if e.get("sale_en") is not None else None
            ret, desde, linea, ident = 0, e.get("subir"), e.get("linea"), None
        else:
            return None
        if sale is None:
            return None
        return {"tipo": "tren" if t == "tren" else "bus", "linea": str(linea or ""), "desde": str(desde or ""), "id": ident,
                "sale": float(sale), "retraso": float(ret), "prog": float(sale) - float(ret), "andar": andar,
                "llega": plan.get("llega")}
    return None


def _alternativas(plan):
    out = []
    for a in plan.get("alternativas") or []:
        vs = a.get("vehiculos") or []
        if not vs or a.get("sale") is None:
            continue
        ret = a.get("retraso") or 0
        out.append({"tipo": vs[0].get("tipo", "tren"), "linea": str(vs[0].get("linea") or ""), "desde": str(a.get("desde") or ""),
                    "id": None, "sale": float(a["sale"]), "retraso": float(ret), "prog": float(a["sale"]) - float(ret),
                    "andar": 0.0, "llega": a.get("llega")})
    return out


def _norm(t):
    return re.sub(r"\W+", "", (t or "").lower())


def _coincide(ref, v):
    if ref.get("id") and v.get("id"):
        return ref["id"] == v["id"]
    return _norm(ref.get("linea")) == _norm(v.get("linea")) and _norm(ref.get("desde")) == _norm(v.get("desde")) \
        and abs(ref.get("prog", 0) - v["prog"]) <= 2.5


def _icono(a):
    return "🚆" if (a.get("ref") or {}).get("tipo") == "tren" else "🚌"


def mensaje_salida(a, ahora):
    ref = a["ref"]
    n = max(1, int(round((a["dep"] - ahora) / 60.0)))
    ret = int(round(a.get("retraso") or 0))
    tit = "%s Tu %s sale en %d min (%s)%s" % (_icono(a), ref["linea"], n, _hm_ts(a["dep"]), " · +%d min de retraso" % ret if ret >= 2 else "")
    andar = int(round(a.get("andar") or 0))
    txt = ("Sal ya: son %d min andando hasta %s." % (andar, ref["desde"])) if andar >= 1 else "Estate ya en %s." % ref["desde"]
    return tit, txt


class Avisos:
    """Avisos pendientes («sal ya, tu bus sale en N min») y avisos fijos que se repiten cada día.

    Cada aviso sigue el tiempo real: si el tren se retrasa mueve la hora, si lo cancelan o deja de ser
    la mejor opción avisa del cambio, y si Renfe publica una incidencia en la línea, también."""

    def __init__(self, almacen=None, reloj=time.time, enviar_fn=enviar, plan_fn=None, cancelado_fn=None, alertas_fn=None):
        token = getattr(almacen, "token", "") if almacen is not None else ""
        self.clave = Clave(token or None)
        self.contacto = os.environ.get("C4_URL", "https://c4-1-lgfj.onrender.com").strip() or "https://c4-1-lgfj.onrender.com"
        self.almacen = almacen
        self.reloj = reloj
        self._enviar = enviar_fn
        self._plan_fn, self._cancelado_fn, self._alertas_fn = plan_fn, cancelado_fn, alertas_fn
        self.lock = threading.Lock()
        self.avisos = []
        self.fijos = []
        self._sucio = False
        self._ult_guardado = 0.0

    # -- persistencia (en la rama «datos» de GitHub, como el resto)
    def cargar(self):
        if not (self.almacen and self.almacen.activo):
            return
        crudo = self.almacen.bajar_archivo(ARCHIVO)
        if not crudo:
            return
        try:
            datos = json.loads(crudo.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return
        with self.lock:
            ids = {a["id"] for a in self.avisos}
            self.avisos += [a for a in datos.get("avisos", []) if a.get("id") not in ids]
            ids = {f["id"] for f in self.fijos}
            self.fijos += [f for f in datos.get("fijos", []) if f.get("id") not in ids]
            for a in self.avisos:
                a["vig"] = False
        print("Avisos al móvil recuperados: %d (+%d fijos)" % (len(datos.get("avisos", [])), len(datos.get("fijos", []))))

    def guardar_si_hace_falta(self, forzar=False):
        ahora = self.reloj()
        with self.lock:
            if not self._sucio or (not forzar and ahora - self._ult_guardado < 120):
                return
            self._sucio = False
            self._ult_guardado = ahora
            copia = json.dumps({"avisos": [{k: v for k, v in a.items() if k not in ("vig", "proximo")} for a in self.avisos],
                                "fijos": self.fijos}, ensure_ascii=False).encode("utf-8")
        if self.almacen and self.almacen.activo:
            threading.Thread(target=self.almacen.subir_archivo, args=(ARCHIVO, copia), daemon=True).start()

    # -- API
    @staticmethod
    def ident(endpoint, propio=None):
        """Identificador: el que da el móvil, atado a su suscripción (nadie puede borrar los de otro)."""
        if not propio:
            return secrets.token_hex(7)
        return hashlib.sha1((endpoint + "|" + str(propio)[:200]).encode("utf-8")).hexdigest()[:14]

    @staticmethod
    def _sub_valida(sub):
        return isinstance(sub, dict) and str(sub.get("endpoint", "")).startswith("https://") \
            and (sub.get("keys") or {}).get("p256dh") and (sub.get("keys") or {}).get("auth")

    @staticmethod
    def _num(d, k, lo, hi, defecto=0.0):
        try:
            v = float(d.get(k, defecto))
        except (TypeError, ValueError):
            raise ValueError("Dato «%s» no válido." % k)
        if not lo <= v <= hi:
            raise ValueError("Dato «%s» fuera de rango." % k)
        return v

    def programar(self, sub, d, ident=None, fijo=None):
        """d: en (min hasta que sale el vehículo), andar, margen, retraso, linea, desde, tipo, trip, llega, q."""
        if not self._sub_valida(sub):
            return {"ok": False, "error": "Suscripción no válida."}
        try:
            falta = self._num(d, "en", -5, MAX_ESPERA_S / 60.0)
            andar = self._num(d, "andar", 0, 180)
            margen = self._num(d, "margen", 0, 120, 10)
            retraso = self._num(d, "retraso", -30, 600)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        linea, desde = str(d.get("linea") or "")[:20], str(d.get("desde") or "")[:80]
        if not linea or not desde:
            return {"ok": False, "error": "Falta qué vehículo coger."}
        ahora = self.reloj()
        dep = ahora + max(-5.0, falta) * 60
        ref = {"tipo": "tren" if d.get("tipo") == "tren" else "bus", "linea": linea, "desde": desde,
               "id": str(d["trip"])[:60] if d.get("trip") else None, "prog": _min_dia(dep - retraso * 60),
               "llega": float(d["llega"]) if isinstance(d.get("llega"), (int, float)) else None}
        q = str(d.get("q") or "")[:600] or None
        ident = self.ident(sub["endpoint"], ident)
        with self.lock:
            self.avisos = [a for a in self.avisos if a["id"] != ident]
            propios = [a for a in self.avisos if a["sub"]["endpoint"] == sub["endpoint"]]
            if len(propios) >= MAX_POR_DISPOSITIVO:
                self.avisos.remove(min(propios, key=lambda a: a["cuando"]))
            if len(self.avisos) >= MAX_AVISOS:
                return {"ok": False, "error": "Hay demasiados avisos pendientes. Prueba más tarde."}
            a = {"id": ident, "sub": {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}},
                 "dep": dep, "cuando": max(ahora, dep - (andar + margen) * 60), "andar": andar, "margen": margen,
                 "retraso": retraso, "ref": ref, "q": q, "enviado": False, "cambios": 0, "perdido": 0, "vistos": [],
                 "fijo": fijo}
            self.avisos.append(a)
            self._sucio = True
        self.guardar_si_hace_falta(True)
        return {"ok": True, "id": ident, "cuando": a["cuando"], "ahora": ahora}

    def programar_fijo(self, sub, d, ident=None):
        if not self._sub_valida(sub):
            return {"ok": False, "error": "Suscripción no válida."}
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(d.get("hora") or ""))
        dias = sorted({int(x) for x in (d.get("dias") or []) if isinstance(x, int) and 0 <= x <= 6})
        q = str(d.get("q") or "")[:600]
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59 or not dias or not q:
            return {"ok": False, "error": "Aviso fijo no válido."}
        try:
            margen = self._num(d, "margen", 0, 120, 10)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        ident = self.ident(sub["endpoint"], ident)
        with self.lock:
            self.fijos = [f for f in self.fijos if f["id"] != ident]
            if len([f for f in self.fijos if f["sub"]["endpoint"] == sub["endpoint"]]) >= MAX_FIJOS:
                return {"ok": False, "error": "Ya tienes %d avisos fijos. Quita alguno." % MAX_FIJOS}
            self.fijos.append({"id": ident, "sub": {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}},
                               "q": q, "hora": "%02d:%02d" % (int(m.group(1)), int(m.group(2))), "dias": dias, "margen": margen,
                               "etiqueta": str(d.get("etiqueta") or "")[:80], "ultimo": None, "prox": 0.0})
            self._sucio = True
        self.guardar_si_hace_falta(True)
        return {"ok": True, "id": ident}

    def cancelar(self, ident, endpoint=None):
        if endpoint:
            ident = self.ident(endpoint, ident)
        with self.lock:
            n = (len(self.avisos), len(self.fijos))
            self.avisos = [a for a in self.avisos if a["id"] != ident and a.get("fijo") != ident]
            self.fijos = [f for f in self.fijos if f["id"] != ident]
            if n != (len(self.avisos), len(self.fijos)):
                self._sucio = True
        self.guardar_si_hace_falta(True)
        return {"ok": True}

    def probar(self, sub):
        cod, txt = self._enviar(self.clave, self.contacto, sub,
                                {"titulo": "🔔 Avisos activados", "texto": "Así te avisaré cuando tengas que salir.", "etiqueta": "prueba"})
        return {"ok": 200 <= cod < 300, "codigo": cod, "detalle": txt}

    def pendientes(self, endpoint):
        with self.lock:
            return {"avisos": [{"id": a["id"], "cuando": a["cuando"], "dep": a["dep"], "linea": a["ref"]["linea"]}
                               for a in self.avisos if a["sub"]["endpoint"] == endpoint],
                    "fijos": [{"id": f["id"], "etiqueta": f["etiqueta"], "hora": f["hora"], "dias": f["dias"]}
                              for f in self.fijos if f["sub"]["endpoint"] == endpoint]}

    # -- reloj: se llama muy a menudo desde el bucle principal
    def revisar(self):
        ahora = self.reloj()
        with self.lock:
            vencidos = [a for a in self.avisos if not a.get("enviado") and a["cuando"] <= ahora and a.get("proximo", 0) <= ahora]
            for a in vencidos:
                a["proximo"] = ahora + 45          # no repetir mientras se envía
            vigilar = [a for a in self.avisos if self._toca_vigilar(a, ahora)]
            for a in vigilar:
                a["ult"], a["vig"] = ahora, True
            caducados = [a["id"] for a in self.avisos if a.get("enviado") and ahora > a["dep"] + 120]
            fijos = []
            if self.fijos:
                d = _dt.datetime.fromtimestamp(ahora)
                hoy, mn = d.date().isoformat(), d.hour * 60 + d.minute
                for f in self.fijos:
                    hh, mm = f["hora"].split(":")
                    h = int(hh) * 60 + int(mm)
                    if d.weekday() in f["dias"] and f.get("ultimo") != hoy and f.get("prox", 0) <= ahora \
                            and h - VENTANA_FIJO_ANTES <= mn <= h + 5:
                        f["prox"] = ahora + 60
                        fijos.append((f, h, hoy))
        for a in vencidos:
            threading.Thread(target=self._mandar, args=(a,), daemon=True).start()
        for a in vigilar:
            threading.Thread(target=self._vigilar, args=(a,), daemon=True).start()
        for f, h, hoy in fijos:
            threading.Thread(target=self._generar, args=(f, h, hoy), daemon=True).start()
        for i in caducados:
            self.cancelar(i)
        self.guardar_si_hace_falta()

    def _toca_vigilar(self, a, ahora):
        if not a.get("q") or a.get("vig") or not self._plan_fn:
            return False
        if ahora - a.get("ult", 0) < (30 if a["cuando"] - ahora < 300 else 60):
            return False
        if a.get("enviado"):
            return ahora < a["dep"]
        return a["cuando"] - ahora <= VIGILA_ANTES_S

    def _push(self, a, titulo, texto, etiqueta=None):
        return self._enviar(self.clave, self.contacto, a["sub"], {"titulo": titulo, "texto": texto, "etiqueta": etiqueta or a["id"]})

    def _mandar(self, a):
        ahora = self.reloj()
        if ahora - a["cuando"] > TOLERANCIA_S or a["dep"] < ahora - 60:
            return self.cancelar(a["id"])          # llegó tarde: avisar ya no sirve de nada
        tit, txt = mensaje_salida(a, ahora)
        cod, det = self._push(a, tit, txt)
        if 200 <= cod < 300:
            a["enviado"], a["dep_anunciada"] = True, a["dep"]
            with self.lock:
                self._sucio = True
            return
        if cod in (400, 401, 403, 404, 410, 413):
            print("Aviso al móvil descartado (%s %s)" % (cod, det))
            return self.cancelar(a["id"])
        a["intentos"] = a.get("intentos", 0) + 1
        if a["intentos"] >= 4:
            return self.cancelar(a["id"])
        print("Aviso al móvil: fallo %s %s; se reintenta" % (cod, det))

    # -- seguimiento en tiempo real
    def _vigilar(self, a):
        try:
            ahora = self.reloj()
            am = _min_dia(ahora)
            p = self._plan_fn(a["q"], None)
            if not isinstance(p, dict) or p.get("cargando") or p.get("preparando"):
                return
            ref, v = a["ref"], primer_vehiculo(p, am)
            if ref["tipo"] == "tren" and self._alertas_fn:
                nuevas = []
                for t in self._alertas_fn(ref["linea"]) or []:
                    h = hashlib.md5(t.encode("utf-8")).hexdigest()[:8]
                    if h not in a["vistos"]:
                        a["vistos"].append(h)
                        nuevas.append(t)
                if nuevas:
                    self._push(a, "⚠️ Aviso de Renfe", " · ".join(nuevas)[:180], a["id"] + "r")
            m = None
            for c in ([v] if v else []) + (_alternativas(p) if p.get("ok") else []):
                if _coincide(ref, c):
                    m = c
                    break
            cancelado = bool(ref.get("id") and self._cancelado_fn and self._cancelado_fn(ref["id"]))
            if m and not cancelado:
                a["perdido"] = 0
                a["retraso"] = m["retraso"]
                self._mover(a, _dep_abs(m["sale"], ahora), ahora)
            else:
                a["perdido"] = a.get("perdido", 0) + 1
                if cancelado or a["perdido"] >= 2:
                    self._cambio(a, p, v, cancelado, ahora)
        except Exception as e:  # noqa: BLE001
            print("Aviso al móvil: no pude seguirlo (%s)" % e)
        finally:
            a["vig"] = False

    def _mover(self, a, dep, ahora):
        if abs(dep - a["dep"]) < 30:
            return
        a["dep"] = dep
        if not a.get("enviado"):
            a["cuando"] = max(ahora, dep - (a["andar"] + a["margen"]) * 60)
            return
        previo = a.get("dep_anunciada", dep)
        if dep - previo >= 180 and dep - ahora > 90 and a.get("demoras", 0) < 3:
            a["demoras"] = a.get("demoras", 0) + 1
            a["dep_anunciada"] = dep
            m = int(round((dep - previo) / 60.0))
            self._push(a, "⏱ Tu %s lleva +%d min" % (a["ref"]["linea"], int(round(a.get("retraso") or m))),
                       "Ahora sale a las %s. Puedes salir %d min más tarde." % (_hm_ts(dep), m), a["id"] + "d")

    def _cambio(self, a, p, v, cancelado, ahora):
        ref = a["ref"]
        if a.get("cambios", 0) >= 3:
            return
        if not cancelado:
            nueva, vieja = p.get("llega"), ref.get("llega")
            if not v or nueva is None or vieja is None or vieja - 3 <= nueva <= vieja + 2:
                return                                     # solo es otra opción igual de buena: no molestar
        prog = _hm_min(ref["prog"])
        if v:
            tit = ("❌ Cancelado: tu %s de las %s" % (ref["linea"], prog)) if cancelado else "⚠️ Cambio en tu viaje"
            txt = "%s %s a las %s desde %s (llegas a las %s)." % ("Alternativa:" if cancelado else "Ahora lo mejor es el", v["linea"],
                                                                 _hm_min(v["sale"]), v["desde"], _hm_min(p["llega"]))
        else:
            tit, txt = "❌ Cancelado: tu %s de las %s" % (ref["linea"], prog), "No veo ninguna alternativa ahora mismo."
        self._push(a, tit, txt, a["id"] + "c")
        a["cambios"] = a.get("cambios", 0) + 1
        a["perdido"] = 0
        if not v:
            return
        a["ref"] = {"tipo": v["tipo"], "linea": v["linea"], "desde": v["desde"], "id": v["id"], "prog": v["prog"], "llega": p.get("llega")}
        a["andar"], a["retraso"], a["dep"] = v["andar"], v["retraso"], _dep_abs(v["sale"], ahora)
        a["cuando"] = max(ahora, a["dep"] - (a["andar"] + a["margen"]) * 60)
        a["enviado"] = a["cuando"] <= ahora          # si ya toca salir, el propio aviso de cambio lo dice
        a["dep_anunciada"] = a["dep"]
        with self.lock:
            self._sucio = True

    # -- avisos fijos: cada día se prepara el del día
    def _generar(self, f, hora_min, hoy):
        try:
            ahora = self.reloj()
            p = self._plan_fn(f["q"], max(hora_min, int(_min_dia(ahora))))
            v = primer_vehiculo(p, _min_dia(ahora)) if isinstance(p, dict) else None
            if not v:
                return
            dep = _dep_abs(v["sale"], ahora)
            r = self.programar(f["sub"], {"en": (dep - ahora) / 60.0, "andar": v["andar"], "margen": f["margen"], "retraso": v["retraso"],
                                          "linea": v["linea"], "desde": v["desde"], "tipo": v["tipo"], "trip": v["id"],
                                          "llega": p.get("llega"), "q": f["q"]}, ident="%s|%s" % (f["id"], hoy), fijo=f["id"])
            if r.get("ok"):
                f["ultimo"] = hoy
                with self.lock:
                    self._sucio = True
        except Exception as e:  # noqa: BLE001
            print("Aviso fijo: no pude prepararlo (%s)" % e)
