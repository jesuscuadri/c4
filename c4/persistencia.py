# -*- coding: utf-8 -*-
"""Guarda lo aprendido FUERA del servidor, para que no se pierda nunca.

En el plan gratis de Render el disco se vacía cada vez que el servidor se duerme o se
redespliega: sin esto, cada mañana el programa empezaba a aprender desde cero.

Solución sin coste: un archivo comprimido («historial.json.gz») en una rama aparte
(«datos») de tu propio repositorio de GitHub. Es una rama distinta de «main», así que
guardar ahí NO provoca redespliegues en Render.

    C4_GH_TOKEN   token de GitHub con permiso «Contents: Read and write» sobre el repositorio
    C4_GH_REPO    usuario/repositorio, p. ej. «jesuscuadri/c4»
    C4_GH_RAMA    (opcional) rama donde guardar; por defecto «datos»

Contenido: el resumen de lo aprendido por día (tiempos de marcha reales y retraso con el que
sale cada servicio) y las mediciones de precisión de las últimas semanas. Pesa unos cientos de KB.
Solo biblioteca estándar. Si GitHub no responde, el programa sigue funcionando igual.
"""
import base64
import csv
import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import historial

API = os.environ.get("C4_GH_API", "https://api.github.com")
ARCHIVO = "historial.json.gz"
DIAS_PRECISION = 30


