# -*- coding: utf-8 -*-
"""Simulación de la línea en vía única: calcula a qué hora llegará cada tren a cada estación."""
from collections import defaultdict

from .util import ahora_min, hm

MOTIVO_MIN = 0.5  # por debajo de esto no merece la pena explicar la espera


class Estimador:
    def __init__(self, linea, cfg, aprendidos=None, sesgos=None, salidas=None):
        self.L = linea
        self.cfg = cfg
        self.aprendidos = aprendidos or {}
        self.sesgos = sesgos or {}                 # {stop_id: min} corrección aprendida de errores
        self.salidas = salidas or {}               # {num: min} retraso típico con el que sale cada servicio
        self.sesgo_damp = cfg.get("sesgo_damp", 0.5)
        self.sesgo_cap = cfg.get("sesgo_cap", 1.0)
        self.adelanto = cfg.get("adelanto_llegada_min", 0.5)
        self.parada_defecto = cfg.get("parada_defecto_min", 1.0)   # si aún no se ha aprendido
        self.cruces_activos = []
        self.paradas = {}                          # {stop_id: min} duración real de cada parada (aprendida)
        self._gps_prev = {}                        # (tren, j) -> km máximo visto (evita retrocesos)
        self._avance = {}                          # tren -> (nivel, posición, minuto) lo más avanzado visto
        self._cruce_movido = {}                    # (tren, tren) -> apartadero al que se movió su cruce
        self._ultimo = {}                          # tren -> (retraso, minuto, j0, llegada) del último dato en directo
        self._material = {}                        # tren -> tren que llega y hace ese servicio (rotación)

    # ------------------------------------------------------------------ estado actual
    def _recorrido_teorico(self, v, j0, j1):
        """Minutos entre la salida de j0 y la llegada a j1 (con tiempos aprendidos si los hay)."""
        t = 0.0
        for j in range(j0, j1):
            t += self._recorrido(v, j, False)
        # las paradas intermedias sin parada comercial no suman
        return t

    def _estado_inicial(self, v, rt, ahora):
        e = self._estado_datos(v, rt, ahora)
        if e["con_datos"] and not e["cancelado"]:
            # último dato bueno de este tren: retraso, cuándo, dónde estaba y cuándo llegaba allí
            self._ultimo[v.id] = (e["retraso"] or 0.0, ahora, e["j0"], e["llegada0"])
        return e

    def _estado_datos(self, v, rt, ahora):
        """Dónde está el tren ahora: próxima estación j0, hora de llegada a ella y retraso actual."""
        L = self.L
        p = rt.pos.get(v.id)
        u = rt.act.get(v.id)
        n = len(v.k)
        e = {"j0": 0, "llegada0": v.sa[0], "parado": False, "fin": False, "cancelado": False,
             "retraso": (u["retraso"] if u else 0.0), "situacion": "",
             # el «retraso» de Renfe es lo que usa la app oficial (se guarda aparte para esa columna)
             "retraso_renfe": (u["retraso"] if u else None),
             # Renfe a veces manda el viaje sin parada ni retraso: eso no es un dato en directo
             "con_datos": bool(p or (u and (u.get("stop") or u.get("cancelado")))),
             "fuente": "horario"}
        if u and u.get("cancelado"):
            e.update(fin=True, cancelado=True, situacion="Cancelado")
            return e
        p = self._sin_retrocesos(v, p, ahora)
        if p and p["stop"] in v.stop_j:
            j = v.stop_j[p["stop"]]
            nombre = L.nombre[v.k[j]]
            if p["estado"] == "STOPPED_AT" or (j == 0 and not self.cfg.get("renfe_en_marcha_desde", True)):
                visto = rt.cuando(v.id, p["stop"], True) if p["estado"] == "STOPPED_AT" else None
                llegada = min(ahora, visto[0]) if visto else ahora
                if p.get("supuesto"):
                    llegada = min(ahora, p["supuesto"])
                if j == 0:
                    llegada = min(ahora, v.sa[0])
                e.update(j0=j, llegada0=llegada, parado=True, fuente="posición",
                         situacion=("En " if j == 0 else "Parado en ") + nombre)
                if p.get("supuesto"):
                    e.update(fuente="estimado", situacion="Ya debería estar en %s (Renfe no lo ha actualizado)" % nombre)
                if j == 0:
                    # En la estación de origen el «retraso» de Renfe no vale: es un contador que sube
                    # un minuto por minuto desde antes de la hora de salida (visto el 25/09: 70254 salió
                    # con +1,4 y Renfe decía +7). Se usa el propio: cuánto pasa de su hora de salida.
                    e["retraso"] = max(0.0, ahora - v.sd[0])
                else:
                    e["retraso"] = u["retraso"] if u else max(0.0, llegada - v.sa[j], ahora - v.sd[j])
                if j == n - 1:
                    e.update(fin=True, situacion="Llegado a " + nombre)
                return e
            # En marcha. Ojo con lo que significa en el tiempo real de Renfe (comprobado en directo):
            #   STOPPED_AT X  ->  IN_TRANSIT_TO X  ->  INCOMING_AT Y  ->  STOPPED_AT Y
            # «IN_TRANSIT_TO X» quiere decir que ACABA DE SALIR de X (Renfe no cambia la parada
            # hasta que se acerca a la siguiente); solo «INCOMING_AT Y» indica que llega a Y.
            saliendo = (p["estado"] != "INCOMING_AT" and j < n - 1
                        and self.cfg.get("renfe_en_marcha_desde", True))
            if saliendo:
                ja, jb = j, j + 1
                jn = next((i for i in range(j + 1, n) if v.para[i]), n - 1)
                arranque = rt.cuando(v.id, p["stop"], False)       # cuándo dejó de estar parado en X
            else:
                jb = j
                ja = max((i for i in range(j) if v.para[i]), default=0)
                jn = j
                # el arranque de la parada anterior es cuando pasó a «en marcha» allí
                arranque = rt.cuando(v.id, L.est[v.k[ja]], False)
            marcha = self._recorrido_teorico(v, ja, jb)
            vref = self.cfg.get("gps_vel_kmh", 0) or 0
            if vref and not (ja + 1 == jb and self._aprendido(v, ja)):
                # holgura del horario: en marcha, el tren no tarda mucho más que la distancia a
                # velocidad normal más arrancar y frenar
                tec = abs(L.km[v.k[jb]] - L.km[v.k[ja]]) / (vref / 60.0) + self.cfg.get("arranque_frenada_min", 0.6)
                marcha = min(marcha, tec)
            fiable = bool(arranque and arranque[1])
            gps = self._resto_por_gps(v, ja, jb, p, marcha, ahora)
            if gps is not None and fiable and gps[1] * abs(L.km[v.k[jb]] - L.km[v.k[ja]]) < 0.15:
                gps = None                  # la coordenada aún es la del andén: manda la hora de arranque
            if gps is not None:             # GPS del tren: sabemos cuánto le queda de verdad
                est, e["progreso"], e["km_gps"] = gps
                e["t_gps"] = ahora
                e["fuente"] = "GPS"
                if fiable:                  # y además vimos cuándo arrancó: se combinan
                    est = 0.75 * est + 0.25 * max(ahora, arranque[0] + marcha)
            elif fiable:                    # vimos el momento en que arrancó
                est = arranque[0] + marcha
                e["fuente"] = "posición"
            elif u and u["stop"] == L.est[v.k[jb]] and u["hora"]:
                est = u["hora"]
                e["fuente"] = "Renfe"
            elif saliendo:                  # acaba de salir, pero no sabemos cuándo exactamente
                est = ahora + marcha * 0.7
                e["fuente"] = "posición (aprox.)"
            else:                           # no sabemos cuándo salió: suponemos media marcha hecha
                est = max(ahora + marcha * 0.5, v.sa[jb] - 0.5)
                e["fuente"] = "posición (aprox.)"
            if p["estado"] == "INCOMING_AT":
                est = min(est, ahora + 1.0)
                if jb == n - 1:
                    # En la estación final Renfe deja el tren «entrando» hasta que borra el viaje, aunque
                    # ya esté en el andén (visto el 25/09: el 70223 llegó a Gijón a las 17:53 y siguió
                    # «entrando» hasta las 17:57). De «entrando» a «parado» pasa ~1 min (medido).
                    visto = rt.cuando(v.id, p["stop"], False)
                    if visto and ahora - visto[0] > 1.5:
                        llegada = visto[0] + 1.0
                        e.update(j0=jb, llegada0=llegada, parado=True, fin=True, fuente="posición",
                                 situacion="Llegado a " + L.nombre[v.k[jb]])
                        e["retraso"] = max(0.0, llegada - v.sa[jb])
                        return e
            tras = self.cfg.get("llegada_supuesta_tras_min", 5)
            est_n = None
            if tras and fiable and gps is None and jn > ja:
                # hora a la que llegaría a la próxima parada (la vía entre medias, a su ritmo)
                m_n = self._recorrido_teorico(v, ja, jn)
                if vref:
                    m_n = min(m_n, abs(L.km[v.k[jn]] - L.km[v.k[ja]]) / (vref / 60.0)
                              + self.cfg.get("arranque_frenada_min", 0.6))
                est_n = arranque[0] + m_n
            if est_n is not None and est_n < ahora - tras:
                jb, est = jn, est_n
                # Sabemos cuándo arrancó y ya tendría que haber llegado hace rato: está en la
                # estación siguiente aunque Renfe siga diciendo la anterior. Pasa sobre todo cuando
                # espera un cruce en la vía de apartado (visto el 26/09: el 70222 esperó 12 min en
                # Veriña y Renfe lo tuvo todo ese tiempo «en Tremañes»; decíamos que el 70315
                # tendría que esperarle en Veriña cuando el 70222 ya estaba allí).
                sint = {"stop": L.est[v.k[jb]], "estado": "STOPPED_AT", "supuesto": est,
                        "lat": None, "lon": None, "via": p.get("via")}
                self._avance[v.id] = (2 * jb, sint, ahora)
                return self._estado_datos_sintetico(v, sint, u, rt, ahora, e)
            llegada = max(ahora + 0.2, est)
            destino = L.nombre[v.k[jn]]
            if saliendo:
                sit = "Saliendo de %s · próxima %s" % (nombre, destino)
            elif p["estado"] == "INCOMING_AT":
                sit = "Entrando en " + nombre
            else:
                sit = "Hacia " + nombre
            e.update(j0=jb, llegada0=llegada, situacion=sit)
            e["retraso"] = u["retraso"] if u else max(0.0, llegada - v.sa[jb])
            return e
        if u and u["stop"] in v.stop_j:
            j = v.stop_j[u["stop"]]
            e.update(j0=j, llegada0=max(ahora, u["hora"] or v.sa[j] + e["retraso"]), fuente="Renfe",
                     situacion="Hacia " + L.nombre[v.k[j]] + " (sin posición)")
            if j == 0:
                e["parado"] = True
            return e
        # Sin ningún dato en directo. Si este servicio suele salir tarde (aprendido de días
        # anteriores), se cuenta con ese retraso en vez de suponerlo puntual.
        r = e["retraso"]
        tip = self.salidas.get(v.num, 0.0) if self.cfg.get("usar_retraso_tipico", True) else 0.0
        # ¿Lo vimos hace poco? (Renfe a veces deja de mandar un tren unos minutos, o se cae el
        # servicio entero, como el sábado 26/09 a mediodía). Entonces sigue con el último retraso
        # conocido, que es mucho mejor que suponerlo en hora de repente.
        ult = self._ultimo.get(v.id)
        if ult and ahora - ult[1] <= self.cfg.get("recordar_retraso_min", 45):
            r = max(r, ult[0])
            tip = 0.0
            e.update(retraso=r, fuente="último dato", ultimo_dato=round(ahora - ult[1]))
            if v.sa[-1] + r < ahora - 2:
                e.update(fin=True, j0=n - 1, situacion="Terminado")
                return e
            # Se parte de donde se le vio y se deja que la simulación lo haga avanzar: así respeta
            # los cruces. (Antes se le hacía avanzar con el horario y, si estaba esperando un cruce,
            # se «pasaba» el apartadero y el cruce se iba a otra estación. Visto el 25/09: 70226.)
            j = min(ult[2], n - 1)
            e.update(j0=j, llegada0=ult[3],
                     situacion="Renfe no da su posición ahora · %s iba %s" % (
                         "hace %d min" % round(ahora - ult[1]) if ahora - ult[1] >= 1 else "hace un momento",
                         "con +%d min" % round(r) if r >= 1 else "en hora"))
            return e
        if tip:
            e["tipico"] = tip
        if v.sd[0] + r + tip > ahora - 1:
            e.update(situacion="Aún no ha salido" + (" · suele salir con +%d min" % round(tip) if tip >= 1 else ""))
            return e
        if v.sa[-1] + r + tip < ahora - 2:
            e.update(fin=True, j0=n - 1, situacion="Terminado")
            return e
        j = next((j for j in range(n) if v.sa[j] + r + tip >= ahora), n - 1)
        e.update(j0=j, llegada0=max(ahora, v.sa[j] + r + tip),
                 situacion="Sin datos en tiempo real: se supone " + ("con su retraso habitual (+%d min)" % round(tip) if tip >= 1 else "en hora"))
        return e

    def _estado_datos_sintetico(self, v, sint, u, rt, ahora, e0):
        """Estado a partir de una posición deducida (no la de Renfe)."""
        guarda = rt.pos.get(v.id)
        rt.pos[v.id] = sint
        try:
            e = self._estado_datos(v, rt, ahora)
        finally:
            if guarda is None:
                rt.pos.pop(v.id, None)
            else:
                rt.pos[v.id] = guarda
        return e

    def _sin_retrocesos(self, v, p, ahora):
        """Renfe a veces hace «saltar» un tren a la estación anterior durante unas lecturas
        (visto en directo: Veriña ↔ Tremañes cada 15 s). Un tren no va marcha atrás: si la
        posición nueva está por detrás de la más avanzada vista hace poco, se sigue usando esa."""
        if not p or p["stop"] not in v.stop_j:
            return p
        j = v.stop_j[p["stop"]]
        # «IN_TRANSIT_TO X» = ya salió de X (va por delante de «parado en X»); «INCOMING_AT Y» = va antes de Y
        sale = 1 if self.cfg.get("renfe_en_marcha_desde", True) else -1
        nivel = 2 * j + (sale if p["estado"] == "IN_TRANSIT_TO" else (-1 if p["estado"] == "INCOMING_AT" else 0))
        previo = self._avance.get(v.id)
        if previo and nivel < previo[0] and ahora - previo[2] < self.cfg.get("retroceso_max_min", 12):
            return previo[1]
        if not previo or nivel >= previo[0]:
            self._avance[v.id] = (nivel, p, ahora)
        return p

    def _resto_por_gps(self, v, ja, jb, p, marcha, ahora):
        """Hora de llegada a j según dónde está el tren (GPS proyectado sobre la vía).

        El tren no va a velocidad constante: arranca, va lanzado y frena. Se usa un perfil
        trapezoidal (aceleración y frenada en unos 350 m) calibrado para que el tramo entero
        dure lo que dura de verdad («marcha»). Devuelve (hora, fracción recorrida) o None si
        la posición no es fiable (lejos de la vía, sin coordenadas o demasiado antigua)."""
        if not self.cfg.get("usar_gps", True) or p.get("lat") is None:
            return None
        L = self.L
        ka, kb = L.km[v.k[ja]], L.km[v.k[jb]]
        lo, hi = min(ka, kb), max(ka, kb)
        largo = hi - lo
        if largo < 0.2 or marcha <= 0:
            return None
        # Renfe a veces pone la coordenada exacta de una estación (la de salida, la de llegada o
        # incluso otra) en vez de la del tren: esa no dice nada de cuánto ha avanzado
        try:
            la, lo_ = float(p["lat"]), float(p["lon"])
        except (TypeError, ValueError):
            return None
        if any(abs(la - c[0]) < 0.0006 and abs(lo_ - c[1]) < 0.0008 for c in L.coord):
            return None
        pr = L.proyectar(la, lo_, lo - 0.3, hi + 0.3)
        if not pr or pr[1] > self.cfg.get("gps_max_km", 0.4):
            return None
        x = abs(pr[0] - ka)                       # km recorridos desde la parada anterior
        x = max(0.0, min(largo, x))
        # Renfe a veces devuelve una coordenada vieja (el tren «salta» hacia atrás): no se retrocede
        clave = (v.id, jb)
        previo = self._gps_prev.get(clave)
        if previo is not None and x < previo:
            x = previo
        self._gps_prev[clave] = x
        if len(self._gps_prev) > 400:
            self._gps_prev = {clave: x}
        # antigüedad real de la coordenada (Renfe la repite un rato entre actualizaciones);
        # se descuenta lo que el tren habrá avanzado desde entonces, como mucho 1 min
        desde = p.get("coord_desde")
        if desde is None and p.get("ts"):
            desde = ahora_min(p["ts"])
        edad = max(0.0, min(1.0, ahora - desde)) if desde is not None else 0.0
        if x < 0.15 or x > largo - 0.15:
            edad = 0.0                            # en el andén (o llegando): no se supone avance
        da = min(0.35, largo / 3.0)
        vel = (largo + 2 * da) / marcha           # km/min en crucero
        if x < da:
            t = 2 * (x * da) ** 0.5 / vel
        elif x <= largo - da:
            t = 2 * da / vel + (x - da) / vel
        else:
            t = marcha - 2 * ((largo - x) * da) ** 0.5 / vel
        resto = max(0.0, marcha - t)
        # El horario lleva holgura en muchos tramos: un tren en marcha no va más lento de lo que
        # le permite la vía, así que lo que le falta no puede ser mucho más que la distancia que
        # le queda a velocidad normal (más la frenada). Solo se usa si el tramo no está aprendido.
        vref = self.cfg.get("gps_vel_kmh", 0) or 0
        if vref and not (self.aprendidos and ja + 1 == jb and self._aprendido(v, ja)):
            resto = min(resto, (largo - x) / (vref / 60.0) + 0.3)
        resto -= edad
        km = ka + x if kb >= ka else ka - x
        return ahora + max(0.2, resto), round(x / largo, 3), round(km, 3)

    def _rotaciones_por_via(self, rt):
        """Renfe indica la vía: si un tren espera en la vía 12 de Gijón y otro está entrando
        en esa misma vía, el que entra es el que hará el servicio."""
        L = self.L
        out = []
        for sig in L.viajes.values():
            p = rt.pos.get(sig.id)
            if not p or not p.get("via") or p["stop"] != L.est[sig.k[0]] or p["estado"] != "STOPPED_AT":
                continue
            for ant in L.viajes.values():
                q = rt.pos.get(ant.id)
                if (ant is not sig and q and q.get("via") == p["via"] and q["stop"] == p["stop"]
                        and q["estado"] != "STOPPED_AT" and ant.k[-1] == sig.k[0]):
                    out.append((ant, sig))
        return out

    def _rotaciones_reales(self, viajes, estados, A0):
        """Qué tren da la vuelta en cabecera para cada salida, con las horas de llegada estimadas."""
        cfg = self.cfg
        vmin, esp = cfg["vuelta_minima_min"], cfg["rotacion_espera_max_min"]
        out = []
        cabeceras = {v.k[0] for v in viajes}
        for K in cabeceras:
            llegan = sorted(((A0[a.id][-1], a) for a in viajes
                             if a.k[-1] == K and not estados[a.id]["cancelado"] and A0[a.id][-1] is not None),
                            key=lambda x: x[0])
            usados = set()
            for sig in sorted((v for v in viajes if v.k[0] == K and not estados[v.id]["cancelado"]),
                              key=lambda v: v.sd[0]):
                for a_lleg, ant in llegan:
                    if ant.id in usados or ant.dir == sig.dir:
                        continue
                    margen = min(vmin, max(2.0, sig.sd[0] - ant.sa[-1]))
                    if a_lleg < sig.sd[0] - 90:
                        continue            # llegó hace mucho: ese tren se habrá guardado
                    if a_lleg + margen > sig.sd[0] + esp:
                        break               # los que quedan llegan aún más tarde: sale con otro tren
                    usados.add(ant.id)
                    out.append((ant, sig, margen))
                    break
        return out

    # ------------------------------------------------------------------ marcha
    def _parada_tipica(self):
        """Parada de una estación de la que aún no se ha aprendido nada: la mediana de las
        aprendidas en las demás o, si no hay ninguna, el valor por defecto."""
        if self.paradas:
            vals = sorted(self.paradas.values())
            return vals[len(vals) // 2]
        return self.parada_defecto

    def _adelanto_en(self, v, j):
        """Cuánto antes de la hora del horario se da por llegado el tren al final del tramo j."""
        if not v.para[j + 1]:
            return 0.0
        return max(0.0, min(self.adelanto, (v.sa[j + 1] - v.sd[j]) - 0.3))

    def _aprendido(self, v, j):
        """¿El tramo j -> j+1 usa el tiempo de marcha aprendido de días anteriores?"""
        return bool(self.aprendidos and v.para[j] and v.para[j + 1]
                    and (self.L.est[v.k[j]], self.L.est[v.k[j + 1]]) in self.aprendidos)

    def _recorrido(self, v, j, tarde):
        base = max(0.3, v.sa[j + 1] - v.sd[j])
        capado = False
        if self._aprendido(v, j):
            clave = (self.L.est[v.k[j]], self.L.est[v.k[j + 1]])
            base = min(max(self.aprendidos[clave], base * 0.35), base * 1.8)
        else:
            # Holgura del horario: hay tramos con muchísimo margen (Gijón→Tremañes: 6 min en el
            # horario para 1,9 km que se hacen en 2). Si el horario da claramente más de lo que
            # permite la vía, se cuenta lo que tarda de verdad; el tren luego espera en la estación
            # a su hora de salida (nunca sale antes).
            ratio = self.cfg.get("holgura_ratio", 0) or 0
            vref = self.cfg.get("gps_vel_kmh", 0) or 0
            if ratio and vref:
                dist = abs(self.L.km[v.k[j + 1]] - self.L.km[v.k[j]])
                af = self.cfg.get("arranque_frenada_min", 0.6) / 2
                tec = dist / (vref / 60.0) + (af if v.para[j] else 0) + (af if v.para[j + 1] else 0)
                if base > ratio * tec:
                    base, capado = max(tec, 0.3), True
        if not self._aprendido(v, j) and v.para[j + 1]:
            # Renfe da el tren por «parado» en cuanto entra en la estación (antes de detenerse del
            # todo) y por «en marcha» cuando ya ha salido: la llegada real es algo antes de lo que
            # sale de sumar la marcha del horario. Ese tiempo se pasa a la parada (ver _resolver).
            base -= self._adelanto_en(v, j)
        if tarde and self.cfg["recuperacion"] > 0 and not self._aprendido(v, j) and not capado:
            # con retraso, el tren no aprovecha la holgura del horario: va más rápido de lo que dice
            # (medido en directo). Si el tramo ya está aprendido, ese tiempo ya es el real.
            base *= 1 - self.cfg["recuperacion"]
        # corrección aprendida de los fallos: si en la estación de llegada solemos
        # equivocarnos siempre en el mismo sentido, ajustamos (amortiguado y acotado)
        if self.sesgos:
            c = self.sesgos.get(self.L.est[v.k[j + 1]])
            if c is not None:
                base = max(0.2, base + max(-self.sesgo_cap, min(self.sesgo_cap, self.sesgo_damp * c)))
        return base

    def _resolver(self, viajes, estados, deps, ahora):
        A = {v.id: [None] * len(v.k) for v in viajes}
        D = {v.id: [None] * len(v.k) for v in viajes}
        por_que = {v.id: {} for v in viajes}
        for v in viajes:  # lo ya recorrido: horario + retraso, nunca después de ahora
            e = estados[v.id]
            r = e["retraso"] or 0.0
            hasta = len(v.k) if e["fin"] else e["j0"]
            for j in range(hasta):
                A[v.id][j] = min(v.sa[j] + r, ahora)
                D[v.id][j] = min(v.sd[j] + r, ahora)
            if e["fin"] and e["parado"]:
                A[v.id][-1] = D[v.id][-1] = e["llegada0"]
        orden = sorted(viajes, key=lambda v: v.sd[0])
        pmin = self.cfg["parada_minima_min"]
        for _ in range(300):
            cambio = False
            for v in orden:
                e = estados[v.id]
                if e["fin"]:
                    continue
                a_v, d_v = A[v.id], D[v.id]
                for j in range(e["j0"], len(v.k)):
                    if j == e["j0"]:
                        a = e["llegada0"]
                    else:
                        a = d_v[j - 1] + self._recorrido(v, j - 1, d_v[j - 1] > v.sd[j - 1] + 0.5)
                    if a_v[j] is None or abs(a - a_v[j]) > 1e-6:
                        cambio = True
                    a_v[j] = a
                    if j == len(v.k) - 1:
                        d_v[j] = a
                        break
                    # la parada programada ya va en el horario; si va tarde puede acortarla
                    if not v.para[j]:
                        parada = 0.0
                    elif j > 0 and self._aprendido(v, j - 1):
                        # la llegada viene de un tiempo de marcha aprendido (de «sale» a «entra»,
                        # como lo marca Renfe): hay que sumar lo que dura de verdad la parada
                        parada = self.paradas.get(self.L.est[v.k[j]], self._parada_tipica())
                    else:
                        # la parada programada ya va en el horario; si va tarde puede acortarla.
                        # Lo que se adelantó la llegada (ver _recorrido) se pasa parado.
                        parada = min(v.sd[j] - v.sa[j], pmin) + (self._adelanto_en(v, j - 1) if j > 0 else 0.0)
                        if j > 0 and a > v.sa[j] + 0.5 + self.cfg.get("salida_tras_hora_min", 0.0):
                            # tren con retraso (no tiene margen del horario que absorba la parada):
                            # medido en directo, entre «entra» y «sale» pasa como mínimo ~1 min; si ya se
                            # ha aprendido lo que dura la parada en esta estación, se usa eso
                            parada = max(parada, self.paradas.get(self.L.est[v.k[j]], self.cfg.get("parada_real_min", 0.0)))
                    # nunca sale antes de su hora (y Renfe lo da por salido unos segundos después)
                    base = max(v.sd[j] + (self.cfg.get("salida_tras_hora_min", 0.0) if j > 0 else 0.0), a + max(0.0, parada))
                    if j == e["j0"]:
                        base = max(base, ahora + (0.1 if e["parado"] else 0.0))
                        if j == 0 and e["retraso"] and e["con_datos"]:
                            base = max(base, v.sd[0] + e["retraso"])
                        elif j == 0 and e.get("tipico"):
                            base = max(base, v.sd[0] + e["tipico"])
                    d, motivo = base, None
                    for tipo, otro, oj, margen, info, tope in deps.get((v.id, j), ()):
                        val = A[otro.id][oj]
                        if val is None:
                            continue
                        val += margen
                        if tope is not None:
                            val = min(val, tope)
                        if val > d + 1e-6:
                            d, motivo = val, (tipo, otro, oj, info)
                    d = min(d, max(ahora, v.sd[j]) + 240)  # tope de seguridad
                    if d_v[j] is None or abs(d - d_v[j]) > 1e-6:
                        cambio = True
                    d_v[j] = d
                    if motivo and d - base > MOTIVO_MIN:
                        por_que[v.id][j] = (motivo, d - base)
                    else:
                        por_que[v.id].pop(j, None)
            if not cambio:
                break
        return A, D, por_que

    # ------------------------------------------------------------------ cálculo completo
    def calcular(self, rt, ahora=None):
        L, cfg = self.L, self.cfg
        ahora = ahora_min() if ahora is None else ahora
        viajes = list(L.viajes.values())
        estados = {v.id: self._estado_inicial(v, rt, ahora) for v in viajes}

        def pasado(v, k):
            e = estados[v.id]
            return e["fin"] or v.pos[k] < e["j0"]

        deps = defaultdict(list)

        def dep(v, j, tipo, otro, oj, margen, info=None, tope=None):
            deps[(v.id, j)].append((tipo, otro, oj, margen, info, tope))

        for lead, fol, k, k2 in L.seguimientos:
            dep(fol, fol.pos[k], "seguimiento", lead, lead.pos[k2], cfg["margen_seguimiento_min"], k2)
        m_cruce = cfg["margen_cruce_min"]
        for b, a, k in L.ordenes:  # vía única: el que sale espera al último que venía de frente
            if not pasado(b, k):
                jb, ja = b.pos[k], a.pos[k]
                dep(b, jb, "tramo", a, ja, max(0.0, min(m_cruce, b.sd[jb] - a.sa[ja])))
        # rotaciones de material en cabecera
        # Ojo: Renfe pone el siguiente servicio «parado en Gijón» unos 30 min antes de su hora aunque
        # el tren que lo va a hacer todavía venga de camino (visto el 25/09: el 70226 de las 17:56
        # figuraba en el andén mientras el 70223, que era el mismo tren, llegaba a las 17:53). Así
        # que aparecer en origen NO quiere decir que el material esté allí: la rotación manda siempre.
        self._material = {}
        con_rot = set()
        for ant, sig, margen in L.rotaciones:
            con_rot.add(sig.id)
            if not estados[ant.id]["cancelado"]:
                dep(sig, 0, "rotacion", ant, len(ant.k) - 1, margen,
                    tope=sig.sd[0] + cfg["rotacion_espera_max_min"])
                self._material[sig.id] = ant
        # la vía de Renfe solo sirve para los que no tienen rotación deducida del horario
        # (en Gijón todos figuran en la vía 12, así que no distingue nada)
        for ant, sig in self._rotaciones_por_via(rt):
            if sig.id not in con_rot:
                dep(sig, 0, "rotacion", ant, len(ant.k) - 1, cfg["vuelta_minima_min"],
                    tope=sig.sd[0] + 3 * cfg["rotacion_espera_max_min"])

        # 1ª pasada sin cruces: hora a la que llegaría cada tren si nadie le cortase el paso
        A0, _, _ = self._resolver(viajes, estados, deps, ahora)
        if cfg.get("rotaciones_en_directo", True):
            # Con las llegadas reales ya estimadas, se rehace qué tren hace cada salida de cabecera: el
            # primero que haya llegado (y haya podido dar la vuelta) hace la siguiente salida. Con retrasos
            # grandes cambia respecto al horario (visto el 23/09: el 70203, con +30, no hizo el 70208 de
            # las 7:56 —salió con otro tren— sino el 70306 de las 8:23).
            nuevas = self._rotaciones_reales(viajes, estados, A0)
            for clave in list(deps):
                deps[clave] = [d for d in deps[clave] if d[0] != "rotacion"]
            self._material = {}
            for ant, sig, margen in nuevas:
                dep(sig, 0, "rotacion", ant, len(ant.k) - 1, margen,
                    tope=sig.sd[0] + cfg["rotacion_espera_max_min"])
                self._material[sig.id] = ant
            A0, _, _ = self._resolver(viajes, estados, deps, ahora)

        def poner_cruce(a, b, k, info, programado):
            m = cfg["margen_cruce_min"]
            ja, jb = a.pos[k], b.pos[k]
            ma = max(0.0, min(m, a.sd[ja] - b.sa[jb])) if programado else m
            mb = max(0.0, min(m, b.sd[jb] - a.sa[ja])) if programado else m
            if k != b.k[0] and ja < len(a.k) - 1 and not pasado(a, k):
                dep(a, ja, "cruce", b, jb, ma, info)
            if k != a.k[0] and jb < len(b.k) - 1 and not pasado(b, k):
                dep(b, jb, "cruce", a, ja, mb, info)

        self.cruces_activos = []
        for a, b, k in L.cruces:  # a va hacia Cudillero/Avilés, b hacia Gijón
            pa, pb = pasado(a, k), pasado(b, k)
            if pa and pb:
                continue
            elegido, info = k, "programado"
            largo = cfg.get("cruce_mover_si_espera_min", 8)
            if pa != pb or cfg["cruces"] == "dinamicos" or largo:
                cands = [c for c in sorted(L.apartaderos)
                         if c in a.pos and c in b.pos and not pasado(a, c) and not pasado(b, c)]
                if not cands:
                    continue
                coste = {c: abs(A0[a.id][a.pos[c]] - A0[b.id][b.pos[c]]) for c in cands}
                if pa != pb:
                    elegido, info = min(cands, key=lambda c: coste[c]), "movido"
                elif cfg["cruces"] == "dinamicos" and k in cands and coste[k] > cfg["umbral_cambio_cruce_min"]:
                    mejor = min(cands, key=lambda c: coste[c] + (0 if c == k else 1))
                    if coste[k] - coste[mejor] > 2:
                        elegido, info = mejor, "movido"
                elif largo and k in cands and coste[k] > largo:
                    # Con un retraso grande, el puesto de mando no deja al otro tren esperando un cuarto
                    # de hora en el apartadero del horario: adelanta el cruce al siguiente apartadero
                    # (visto el 26/09: el 70320 no esperó en Candás al 70315, que iba 20 min tarde; se
                    # cruzaron en Regueral). Nunca en la cabecera de uno de los dos, y sin cambiar de
                    # idea a cada momento (se mantiene el elegido mientras siga valiendo).
                    extremos = {a.k[0], a.k[-1], b.k[0], b.k[-1]}
                    # El que esperaba en el cruce del horario (el que llega antes) es el que va en hora:
                    # se le deja avanzar hasta otro apartadero, pero sigue siendo él quien espera. Nunca
                    # se traslada el cruce a un sitio donde el que va tarde tuviera que esperar al otro
                    # (visto el 26/09: se decía que el 70315, con +10, esperaría 7 min en Perlora al
                    # 70222; en realidad el 70222 le esperó en Veriña y el 70315 pasó sin parar).
                    espera_a = A0[a.id][a.pos[k]] <= A0[b.id][b.pos[k]]
                    buenos = [c for c in cands if c not in extremos
                              and (A0[a.id][a.pos[c]] <= A0[b.id][b.pos[c]]) == espera_a]
                    previo = self._cruce_movido.get((a.id, b.id))
                    if buenos:
                        mejor = min(buenos, key=lambda c: coste[c])
                        # sin cambiar de idea a cada lectura: se mantiene el elegido mientras siga valiendo
                        if previo in buenos and coste[previo] - coste[mejor] <= 3:
                            mejor = previo
                        if coste[k] - coste[mejor] > (1 if mejor == previo else 4):
                            elegido, info = mejor, "movido"
                            self._cruce_movido[(a.id, b.id)] = mejor
            poner_cruce(a, b, elegido, info, elegido == k)
            self.cruces_activos.append((a, b, elegido, info, k))

        A, D, por_que = self._resolver(viajes, estados, deps, ahora)
        return self._salida(viajes, estados, A, D, por_que, ahora, rt)

    # ------------------------------------------------------------------ resultado
    def _mezclar(self, v, e, a_v, d_v, r, ahora):
        """A mucha distancia, la simulación acumula incertidumbre (cruces que se mueven, retrasos
        que se recuperan...). Medido con datos reales de la C-4: a partir de ~5 min, mezclar lo
        calculado con «horario + retraso actual» reduce el error. Cerca, manda la simulación.
        El peso de la simulación baja de 1 (a 5 min) a peso_min (a 20 min o más); si ya hay muchos
        tramos aprendidos, la simulación es mejor y se le da más peso."""
        if not self.cfg.get("mezcla_oficial", True) or e["fin"] or not e["con_datos"]:
            return a_v, d_v
        h0, h1 = self.cfg.get("mezcla_desde_min", 5.0), self.cfg.get("mezcla_hasta_min", 20.0)
        pmin = self.cfg.get("mezcla_peso_min", 0.6)
        if len(self.aprendidos) >= 20:
            pmin = max(pmin, self.cfg.get("mezcla_peso_min_aprendido", 0.75))

        def peso(t):
            h = t - ahora
            if h <= h0:
                return 1.0
            return 1.0 - (1.0 - pmin) * min(1.0, (h - h0) / max(0.1, h1 - h0))

        a2, d2 = list(a_v), list(d_v)
        previo = None
        for j in range(len(v.k)):
            if a2[j] is None:
                continue
            if j >= e["j0"] and a2[j] > ahora:
                w = peso(a2[j])
                a2[j] = w * a2[j] + (1 - w) * (v.sa[j] + r)
                if d2[j] is not None:
                    wd = peso(d2[j])
                    d2[j] = max(wd * d2[j] + (1 - wd) * (v.sd[j] + r), v.sd[j] if v.para[j] else 0.0)
            # coherencia: nunca antes de ahora, ni antes de salir de la anterior, ni salir antes de llegar
            if j >= e["j0"]:
                a2[j] = max(a2[j], ahora if not (j == e["j0"] and e["parado"]) else a2[j])
                if previo is not None:
                    a2[j] = max(a2[j], previo)
                if d2[j] is not None:
                    d2[j] = max(d2[j], a2[j])
            previo = d2[j] if d2[j] is not None else a2[j]
        return a2, d2

    def _salida(self, viajes, estados, A, D, por_que, ahora, rt):
        L = self.L
        trenes = []
        for v in viajes:
            e = estados[v.id]
            motivos = []
            for j, ((tipo, otro, oj, info), espera) in sorted(por_que[v.id].items()):
                sitio = L.nombre[v.k[j]]
                if tipo == "cruce":
                    txt = "Espera en %s a que llegue el tren que va a %s" % (sitio, _corto(L.nombre[otro.k[-1]]))
                    if info == "movido":
                        txt += " · cruce trasladado"
                elif tipo == "tramo":
                    txt = "Espera en %s a que llegue el tren que viene de frente por la vía única (va a %s)" % (
                        sitio, _corto(L.nombre[otro.k[-1]]))
                elif tipo == "seguimiento":
                    txt = "Va detrás de otro tren (el que va a %s): no sale de %s hasta que ese llegue a %s" % (
                        _corto(L.nombre[otro.k[-1]]), sitio, _corto(L.nombre[info]))
                elif estados[otro.id]["fin"]:
                    txt = "Sale cuando dé la vuelta el tren que acaba de llegar de %s (es el mismo tren)" % _corto(L.nombre[otro.k[0]])
                else:
                    txt = "Espera a que llegue el tren que viene de %s: es el que hace este servicio" % _corto(L.nombre[otro.k[0]])
                motivos.append({"j": j, "k": v.k[j], "texto": txt, "min": round(espera, 1), "tipo": tipo,
                                "con": otro.num})
            # ¿El tren que hará este servicio aún viene de camino? Entonces no está en el andén,
            # aunque Renfe lo ponga «parado en origen» (lo hace media hora antes de la salida).
            material = None
            ant = self._material.get(v.id)
            if (ant is not None and e["j0"] == 0 and not e["fin"] and not estados[ant.id]["fin"]
                    and not estados[ant.id]["cancelado"] and A[ant.id][-1] is not None
                    and (estados[ant.id]["con_datos"] or estados[ant.id].get("ultimo_dato") is not None)
                    and A[ant.id][-1] + self.cfg["vuelta_minima_min"] <= v.sd[0] + self.cfg["rotacion_espera_max_min"]):
                llega = A[ant.id][-1]
                material = {"num": ant.num, "id": ant.id, "de": L.nombre[ant.k[0]], "llega": round(llega, 2)}
                e = dict(e, parado=False,
                         situacion="Aún no está en el andén: lo hace el tren que viene de %s (llega %s)" % (
                             _corto(L.nombre[ant.k[0]]), hm(llega)))
            p = rt.pos.get(v.id)
            r = e["retraso"] or 0.0
            r_adif = e["retraso_renfe"] if e.get("retraso_renfe") is not None else r   # lo que diría la app oficial
            j0 = e["j0"]
            if motivos and not self.cfg.get("mezcla_con_esperas", False):
                est_a, est_d = A[v.id], D[v.id]     # espera explicada (cruce, vía única...): manda la simulación
            else:
                est_a, est_d = self._mezclar(v, e, A[v.id], D[v.id], r, ahora)
            trenes.append({
                "id": v.id, "num": v.num, "dir": v.dir,
                "origen": L.nombre[v.k[0]], "destino": L.nombre[v.k[-1]],
                "situacion": e["situacion"], "fin": e["fin"], "cancelado": e["cancelado"],
                "j0": j0, "parado": e["parado"], "con_datos": e["con_datos"], "fuente": e["fuente"],
                "retraso": round(r, 1),
                "tipico": round(e.get("tipico", 0.0), 1),
                "ultimo_dato": e.get("ultimo_dato"),
                "km_gps": e.get("km_gps"), "t_gps": round(e["t_gps"], 3) if e.get("t_gps") else None,
                "k": v.k, "para": v.para,
                "prog_a": [round(x, 2) for x in v.sa], "prog_d": [round(x, 2) for x in v.sd],
                "est_a": [None if x is None else round(x, 3) for x in est_a],
                "est_d": [None if x is None else round(x, 3) for x in est_d],
                # lo que calcularía la app oficial: horario + retraso actual, igual en todo el recorrido
                "adif_a": [round(x + r_adif, 2) for x in v.sa],
                "adif_d": [round(x + r_adif, 2) for x in v.sd],
                "motivos": motivos,
                "material": material,
                "via": p.get("via") if p else None,
            })
        cruces = []
        for a, b, k, info, k0 in self.cruces_activos:
            ja, jb = a.pos[k], b.pos[k]
            aa, ab = A[a.id][ja], A[b.id][jb]
            if aa is None or ab is None:
                continue
            da, db = D[a.id][ja], D[b.id][jb]
            espera_a = (da - aa) if da is not None else 0
            espera_b = (db - ab) if db is not None else 0
            extra = {}
            for t, jj in ((a, ja), (b, jb)):
                m = por_que[t.id].get(jj)
                extra[t.num] = round(m[1], 1) if m and m[0][0] == "cruce" else 0
            cruces.append({"k": k, "estacion": L.nombre[k], "programado": L.nombre[k0], "info": info,
                           "hora": round(max(aa, ab), 2),
                           "ida": {"num": a.num, "id": a.id, "llega": round(aa, 2),
                                   "sale": None if da is None else round(da, 2),
                                   "espera": round(espera_a, 1), "retraso_extra": extra[a.num]},
                           "vuelta": {"num": b.num, "id": b.id, "llega": round(ab, 2),
                                      "sale": None if db is None else round(db, 2),
                                      "espera": round(espera_b, 1), "retraso_extra": extra[b.num]}})
        cruces.sort(key=lambda c: c["hora"])
        return {"ahora": round(ahora, 3), "trenes": trenes, "cruces": cruces}


def _corto(nombre):
    return nombre.replace("Gijón-Sanz Crespo", "Gijón").replace("-Apeadero", " Apd.")


def viajes_entre(res, origen, destino, limite=6):
    """Próximos trenes que paran en origen y después en destino."""
    ahora = res["ahora"]
    filas = []
    for t in res["trenes"]:
        if origen not in t["k"] or destino not in t["k"] or t["fin"]:
            continue
        jo, jd = t["k"].index(origen), t["k"].index(destino)
        if jo >= jd or not t["para"][jo] or not t["para"][jd] or t["j0"] > jo:
            continue
        sal = t["est_d"][jo]
        if sal is None or sal < ahora - 0.5:
            continue
        filas.append({"tren": t, "jo": jo, "jd": jd,
                      "motivos": [m for m in t["motivos"] if jo <= m["j"] < jd],
                      "motivos_antes": [m for m in t["motivos"] if m["j"] < jo]})
    filas.sort(key=lambda f: f["tren"]["est_d"][f["jo"]])
    return filas[:limite]
