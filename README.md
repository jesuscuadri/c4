# Transporte público de Asturias en tiempo real

App web (también se instala en el móvil) con **todo el transporte público de Asturias**:

- **Trenes.** Cercanías de toda Asturias (C-1 a C-8 y las demás), regionales (FEVE, León) y larga distancia (AVE, Alvia, Avlo). La hora de llegada es la **real**: horario de Renfe + tiempo real (GTFS-Realtime) + simulación de los cruces en vía única. Hay planificador «puerta a puerta» con transbordos y bus urbano.
- **Bus de Gijón (EMTUSA).** En directo, con la API pública de EMTUSA.
- **Bus del Consorcio de Transportes de Asturias.** Urbanos de Oviedo (TUA), Avilés y Mieres (EMUTSA) e **interurbanos de toda Asturias** (ALSA y el resto de operadores). Con el horario oficial (GTFS): el Consorcio no publica dónde va cada autobús, así que sus posiciones son estimadas y la app lo dice. Los interurbanos tienen un buscador de viaje de A a B (hoy y mañana) que respeta dónde se puede subir y bajar en cada línea.
- **Cerca de mí.** Desde el inicio: la estación más cercana con sus próximos trenes, las paradas de bus de Gijón y las del Consorcio.

Empezó como «C-4 en tiempo real · Gijón – Cudillero». La app de Adif calcula la llegada como horario + retraso actual y supone que ese retraso se mantiene todo el viaje; en vía única no es así (el tren que viene de frente, si va tarde, hace esperar al tuyo en el apartadero). Este programa simula la línea con los datos en tiempo real y explica **por qué** llega a esa hora.

## Arrancar

Necesitas Python 3.8 o superior (Render usa el de `.python-version`). No hay que instalar nada más.

| Qué quieres | Cómo |
|---|---|
| Abrir la web | Doble clic en `iniciar.bat` (o `python iniciar.py`) → se abre http://localhost:8765 |
| Verlo en el móvil (misma wifi) | `python iniciar.py --movil`. Te dice la dirección que tienes que abrir en el móvil |
| Consulta rápida por consola | `python iniciar.py --consulta Xivares Gijon` |
| Forzar descarga del horario | `python iniciar.py --actualizar-horario` |
| Pasar las pruebas | `python -m unittest discover tests` |

### En el iPhone

1. En el ordenador, abre **`iniciar_con_iphone.bat`**. Si Windows pregunta por el cortafuegos, pulsa **Permitir**.
2. En la web del ordenador pulsa el botón **📱 iPhone**. Sale un código QR.
3. Apunta al código con la **Cámara** del iPhone y abre el enlace en Safari.
4. En Safari pulsa **Compartir → Añadir a pantalla de inicio**. Te queda un icono **C-4** que se abre a pantalla completa, como una app.

Para que funcione, el iPhone tiene que estar en la **misma wifi** que el ordenador y el programa tiene que estar abierto en el ordenador. Si no conecta, revisa en Windows que la red wifi de casa esté marcada como **privada**.

La primera vez descarga el horario oficial de Renfe (unos 2 MB) y lo guarda en `cache/`. Luego lo actualiza solo una vez al día.

## Pantallas

- **Mi viaje.** Guarda tus trayectos favoritos (★) y pon cuántos minutos tardas andando a la estación: te dice **a qué hora salir de casa** y qué trenes ya no pillas. Botón **Compartir llegada** para mandarla por WhatsApp. Si ya no quedan trenes hoy, enseña los primeros de mañana. Eliges origen y destino y ves los próximos trenes. Para cada uno salen tres horas: *horario*, *app oficial* y *estimado*. También se indica cuándo llegarás más tarde (o antes) que lo que dice la app y el motivo, por ejemplo: «Espera en Veriña a que llegue el 70210».
- **Estación.** Panel de salidas de una estación en los dos sentidos. Indica con qué tren se cruza cada uno y si está en el andén (con su vía).
- **Mapa.** Mapa real (OpenStreetMap) con el trazado de la vía y los trenes moviéndose en su posición estimada, con su retraso. Si pulsas un tren ves su recorrido completo.
- **Malla.** El gráfico que usan los ferroviarios para planificar la vía única: estaciones frente a hora. Se ven los cruces, las esperas y la diferencia con el horario oficial.
- **Cruces.** Lista de los próximos cruces: dónde y a qué hora, cuánto espera cada tren y si el cruce se ha trasladado.
- **Precisión.** Mide sola si el programa acierta más que la app oficial. Compara las estimaciones hechas 5, 10, 20 y 30 minutos antes con la hora real de llegada.
- **Cómo funciona.** Explicación del modelo y lista de apartaderos.

