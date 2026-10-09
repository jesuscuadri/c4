# -*- coding: utf-8 -*-
"""Avisos de incidencias de Adif para Cercanías Asturias.

Renfe no publica el tiempo real de los trenes de Asturias ni las incidencias en su fichero de avisos, pero Adif sí
las enseña en la página pública de cada estación («Se están produciendo retrasos en línea C-4… debido a un robo
de cable entre VERIÑA y GIJON-SANZ CRESPO»). Aquí se lee esa página de vez en cuando (pocas peticiones, con caché)
y se sacan esos avisos. Es una página web, no una API: si Adif cambia su diseño esto deja de ver avisos, y
nada más se rompe (la app sigue como antes).
"""
import html
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta

# estaciones grandes por las que pasan todas las líneas: cada una con direcciones alternativas por si una falla
ESTACIONES = {
    "Gijón": ["https://www.adif.es/w/15410-gij%C3%B3n-"],
    "Oviedo": ["https://www.adif.es/w/15211-oviedo", "https://www.adif.es/w/oviedo", "https://www.adif.es/en/w/15211-oviedo"],
}
CADA_S = 180
UA = "Mozilla/5.0 (compatible; c4-tiempo-real; uso personal)"
_FECHA = re.compile(r"^\s*(\d{2})/(\d{2})/(\d{4})\s*-\s*(\S.*)$", re.S)


def _texto(trozo):
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", trozo, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def lineas_de(texto):
    """Líneas de Cercanías que nombra el aviso, como «C4», «C5»… (vacío = no dice cuáles)."""
    out = []
    for m in re.finditer(r"l[ií]neas?\s+((?:C-?\d+[A-Za-z]?\s*(?:,|y|e|/|-)?\s*)+)", texto, flags=re.I):
        for n in re.findall(r"C-?(\d+)", m.group(1), flags=re.I):
            if "C" + n not in out:
                out.append("C" + n)
    return out


def parsear(pagina, hoy=None):
    """Avisos de una página de estación de Adif: [{texto, lineas, fecha}]. Solo los de hoy o ayer."""
    hoy = hoy or date.today()
    candidatos = []
    for fila in re.split(r"</tr\s*>", pagina, flags=re.I):
        fila = fila[fila.lower().rfind("<tr"):] if "<tr" in fila.lower() else fila
        candidatos.append(_texto(fila))
    if not any(_FECHA.match(c) for c in candidatos):                # otro diseño: se busca en todo el texto
        candidatos = re.findall(r"\d{2}/\d{2}/\d{4}\s*-\s*[^<>]{20,600}", html.unescape(pagina))
    out, vistos = [], set()
    for c in candidatos:
        m = _FECHA.match(c)
        if not m:
            continue
        try:
            fecha = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        cuerpo = re.sub(r"\s+", " ", m.group(4)).strip()
        if fecha < hoy - timedelta(days=1) or len(cuerpo) < 20 or cuerpo in vistos:
            continue
        vistos.add(cuerpo)
        out.append({"texto": "%s - %s" % (m.group(0).split("-")[0].strip(), cuerpo), "lineas": lineas_de(cuerpo), "fecha": fecha.isoformat()})
    return out


class Adif:
    def __init__(self, leer=None):
        self._leer = leer or self._http
        self.avisos = []
        self.ts = None
        self.error = None
        self.estado = {}                  # estación -> "ok" | error
        self._t = 0.0
        self._t_ok = 0.0
        self._ocupado = False

    @staticmethod
    def _http(url):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "replace")

    def actualizar(self):
        """Pide las páginas si toca (cada 3 min), en segundo plano. Llamar cuando se quiera: no bloquea."""
        if self._ocupado or time.time() - self._t < CADA_S:
            return
        self._ocupado = True
        self._t = time.time()
        threading.Thread(target=self._trabajo, daemon=True).start()

    def _trabajo(self):
        try:
            nuevos, ok = [], 0
            for nombre, urls in ESTACIONES.items():
                self.estado[nombre] = "sin respuesta"
                for u in urls:
                    try:
                        nuevos += parsear(self._leer(u))
                        self.estado[nombre] = "ok"
                        ok += 1
                        break
                    except Exception as e:  # noqa: BLE001
                        self.estado[nombre] = "%s: %s" % (type(e).__name__, e)
            if ok:
                vistos, unicos = set(), []
                for a in nuevos:
                    if a["texto"] not in vistos:
                        vistos.add(a["texto"])
                        unicos.append(a)
                self.avisos, self.ts, self.error = unicos, datetime.now().strftime("%H:%M:%S"), None
                self._t_ok = time.time()
            else:
                self.error = "No se pudo leer ninguna página de Adif"
                if self.avisos and time.time() - self._t_ok > 30 * 60:          # lo último es muy viejo: fuera
                    self.avisos = []
        finally:
            self._ocupado = False

    def para_lineas(self, lineas):
        """Textos de los avisos que afectan a alguna de esas líneas (o que no dicen línea)."""
        lineas = set(lineas or ())
        return [a["texto"] for a in self.avisos if not a["lineas"] or not lineas or set(a["lineas"]) & lineas]
