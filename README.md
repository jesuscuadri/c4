# C-4 en tiempo real · Gijón – Cudillero

La app de Adif calcula la llegada como **horario + retraso actual** y supone que ese retraso se mantiene igual todo el viaje. En la C-4 no es así, porque es **vía única**. Si el tren que viene de frente va tarde, el tuyo le espera en el apartadero (Veriña, Perlora, Candás…) y llegas más tarde de lo que dice la app. En otras ocasiones pasa lo contrario: el horario tiene márgenes y el tren recupera tiempo.

Este programa simula la línea entera con los datos en tiempo real de Renfe y te da una hora de llegada más realista. Además te explica **por qué** llega a esa hora.

## Arrancar

Necesitas Python 3.8 o superior. No hay que instalar nada más.

| Qué quieres | Cómo |
|---|---|
| Abrir la web | Doble clic en `iniciar.bat` (o `python c4_tiempo_real.py`) → se abre http://localhost:8765 |
| Verlo en el móvil (misma wifi) | `python c4_tiempo_real.py --movil`. Te dice la dirección que tienes que abrir en el móvil |
| Consulta rápida por consola | `python c4_tiempo_real.py --consulta Xivares Gijon` |
| Forzar descarga del horario | `python c4_tiempo_real.py --actualizar-horario` |
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
c4_tiempo_real.py   programa principal
c4/gtfs.py          descarga y lectura del horario oficial
c4/linea.py         estaciones, apartaderos, cruces, rotaciones
c4/tiemporeal.py    lectura del tiempo real de Renfe
c4/estimador.py     simulación de la vía única
c4/historial.py     observaciones, aprendizaje y medida de precisión
c4/emtusa.py        autobús urbano de Gijón (EMTUSA) en tiempo real
c4/app.py           servidor web local
web/                interfaz (HTML, CSS, JS; el mapa usa Leaflet y OpenStreetMap)
tests/              pruebas con el horario real del 23/09/2026 y posiciones reales de Renfe
```

## Límites

- Renfe no publica cuándo el puesto de mando cambia un cruce de sitio, así que el programa lo supone.
- Los trenes sin datos en tiempo real se suponen en hora. En la web aparecen marcados.
- El mapa necesita internet para los planos. Todo lo demás funciona aunque no cargue el mapa.
- El ordenador tiene que estar en hora de Madrid.

## En internet (Render)

- **No se duerme en horario de trenes:** en Render el programa se visita a sí mismo cada 10 minutos entre las 5:00 y las 0:45, así que no hay que esperar a que arranque. De madrugada se deja dormir para no gastar horas del plan gratis. Si lo abres a las 5:30 puede tardar un minuto la primera vez.
- **Abre al instante:** la app guarda una copia en el móvil. Si el servidor está arrancando o no hay cobertura, enseña los últimos datos con un aviso y se actualiza sola en cuanto puede.

## Publicar mejoras (Render + GitHub)

La web en internet se actualiza sola cada vez que se suben cambios a GitHub:

1. Los archivos cambiados aparecen en **GitHub Desktop**, en la carpeta `PycharmProjects\c4`.
2. Abajo a la izquierda escribe un resumen (por ejemplo «Mejora del mapa») y pulsa **Commit to main**.
3. Arriba pulsa **Push origin**.
4. Render detecta el cambio y publica la versión nueva en 1–2 minutos. El icono del iPhone la muestra al abrirlo.

En Render: *Start Command* `python servidor.py`, *Build Command* `python --version`, plan **Free**, región **Frankfurt**.
En el plan gratis el servidor se reinicia a menudo, así que el historial de la pestaña Precisión allí no se conserva mucho tiempo.