### Enlace con el autobús urbano (EMTUSA)

En **Mi viaje**, cuando el destino es una estación de Gijón, debajo de los trenes aparece **«Autobuses al llegar a…»**: las paradas de EMTUSA a pie de estación, cuántos minutos se tarda andando hasta cada una, y los autobuses que están por llegar con su línea y sus minutos, **en tiempo real**. Así encadenas tren + bus.

Los minutos del bus son los de *ahora*: cuando tu tren esté a punto de llegar, vuelve a mirar para ver el autobús que vas a pillar. Usa la API pública de EMTUSA (`emtusasiri.pub.gijon.es`, la misma que su app oficial). Si esa API no responde, el tren sigue funcionando igual y el bus simplemente no se muestra. Se puede apagar con `"bus": false` en `config.json`.

## Cómo calcula

1. **Horario oficial.** GTFS de Renfe Cercanías, del que se queda con los trenes de la C-4 de hoy y el trazado de la vía.
2. **Apartaderos y cruces.** Los deduce del propio horario, sin tenerlos que meter a mano. Salen Veriña, Perlora, Candás, Trasona, Avilés, Piedras Blancas y Pravia, además de las cabeceras. También sabe qué pareja de trenes se cruza en cada uno.
3. **Tiempo real.** Cada 20 s lee la posición, el retraso y los avisos de Renfe (GTFS-Realtime). Para la C-4, Renfe a menudo da la posición pero no el retraso. En ese caso el programa lo calcula a partir del momento en que el tren sale de cada estación.
4. **Simulación de la línea completa.** Reglas:
   - **Cruce:** un tren no sale del apartadero hasta que ha entrado el que viene de frente. Si uno de los dos ya pasó el sitio previsto, el cruce se traslada al siguiente apartadero posible.
   - **Vía única en cabeceras:** en Cudillero, Pravia, Avilés o Gijón no se sale hasta que ha llegado el tren que venía de frente por ese tramo.
   - **Tren de delante:** no se entra en un tramo hasta que el tren anterior del mismo sentido ha llegado al siguiente apartadero.
   - **Rotación de material:** el tren que sale de cabecera suele ser el que acaba de llegar. Para saber cuál es usa la vía de estacionamiento que publica Renfe.
   - **Horario:** nunca se sale antes de hora. Si va tarde, puede recortar las esperas que ya trae el horario.
5. **Aprendizaje.** Guarda lo observado en `historial/`. Cuando tiene al menos 5 observaciones de un tramo, usa su tiempo real de marcha en lugar del teórico.

## Seguridad ante datos malos

- Si Renfe deja de actualizar sus datos (a veces se quedan congelados), el programa lo detecta, vuelve al horario y lo avisa en rojo o naranja.
- Las posiciones con más de 5 minutos de antigüedad se ignoran.
- Si no hay conexión, funciona con el horario y lo indica.

## Ajustes (`config.json`, opcional)

Copia `config.ejemplo.json` como `config.json` y cambia solo lo que quieras:

| Clave | Por defecto | Qué hace |
|---|---|---|
| `cruces` | `"fijos"` | `"dinamicos"`: simula que el puesto de mando mueve un cruce cuando la espera iba a ser larga |
| `umbral_cambio_cruce_min` | 8 | Espera a partir de la cual se considera mover el cruce (modo dinámico) |
| `margen_cruce_min` | 0.5 | Minutos desde que entra el tren contrario hasta que sale el que espera |
| `vuelta_minima_min` | 4 | Tiempo mínimo para dar la vuelta en cabecera |
| `rotacion_espera_max_min` | 10 | Si el tren que da la vuelta llega muy tarde, se supone que ponen otro |
| `recuperacion` | 0.0 | Parte del tiempo de marcha que puede recuperar un tren retrasado (0.03 = 3 %) |
| `estaciones_cruce_extra` / `_excluir` | [] | Corregir la lista de apartaderos |
| `puerto` | 8765 | Puerto de la web local |