class Almacen:
    def __init__(self, token=None, repo=None, rama=None):
        self.token = token if token is not None else os.environ.get("C4_GH_TOKEN", "").strip()
        self.repo = repo if repo is not None else os.environ.get("C4_GH_REPO", "").strip()
        self.rama = rama or os.environ.get("C4_GH_RAMA", "datos").strip() or "datos"
        self.sha = None
        self.lock = threading.Lock()
        self._subido = None
        self.ultimo_guardado = None
        self.ultima_carga = None
        self.error = None

    @property
    def activo(self):
        return bool(self.token and self.repo)

    def estado(self):
        return {"activo": self.activo, "repo": self.repo if self.activo else None, "rama": self.rama,
                "ultimo_guardado": self.ultimo_guardado, "ultima_carga": self.ultima_carga, "error": self.error}

    # ------------------------------------------------------------------ API de GitHub
    def _api(self, metodo, ruta, cuerpo=None):
        req = urllib.request.Request(API + ruta, method=metodo,
                                     data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "c4-tiempo-real")
        if cuerpo is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "null")
            except Exception:  # noqa: BLE001
                return e.code, None

    def _asegurar_rama(self):
        cod, _ = self._api("GET", "/repos/%s/branches/%s" % (self.repo, self.rama))
        if cod == 200:
            return
        cod, info = self._api("GET", "/repos/%s" % self.repo)
        if cod != 200:
            raise RuntimeError("no se puede acceder al repositorio %s (HTTP %s)" % (self.repo, cod))
        base = info["default_branch"]
        cod, ref = self._api("GET", "/repos/%s/git/ref/heads/%s" % (self.repo, base))
        if cod != 200:
            raise RuntimeError("no se encuentra la rama %s (HTTP %s)" % (base, cod))
        cod, _ = self._api("POST", "/repos/%s/git/refs" % self.repo,
                           {"ref": "refs/heads/" + self.rama, "sha": ref["object"]["sha"]})
        if cod not in (200, 201, 422):  # 422 = ya existía
            raise RuntimeError("no se pudo crear la rama %s (HTTP %s)" % (self.rama, cod))

    def _leer_remoto(self):
        cod, info = self._api("GET", "/repos/%s/contents/%s?ref=%s" % (self.repo, ARCHIVO, self.rama))
        if cod == 404:
            return None
        if cod != 200:
            raise RuntimeError("HTTP %s al leer %s" % (cod, ARCHIVO))
        self.sha = info.get("sha")
        contenido = info.get("content") or ""
        if not contenido and info.get("download_url"):   # archivos grandes: descarga directa
            req = urllib.request.Request(info["download_url"], headers={"Authorization": "Bearer " + self.token,
                                                                        "User-Agent": "c4-tiempo-real"})
            with urllib.request.urlopen(req, timeout=30) as r:
                bruto = r.read()
        else:
            bruto = base64.b64decode(contenido)
        return json.loads(gzip.decompress(bruto).decode("utf-8"))

    # ------------------------------------------------------------------ cargar (al arrancar)
    def cargar(self):
        """Recupera lo guardado y lo deja en el disco local (se mezcla con lo que ya hubiera)."""
        if not self.activo:
            return False
        try:
            with self.lock:
                paquete = self._leer_remoto()
            if paquete:
                materializar(paquete)
                dias = set(paquete.get("tramos") or {}) | set(paquete.get("salidas") or {})
                print("Historial recuperado de GitHub: %d días de aprendizaje, %d días de precisión" % (
                    len(dias), len(paquete.get("precision") or {})))
            else:
                print("Aún no hay historial guardado en GitHub (se creará en el primer guardado)")
            self.ultima_carga = datetime.now().strftime("%d/%m %H:%M")
            self.error = None
            return True
        except Exception as e:  # noqa: BLE001
            self.error = "No se pudo recuperar el historial de GitHub: %s" % e
            print("Aviso:", self.error)
            return False

    # ------------------------------------------------------------------ guardar (periódico y al apagar)
    def guardar(self):
        if not self.activo:
            return False
        try:
            datos = gzip.compress(json.dumps(empaquetar(), separators=(",", ":")).encode("utf-8"), 9)
            with self.lock:
                if datos == self._subido:
                    return True
                if self.sha is None:
                    self._asegurar_rama()
                    try:
                        self._leer_remoto()      # para conocer el sha del archivo si ya existe
                    except Exception:  # noqa: BLE001
                        pass
                for intento in range(2):
                    cuerpo = {"message": "historial %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "content": base64.b64encode(datos).decode("ascii"), "branch": self.rama}
                    if self.sha:
                        cuerpo["sha"] = self.sha
                    cod, info = self._api("PUT", "/repos/%s/contents/%s" % (self.repo, ARCHIVO), cuerpo)
                    if cod in (200, 201):
                        self.sha = info["content"]["sha"]
                        self._subido = datos
                        self.ultimo_guardado = datetime.now().strftime("%d/%m %H:%M")
                        self.error = None
                        return True
                    if cod in (409, 422) and intento == 0:   # el sha cambió: se vuelve a leer y se reintenta
                        self.sha = None
                        try:
                            self._leer_remoto()
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    raise RuntimeError("HTTP %s al guardar" % cod)
        except Exception as e:  # noqa: BLE001
            self.error = "No se pudo guardar el historial en GitHub: %s" % e
            print("Aviso:", self.error)
            return False

    def guardar_en_segundo_plano(self):
        threading.Thread(target=self.guardar, daemon=True).start()


# ---------------------------------------------------------------------- paquete <-> disco
CABECERA_PRECISION = ["fecha", "trip", "stop", "horizonte", "nuestra", "adif", "real"]


def empaquetar():
    """Lo que se guarda fuera: resumen de aprendizaje + mediciones de precisión recientes."""
    res = historial.resumen()
    precision = {}
    if os.path.isdir(historial.HIST):
        for fn in sorted(f for f in os.listdir(historial.HIST) if f.startswith("precision_"))[-DIAS_PRECISION:]:
            try:
                with open(os.path.join(historial.HIST, fn), encoding="utf-8") as f:
                    filas = [r for r in csv.reader(f)][1:]
                if filas:
                    precision[fn[10:18]] = filas
            except Exception:  # noqa: BLE001
                continue
    return {"version": 1, "generado": datetime.now().isoformat(timespec="seconds"),
            "tramos": res["tramos"], "salidas": res["salidas"], "precision": precision}


def materializar(paquete):
    """Escribe en el disco local lo recuperado, sin pisar lo que ya haya de hoy."""
    os.makedirs(historial.HIST, exist_ok=True)
    local = historial._leer_resumen()
    for clave in ("tramos", "salidas"):
        for fecha, valor in (paquete.get(clave) or {}).items():
            local[clave].setdefault(fecha, valor)
    historial.guardar_resumen(local)
    for fecha, filas in (paquete.get("precision") or {}).items():
        ruta = os.path.join(historial.HIST, "precision_%s.csv" % fecha)
        existentes = set()
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                existentes = {tuple(r) for r in list(csv.reader(f))[1:]}
        nuevas = [r for r in filas if tuple(r) not in existentes]
        if not nuevas:
            continue
        nuevo = not os.path.exists(ruta)
        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(CABECERA_PRECISION)
            w.writerows(nuevas)


def ciclo_guardado(almacen, cada_s=1200):
    """Hilo: guarda cada 20 minutos (solo si algo ha cambiado)."""
    while True:
        time.sleep(cada_s)
        almacen.guardar()
