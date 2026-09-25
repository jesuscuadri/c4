#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C-4 Cercanías Asturias (Gijón – Cudillero): hora de llegada real teniendo en cuenta
los cruces en vía única, las rotaciones de material y los trenes de delante.

    python c4_tiempo_real.py                      abre la web local (http://localhost:8765)
    python c4_tiempo_real.py --movil              además accesible desde el móvil en la misma wifi
    python c4_tiempo_real.py --consulta Xivares Gijon
    python c4_tiempo_real.py --actualizar-horario

Solo necesita Python 3.8 o superior (biblioteca estándar).
"""
import argparse
import glob
import os
import signal
import sys
import threading
import time

if sys.version_info < (3, 8):
    sys.exit("Hace falta Python 3.8 o superior.")

from c4 import gtfs  # noqa: E402
from c4.app import App, servir  # noqa: E402
from c4.estimador import viajes_entre  # noqa: E402
from c4.util import CACHE, cargar_config, hm  # noqa: E402


def imprimir_consulta(app, origen, destino):
    L, res = app.linea, app.res
    try:
        o, d = L.buscar(origen), L.buscar(destino)
    except KeyError as e:
        sys.exit("No encuentro la estación %s. Estaciones: %s" % (e, ", ".join(L.nombre)))
    calidad = {"directo": "tiempo real", "congelado": "Renfe no está actualizando: se usa el horario",
               "sin_conexion": "sin conexión con Renfe: se usa el horario"}[res["calidad"]]
    print("\n%s → %s · %s · %s" % (L.nombre[o], L.nombre[d], hm(res["ahora"]), calidad))
    if res.get("error"):
        print("  (%s)" % res["error"])
    for aviso in res.get("avisos", []):
        print("  AVISO RENFE: " + aviso)
    filas = viajes_entre(res, o, d)
    if not filas:
        print("  No hay más trenes directos hoy.")
    for f in filas:
        t, jo, jd = f["tren"], f["jo"], f["jd"]
        dif = t["est_a"][jd] - t["adif_a"][jd]
        print("\n  Tren %s → %s · %s" % (t["num"], t["destino"], t["situacion"]))
        print("    %-6s %9s %11s %10s" % ("", "horario", "app Adif", "estimado"))
        print("    %-6s %9s %11s %10s" % ("Sale", hm(t["prog_d"][jo]), hm(t["adif_d"][jo]), hm(t["est_d"][jo])))
        print("    %-6s %9s %11s %10s%s" % ("Llega", hm(t["prog_a"][jd]), hm(t["adif_a"][jd]), hm(t["est_a"][jd]),
                                          "   (+%d min sobre la app)" % round(dif) if dif >= 1 else ""))
        for m in f["motivos_antes"] + f["motivos"]:
            print("      · %s (+%.0f min)" % (m["texto"], m["min"]))


def main():
    for flujo in (sys.stdout, sys.stderr):  # la consola de Windows no siempre es UTF-8
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Llegada real C-4 Asturias con cruces en vía única")
    ap.add_argument("--consulta", nargs=2, metavar=("ORIGEN", "DESTINO"), help="consulta en consola y sale")
    ap.add_argument("--movil", action="store_true", help="accesible desde otros dispositivos de tu wifi")
    ap.add_argument("--sin-navegador", action="store_true", help="no abrir el navegador")
    ap.add_argument("--actualizar-horario", action="store_true", help="vuelve a descargar el horario de Renfe")
    ap.add_argument("--puerto", type=int, help="puerto de la web local (8765 por defecto)")
    args = ap.parse_args()
    cfg = cargar_config()
    if args.puerto:
        cfg["puerto"] = args.puerto
    # Modo servidor en internet (Render y similares ponen la variable PORT)
    en_internet = bool(os.environ.get("PORT"))
    if en_internet:
        cfg["puerto"] = int(os.environ["PORT"])
        os.environ["TZ"] = os.environ.get("C4_ZONA", "Europe/Madrid")  # los servidores suelen ir en UTC
        if hasattr(time, "tzset"):
            time.tzset()
        print("Modo servidor en internet · puerto %d · hora local %s" % (cfg["puerto"], time.strftime("%H:%M")))
    if args.actualizar_horario:
        gtfs.obtener_zip(forzar=True)
        for f in glob.glob(os.path.join(CACHE, "*.json")):
            os.remove(f)
        print("Horario actualizado.")
    app = App(cfg)
    if args.consulta:
        app.ciclo()
        imprimir_consulta(app, *args.consulta)
        return
    # el horario se prepara en segundo plano: la web abre ya y muestra «Preparando el horario…»
    threading.Thread(target=app.bucle, daemon=True).start()
    if en_internet:
        # Render avisa con SIGTERM antes de dormir o redesplegar el servidor: último guardado
        def al_apagar(signum, frame):
            print("Apagando: guardando lo aprendido…")
            try:
                app.almacen.guardar()
            finally:
                os._exit(0)
        signal.signal(signal.SIGTERM, al_apagar)
    try:
        servir(app, abrir=not (args.sin_navegador or en_internet), en_red=args.movil or en_internet,
               publico=en_internet)
    except KeyboardInterrupt:
        print("\nHasta luego.")
    except OSError as e:
        sys.exit("No se pudo abrir el puerto %d (%s). ¿Está ya abierto el programa? Prueba --puerto 8766"
                 % (cfg["puerto"], e))


if __name__ == "__main__":
    main()