Usa la pestaña **Precisión** para decidir los ajustes. Si el sesgo sale positivo, el programa es pesimista. Si sale negativo, es optimista.

## Estructura

```
servidor.py              arranque en internet (Render)
iniciar.py               arranque en tu ordenador (y consulta por consola)
iniciar.bat              doble clic para abrir la web en Windows
iniciar_con_iphone.bat   lo mismo, accesible desde el móvil por la wifi

asturias/                el programa
  app.py                   servidor web y API
  util.py                  rutas, configuración, distancias, horas
  persistencia.py          guarda lo aprendido y los horarios ya procesados en la rama «datos» de GitHub
  trenes/                  todo lo de Renfe
    gtfs.py                  horario de Renfe (Cercanías, regionales, larga distancia)
    red.py                   modelo de la red: estaciones, apartaderos, cruces, rotaciones
    linea.py                 alias de red.py (lo usan las pruebas)
    tiemporeal.py            lectura del tiempo real de Renfe
    estimador.py             simulación de la vía única
    historial.py             observaciones, aprendizaje y medida de precisión
    planificador.py          «Ir a…»: rutas puerta a puerta con transbordos, bus urbano y andar
  bus/                     autobuses
    emtusa.py                Gijón (EMTUSA) en tiempo real
    consorcio.py             Consorcio de Transportes de Asturias (GTFS): urbanos e interurbanos
    datos/red_emtusa.json    red de EMTUSA (paradas y líneas)

web/                     interfaz de trenes y pantalla de inicio (PWA)
web-gijon/               interfaz del bus de Gijón        → se sirve en /bus/
web-consorcio/           interfaz de los buses del Consorcio → se sirve en /cta/
tests/                   pruebas (horario real, posiciones reales, muestras del Consorcio)
.github/workflows/       despierta el servidor de Render de madrugada
```

## Límites

- Renfe no publica cuándo el puesto de mando cambia un cruce de sitio, así que el programa lo supone.
- Los trenes sin datos en tiempo real se suponen en hora. En la web aparecen marcados.
- El mapa necesita internet para los planos. Todo lo demás funciona aunque no cargue el mapa.
- El ordenador tiene que estar en hora de Madrid.

## En internet (Render)

- **Para que no se duerma:** el plan gratis de Render duerme el servidor tras ~15 minutos sin visitas. Un monitor gratis (UptimeRobot, cada 5 minutos, a `/api/ping`) lo mantiene despierto. El propio programa también se visita a sí mismo en horario de trenes, y un trabajo de GitHub lo despierta de madrugada.
- **Arranque rápido:** el disco de Render se vacía en cada reinicio o despliegue. Por eso, lo aprendido (`historial.json.gz`) y los horarios ya procesados (carpeta `horarios/`: trenes y buses) se guardan en la rama `datos` de este repositorio, que no provoca despliegues. Al arrancar se recuperan de ahí en vez de recalcularlos. Hace falta en Render `C4_GH_TOKEN` (token con «Contents: Read and write») y `C4_GH_REPO` (`usuario/repositorio`).
- **Abre al instante:** la app guarda una copia en el móvil. Si el servidor está arrancando o no hay cobertura, enseña los últimos datos con un aviso y se actualiza sola.

## Publicar mejoras (Render + GitHub)

1. Los archivos cambiados aparecen en **GitHub Desktop**, en la carpeta `PycharmProjects\c4`.
2. Abajo a la izquierda escribe un resumen y pulsa **Commit to main**.
3. Arriba pulsa **Push origin**.
4. Render publica la versión nueva en 1–2 minutos.

En Render: *Start Command* `python servidor.py`, *Build Command* `python --version`, plan **Free**, región **Frankfurt**.
