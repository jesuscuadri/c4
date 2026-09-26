"use strict";
/* C-4 en tiempo real · interfaz web (sin dependencias salvo Leaflet para el mapa) */

let LINEA = null, R = null, tRecibido = 0, PREC = null, APREN = null;
let tabActual = "viaje", mapa = null, capaTrenes = null, marcas = {}, cajonTren = null;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------------------------------------------------------- utilidades */
const hm = (m) => {
  if (m == null || isNaN(m)) return "--:--";
  m = Math.round(m);
  return String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(((m % 60) + 60) % 60).padStart(2, "0");
};
// Hora de SALIDA: se redondea hacia abajo. Si sale a las 18:25:40, decir «18:26» haría perder el tren.
const hmS = (m) => (m == null || isNaN(m) ? "--:--" : hm(Math.floor(m + 1e-6)));
// Si el reloj del móvil va adelantado o atrasado, las cuentas atrás («sale en 3 min») estarían mal:
// se usa la hora del servidor (cabecera X-Hora-Servidor) para corregirlo.
let DESFASE = 0;   // segundos: hora del servidor − hora del móvil
const ahoraSeg = () => Date.now() / 1000 + DESFASE;
const ahora = () => (!R ? 0 : R.ts ? R.ahora + (ahoraSeg() - R.ts) / 60 : R.ahora + (Date.now() - tRecibido) / 60000);
const edadDatos = () => (R && R.ts ? ahoraSeg() - R.ts : 0);  // segundos desde que el servidor calculó
async function pedir(url, ms = 12000) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
  try { return await (await fetch(url, { signal: ctl.signal })).json(); } finally { clearTimeout(t); }
}
const est = (k) => LINEA.estaciones[k];
const nombreCorto = (n) => n.replace("Gijón-Sanz Crespo", "Gijón").replace("-Apeadero", " Apd.");
const colorDir = (d) => (d > 0 ? "var(--ida)" : "var(--vta)");
const colorDirHex = (d) => {
  const oscuro = matchMedia("(prefers-color-scheme: dark)").matches;
  return d > 0 ? (oscuro ? "#f25cbc" : "#d42a8f") : (oscuro ? "#60a5fa" : "#2563eb");
};
function tagRetraso(min, corto) {
  if (min == null) return "";
  const r = Math.round(min);
  if (r <= 0) return `<span class="tag ok">${corto ? "+0" : "En hora"}</span>`;
  return `<span class="tag ${r <= 5 ? "warn" : "bad"}">+${r}${corto ? "" : " min"}</span>`;
}
function guardar(k, v) { try { localStorage.setItem("c4." + k, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } }
function leer(k, def) { try { const v = localStorage.getItem("c4." + k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } }
function destinoCorto(t) { return nombreCorto(t.destino); }
/* Lo que importa de un tren es a dónde va, su retraso y cuándo llega a tu estación.
   El número (70311…) solo se muestra pequeño y en gris, por si hace falta cuadrarlo con Renfe. */
const trenPorId = (id) => (R && R.trenes.find((x) => x.id === id)) || null;
const hacia = (id) => { const t = trenPorId(id); return t ? "→ " + destinoCorto(t) : "otro tren"; };
const elQueVa = (id) => { const t = trenPorId(id); return t ? "el que va a " + destinoCorto(t) : "otro tren"; };
const numT = (n) => `<span class="num-t">${esc(n)}</span>`;
function miViaje() {
  const o = +$("o").value, d = +$("d").value;
  if (o === d) return null;
  const v = viajesEntre(o, d, 1)[0];
  return v ? { id: v.t.id, lle: v.t.est_a[v.jd], a: nombreCorto(est(d).nombre), v } : null;
}

/* ---------------------------------------------------------------- datos */
async function cargarLinea() {
  for (;;) {
    try {
      const j = await pedir("/api/linea");
      if (!j.cargando) { LINEA = j; return; }
      $("chip-txt").textContent = "Preparando el horario…";
      $("avisos").innerHTML = j.error
        ? `<div class="aviso bad"><span>⚠</span><div><b>No se pudo cargar el horario de Renfe.</b> Se reintenta solo. (${esc(j.error)})</div></div>`
        : `<div class="aviso info"><span>ℹ</span><div>Descargando el horario oficial de Renfe. La primera vez tarda unos segundos…</div></div>`;
    } catch (e) {
      $("chip-txt").textContent = "Despertando el servidor…";
      $("avisos").innerHTML = `<div class="aviso info"><span>ℹ</span><div><b>Despertando el servidor.</b> En el plan gratis tarda hasta 1 minuto si llevaba rato sin usarse. No cierres la app.</div></div>`;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}
async function cargarEstado() {
  if (cargarEstado._en) return;            // no solapar peticiones
  cargarEstado._en = true;
  try {
    const url = R && R.version ? `/api/estado?v=${encodeURIComponent(R.version)}` : "/api/estado";
    const ctl = new AbortController(), tm = setTimeout(() => ctl.abort(), 12000);
    const t0 = Date.now();
    let resp;
    try { resp = await fetch(url, { signal: ctl.signal }); } finally { clearTimeout(tm); }
    const t1 = Date.now(), hs = +resp.headers.get("X-Hora-Servidor");
    const j = await resp.json();
    // solo con respuestas recién hechas (rápidas y con la hora dentro de lo razonable)
    if (hs && t1 - t0 < 3000 && (!j.ts || Math.abs(hs - j.ts) < 120)) {
      const d = hs - (t0 + t1) / 2000;
      DESFASE = Math.abs(d - DESFASE) > 2 ? d : DESFASE * 0.7 + d * 0.3;
    }
    if (j.sin_cambios) { tRecibido = Date.now(); return; }   // nada nuevo: la respuesta pesa unos bytes
    if (j.cargando) { $("chip-txt").textContent = "Leyendo el tiempo real…"; return; }
    if (R && j.ts && R.ts && j.ts < R.ts) return;  // copia guardada más vieja que lo que ya tenemos
    if (LINEA && j.fecha && j.fecha !== LINEA.fecha) { await cargarLinea(); iniciarSelectores(); }
    R = j; tRecibido = Date.now();
    pintarTrenesMapa._cruces = null;
    pintarTodo();
  } catch (e) {
    const c = $("chip"); c.className = "chip sin_conexion";
    $("chip-txt").textContent = R ? "Reconectando…" : "Despertando el servidor…";
    if (R) pintarEstado();
  } finally { cargarEstado._en = false; }
}
async function cargarPrecision() {
  try {
    PREC = await pedir("/api/precision");
    try { APREN = await pedir("/api/aprendizaje"); } catch (e) { /* opcional */ }
    pintarPrecision();
  } catch (e) { /* nada */ }
}

/* ---------------------------------------------------------------- estado y avisos */
function pintarEstado() {
  const c = $("chip");
  const viejo = edadDatos() > 90;
  c.className = "chip " + (viejo ? "congelado" : R.calidad);
  const txt = { directo: `En directo · ${R.con_posicion} trenes localizados`,
                congelado: "Renfe no actualiza · usando horario",
                sin_posiciones: "Renfe no da posiciones · último retraso conocido",
                sin_conexion: "Sin conexión con Renfe · usando horario" }[R.calidad];
  const corto = { directo: "En directo", congelado: "Sin datos Renfe", sin_posiciones: "Renfe sin datos", sin_conexion: "Sin conexión" }[R.calidad];
  $("chip-txt").innerHTML = viejo ? `<span class="txt">Actualizando…</span><span class="corto">Actualizando…</span>`
    : `<span class="txt">${txt}</span><span class="corto">${corto}</span>`;
  c.title = `Última lectura ${R.actualizado}` + (R.ts_feed ? ` · datos de Renfe de las ${R.ts_feed}` : "");
  let h = "";
  if (viejo)
    h += `<div class="aviso warn"><span>⏳</span><div><b>Datos de las ${esc(R.actualizado.slice(0, 5))}.</b> El servidor se está despertando o no hay conexión: en cuanto responda se actualiza solo.</div></div>`;
  if (R.calidad === "congelado")
    h += `<div class="aviso warn"><span>⚠</span><div><b>Los datos en tiempo real de Renfe están parados</b> (último dato ${R.ts_feed || "?"}). Mientras tanto se calcula con el horario, así que las esperas por cruces con trenes retrasados no se ven.</div></div>`;
  if (R.calidad === "sin_posiciones")
    h += `<div class="aviso warn"><span>⚠</span><div><b>Renfe no está publicando la posición de ningún tren</b> (su servicio de tiempo real va vacío en toda España). Mientras tanto cada tren sigue con el último retraso que se le vio; los que no se habían visto, con el horario.</div></div>`;
  if (R.calidad === "sin_conexion")
    h += `<div class="aviso bad" title="${esc(R.error || "")}"><span>⚠</span><div><b>No se puede leer el tiempo real de Renfe ahora mismo.</b> Mientras tanto se muestra el horario oficial; en cuanto vuelva, se actualiza solo.</div></div>`;
  for (const a of R.avisos || []) h += `<div class="aviso info"><span>ℹ</span><div><b>Aviso de Renfe:</b> ${esc(a)}</div></div>`;
  $("avisos").innerHTML = h;
  $("pie").textContent = `Datos: Renfe (horario oficial GTFS y tiempo real) · actualizado ${R.actualizado} · ` +
    `${LINEA.n_trenes} trenes hoy · cruces ${R.modo_cruces} · ${R.tramos_aprendidos} tramos con tiempos aprendidos · v${LINEA.version}`;
}

/* ---------------------------------------------------------------- mi viaje */
function iniciarSelectores() {
  const opts = (corto) => LINEA.estaciones.map((e) => `<option value="${e.k}">${esc(corto ? nombreCorto(e.nombre) : e.nombre)}</option>`).join("");
  $("o").innerHTML = $("d").innerHTML = opts(true);
  $("est").innerHTML = opts(false);
  const buscar = (n) => (LINEA.estaciones.find((e) => e.nombre.toLowerCase().includes(n)) || LINEA.estaciones[0]).k;
  $("o").value = leer("o", buscar("xivares"));
  $("d").value = leer("d", 0);
  $("est").value = leer("est", buscar("veri"));
}
function viajesEntre(o, d, lim = 6) {
  const now = ahora(), out = [];
  for (const t of R.trenes) {
    if (t.fin || t.cancelado) continue;
    const jo = t.k.indexOf(o), jd = t.k.indexOf(d);
    if (jo < 0 || jd < 0 || jo >= jd || !t.para[jo] || !t.para[jd] || t.j0 > jo) continue;
    if (t.est_d[jo] == null || t.est_d[jo] < now - 0.5) continue;
    out.push({ t, jo, jd, mot: t.motivos.filter((m) => m.j >= jo && m.j < jd), antes: t.motivos.filter((m) => m.j < jo) });
  }
  return out.sort((a, b) => a.t.est_d[a.jo] - b.t.est_d[b.jo]).slice(0, lim);
}
/* favoritos y tiempo andando (se guardan en este dispositivo) */
const favoritos = () => leer("favs", []);
const andarA = (k) => +leer("andar." + est(k).id, 0) || 0;
function pintarFavoritos() {
  const o = +$("o").value, d = +$("d").value, fs = favoritos();
  const es = fs.some((f) => f.o === est(o).id && f.d === est(d).id);
  $("fav").textContent = es ? "★ Guardado" : "☆ Guardar trayecto";
  $("fav").classList.toggle("on", es);
  $("favoritos").innerHTML = fs.map((f, i) => {
    const eo = LINEA.estaciones.find((e) => e.id === f.o), ed = LINEA.estaciones.find((e) => e.id === f.d);
    if (!eo || !ed) return "";
    const act = eo.k === o && ed.k === d;
    return `<button type="button" class="fav-chip${act ? " act" : ""}" data-fav="${i}">${esc(nombreCorto(eo.nombre))} → ${esc(nombreCorto(ed.nombre))}</button>`;
  }).join("");
  $("favoritos").hidden = !fs.length;
  $("andar").value = andarA(o) || "";
}
function alternarFavorito() {
  const o = est(+$("o").value).id, d = est(+$("d").value).id;
  let fs = favoritos();
  if (fs.some((f) => f.o === o && f.d === d)) fs = fs.filter((f) => !(f.o === o && f.d === d));
  else fs.push({ o, d });
  guardar("favs", fs.slice(-6));
  pintarFavoritos();
}
async function compartir(texto) {
  try {
    if (navigator.share) { await navigator.share({ text: texto }); return; }
    await navigator.clipboard.writeText(texto);
    aviso("Copiado. Pégalo en WhatsApp o donde quieras.");
  } catch (e) { if (e && e.name !== "AbortError") aviso("No se pudo compartir"); }
}
function aviso(txt) {
  const t = $("toast");
  t.textContent = txt; t.hidden = false;
  clearTimeout(aviso._t); aviso._t = setTimeout(() => { t.hidden = true; }, 2600);
}
const fiabilidad = (t) => ({ "GPS": ["ok", "GPS en directo"], "posición": ["ok", "Posición en directo"], "Renfe": ["ok", "Dato de Renfe"],
  "posición (aprox.)": ["warn", "Posición aproximada"] }[t.fuente] || null);

async function pintarManana(o, d) {
  const cont = $("viajes");
  cont.innerHTML = `<div class="vacio">No quedan trenes hoy entre ${esc(est(o).nombre)} y ${esc(est(d).nombre)}.<br>Buscando los de mañana…</div>`;
  try {
    const j = await pedir(`/api/manana?o=${encodeURIComponent(est(o).id)}&d=${encodeURIComponent(est(d).id)}`);
    if (+$("o").value !== o || +$("d").value !== d) return;
    if (!j.trenes || !j.trenes.length) { cont.innerHTML = `<div class="vacio">No quedan trenes hoy ni hay trenes directos mañana entre estas estaciones.</div>`; return; }
    const f = new Date(j.fecha + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
    cont.innerHTML = `<div class="manana"><div class="manana-tit">Hoy ya no quedan trenes. Primeros de mañana, ${esc(f)}:</div>
      <table class="tabla num"><thead><tr><th>Sale</th><th>Llega</th><th>Destino</th></tr></thead><tbody>` +
      j.trenes.map((x) => `<tr><td class="e">${hm(x.sale)}</td><td>${hm(x.llega)}</td><td>→ ${esc(nombreCorto(x.destino))} ${numT(x.num)}</td></tr>`).join("") +
      `</tbody></table><p class="sub" style="margin:8px 0 0">Horario oficial. El tiempo real aparecerá cuando el tren empiece a circular.</p></div>`;
  } catch (e) { cont.innerHTML = `<div class="vacio">No quedan trenes directos hoy entre ${esc(est(o).nombre)} y ${esc(est(d).nombre)}.</div>`; }
}

function pintarViaje() {
  const o = +$("o").value, d = +$("d").value;
  pintarFavoritos();
  if (o === d) { $("viajes").innerHTML = `<div class="vacio">Elige dos estaciones distintas.</div>`; return; }
  const v = viajesEntre(o, d);
  if (!v.length) {
    if (pintarViaje._manana !== `${o}-${d}-${LINEA.fecha}`) { pintarViaje._manana = `${o}-${d}-${LINEA.fecha}`; pintarManana(o, d); }
    return;
  }
  pintarViaje._manana = null;
  const now = ahora(), andar = andarA(o);
  let primero = true;
  $("viajes").innerHTML = v.map(({ t, jo, jd, mot, antes }) => {
    const sal = t.est_d[jo], lle = t.est_a[jd], app = t.adif_a[jd], dif = lle - app;
    const falta = Math.floor(sal - now);
    const cuando = falta <= 0 ? "sale ahora" : falta < 60 ? `sale en ${falta} min` : `sale en ${Math.floor(falta / 60)} h ${falta % 60} min`;
    const cambiaSal = Math.abs(sal - t.prog_d[jo]) >= 1, cambiaLle = Math.abs(lle - t.prog_a[jd]) >= 1;
    const salirCasa = sal - andar, margen = salirCasa - now;
    const perdido = andar > 0 && margen < -0.5;
    const destacado = !perdido && primero;
    if (destacado) primero = false;
    let casa = "";
    if (andar > 0) casa = perdido
      ? `<div class="casa perdido">🚶 Andando (${andar} min) ya no llegas a este tren</div>`
      : `<div class="casa${margen <= 3 ? " prisa" : ""}">🚶 Sal de casa ${margen < 1 ? "<b>ya</b>" : `a las <b class="num">${hmS(salirCasa)}</b> (en ${Math.floor(margen)} min)`}</div>`;
    let aviso = "";
    if (t.con_datos && dif >= 1) aviso = `<div class="dif">La app oficial dirá <b class="num">${hm(app)}</b>. Llegarás sobre las <b class="num">${hm(lle)}</b> (<b>+${Math.round(dif)} min</b>).</div>`;
    else if (t.con_datos && dif <= -1) aviso = `<div class="dif bien">Llegarás antes de lo que dice la app oficial (${hm(app)}): recupera ${Math.round(-dif)} min con los márgenes del horario.</div>`;
    const lis = antes.map((m) => `<li class="antes">Antes de tu estación: ${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)
      .concat(mot.map((m) => `<li>${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)).join("");
    const fia = fiabilidad(t);
    const texto = `Voy en la C-4 hacia ${destinoCorto(t)} (sale de ${nombreCorto(est(o).nombre)} a las ${hmS(sal)}). Llego a ${nombreCorto(est(d).nombre)} sobre las ${hm(lle)}.`;
    return `<div class="tv${destacado ? " primero" : ""}${perdido ? " perdido" : ""}" data-tren="${t.id}" data-jo="${jo}" data-jd="${jd}">
      <div class="tv-cab">
        <div>
          <div class="tv-tren"><span class="bola" style="background:${colorDir(t.dir)}"></span>→ ${esc(destinoCorto(t))}${tagEstado(t, jo)}${numT(t.num)}</div>
          <div class="tv-sit">${esc(t.situacion)}${t.via ? ` · vía ${esc(t.via)}` : ""}${fia ? ` · <span class="fia ${fia[0]}">${fia[1]}</span>` : ""}</div>
        </div>
        <div class="tv-grande"><div class="h num">${hmS(sal)}</div><div class="l">${cuando}</div><div class="l2">llega <b class="num">${hm(lle)}</b></div></div>
      </div>${casa}
      <div class="cmp num">
        <span></span><span class="c">Horario</span><span class="c">App oficial</span><span class="c">Estimado</span>
        <span class="c" style="align-self:center">Sale</span><span class="${cambiaSal ? "x" : ""}">${hm(t.prog_d[jo])}</span><span>${hm(t.adif_d[jo])}</span><span class="e">${hmS(sal)}</span>
        <span class="c" style="align-self:center">Llega</span><span class="${cambiaLle ? "x" : ""}">${hm(t.prog_a[jd])}</span><span>${hm(app)}</span><span class="e">${hm(lle)}</span>
      </div>${aviso}${lis ? `<ul class="motivos">${lis}</ul>` : ""}
      <div class="tv-acciones"><button type="button" class="btn-mini" data-compartir="${esc(texto)}">Compartir llegada</button><span class="pp-sub">Toca la tarjeta para ver todo el recorrido</span></div></div>`;
  }).join("");
  pintarBusEnlace(d);
}

/* ---------------------------------------------------------------- enlace con el bus (EMTUSA) */
let BUS = {};   // cache por estación: {k:{ts,data}}
async function pintarBusEnlace(d) {
  const cont = $("bus-enlace"), e = est(d);
  if (!e || e.lat == null) { cont.hidden = true; return; }
  const cache = BUS[d];
  if (cache && Date.now() - cache.ts < 20000) return dibujarBus(d, cache.data);
  try {
    const j = await pedir(`/api/bus/enlace?lat=${e.lat}&lon=${e.lon}`, 9000);
    BUS[d] = { ts: Date.now(), data: j };
    if (+$("d").value === d) dibujarBus(d, j);
  } catch (err) { /* si falla, el bus simplemente no se muestra */ }
}
const andandoMin = (m) => Math.max(1, Math.round(m / 75));   // ~4,5 km/h
function dibujarBus(d, j) {
  const cont = $("bus-enlace");
  if (!j || !j.paradas || !j.paradas.length) { cont.hidden = true; return; }
  const nom = nombreCorto(est(d).nombre);
  const parada = (p) => {
    const lls = (p.llegadas || []).filter((l) => l.minutos != null).slice(0, 4);
    const chips = lls.length ? lls.map((l) => `<span class="bus-linea" style="background:${esc(l.color)}" title="${esc(l.nombre_linea)} → ${esc(l.destino)}">${esc(l.linea)}<b>${l.minutos <= 0 ? "ya" : l.minutos + "′"}</b></span>`).join("")
      : `<span class="pp-sub">sin autobuses ahora</span>`;
    return `<div class="bus-parada"><div class="bus-p-cab"><b>${esc(p.nombre)}</b><span class="pp-sub">${andandoMin(p.metros)} min andando · ${p.metros} m</span></div><div class="bus-chips">${chips}</div></div>`;
  };
  cont.hidden = false;
  cont.innerHTML = `<div class="panel bus-panel"><div class="bus-cab"><b>🚌 Autobuses al llegar a ${esc(nom)}</b><span class="pp-sub">en directo · ahora mismo</span></div>
    ${j.paradas.map(parada).join("")}
    <p class="sub" style="margin:8px 0 0">Los minutos son los de <b>ahora</b>; cuando tu tren esté llegando, vuelve a mirar para ver el autobús que vas a pillar. Datos: EMTUSA.</p></div>`;
}

/* ---------------------------------------------------------------- panel de estación */
function pintarEstacion() {
  const k = +$("est").value, e = est(k), now = ahora();
  $("est-info").innerHTML = e.cruce && !e.cruces_dia
    ? `<span class="tag warn" style="margin:0 6px 0 0">Cabecera</span>Aquí empiezan y terminan trenes: el que sale suele ser el mismo que acaba de llegar, dando la vuelta.`
    : e.cruce
    ? `<span class="tag warn" style="margin:0 6px 0 0">Vía de cruce</span>Aquí se cruzan trenes unas ${e.cruces_dia} veces al día según el horario.`
    : "Apeadero sin vía de cruce: los trenes no pueden cruzarse aquí.";
  const col = (dir) => {
    const filas = [];
    for (const t of R.trenes) {
      if (t.dir !== dir || t.fin || t.cancelado) continue;
      const j = t.k.indexOf(k);
      if (j < 0 || !t.para[j] || t.j0 > j || j === t.k.length - 1) continue;
      const s = t.est_d[j];
      if (s == null || s < now - 0.5) continue;
      filas.push({ t, j, s });
    }
    filas.sort((a, b) => a.s - b.s);
    if (!filas.length) return `<div class="vacio">No quedan salidas hoy.</div>`;
    return `<table class="tabla num"><thead><tr><th>Sale</th><th>Destino</th><th>Estado</th></tr></thead><tbody>` +
      filas.slice(0, 8).map(({ t, j, s }) => {
        const cr = (R.cruces || []).find((c) => c.k === k && (c.ida.id === t.id || c.vuelta.id === t.id));
        const aqui = t.parado && t.j0 === j;
        const mot = t.motivos.find((m) => m.j === j);
        const retr = s - t.prog_d[j];
        return `<tr class="clic${aqui ? " aqui" : ""}" data-tren="${t.id}" data-jo="${j}">
          <td><span class="e">${hmS(s)}</span>${Math.abs(retr) >= 1 ? `<br><span style="color:var(--mut);text-decoration:line-through">${hm(t.prog_d[j])}</span>` : ""}</td>
          <td>→ ${esc(destinoCorto(t))} ${numT(t.num)}${aqui ? `<br><span class="tag ok" style="margin:0">En andén${t.via ? " · vía " + esc(t.via) : ""}</span>` : ""}${t.material && j === 0 ? `<br><span style="font-size:12px;color:var(--tx2)">Aún no está: es el tren que llega de ${esc(nombreCorto(t.material.de))} a las ${hm(t.material.llega)}</span>` : ""}</td>
          <td>${tagEstado(t, j)}${cr ? `<br><span style="font-size:12px;color:var(--tx2)">Cruza aquí con ${elQueVa(cr.ida.id === t.id ? cr.vuelta.id : cr.ida.id)}</span>` : ""}${mot ? `<br><span style="font-size:12px;color:var(--warn)">Espera ${Math.round(mot.min)} min</span>` : ""}</td></tr>`;
      }).join("") + `</tbody></table>`;
  };
  $("panel-estacion").innerHTML =
    `<div><h3><span class="bola" style="background:var(--vta)"></span>Hacia Gijón</h3>${col(-1)}</div>` +
    `<div><h3><span class="bola" style="background:var(--ida)"></span>Hacia Avilés / Pravia / Cudillero</h3>${col(1)}</div>`;
}

/* ---------------------------------------------------------------- posición de los trenes */
function kmTren(t, now) {
  if (t.fin || t.cancelado) return null;
  const n = t.k.length, km = (j) => est(t.k[j]).km;
  if (!t.con_datos && t.j0 === 0 && now < t.est_d[0]) return null; // aún no ha salido
  // el tren que hará este servicio aún viene de camino: en el mapa ya se ve ese, no hay otro en el andén
  if (t.material && t.j0 === 0 && now < t.est_d[0]) return null;
  for (let j = Math.max(0, t.j0 - 1); j < n; j++) {
    const a = t.est_a[j], d = t.est_d[j];
    if (a == null) continue;
    if (j >= t.j0 && now < a) {
      if (j === 0) return t.con_datos ? km(0) : null;
      if (j === t.j0 && t.km_gps != null && t.t_gps != null && a > t.t_gps) {
        // posición GPS real del tren, avanzando hacia la estación a su ritmo hasta la llegada
        const f = Math.min(1, Math.max(0, (now - t.t_gps) / (a - t.t_gps)));
        return t.km_gps + (km(j) - t.km_gps) * f;
      }
      const d0 = t.est_d[j - 1];
      if (d0 == null || a <= d0) return km(j);
      const f = Math.min(1, Math.max(0, (now - d0) / (a - d0)));
      return km(j - 1) + (km(j) - km(j - 1)) * f;
    }
    if (d != null && now <= d) return km(j);
  }
  return now < t.est_a[n - 1] + 1 ? km(n - 1) : null;
}
function latlonKm(x) {
  const K = LINEA.trazado_km, P = LINEA.trazado;
  if (!K || !K.length) return null;
  if (x <= K[0]) return P[0];
  if (x >= K[K.length - 1]) return P[P.length - 1];
  let lo = 0, hi = K.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (K[m] <= x) lo = m; else hi = m; }
  const f = (x - K[lo]) / Math.max(1e-9, K[hi] - K[lo]);
  return [P[lo][0] + (P[hi][0] - P[lo][0]) * f, P[lo][1] + (P[hi][1] - P[lo][1]) * f];
}
// próxima parada comercial (el tren pasa sin parar por algunos apeaderos)
function proximaJ(t) {
  for (let j = t.j0; j < t.k.length; j++) if (t.para[j]) return j;
  return t.k.length - 1;
}
function retrasoActual(t) {
  const j = Math.min(t.j0, t.k.length - 1);
  return retrasoEn(t, j);
}
// Retraso en una estación concreta: el de la SALIDA (lo que importa para cogerlo); en la última, el de llegada
function retrasoEn(t, j) {
  if (j >= t.k.length - 1 || t.est_d[j] == null) return t.est_a[j] != null ? Math.max(0, t.est_a[j] - t.prog_a[j]) : 0;
  return Math.max(0, t.est_d[j] - t.prog_d[j]);
}
// Etiqueta de estado de un tren en una estación: retraso real si hay datos; si no, «suele +N» o «Previsto»
function tagEstado(t, j) {
  if (t.con_datos) return tagRetraso(retrasoEn(t, j));
  if (t.ultimo_dato != null) {
    const r = Math.round(retrasoEn(t, j));
    return `<span class="tag ${r <= 0 ? "gris" : r <= 5 ? "warn" : "bad"}" title="Renfe no da su posición ahora; hace ${t.ultimo_dato} min iba así">${r > 0 ? "+" + r + " min" : "en hora"} · hace ${t.ultimo_dato < 1 ? "<1" : t.ultimo_dato}′</span>`;
  }
  if (t.tipico >= 1) return `<span class="tag warn" title="Retraso habitual de este tren, aprendido de días anteriores">suele +${Math.round(t.tipico)}</span>`;
  return `<span class="tag gris" title="Sin datos en directo: hora del horario">Previsto</span>`;
}

/* ---------------------------------------------------------------- mapa */
let capaCruces = null, capaRuta = null, marcaYo = null, trenSel = null, seguir = false, etiquetasEst = [];
const oscuroMapa = () => matchMedia("(prefers-color-scheme: dark)").matches;

function iniciarMapa() {
  if (mapa || !window.L) {
    if (!window.L) $("mapa").innerHTML = `<div class="vacio">No se pudo cargar el mapa (hace falta internet para los planos de OpenStreetMap).</div>`;
    return;
  }
  mapa = L.map("mapa", { zoomControl: false, attributionControl: true, zoomSnap: 0.25, tap: true });
  L.control.zoom({ position: "bottomright" }).addTo(mapa);
  // Fondos sin clave. «Sencillo»: lienzo gris de Esri (claro u oscuro según el tema) con los nombres
  // de los pueblos encima: limpio, para que destaquen la vía y los trenes. «Detallado»: OpenStreetMap.
  const esri = (srv) => `https://server.arcgisonline.com/ArcGIS/rest/services/${srv}/MapServer/tile/{z}/{y}/{x}`;
  const tono = oscuroMapa() ? "Dark" : "Light";
  const sencillo = L.layerGroup([
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Base`), { maxZoom: 19, maxNativeZoom: 16, attribution: "Mapa &copy; Esri, HERE, Garmin, &copy; OpenStreetMap" }),
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Reference`), { maxZoom: 19, maxNativeZoom: 16, zIndex: 3 }),
  ]);
  const planos = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, className: "base-osm",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const satelite = L.tileLayer(esri("World_Imagery"), { maxZoom: 19, attribution: "Imágenes &copy; Esri, Maxar, Earthstar Geographics" });
  const fondos = { "Sencillo": sencillo, "Detallado": planos, "Satélite": satelite };
  (fondos[leer("fondo", "Sencillo")] || sencillo).addTo(mapa);
  L.control.layers(fondos, null, { position: "topright" }).addTo(mapa);
  mapa.on("baselayerchange", (e) => guardar("fondo", e.name));

  // Vía: borde + línea para que se lea sobre cualquier fondo
  L.polyline(LINEA.trazado, { color: oscuroMapa() ? "#0b0b0e" : "#fff", weight: 10, opacity: 0.85, interactive: false, lineCap: "round", lineJoin: "round" }).addTo(mapa);
  L.polyline(LINEA.trazado, { color: "#e93cac", weight: 4.5, opacity: 1, interactive: false, lineCap: "round", lineJoin: "round" }).addTo(mapa);
  capaRuta = L.layerGroup().addTo(mapa);

  for (const e of LINEA.estaciones) {
    const m = L.circleMarker([e.lat, e.lon], e.cruce
      ? { radius: 7, color: "#e93cac", weight: 3.5, fillColor: "#fff", fillOpacity: 1 }
      : { radius: 4, color: "#e93cac", weight: 2.5, fillColor: "#fff", fillOpacity: 1 })
      .addTo(mapa);
    m.bindTooltip(esc(nombreCorto(e.nombre)), { permanent: true, direction: "right", offset: [8, 0],
      className: "etq-est" + (e.cruce ? " cruce" : "") });
    m.bindPopup(() => popupEstacion(e.k), { maxWidth: 300, minWidth: 230, autoPanPaddingTopLeft: [70, 70], autoPanPaddingBottomRight: [20, 20] });
    m.on("popupopen", () => { m._abierto = true; }).on("popupclose", () => { m._abierto = false; });
    e._marca = m;
    etiquetasEst.push([e, m]);
  }
  capaCruces = L.layerGroup().addTo(mapa);
  capaTrenes = L.layerGroup().addTo(mapa);
  mapa.createPane("grupos").style.zIndex = 660;
  const pg = mapa.getPane("grupos");
  L.DomEvent.disableClickPropagation(pg);
  pg.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-selmapa]");
    if (b) seleccionarTren(b.dataset.selmapa);
  });
  mapa.on("zoomstart", () => { mapa.getPane("grupos").style.visibility = "hidden"; });
  mapa.on("zoomend", () => { ajustarEtiquetas(); despejarEtiquetas(); mapa.getPane("grupos").style.visibility = ""; });
  mapa.on("moveend", despejarEtiquetas);
  mapa.on("dragstart", () => { if (seguir) { seguir = false; pintarFichaTren(); } });
  mapa.on("click", () => seleccionarTren(null));
  crearControlesMapa();
  mapa.attributionControl.setPrefix(false);
  vistaInicialMapa();
  ajustarEtiquetas();
}
/* En el móvil la línea entera (Gijón–Cudillero, muy alargada) queda diminuta en una pantalla
   vertical: se abre encuadrando TU trayecto de «Mi viaje». En el ordenador, la línea completa. */
function vistaInicialMapa() {
  const o = +$("o").value, d = +$("d").value;
  if (window.innerWidth < 600 && o !== d) {
    const a = Math.min(o, d), b = Math.max(o, d);
    const pts = LINEA.estaciones.slice(a, b + 1).map((e) => [e.lat, e.lon]);
    mapa.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 14 });
  } else mapa.fitBounds(L.latLngBounds(LINEA.trazado), { padding: [20, 20] });
}

function crearControlesMapa() {
  const Ctl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const d = L.DomUtil.create("div", "mapa-botones");
      d.innerHTML = `<button type="button" id="m-yo" title="Mi estación más cercana">📍<span class="txt-l" id="m-yo-txt"> Cerca de mí</span></button>
        <button type="button" id="m-mio" title="Ir a mi tren (el próximo de «Mi viaje») y seguirlo">★<span class="txt-l"> Mi tren</span></button>
        <button type="button" id="m-linea" title="Ver toda la línea">↔<span class="txt-l"> Toda la línea</span></button>
        <button type="button" id="m-grande" title="Ampliar mapa">⤢</button>`;
      L.DomEvent.disableClickPropagation(d);
      return d;
    },
  });
  mapa.addControl(new Ctl());
  const ficha = L.DomUtil.create("div", "ficha-tren");
  ficha.id = "ficha-tren";
  ficha.hidden = true;
  $("mapa").appendChild(ficha);
  L.DomEvent.disableClickPropagation(ficha);
  $("m-linea").onclick = () => { seguir = false; mapa.fitBounds(L.latLngBounds(LINEA.trazado), { padding: [20, 20] }); };
  $("m-grande").onclick = () => {
    document.body.classList.toggle("mapa-grande");
    $("m-grande").textContent = document.body.classList.contains("mapa-grande") ? "✕" : "⤢";
    setTimeout(() => mapa.invalidateSize(), 80);
  };
  $("m-yo").onclick = cercaDeMi;
  $("m-mio").onclick = () => {
    const id = miTrenId();
    if (!id) { aviso("Elige tu trayecto en «Mi viaje» para seguir tu tren."); return; }
    if (!marcas[id]) {
      const v = viajesEntre(+$("o").value, +$("d").value, 1)[0];
      aviso(v ? `Tu tren (→ ${destinoCorto(v.t)}) aún no ha salido: sale a las ${hm(v.t.est_d[v.jo])}.` : "Tu tren aún no está en circulación.");
      return;
    }
    seguir = true; seleccionarTren(id);
    mapa.setView(marcas[id].getLatLng(), Math.max(mapa.getZoom(), 13), { animate: true });
  };
}
function miTrenId() { const vm = miViaje(); return vm && vm.id; }

function ajustarEtiquetas() {
  const z = mapa.getZoom();
  mapa.getContainer().classList.toggle("lejos", z < 11);  // vista general: menos texto
  for (const [e, m] of etiquetasEst) {
    const el = m.getTooltip() && m.getTooltip().getElement();
    if (el) el.style.display = e.cruce ? (z >= 10 ? "" : "none") : (z >= 12.5 ? "" : "none");
  }
}

function cercaDeMi() {
  const b = $("m-yo");
  const txt = (t) => { b.innerHTML = `📍<span class="txt-l"> ${esc(t)}</span>`; b.classList.add("con-txt"); };
  if (!navigator.geolocation) { txt("Sin ubicación"); return; }
  txt("Buscando…");
  navigator.geolocation.getCurrentPosition((pos) => {
    const yo = [pos.coords.latitude, pos.coords.longitude];
    const d = (e) => { const dx = (e.lon - yo[1]) * 81, dy = (e.lat - yo[0]) * 111; return Math.hypot(dx, dy); };
    const e = LINEA.estaciones.reduce((a, x) => (d(x) < d(a) ? x : a));
    if (!marcaYo) marcaYo = L.circleMarker(yo, { radius: 7, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(mapa);
    else marcaYo.setLatLng(yo);
    marcaYo.bindTooltip("Estás aquí");
    mapa.fitBounds(L.latLngBounds([yo, [e.lat, e.lon]]).pad(0.6), { maxZoom: 15 });
    setTimeout(() => e._marca.openPopup(), 350);
    txt(`${nombreCorto(e.nombre)} · ${d(e) < 1 ? Math.round(d(e) * 1000) + " m" : d(e).toFixed(1) + " km"}`);
  }, (err) => {
    txt(err.code === 1 ? "Permiso de ubicación denegado" : "No se pudo obtener la ubicación");
    setTimeout(() => { b.innerHTML = `📍<span class="txt-l"> Cerca de mí</span>`; b.classList.remove("con-txt"); }, 4000);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

function proximasSalidas(k, dir, n) {
  const now = ahora(), out = [];
  for (const t of R.trenes) {
    if (t.dir !== dir || t.fin || t.cancelado) continue;
    const j = t.k.indexOf(k);
    if (j < 0 || !t.para[j] || t.j0 > j) continue;
    const esFin = j === t.k.length - 1, h = esFin ? t.est_a[j] : t.est_d[j];
    if (h == null || h < now - 0.5 || esFin) continue;
    out.push({ t, j, h, retr: h - (t.prog_d[j]) });
  }
  return out.sort((a, b) => a.h - b.h).slice(0, n);
}
function popupEstacion(k) {
  const e = est(k);
  const fila = ({ t, h, retr }) => {
    const min = Math.max(0, Math.round(h - ahora()));
    return `<tr data-tren="${t.id}" class="clic"><td class="num"><b>${hm(h)}</b></td><td>${esc(destinoCorto(t))}<br><span class="pp-sub">tren ${t.num}</span></td>
      <td class="num" style="text-align:right">${min < 60 ? min + " min" : ""}<br>${retr >= 1 ? `<span class="tag ${retr <= 5 ? "warn" : "bad"}" style="margin:0">+${Math.round(retr)}</span>` : ""}</td></tr>`;
  };
  const col = (dir, tit, color) => {
    const l = proximasSalidas(k, dir, 3);
    return `<div class="pp-tit"><span class="bola" style="background:${color}"></span>${tit}</div>` +
      (l.length ? `<table class="pp-tabla">${l.map(fila).join("")}</table>` : `<div class="pp-sub" style="padding:4px 0 8px">Sin más salidas hoy</div>`);
  };
  return `<div class="pp"><div class="pp-cab"><b>${esc(e.nombre)}</b>${e.cruce ? '<span class="tag warn" style="margin-left:6px">vía de cruce</span>' : ""}</div>
    ${col(-1, "Hacia Gijón", "var(--vta)")}${col(1, "Hacia Avilés / Cudillero", "var(--ida)")}
    <button type="button" class="pp-btn" onclick="$('est').value=${k};guardar('est',${k});irA('estacion')">Ver todas las salidas</button></div>`;
}

function pintarCrucesMapa() {
  if (!capaCruces) return;
  capaCruces.clearLayers();
  const now = ahora(), vistos = new Set();
  for (const c of R.cruces || []) {  // vienen ordenados por hora: solo el próximo de cada estación
    if (c.hora < now - 1 || c.hora > now + 45 || vistos.has(c.k)) continue;
    vistos.add(c.k);
    const e = est(c.k), malo = c.ida.retraso_extra > 0.5 || c.vuelta.retraso_extra > 0.5;
    const espera = Math.max(c.ida.retraso_extra, c.vuelta.retraso_extra);
    L.marker([e.lat, e.lon], {
      icon: L.divIcon({ className: "", iconSize: null,
        html: `<div class="cruce-m${malo ? " malo" : ""}">⇄ ${hm(c.hora)}</div>` }),
      zIndexOffset: 200, keyboard: false,
    }).bindTooltip(`<b>Cruce en ${esc(c.estacion)} · ${hm(c.hora)}</b><br>Se cruzan el tren ${hacia(c.ida.id)} y el tren ${hacia(c.vuelta.id)}` +
      (malo ? `<br>Uno de los dos espera ~${Math.round(espera)} min más de lo previsto` : "") +
      (c.info === "movido" ? `<br>Cruce trasladado (en horario: ${esc(c.programado)})` : ""), { direction: "top", offset: [0, -14] })
      .addTo(capaCruces);
  }
}

function rumbo(x, dir) {
  const a = latlonKm(x), b = latlonKm(x + 0.15 * dir), c = latlonKm(x - 0.15 * dir);
  const p1 = mapa.latLngToLayerPoint(b ? a : c), p2 = mapa.latLngToLayerPoint(b || a);
  return (Math.atan2(p2.y - p1.y, p2.x - p1.x) * 180) / Math.PI;
}

function seleccionarTren(id) {
  trenSel = id;
  seguir = !!id && seguir;
  capaRuta && capaRuta.clearLayers();
  pintarFichaTren();
  pintarTrenesMapa();
}

function pintarFichaTren() {
  const f = $("ficha-tren");
  if (!f) return;
  const t = trenSel && R && R.trenes.find((x) => x.id === trenSel);
  if ($("mapa")) $("mapa").classList.toggle("con-ficha", !!t);   // la ficha tapa los botones de zoom
  if (!t) { f.hidden = true; f._html = null; return; }
  const j = t.parado ? Math.min(t.j0, t.k.length - 1) : proximaJ(t), sig = est(t.k[j]).nombre;
  const r = Math.round(retrasoActual(t));
  const proxCruce = (R.cruces || []).find((c) => (c.ida.id === t.id || c.vuelta.id === t.id) && c.hora >= ahora() - 0.5);
  f.hidden = false;
  const html = `<div class="ft-cab"><span class="ft-num" style="background:${colorDirHex(t.dir)}">C4</span>
      <div><b>→ ${esc(nombreCorto(t.destino))}</b>${numT(t.num)}<div class="pp-sub">${esc(t.situacion)}</div></div>
      <button type="button" class="ft-x" id="ft-cerrar" aria-label="Cerrar">×</button></div>
    <div class="ft-datos num">
      <div><span class="pp-sub">${t.parado ? "Sale de" : "Próxima"}</span><b>${esc(nombreCorto(sig))} ${hm(t.parado ? t.est_d[j] : t.est_a[j])}</b></div>
      <div><span class="pp-sub">Llega a ${esc(nombreCorto(t.destino))}</span><b>${hm(t.est_a[t.k.length - 1])}</b></div>
      <div><span class="pp-sub">Retraso</span><b>${t.con_datos ? (r > 0 ? "+" + r + " min" : "en hora") : "sin datos"}</b></div>
    </div>
    ${proxCruce ? `<div class="ft-cruce">⇄ Cruza con ${elQueVa(proxCruce.ida.id === t.id ? proxCruce.vuelta.id : proxCruce.ida.id)} en ${esc(proxCruce.estacion)} a las ${hm(proxCruce.hora)}</div>` : ""}
    <div class="ft-botones"><button type="button" id="ft-seguir" class="${seguir ? "on" : ""}">${seguir ? "Siguiendo ✓" : "Seguir tren"}</button>
      <button type="button" id="ft-detalle">Recorrido completo</button></div>`;
  if (f._html === html) return;  // sin cambios: no rehacer (así no se pierden toques)
  f._html = html;
  f.innerHTML = html;
  $("ft-cerrar").onclick = () => seleccionarTren(null);
  $("ft-seguir").onclick = () => { seguir = !seguir; pintarFichaTren(); pintarTrenesMapa(); };
  $("ft-detalle").onclick = () => abrirTren(t.id);
}

function pintarRutaSel(t, x) {
  capaRuta.clearLayers();
  const fin = est(t.k[t.k.length - 1]).km;
  const K = LINEA.trazado_km, pts = [latlonKm(x)];
  for (let i = 0; i < K.length; i++) if ((K[i] - x) * t.dir > 0 && (fin - K[i]) * t.dir > 0) pts.push(LINEA.trazado[i]);
  if (t.dir < 0) pts.splice(1, pts.length - 1, ...pts.slice(1).reverse());
  pts.push(latlonKm(fin));
  L.polyline(pts, { color: colorDirHex(t.dir), weight: 8, opacity: 0.9, interactive: false }).addTo(capaRuta);
  for (let j = t.j0; j < t.k.length; j++) {
    if (!t.para[j]) continue;
    const e = est(t.k[j]);
    L.marker([e.lat, e.lon], { interactive: false, keyboard: false, icon: L.divIcon({ className: "", iconSize: null,
      html: `<div class="hora-parada" style="border-color:${colorDirHex(t.dir)}">${hm(t.est_a[j])}</div>` }) }).addTo(capaRuta);
  }
}

function pintarTrenesMapa() {
  if (!mapa || !R) return;
  const now = ahora(), vivos = new Set(), vm = miViaje(), mio = vm && vm.id;
  if (pintarTrenesMapa._cruces !== R) {  // datos nuevos (cada 15 s): cruces y ventanas de estación
    pintarCrucesMapa();
    pintarTrenesMapa._cruces = R;
    for (const [e, m] of etiquetasEst) if (m._abierto) m.setPopupContent(popupEstacion(e.k));
  }
  const tnow = performance.now();
  for (const t of R.trenes) {
    const x = posSuave(t, now, tnow);
    if (x == null) continue;
    const ll = latlonKm(x);
    if (!ll) continue;
    vivos.add(t.id);
    const r = Math.round(retrasoActual(t));
    const visto = t.con_datos || t.ultimo_dato != null;   // en directo, o visto hace poco
    const rc = !visto ? "gris" : r <= 0 ? "ok" : r <= 5 ? "warn" : "bad";
    const ang = Math.round(rumbo(x, t.dir));
    const sel = t.id === trenSel, esMio = t.id === mio;
    const html = `<div class="tren-m ${t.dir > 0 ? "ida" : "vta"}${sel ? " sel" : ""}${esMio ? " mio" : ""}${t.con_datos ? "" : " sindatos"}">
      <div class="tm-punto" style="background:${colorDirHex(t.dir)}"><svg class="tm-flecha" width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true"><path d="M6 0 L-4 -5 L-1.5 0 L-4 5 Z" fill="#fff"/></svg></div>
      <div class="tm-etq">${esMio ? '<span class="tm-mio">★</span>' : ""}<b>${esMio ? `llega a ${esc(vm.a)} ${hm(vm.lle)}` : "→ " + esc(destinoCorto(t))}</b><span class="tm-r ${rc}">${visto ? (r > 0 ? "+" + r : "✓") + (t.con_datos ? "" : "?") : "?"}</span></div></div>`;
    let m = marcas[t.id];
    if (!m) {
      m = marcas[t.id] = L.marker(ll, { icon: L.divIcon({ html, className: "", iconSize: null }), zIndexOffset: 1000, riseOnHover: true })
        .on("click", (ev) => { L.DomEvent.stopPropagation(ev); seleccionarTren(t.id); }).addTo(capaTrenes);
      m._html = html; m._ang = null;
    } else {
      m.setLatLng(ll);
      // el icono solo se rehace si cambia lo que pone (no por moverse ni girar): así no parpadea
      if (m._html !== html) { m.setIcon(L.divIcon({ html, className: "", iconSize: null })); m._html = html; m._ang = null; }
    }
    girar(m, ang);
    m.setZIndexOffset(sel ? 3000 : esMio ? 2000 : 1000);
    if (sel) {
      if (!pintarTrenesMapa._rutaT || now - pintarTrenesMapa._rutaT > 0.25 || pintarTrenesMapa._rutaId !== t.id) {
        pintarRutaSel(t, x); pintarTrenesMapa._rutaT = now; pintarTrenesMapa._rutaId = t.id;
      }
    }
  }
  for (const id of Object.keys(marcas)) if (!vivos.has(id)) { capaTrenes.removeLayer(marcas[id]); delete marcas[id]; }
  if (trenSel && !vivos.has(trenSel)) seleccionarTren(null);
  pintarFichaTren();
  despejarEtiquetas();
}

/* ---------------------------------------------------------------- movimiento fluido
   Los datos llegan cada pocos segundos, pero los trenes se dibujan ~30 veces por segundo en su
   posición calculada para ese instante. Cuando llega un dato nuevo que corrige la posición, el tren
   no salta: se desliza hasta ella en menos de un segundo. */
const SUAVE = {};   // id -> {x (km mostrado), t (ms)}
function posSuave(t, now, tnow) {
  const obj = kmTren(t, now);
  if (obj == null) { delete SUAVE[t.id]; return null; }
  const s = SUAVE[t.id];
  if (!s || Math.abs(obj - s.x) > 2.5) { SUAVE[t.id] = { x: obj, t: tnow }; return obj; }  // lejos: se coloca sin más
  const dt = Math.max(0, Math.min(1000, tnow - s.t)) / 1000;
  s.t = tnow;
  const dif = obj - s.x;
  // si el dato nuevo lo deja un poco por detrás, el tren no retrocede: espera a que la posición real
  // lo alcance (un tren nunca va marcha atrás). Si la corrección es grande, se desliza hasta ella.
  if (dif * t.dir < 0 && Math.abs(dif) < 0.25) return s.x;
  s.x += dif * (1 - Math.exp(-dt / 0.9));
  return s.x;
}
function girar(m, ang) {
  if (m._ang === ang || !m._icon) return;
  const p = m._icon.querySelector(".tm-flecha path");
  if (p) { p.setAttribute("transform", `rotate(${ang})`); m._ang = ang; }
}
let ultFrame = 0;
function moverTrenes(tnow) {
  requestAnimationFrame(moverTrenes);
  if (tabActual !== "mapa" || !mapa || !R || document.hidden || mapa._animatingZoom) return;
  if (tnow - ultFrame < 33) return;          // ~30 imágenes por segundo: fluido sin gastar batería
  ultFrame = tnow;
  const now = ahora();
  let llSel = null;
  for (const t of R.trenes) {
    const m = marcas[t.id];
    if (!m) continue;
    const x = posSuave(t, now, tnow);
    if (x == null) continue;
    const ll = latlonKm(x);
    if (!ll) continue;
    m.setLatLng(ll);
    girar(m, Math.round(rumbo(x, t.dir)));
    if (t.id === trenSel) llSel = ll;
  }
  // «Seguir tren»: el mapa acompaña al tren de forma continua, como un navegador
  if (seguir && llSel) mapa.panTo(llSel, { animate: false });
}
requestAnimationFrame(moverTrenes);

/* Etiquetas sin solapes, como en los mapas profesionales: lo que se mueve (los trenes) manda.
   - La etiqueta de un tren solo se desplaza un poco (o cambia de lado) si pisa a otro tren o un cruce.
   - Si pisa el nombre de una estación, es el nombre de la estación el que se oculta mientras pasa.
   Se mantiene la posición anterior mientras siga libre, para que no bailen al moverse. */
function despejarEtiquetas() {
  if (!mapa || !capaTrenes) return;
  const cont = mapa.getContainer(), cr = cont.getBoundingClientRect();
  const rel = (el) => { const r = el.getBoundingClientRect(); return [r.left - cr.left, r.top - cr.top, r.width, r.height]; };
  const pisa = (a, b) => a[0] < b[0] + b[2] && a[0] + a[2] > b[0] && a[1] < b[1] + b[3] && a[1] + a[3] > b[1];
  const obst = [], ocupado = [], items = [];
  for (const id in marcas) {
    const m = marcas[id], tm = m._icon && m._icon.querySelector(".tren-m");
    const et = tm && tm.querySelector(".tm-etq");
    if (!et) continue;
    const p = mapa.latLngToContainerPoint(m.getLatLng()), punto = [p.x - 13, p.y - 13, 26, 26];
    obst.push(punto); ocupado.push(punto);
    items.push({ id, et, p, sube: tm.classList.contains("ida"),
                 pr: tm.classList.contains("sel") ? 0 : tm.classList.contains("mio") ? 1 : 2,
                 clase: tm.classList.contains("sel") ? " sel" : tm.classList.contains("mio") ? " mio" : "" });
  }
  // aviso de cruce: su sitio natural es a la izquierda de la estación; si un tren lo tapa, encima.
  // (se calcula, no se mide, para que no parpadee mientras se anima)
  if (capaCruces) capaCruces.eachLayer((m) => {
    const el = m._icon && m._icon.firstElementChild;
    if (!el || !el.offsetWidth) return;
    const q = mapa.latLngToContainerPoint(m.getLatLng()), w = el.offsetWidth, h = el.offsetHeight;
    const natural = [q.x - w - 11, q.y - h / 2, w, h], arriba = [q.x - w / 2, q.y - h - 13, w, h];
    const subir = ocupado.some((b) => pisa(natural, b));
    el.classList.toggle("arriba", subir);
    obst.push(subir ? arriba : natural);
  });
  // trenes casi en el mismo punto (p. ej. varios en Gijón): una sola ficha con la lista
  items.sort((a, b) => a.pr - b.pr || a.p.y - b.p.y);
  const grupos = [];
  for (const it of items) {
    const g = grupos.find((gr) => gr.some((o) => Math.hypot(o.p.x - it.p.x, o.p.y - it.p.y) < 34));
    if (g) g.push(it); else grupos.push([it]);
  }
  const pane = mapa.getPane("grupos"), usados = new Set();
  for (const gr of grupos) {
    if (gr.length === 1) {
      const it = gr[0];
      it.et.style.visibility = "";
      const w = it.et.offsetWidth, h = it.et.offsetHeight, ol = it.et.offsetLeft;
      const bx = it.p.x + ol, by = it.p.y + it.et.offsetTop, izq = -(w + 2 * ol), s = it.sube ? -1 : 1;
      const huecos = [];
      for (const dx of bx + w > cr.width - 6 ? [izq] : [0, izq]) for (const dy of [0, s * 20, -s * 36, s * 40, -s * 56]) huecos.push([dx, dy]);
      const prev = it.et._d;
      if (prev) huecos.sort((u, v) => (v[0] === prev[0] && v[1] === prev[1]) - (u[0] === prev[0] && u[1] === prev[1]));
      // la primera posición libre; si no hay ninguna, la que menos pisa
      const pisado = (c) => obst.reduce((t, b) => t + Math.max(0, Math.min(c[0] + c[2], b[0] + b[2]) - Math.max(c[0], b[0])) *
                                                       Math.max(0, Math.min(c[1] + c[3], b[1] + b[3]) - Math.max(c[1], b[1])), 0);
      let d = null, mejor = [huecos[0], Infinity];
      for (const o of huecos) {
        const caja = [bx + o[0], by + o[1], w, h];
        if (caja[1] < 0 || caja[1] + caja[3] > cr.height || caja[0] < 0) continue;
        const a = pisado(caja);
        if (a === 0) { d = o; break; }
        if (a < mejor[1]) mejor = [o, a];
      }
      d = d || mejor[0];
      it.et._d = d;
      it.et.style.transform = d[0] || d[1] ? `translate(${d[0]}px,${d[1]}px)` : "";
      const caja = [bx + d[0], by + d[1], w, h];
      obst.push(caja); ocupado.push(caja);
      continue;
    }
    // ficha agrupada
    for (const it of gr) it.et.style.visibility = "hidden";
    const clave = gr.map((it) => it.id).sort().join("|");
    usados.add(clave);
    let el = pane.querySelector(`[data-grupo="${clave}"]`);
    if (!el) { el = L.DomUtil.create("div", "grupo-etq", pane); el.dataset.grupo = clave; }
    const html = gr.map((it) => `<div class="ge-fila${it.clase}" data-selmapa="${it.id}">${it.et.innerHTML}</div>`).join("");
    if (el._html !== html) { el.innerHTML = html; el._html = html; }
    const xs = gr.map((it) => it.p.x), ys = gr.map((it) => it.p.y);
    const w = el.offsetWidth, h = el.offsetHeight, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    let x0 = Math.max(...xs) + 18;
    if (x0 + w > cr.width - 6) x0 = Math.min(...xs) - 18 - w;
    const y0 = Math.max(4, Math.min(cr.height - h - 4, cy - h / 2));
    L.DomUtil.setPosition(el, mapa.containerPointToLayerPoint([x0, y0]));
    const caja = [x0, y0, w, h];
    obst.push(caja); ocupado.push(caja);
  }
  for (const el of [...pane.querySelectorAll("[data-grupo]")]) if (!usados.has(el.dataset.grupo)) el.remove();
  for (const [, m] of etiquetasEst) {   // los nombres de estación ceden ante los trenes
    const el = m.getTooltip() && m.getTooltip().getElement();
    if (!el || el.style.display === "none") continue;
    el.classList.remove("tapada");
    if (el.offsetWidth && ocupado.some((b) => pisa(rel(el), b))) el.classList.add("tapada");
  }
}

/* ---------------------------------------------------------------- malla */
function pintarMalla() {
  const cont = $("malla"), E = LINEA.estaciones, now = ahora();
  const vent = leer("ventana", 180), verProg = $("ver-prog").checked, resaltar = $("ver-mio").checked;
  const ancho = cont.clientWidth || 900, estrecho = ancho < 600;
  const left = estrecho ? 112 : 132, right = 38, top = 32, L0 = 6;   // left = columna fija de estaciones
  const W = Math.max(ancho - left, estrecho ? 600 : 640);              // ancho del gráfico (desplazable)
  // Estaciones a escala real (km): la pendiente de cada línea es la velocidad del tren.
  // Separación mínima para que no se pisen los nombres de estaciones muy próximas.
  const minGap = 13, alto = Math.max(460, (E.length - 1) * 20);
  const kmTot = Math.max(1e-6, E[E.length - 1].km - E[0].km);
  const Y = [top];
  for (let i = 1; i < E.length; i++) Y.push(Y[i - 1] + Math.max(minGap, ((E[i].km - E[i - 1].km) / kmTot) * alto));
  const H = Y[Y.length - 1] + 36;
  const t0 = now - vent * 0.2, t1 = now + vent * 0.8;
  const x = (t) => L0 + ((t - t0) / (t1 - t0)) * (W - L0 - right), y = (k) => Y[k];
  const yKm = (kx) => {
    if (kx <= E[0].km) return Y[0];
    for (let i = 0; i < E.length - 1; i++)
      if (E[i].km <= kx && kx <= E[i + 1].km) return Y[i] + ((kx - E[i].km) / Math.max(1e-9, E[i + 1].km - E[i].km)) * (Y[i + 1] - Y[i]);
    return Y[E.length - 1];
  };
  const paso = vent <= 90 ? 10 : vent <= 180 ? 15 : 30;
  // «tu tren»: el próximo del trayecto elegido en Mi viaje
  const vm = resaltar ? miViaje() : null, mio = vm && vm.id;

  let ejes = `<svg width="${left}" height="${H}" style="display:block" aria-hidden="true">`;
  for (const e of E)
    ejes += `<text x="${left - 8}" y="${y(e.k) + 4}" text-anchor="end" class="${e.cruce ? "est-cruce" : ""}">${esc(((n, m) => n.length > m ? n.slice(0, m - 1) + "…" : n)(nombreCorto(e.nombre), estrecho ? 16 : 21))}</text>`;
  ejes += `</svg>`;
  let s = `<svg width="${W}" height="${H}" style="display:block" role="img" aria-label="Malla de circulación">`;
  for (const e of E) if (e.cruce) s += `<rect x="${L0}" y="${y(e.k) - 5}" width="${W - L0 - right}" height="10" class="banda-cruce"/>`;
  for (let t = Math.ceil(t0 / paso) * paso; t <= t1; t += paso)
    s += `<line x1="${x(t)}" y1="${top - 8}" x2="${x(t)}" y2="${H - 22}" stroke="var(--bd)"/><text x="${x(t)}" y="${H - 6}" text-anchor="middle">${hm(t)}</text>`;
  for (const e of E)
    s += `<line x1="${L0}" y1="${y(e.k)}" x2="${W - right}" y2="${y(e.k)}" stroke="var(--bd${e.cruce ? "2" : ""})" stroke-width="${e.cruce ? 1.2 : 0.5}"/>` +
      `<text x="${W - right + 5}" y="${y(e.k) + 4}" class="km">${Math.round(e.km)}</text>`;
  s += `<clipPath id="recorte"><rect x="${L0}" y="0" width="${W - L0 - right}" height="${H}"/></clipPath><g clip-path="url(#recorte)">`;
  const camino = (t, A, D, desde, sinLlegada0) => {
    const p = [];
    for (let j = desde; j < t.k.length; j++) {
      if (A[j] != null && !(sinLlegada0 && j === desde)) p.push([x(A[j]), y(t.k[j])]);
      if (D[j] != null && Math.abs(D[j] - A[j]) > 0.01) p.push([x(D[j]), y(t.k[j])]);
    }
    return p.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("");
  };
  // ---- etiquetas sin solapes: cada texto busca un hueco libre cerca de su sitio
  const cajas = [], puntos = [], textos = [];
  const libre = (bx, by, w, h) => !cajas.some((b) => bx < b[0] + b[2] && bx + w > b[0] && by < b[1] + b[3] && by + h > b[1]);
  const ocupar = (cx, cy, r) => cajas.push([cx - r, cy - r, 2 * r, 2 * r]);
  const poner = (it) => {   // {x, y, txt, cls, fill, op, anchor, offs, forzar, guia:[x,y], prio}
    const fs = 11, w = it.txt.length * fs * 0.62 + 4, h = 12;
    let anchor = it.anchor || "start", xx = it.x;
    let bx = anchor === "middle" ? xx - w / 2 : anchor === "end" ? xx - w : xx;
    if (bx + w > W - right - 2) { anchor = "end"; xx = Math.min(it.x, W - right - 2); bx = xx - w; }
    if (bx < L0 + 2) { anchor = "start"; xx = L0 + 2; bx = xx; }
    for (const dy of it.offs || [0, -12, 12, -24, 24]) {
      const by = it.y + dy - 9;
      if (by < 2 || by + h > H - 24) continue;
      if (libre(bx, by, w, h)) { cajas.push([bx, by, w, h]); return emitir(it, xx, it.y + dy, anchor, dy); }
    }
    if (it.forzar) { cajas.push([bx, it.y - 9, w, h]); return emitir(it, xx, it.y, anchor, 0); }
    return "";
  };
  const emitir = (it, xx, yy, anchor, dy) =>
    (dy && it.guia ? `<line x1="${it.guia[0]}" y1="${it.guia[1]}" x2="${anchor === "end" ? xx + 2 : xx - 2}" y2="${yy - 4}" stroke="${it.fill}" stroke-width="1" opacity="${0.6 * (it.op ?? 1)}"/>` : "") +
    `<text x="${xx}" y="${yy}" text-anchor="${anchor}" class="${it.cls}" style="fill:${it.fill};opacity:${it.op ?? 1}">${esc(it.txt)}</text>`;
  cajas.push([x(now) - 44, 0, 88, top - 10]);   // hueco del chip «Ahora»
  let capaMio = "";
  for (const t of R.trenes) {
    const fin = t.est_a[t.k.length - 1] ?? t.prog_a[t.k.length - 1];
    if (Math.max(fin, t.prog_a[t.k.length - 1]) < t0 || Math.min(t.prog_d[0], t.est_d[0] ?? 1e9) > t1) continue;
    if (verProg) s += `<path d="${camino(t, t.prog_a, t.prog_d, 0)}" fill="none" stroke="var(--mut)" stroke-width="1" stroke-dasharray="3 3" opacity=".32"/>`;
    if (t.fin || t.cancelado) continue;
    const c = colorDir(t.dir), esMio = t.id === mio, tenue = mio && !esMio;
    const desde = Math.max(0, t.j0 - 1);
    const A0 = t.est_a[0], D0 = t.est_d[0];
    // parado en la estación de origen esperando para salir: se dibuja discontinuo, no como si circulara
    const enCab = desde === 0 && A0 != null && D0 != null && D0 - A0 > 1 && D0 > now - 0.5;
    const dd = camino(t, t.est_a, t.est_d, desde, enCab);
    if (!dd) continue;
    const op = tenue ? 0.55 : (t.con_datos ? 1 : 0.6);
    const ret = t.con_datos && retrasoActual(t) >= 1 ? ` · +${Math.round(retrasoActual(t))} min` : "";
    const cab = enCab ? `<path d="M${x(Math.max(A0, t0)).toFixed(1)} ${y(t.k[0])}L${x(D0).toFixed(1)} ${y(t.k[0])}" class="cab" stroke="${c}" opacity="${op}"/>` : "";
    const g = `<g class="tr${esMio ? " mio" : ""}" data-tren="${t.id}">` +
      (esMio ? `<path d="${dd}" class="halo" stroke="${c}"/>` : "") + cab +
      `<path d="${dd}" class="vis" stroke="${c}" stroke-width="${esMio ? 4 : 2.4}" opacity="${op}"/>` +
      `<path d="${dd}" class="hit"/>${enCab ? `<path d="M${x(Math.max(A0, t0)).toFixed(1)} ${y(t.k[0])}L${x(D0).toFixed(1)} ${y(t.k[0])}" class="hit"/>` : ""}` +
      `<title>→ ${esc(destinoCorto(t))}${ret} · ${esc(t.situacion)}${esMio ? " · TU TREN" : ""} · tren ${t.num}</title></g>`;
    if (esMio) capaMio = g; else s += g;
    const prio = esMio ? 0 : 1, lbl = `${esMio ? `★ llega a ${vm.a} ${hm(vm.lle)}` : "→ " + destinoCorto(t)}${t.con_datos && retrasoActual(t) >= 1 ? " +" + Math.round(retrasoActual(t)) : ""}`;
    // esperas que no son un cruce (el cruce ya lleva su círculo con los minutos)
    for (const m of t.motivos) {
      if (m.min < 1 || m.tipo === "cruce") continue;
      const dj = t.est_d[m.j], aj = t.est_a[m.j] ?? dj;
      if (dj == null || dj < t0 || aj > t1) continue;
      textos.push({ prio: 4, x: (x(Math.max(aj, t0)) + x(dj)) / 2, y: y(t.k[m.j]) - 6, txt: `‖ ${Math.round(m.min)}'`, cls: "espera", fill: c, op: tenue ? 0.5 : 1, anchor: "middle", offs: [0, -11] });
    }
    const kx = kmTren(t, now);
    if (kx != null) {
      const yy = yKm(kx);
      puntos.push(`<circle cx="${x(now)}" cy="${yy}" r="${esMio ? 6 : 4.5}" fill="${c}" stroke="var(--panel)" stroke-width="1.5"/>`);
      ocupar(x(now), yy, esMio ? 7 : 5.5);
      textos.push({ prio, x: x(now) + 9, y: yy + 4, txt: enCab ? `${lbl} · sale ${hm(D0)}` : lbl, cls: "lbl", fill: c, op: tenue ? 0.6 : 1,
                    offs: enCab ? (t.dir > 0 ? [13, 26, -13] : [-13, -26, 13]) : [0, 13, -13, 26, -26, 39, -39], forzar: esMio, guia: [x(now) + 5, yy] });
    } else if (enCab) {
      textos.push({ prio: 2, x: x(D0) + 5, y: y(t.k[0]) + (t.dir > 0 ? 13 : -5), txt: `${lbl} · sale ${hm(D0)}`, cls: "lbl", fill: c, op: tenue ? 0.6 : 1 });
    } else {
      const j = t.k.findIndex((_, i) => { const tt = t.est_d[i] ?? t.est_a[i]; return tt != null && tt >= t0; });
      if (j >= 0) {
        const tt = t.est_d[j] ?? t.est_a[j];
        if (tt <= t1 && tt >= now - 1) textos.push({ prio: 2, x: x(tt) + 5, y: y(t.k[j]) + (t.dir > 0 ? 13 : -5), txt: lbl, cls: "lbl", fill: c, op: tenue ? 0.6 : 1 });
      }
    }
  }
  for (const c of R.cruces || []) {
    if (c.hora < t0 || c.hora > t1) continue;
    const extra = Math.max(c.ida.retraso_extra || 0, c.vuelta.retraso_extra || 0), malo = extra > 0.5;
    const cx = x(c.hora), cy = y(c.k);
    puntos.push(`<g><circle cx="${cx}" cy="${cy}" r="${malo ? 6 : 5}" class="cruce${malo ? " malo" : ""}"/>` +
      `<title>Cruce en ${esc(c.estacion)} · ${hm(c.hora)} entre el tren ${hacia(c.ida.id)} y el tren ${hacia(c.vuelta.id)}${malo ? ` · añade ${Math.round(extra)} min de espera` : ""}${c.info === "movido" ? " · cruce trasladado" : ""}</title></g>`);
    ocupar(cx, cy, 7);
    if (malo) textos.push({ prio: 3, x: cx, y: cy + 19, txt: `+${Math.round(extra)}'`, cls: "cruce-t", fill: "var(--warn)", anchor: "middle", offs: [0, -26, 12], forzar: true });
  }
  textos.sort((a, b) => a.prio - b.prio);
  const capaTxt = textos.map(poner).join("");
  s += capaMio;
  s += `<rect x="${L0}" y="0" width="${Math.max(0, x(now) - L0)}" height="${H - 22}" class="pasado"/>`;
  s += puntos.join("") + capaTxt;
  s += `</g><line x1="${x(now)}" y1="${top - 12}" x2="${x(now)}" y2="${H - 22}" stroke="var(--c4)" stroke-width="1.6"/>` +
    `<rect x="${x(now) - 42}" y="${top - 30}" width="84" height="18" rx="9" fill="var(--c4)"/>` +
    `<text x="${x(now)}" y="${top - 17}" text-anchor="middle" class="ahora">Ahora ${hm(now)}</text>` +
    `<text x="${W - right + 5}" y="${top - 17}" class="km">km</text></svg>`;
  // conserva el desplazamiento entre refrescos; la primera vez, «ahora» cerca del borde izquierdo
  const prev = cont.querySelector(".malla-scroll"), clave = vent + "|" + W;
  const sl = prev && pintarMalla._clave === clave ? prev.scrollLeft : Math.max(0, x(now) - 60);
  pintarMalla._clave = clave;
  cont.innerHTML = `<div class="malla-wrap"><div class="malla-ejes">${ejes}</div><div class="malla-scroll">${s}</div></div>`;
  cont.querySelector(".malla-scroll").scrollLeft = sl;
}

/* ---------------------------------------------------------------- cruces */
function pintarCruces() {
  const now = ahora();
  const lista = (R.cruces || []).filter((c) => c.hora >= now - 2).slice(0, 30);
  if (!lista.length) { $("lista-cruces").innerHTML = `<div class="vacio">No quedan cruces hoy.</div>`; return; }
  const lado = (x, dir) => {
    const espera = x.retraso_extra > 0.5 ? `<span class="tag warn">espera +${Math.round(x.retraso_extra)} min</span>`
      : x.espera >= 1 ? `<span class="cr-prev">parado ${Math.round(x.espera)} min (previsto)</span>` : "";
    return `<div class="cr-tren clic" data-tren="${x.id}"><span class="bola" style="background:${colorDir(dir)}"></span>
      <b>${hacia(x.id)}</b><span class="cr-h num">llega ${hm(x.llega)}${x.sale != null ? ` · sale ${hm(x.sale)}` : ""}</span>${espera}</div>`;
  };
  $("lista-cruces").innerHTML = `<div class="cruces">` + lista.map((c) => {
    const malo = c.ida.retraso_extra > 0.5 || c.vuelta.retraso_extra > 0.5;
    return `<div class="cr${malo ? " malo" : ""}">
      <div class="cr-cab"><span class="cr-hora num">${hm(c.hora)}</span><b>${esc(c.estacion)}</b>
        ${c.info === "movido" ? `<span class="tag warn" title="En el horario era en ${esc(c.programado)}">Trasladado (era en ${esc(nombreCorto(c.programado))})</span>` : ""}</div>
      ${lado(c.ida, 1)}${lado(c.vuelta, -1)}</div>`;
  }).join("") + `</div>`;
}

/* ---------------------------------------------------------------- precisión */
const fechaCorta = (f) => f ? `${+f.slice(6, 8)}/${+f.slice(4, 6)}` : "";
const dec = (v, n = 1) => (+v).toFixed(n).replace(".", ",");
function tarjetaAprendizaje() {
  if (!APREN || APREN.cargando) return "";
  const kpi = (v, l) => `<div class="kpi"><div class="v num">${v}</div><div class="l">${l}</div></div>`;
  const ns = APREN.sesgos_n || 0;
  let s = `<div class="card apr-card"><div class="card-cab"><h3>🧠 Aprende de sus errores</h3></div>
    <p class="sub" style="margin:0 0 10px">El programa se corrige solo: guarda cada predicción, la compara con lo que pasó de verdad y ajusta lo que falla. Cuanto más se usa, más afina.</p>
    <div class="kpis kpis-4">${kpi(APREN.tramos || 0, "tramos con tiempo real aprendido")}${kpi(APREN.salidas_n || 0, "trenes con su retraso habitual aprendido")}${kpi(APREN.paradas_n || 0, "estaciones con su tiempo de parada real")}${kpi(ns, "estaciones con sesgo corregido")}</div>`;
  const g = APREN.guardado || {};
  s += g.activo
    ? `<p class="apr-guardado ok">☁️ Guardado en GitHub${g.ultimo_guardado ? ` · último guardado ${esc(g.ultimo_guardado)}` : ""}. Aunque el servidor se reinicie, no se pierde nada.</p>`
    : `<p class="apr-guardado warn">⚠ El aprendizaje solo está en el servidor: si Render lo reinicia (cada noche) se pierde. Configura el guardado en GitHub (C4_GH_TOKEN y C4_GH_REPO) para conservarlo.</p>`;
  if (g.error) s += `<p class="apr-guardado warn">⚠ ${esc(g.error)}</p>`;
  if (APREN.salidas && APREN.salidas.length)
    s += `<h4 class="apr-sub">Trenes que suelen salir con retraso</h4><div class="apr-lista">` + APREN.salidas.slice(0, 8).map((x) =>
      {
        const t = R && R.trenes.find((y) => y.num === x.num);
        const nom = t ? `El de las ${hm(t.prog_d[0])} · ${esc(nombreCorto(t.origen))} → ${esc(destinoCorto(t))}` : "Un tren que hoy no circula";
        return `<div class="apr-fila"><span>${nom} ${numT(x.num)}</span><b class="num mas">+${dec(x.min)} min</b></div>`;
      }).join("") +
      `</div><p class="sub" style="margin-top:6px">Mientras no han salido, esos trenes ya se calculan con su retraso habitual (y sus cruces), en vez de suponerlos puntuales.</p>`;
  if (APREN.sesgos && APREN.sesgos.length)
    s += `<h4 class="apr-sub">Estaciones donde se corrige la hora estimada</h4><div class="apr-lista">` + APREN.sesgos.slice(0, 8).map((x) =>
      `<div class="apr-fila"><span>${esc(x.estacion)}</span><b class="num ${x.min > 0 ? "mas" : "menos"}">${x.min > 0 ? "+" : ""}${dec(x.min)} min</b></div>`).join("") +
      `</div><p class="sub" style="margin-top:6px">«+» = ahí los trenes suelen llegar algo más tarde de lo previsto; ya está corregido y acotado para no pasarse.</p>`;
  return s + `</div>`;
}
function pintarPrecision() {
  const el = $("precision");
  if (!PREC) { el.innerHTML = tarjetaAprendizaje() || `<div class="vacio">Cargando…</div>`; return; }
  const sem = PREC.semana || {}, hoy = PREC.hoy || {}, tot = sem.total, th = hoy.total;
  const apr = tarjetaAprendizaje();
  if (!tot) {
    el.innerHTML = apr + `<div class="aviso info"><span>ℹ</span><div><b>Aún no hay mediciones de acierto.</b> Se acumulan solas mientras el programa está abierto y Renfe da datos en directo. Con un par de días de uso verás si acierta más que la app oficial.</div></div>`;
    return;
  }
  const kpi = (v, l, cls) => `<div class="kpi${cls ? " " + cls : ""}"><div class="v num">${v}</div><div class="l">${l}</div></div>`;
  const gana = tot.error_nuestro < tot.error_adif;
  let h = `<div class="veredicto ${gana ? "ok" : "warn"}">${gana
      ? `<b>Sí:</b> se equivoca de media <b>${dec(tot.error_nuestro)} min</b>; la app oficial, <b>${dec(tot.error_adif)} min</b>.`
      : `<b>Todavía no:</b> se equivoca de media ${dec(tot.error_nuestro)} min y la app oficial ${dec(tot.error_adif)} min. Está aprendiendo.`}
      <span class="sub"> ${tot.n} llegadas medidas${PREC.incluye_modelo_anterior ? " con la versión anterior del cálculo (la actual empieza a medirse el " + fechaCorta(PREC.modelo_desde) + ")" : PREC.modelo_desde ? " con el cálculo actual (desde el " + fechaCorta(PREC.modelo_desde) + ")" : " en los últimos 7 días"}.</span></div>`;
  h += `<div class="kpis kpis-4">` +
    kpi(`${dec(tot.error_nuestro)} min`, "error medio de esta app", "yo") +
    kpi(`${dec(tot.error_adif)} min`, "error medio de la app oficial") +
    kpi(`${tot.acierto_nuestro}%`, "aciertos a ±1 min (esta app)", "yo") +
    kpi(`${tot.acierto_adif}%`, "aciertos a ±1 min (app oficial)") + `</div>`;
  const fila = (hz, x) => {
    if (!x) return `<tr><td>${hz} min</td><td colspan="3" style="color:var(--mut)">sin datos aún</td></tr>`;
    const max = Math.max(x.error_nuestro, x.error_adif, 0.1);
    const barra = (v, c) => `<div class="barra"><i style="width:${(100 * v) / max}%;background:${c}"></i></div>`;
    return `<tr><td>${hz} min<div class="sub">${x.n} llegadas</div></td><td><b>${dec(x.error_nuestro)}</b>${barra(x.error_nuestro, "var(--c4)")}</td>
      <td>${dec(x.error_adif)}${barra(x.error_adif, "var(--mut)")}</td><td><b>${x.acierto_nuestro}%</b><div class="sub">oficial ${x.acierto_adif}%</div></td></tr>`;
  };
  const tabla = (g, titulo) => `<h3 style="font-size:14px;margin:14px 0 6px">${titulo}</h3><div class="scroll-x"><table class="tabla num tabla-prec">
    <thead><tr><th>Antelación</th><th>Esta app</th><th>Oficial</th><th>Aciertos</th></tr></thead>
    <tbody>${["5", "10", "20", "30"].map((hz) => fila(hz, g[hz])).join("")}</tbody></table></div>
    <p class="sub" style="margin:4px 0 0">Error medio en minutos. «Aciertos»: llegadas en las que se equivocó 1 minuto o menos.</p>`;
  // si hoy es lo único que hay, las dos tablas serían iguales: se enseña una
  const igual = th && th.n === tot.n;
  h += tabla(sem, igual ? "Por antelación (hoy)" : "Por antelación · últimos 7 días") + (th && !igual ? tabla(hoy, "Hoy") : "");
  el.innerHTML = h + apr;
}

/* ---------------------------------------------------------------- cómo funciona */
function pintarInfo() {
  const cr = LINEA.estaciones.filter((e) => e.cruce);
  $("info").innerHTML = `<h2>Cómo calcula la hora de llegada</h2>
  <p>La app oficial calcula <b>horario + retraso actual</b> y da por hecho que el retraso se mantiene igual hasta el final. En la C-4 eso falla porque es <b>vía única</b>: los trenes solo pueden cruzarse en algunas estaciones y, si el tren que viene de frente va tarde, el tuyo tiene que esperarle.</p>
  <h3>Qué tiene en cuenta</h3>
  <ul>
    <li><b>Cruces.</b> Los saca del propio horario oficial: dónde coinciden dos trenes de sentido contrario. Un tren no sale del apartadero hasta que ha entrado el contrario. Si uno de los dos ya pasó la estación prevista, busca el siguiente apartadero donde pueden cruzarse.</li>
    <li><b>Vía única en cabeceras.</b> En Cudillero, Pravia, Avilés o Gijón, un tren no sale hasta que ha llegado el que venía de frente por ese tramo.</li>
    <li><b>Tren de delante.</b> No se entra en un tramo mientras el tren anterior del mismo sentido no haya llegado al siguiente apartadero.</li>
    <li><b>Rotaciones.</b> El tren que sale de cabecera suele ser el que acaba de llegar. Si Renfe indica la vía, se usa para saber cuál es.</li>
    <li><b>Márgenes del horario.</b> Nunca sale antes de su hora. Si va tarde, puede recortar las esperas que ya trae el horario, y así se ve cuándo recupera tiempo.</li>
    <li><b>Retraso por posición.</b> Para la C-4, Renfe a menudo da la posición pero no el retraso. El programa lo calcula viendo cuándo sale de cada estación. Ojo: cuando Renfe dice «en tránsito a X», el tren <i>acaba de salir</i> de X (comprobado en directo); solo «llegando a Y» significa que va a entrar en Y.</li>
    <li><b>GPS del tren.</b> Si Renfe da la posición real del tren entre dos estaciones, se proyecta sobre la vía para saber cuánto le queda (con arranque y frenada). Se descartan las coordenadas que en realidad son las de una estación y los saltos hacia atrás.</li>
    <li><b>Holgura del horario.</b> En marcha, un tren no tarda mucho más de lo que le permite la vía: si el horario da más tiempo del necesario, lo recupera y espera en la siguiente estación.</li>
    <li><b>Aprendizaje.</b> Con el tiempo sustituye los tiempos teóricos entre estaciones por los que observa de verdad, aprende cuánto dura cada parada y el retraso habitual de cada tren, y corrige las estaciones donde se equivoca siempre en el mismo sentido.</li>
    <li><b>Lejos, prudencia.</b> A más de 5 minutos, si no hay un cruce que lo explique, mezcla lo calculado con «horario + retraso»: medido con datos reales, así se equivoca menos.</li>
  </ul>
  <h3>Estaciones con vía de cruce (deducidas del horario de hoy)</h3>
  <p>${cr.map((e) => `<span class="tag gris" style="margin:0 6px 6px 0">${esc(e.nombre)}${e.cruces_dia ? ` · ${e.cruces_dia}` : ""}</span>`).join("")}</p>
  <h3>Límites</h3>
  <ul>
    <li>El puesto de mando puede cambiar un cruce de sitio y Renfe no lo publica. El programa lo supone con reglas (modo actual: <b>${esc(R ? R.modo_cruces : "")}</b>).</li>
    <li>Si Renfe deja de actualizar sus datos, el programa lo detecta y vuelve al horario, y lo indica arriba.</li>
    <li>Los trenes sin datos en tiempo real se suponen en hora (aparecen más claros).</li>
  </ul>
  <h3>Fuentes</h3>
  <p>Horario oficial: GTFS de Renfe Cercanías (Punto de Acceso Nacional de Transportes). Tiempo real: GTFS-Realtime de Renfe (posiciones, retrasos y avisos). Mapa: © OpenStreetMap, © CARTO.</p>`;
}

/* ---------------------------------------------------------------- detalle de tren */
function abrirTren(id, jo, jd) {
  cajonTren = { id, jo: jo == null ? null : +jo, jd: jd == null ? null : +jd };
  pintarCajon();
  $("cajon").classList.add("abierto"); $("cajon").setAttribute("aria-hidden", "false"); $("velo").classList.add("on");
}
function cerrarTren() {
  cajonTren = null;
  $("cajon").classList.remove("abierto"); $("cajon").setAttribute("aria-hidden", "true"); $("velo").classList.remove("on");
}
function pintarCajon() {
  if (!cajonTren || !R) return;
  const t = R.trenes.find((x) => x.id === cajonTren.id);
  if (!t) return cerrarTren();
  const { jo, jd } = cajonTren;
  const filas = [];
  for (let j = 0; j < t.k.length; j++) {
    const e = est(t.k[j]);
    const mot = t.motivos.filter((m) => m.j === j);
    const cr = (R.cruces || []).find((c) => c.k === t.k[j] && (c.ida.id === t.id || c.vuelta.id === t.id));
    if (!t.para[j] && !mot.length && !cr) continue;
    const pasado = t.fin || j < t.j0;
    const salida = j === 0 || (t.parado && j === t.j0 && j < t.k.length - 1);
    const hEst = salida ? t.est_d[j] : t.est_a[j];
    const hProg = salida ? t.prog_d[j] : t.prog_a[j];
    const hApp = salida ? t.adif_d[j] : t.adif_a[j];
    const notas = mot.map((m) => `<div style="color:var(--warn);font-size:12.5px">${esc(m.texto)} (+${Math.round(m.min)} min)</div>`).join("") +
      (cr && !mot.some((m) => m.tipo === "cruce") ? `<div style="font-size:12.5px;color:var(--tx2)">Cruce con ${elQueVa(cr.ida.id === t.id ? cr.vuelta.id : cr.ida.id)}${cr.info === "movido" ? " (trasladado)" : ""}</div>` : "") +
      (!t.para[j] ? `<div style="font-size:12px;color:var(--mut)">sin parada</div>` : "") +
      (j === (t.parado ? t.j0 : proximaJ(t)) && !t.fin ? `<div style="font-size:12.5px;font-weight:700;color:var(--c4)">◀ ${esc(t.situacion)}</div>` : "");
    filas.push(`<tr class="${pasado ? "pasado" : ""}${j === jo || j === jd ? " aqui" : ""}">
      <td>${e.cruce ? "<b>" : ""}${esc(e.nombre)}${e.cruce ? "</b>" : ""}${notas}</td>
      <td>${hm(hProg)}</td><td>${pasado ? "" : hm(hApp)}</td><td class="e">${pasado ? "" : (salida ? hmS(hEst) : hm(hEst))}${!pasado && salida && j > 0 ? `<div class="sub" style="font-weight:400">sale</div>` : ""}</td></tr>`);
  }
  $("cajon").innerHTML = `<div class="cajon-cab"><div><h2>→ ${esc(destinoCorto(t))} ${numT(t.num)}</h2>
      <div style="color:var(--tx2)">${esc(t.origen)} → ${esc(t.destino)}</div></div>
      <button class="cerrar" id="cerrar" aria-label="Cerrar">×</button></div>
    <div class="datos"><span class="tag ${t.dir > 0 ? "ida" : "vta"}" style="margin:0">${t.dir > 0 ? "Hacia Cudillero/Avilés" : "Hacia Gijón"}</span>
      ${(t.con_datos || t.ultimo_dato != null) ? tagEstado(t, Math.min(t.j0, t.k.length - 1)).replace('class="tag', 'style="margin:0" class="tag') : '<span class="tag gris" style="margin:0">Sin datos en tiempo real</span>'}
      ${t.via ? `<span class="tag gris" style="margin:0">Vía ${esc(t.via)}</span>` : ""}
      <span class="tag gris" style="margin:0">Fuente: ${esc(t.fuente)}</span></div>
    <p style="margin:0 0 10px;color:var(--tx2)">${esc(t.situacion)}</p>
    <table class="tabla num"><thead><tr><th>Estación</th><th>Horario</th><th>App oficial</th><th>Estimado</th></tr></thead><tbody>${filas.join("")}</tbody></table>`;
  $("cerrar").onclick = cerrarTren;
}

/* ---------------------------------------------------------------- navegación */
/* ---------------------------------------------------------------- Inicio (panel de resumen) */
function proxDesde(k, n) {
  const now = ahora(), out = [];
  for (const t of R.trenes) {
    if (t.fin || t.cancelado) continue;
    const j = t.k.indexOf(k);
    if (j < 0 || !t.para[j] || t.j0 > j || j === t.k.length - 1) continue;   // el que termina aquí no sale
    const sale = t.est_d[j];
    if (sale == null || sale < now - 0.5) continue;
    out.push({ num: t.num, dir: t.dir, destino: t.destino, sale, t });
  }
  return out.sort((a, b) => a.sale - b.sale).slice(0, n);
}
function pintarInicio() {
  if (!R || !LINEA) return;
  const viejo = edadDatos() > 90;
  const meta = { directo: ["ok", "En directo"], congelado: ["warn", "Renfe no actualiza"], sin_posiciones: ["warn", "Renfe no da posiciones"], sin_conexion: ["bad", "Sin conexión con Renfe"] };
  const [cls, lbl] = viejo ? ["warn", "Actualizando…"] : (meta[R.calidad] || ["", "En directo"]);
  const prox = (R.cruces || []).find((c) => c.hora >= R.ahora - 1);
  let h = `<div class="dash">`;
  h += `<div class="hero hero-${cls}">
      <div class="hero-top"><span class="pill-linea">C4</span>
        <div><div class="hero-tit">${R.calidad === "directo" && !viejo ? "Línea en directo" : esc(lbl)}</div>
        <div class="hero-sub">Gijón – Cudillero${R.actualizado ? " · actualizado " + esc(R.actualizado.slice(0, 5)) : ""}</div></div>
        <span class="hero-dot"></span></div>
      <div class="hero-kpis">
        <div class="hk"><div class="hk-n num">${R.en_circulacion || 0}</div><div class="hk-l">trenes en circulación</div></div>
        <div class="hk"><div class="hk-n num">${R.con_posicion || 0}</div><div class="hk-l">localizados en vivo</div></div>
        <div class="hk"><div class="hk-n num">${prox ? hm(prox.hora) : "—"}</div><div class="hk-l">${prox ? "próx. cruce · " + esc(nombreCorto(prox.estacion)) : "sin cruces próximos"}</div></div>
      </div></div>`;
  if ((R.tramos_aprendidos || 0) + (R.sesgos_corregidos || 0) + (R.salidas_aprendidas || 0) > 0)
    h += `<div class="apr-strip" onclick="irA('precision')">🧠 <span>Aprendido: <b>${R.tramos_aprendidos || 0}</b> tramos, <b>${R.salidas_aprendidas || 0}</b> trenes con su retraso habitual y <b>${R.sesgos_corregidos || 0}</b> estaciones corregidas</span><span class="cta-fl">›</span></div>`;
  h += `<div class="dash-cols"><div class="dash-col">`;
  h += `<button class="cta-ir" onclick="irA('ir')">
      <div class="cta-ic">🧭</div>
      <div class="cta-tx"><div class="cta-t">¿A dónde vas?</div><div class="cta-s">Ruta puerta a puerta: bus urbano + tren, con la hora real de llegada</div></div>
      <span class="cta-fl">→</span></button>`;
  const o = +$("o").value, d = +$("d").value;
  h += `<div class="card"><div class="card-cab"><h3>Tu próximo tren</h3><button class="link" onclick="irA('viaje')">Ver todos ›</button></div>`;
  if (o === d) h += `<div class="vacio">Elige un trayecto en «Mi viaje».</div>`;
  else {
    const v = viajesEntre(o, d, 2), now = ahora();
    h += `<div class="mini-ruta">${esc(nombreCorto(est(o).nombre))} <span class="mr-fl">→</span> ${esc(nombreCorto(est(d).nombre))}</div>`;
    h += v.length ? v.map(({ t, jo, jd }) => {
      const sal = t.est_d[jo], lle = t.est_a[jd], falta = Math.floor(sal - now);
      return `<div class="mini-tren" data-tren="${t.id}" data-jo="${jo}" data-jd="${jd}">
        <div class="mt-l"><div class="mt-dest"><span class="bola" style="background:${colorDir(t.dir)}"></span><b>→ ${esc(destinoCorto(t))}</b>${tagEstado(t, jo)}</div>
          <div class="mt-sub">llega a ${esc(nombreCorto(est(d).nombre))} <b class="num">${hm(lle)}</b></div></div>
        <div class="mt-r"><span class="mt-lle num">${hmS(sal)}</span><span class="mt-when">${falta <= 0 ? "sale ya" : falta < 60 ? "sale en " + falta + " min" : "sale"}</span></div></div>`;
    }).join("") : `<div class="vacio">No quedan trenes hoy en este trayecto.</div>`;
  }
  h += `</div>`;
  h += `<div class="card"><div class="card-cab"><h3>Cerca de ti</h3></div>
      <button class="btn-grande2" id="inicio-cerca">📍 Estaciones y buses cerca de mí</button>
      <div id="inicio-cerca-res"></div></div>`;
  h += `</div><div class="dash-col">` + tarjetaLinea() + tarjetaCruces() + `</div></div>`;
  const tools = [["ir", "Ir a…", "🧭"], ["mapa", "Mapa en vivo", "🗺️"], ["/bus/", "Bus en vivo", "🚌"],
                 ["malla", "Malla", "📈"], ["cruces", "Cruces", "⇄"], ["precision", "Precisión", "🎯"],
                 ["estacion", "Estación", "🚉"], ["info", "Cómo funciona", "ℹ️"]];
  h += `<div class="card solo-movil"><div class="card-cab"><h3>Todo</h3></div><div class="tools">` +
    tools.map(([t, n, ic]) => t[0] === "/" ? `<a class="tool" href="${t}">${ic}<span>${n}</span></a>`
      : `<button class="tool" onclick="irA('${t}')">${ic}<span>${n}</span></button>`).join("") + `</div></div>`;
  h += `</div>`;
  $("inicio").innerHTML = h;
  const b = $("inicio-cerca"); if (b) b.onclick = inicioCerca;
}
// «Ahora en la línea»: cada tren circulando, dónde está y cómo va
function tarjetaLinea() {
  const now = ahora();
  const ts = R.trenes.filter((t) => !t.fin && !t.cancelado && (t.con_datos || t.ultimo_dato != null) && !(t.j0 === 0 && t.est_d[0] > now + 1));
  const fila = (t) => {
    const j = t.parado ? Math.min(t.j0, t.k.length - 1) : proximaJ(t);
    const sig = t.parado ? `sale ${hm(t.est_d[j])}` : `${esc(nombreCorto(est(t.k[j]).nombre))} ${hm(t.est_a[j])}`;
    return `<div class="al-f clic" data-tren="${t.id}"><span class="bola" style="background:${colorDir(t.dir)}"></span>
      <div class="al-m"><div><b>→ ${esc(destinoCorto(t))}</b>${tagEstado(t, j)}</div><div class="al-s">${esc(t.situacion)}</div></div>
      <div class="al-h num">${sig}</div></div>`;
  };
  const grupo = (dir, tit) => {
    const l = ts.filter((t) => t.dir === dir).sort((a, b) => dir * (est(b.k[Math.min(b.j0, b.k.length - 1)]).km - est(a.k[Math.min(a.j0, a.k.length - 1)]).km));
    return l.length ? `<div class="al-g">${tit}</div>` + l.map(fila).join("") : "";
  };
  const cuerpo = grupo(1, "Hacia Avilés / Pravia / Cudillero") + grupo(-1, "Hacia Gijón");
  return `<div class="card"><div class="card-cab"><h3>Ahora en la línea</h3><button class="link" onclick="irA('mapa')">Mapa ›</button></div>` +
    (cuerpo || `<div class="vacio">Ahora mismo no hay trenes circulando con datos en directo.</div>`) + `</div>`;
}
function tarjetaCruces() {
  const now = ahora();
  const l = (R.cruces || []).filter((c) => c.hora >= now - 1).slice(0, 4);
  if (!l.length) return "";
  return `<div class="card"><div class="card-cab"><h3>Próximos cruces</h3><button class="link" onclick="irA('cruces')">Todos ›</button></div>` +
    l.map((c) => {
      const extra = Math.max(c.ida.retraso_extra, c.vuelta.retraso_extra);
      return `<div class="al-f"><span class="al-cr num">${hm(c.hora)}</span><div class="al-m"><div><b>${esc(nombreCorto(c.estacion))}</b>${extra > 0.5 ? `<span class="tag warn">espera +${Math.round(extra)}</span>` : ""}</div>
        <div class="al-s">${hacia(c.ida.id)} · ${hacia(c.vuelta.id)}</div></div></div>`;
    }).join("") + `</div>`;
}
function inicioCerca() {
  const res = $("inicio-cerca-res"), b = $("inicio-cerca");
  if (!navigator.geolocation) { res.innerHTML = `<div class="vacio">Este dispositivo no da la ubicación.</div>`; return; }
  b.disabled = true; b.textContent = "📍 Buscando…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const yo = [pos.coords.latitude, pos.coords.longitude];
    const dk = (e) => Math.hypot((e.lon - yo[1]) * 81, (e.lat - yo[0]) * 111);
    const e = LINEA.estaciones.reduce((a, x) => dk(x) < dk(a) ? x : a);
    const sal = proxDesde(e.k, 3);
    const lejos = dk(e) > 1.2;
    let h = `<div class="cerca-est"><div class="ce-cab">🚉 <b>${esc(e.nombre)}</b> <span class="sub">a ${dk(e) < 1 ? Math.round(dk(e) * 1000) + " m" : dec(dk(e)) + " km"}</span></div>`;
    h += sal.length ? sal.map((s) => `<div class="ce-fila"><span class="tag ${s.dir > 0 ? "ida" : "vta"}">→ ${esc(nombreCorto(s.destino))}</span><span class="ce-mid">${tagEstado(s.t, s.t.k.indexOf(e.k))}</span><b class="num">${hmS(s.sale)}</b></div>`).join("") : `<div class="vacio">Sin trenes próximos.</div>`;
    if (lejos) h += `<button class="link" id="cerca-ruta" style="margin-top:6px">🧭 Cómo llegar desde aquí a un tren ›</button>`;
    h += `</div>`;
    b.disabled = false; b.textContent = "📍 Actualizar mi posición";
    res.innerHTML = h + `<div class="vacio" id="cb">Buscando buses cerca…</div>`;
    const cr = $("cerca-ruta");
    if (cr) cr.onclick = () => { GPS = yo; ELEGIDO.o = null; $("ir-o").value = MI_UBIC; irA("ir"); $("ir-d").focus(); };
    try {
      const j = await pedir(`/api/bus/cercanas?lat=${yo[0]}&lon=${yo[1]}`, 9000);
      const el = $("cb"); if (!el) return;
      el.outerHTML = (j.paradas && j.paradas.length)
        ? `<div class="cerca-bus"><div class="ce-cab">🚌 Paradas de bus cerca</div>` +
          j.paradas.slice(0, 4).map((p) => `<a class="ce-fila ce-link" href="/bus/#parada-${p.id}"><span class="ce-mid" style="flex:1">${esc(p.nombre)}<span class="ce-lin">${(p.lineas || []).slice(0, 5).map((l) => esc(l)).join(" · ")}</span></span><span class="sub">${p.metros} m ›</span></a>`).join("") +
          `<a class="link" href="/bus/" style="display:inline-block;margin-top:8px">Abrir el mapa de buses en vivo ›</a></div>`
        : "";
    } catch (err) { const el = $("cb"); if (el) el.remove(); }
  }, () => {
    b.disabled = false; b.textContent = "📍 Estaciones y buses cerca de mí";
    res.innerHTML = `<div class="vacio">No se pudo obtener la ubicación.</div>`;
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

function irA(tab) {
  if (tab !== tabActual) window.scrollTo(0, 0);
  tabActual = tab;
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", b.dataset.tab === tab);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "tab-" + tab;
  history.replaceState(null, "", "#" + tab);
  if (tab === "mapa") {
    const nuevo = !mapa;
    iniciarMapa();
    setTimeout(() => { if (mapa) { mapa.invalidateSize(); if (nuevo) vistaInicialMapa(); pintarTrenesMapa(); } }, 60);
  }
  if (tab === "precision") cargarPrecision();
  pintarTodo();
}
function pintarTodo() {
  if (!R || !LINEA) return;
  pintarEstado();
  if (tabActual === "inicio") pintarInicio();
  if (tabActual === "viaje") pintarViaje();
  if (tabActual === "estacion") pintarEstacion();
  if (tabActual === "malla") pintarMalla();
  if (tabActual === "cruces") pintarCruces();
  if (tabActual === "info") pintarInfo();
  if (tabActual === "mapa") pintarTrenesMapa();
  pintarCajon();
}

document.addEventListener("click", (ev) => {
  const comp = ev.target.closest("[data-compartir]");
  if (comp) { ev.stopPropagation(); compartir(comp.dataset.compartir); return; }
  const fav = ev.target.closest("[data-fav]");
  if (fav) {
    const f = favoritos()[+fav.dataset.fav];
    const eo = LINEA.estaciones.find((e) => e.id === f.o), ed = LINEA.estaciones.find((e) => e.id === f.d);
    if (eo && ed) { $("o").value = eo.k; $("d").value = ed.k; $("o").onchange(); }
    return;
  }
  const el = ev.target.closest("[data-tren]");
  if (el && !ev.target.closest("#cajon")) abrirTren(el.dataset.tren, el.dataset.jo, el.dataset.jd);
});
$("velo").onclick = cerrarTren;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarTren(); });
for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => irA(b.dataset.tab);
$("o").onchange = $("d").onchange = () => { guardar("o", +$("o").value); guardar("d", +$("d").value); pintarViaje(); };
$("fav").onclick = alternarFavorito;
$("andar").onchange = $("andar").oninput = () => {
  const v = Math.max(0, Math.min(90, Math.round(+$("andar").value || 0)));
  guardar("andar." + est(+$("o").value).id, v);
  pintarViaje();
};
$("invertir").onclick = () => { const a = $("o").value; $("o").value = $("d").value; $("d").value = a; $("o").onchange(); };
$("est").onchange = () => { guardar("est", +$("est").value); pintarEstacion(); };
$("ver-prog").onchange = pintarMalla;
$("ver-mio").checked = leer("malla.mio", true);
$("ver-mio").onchange = () => { guardar("malla.mio", $("ver-mio").checked); pintarMalla(); };
for (const b of document.querySelectorAll("#ventana button")) b.onclick = () => {
  guardar("ventana", +b.dataset.v);
  for (const x of document.querySelectorAll("#ventana button")) x.setAttribute("aria-pressed", x === b);
  pintarMalla();
};
window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (h && h !== tabActual && $("tab-" + h)) irA(h); });
window.addEventListener("resize", () => { if (tabActual === "malla" && R) pintarMalla(); });

/* ---------------------------------------------------------------- Ir a… (planificador puerta a puerta) */
let GPS = null;
const MI_UBIC = "📍 Mi ubicación";
const ELEGIDO = { o: null, d: null };   // sugerencia elegida: {nombre, lat, lon}
let PLAN_ULTIMO = null;                  // para refrescar la ruta sola mientras está en pantalla

function paramPunto(pref, inp, el) {
  const v = inp.value.trim();
  if (pref === "o" && GPS && (!v || v === MI_UBIC)) return `&olat=${GPS[0]}&olon=${GPS[1]}`;
  if (el && el.nombre === v) return `&${pref}lat=${el.lat}&${pref}lon=${el.lon}&${pref}name=${encodeURIComponent(el.nombre)}`;
  return v ? `&${pref === "o" ? "origen" : "destino"}=${encodeURIComponent(v)}` : "";
}

async function planificar(silencioso) {
  const po = paramPunto("o", $("ir-o"), ELEGIDO.o), pd = paramPunto("d", $("ir-d"), ELEGIDO.d);
  if (!pd) { if (!silencioso) $("ir-d").focus(); return; }
  if (!po) { if (!silencioso) $("ir-o").focus(); return; }
  const url = "/api/ir?" + (po + pd).slice(1);
  if (!silencioso) {
    $("ir-resultado").innerHTML = `<div class="ir-cargando">Buscando la mejor combinación…</div>`;
    $("ir-buscar").disabled = true;
  }
  try {
    const p = await pedir(url, 16000);
    PLAN_ULTIMO = { url, ts: Date.now() };
    pintarPlan(p);
    if (p.ok && !silencioso) guardarReciente($("ir-o").value.trim(), $("ir-d").value.trim());
  } catch (e) { if (!silencioso) pintarPlan({ ok: false, error: "No pude conectar con el servidor. Prueba otra vez en un momento." }); }
  finally { $("ir-buscar").disabled = false; }
}
// la ruta cambia con el tiempo real: mientras la estás mirando, se recalcula cada minuto
setInterval(() => {
  if (tabActual === "ir" && PLAN_ULTIMO && Date.now() - PLAN_ULTIMO.ts > 55000 && !document.hidden) planificar(true);
}, 15000);

function usarGps() {
  if (!navigator.geolocation) { aviso("Este dispositivo no permite la ubicación."); return; }
  const inp = $("ir-o"); inp.value = "Localizando…";
  navigator.geolocation.getCurrentPosition((pos) => {
    GPS = [pos.coords.latitude, pos.coords.longitude]; inp.value = MI_UBIC; ELEGIDO.o = null;
    if ($("ir-d").value.trim()) planificar();
  }, (err) => {
    inp.value = ""; GPS = null;
    aviso(err.code === 1 ? "Permiso de ubicación denegado." : "No se pudo obtener la ubicación.");
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

/* sugerencias mientras escribes */
function prepararSugerencias(inp, pref) {
  const caja = document.createElement("div");
  caja.className = "sug"; caja.hidden = true; caja.setAttribute("role", "listbox");
  inp.parentElement.appendChild(caja);
  let timer = null, pedido = 0, activa = -1, items = [];
  const cerrar = () => { caja.hidden = true; activa = -1; };
  const elegir = (it) => {
    inp.value = it.texto; ELEGIDO[pref] = { nombre: it.texto, lat: it.lat, lon: it.lon };
    if (pref === "o") GPS = null;
    cerrar();
    if (pref === "o") $("ir-d").focus(); else planificar();
  };
  const ico = { lugar: "📍", estacion: "🚆", parada: "🚌" };
  inp.addEventListener("input", () => {
    ELEGIDO[pref] = null;
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2 || q === MI_UBIC) { cerrar(); return; }
    timer = setTimeout(async () => {
      const n = ++pedido;
      try {
        const j = await pedir("/api/sugerir?q=" + encodeURIComponent(q), 5000);
        if (n !== pedido || inp.value.trim() !== q) return;
        items = j.sugerencias || [];
        if (!items.length) { cerrar(); return; }
        caja.innerHTML = items.map((it, i) => `<button type="button" class="sug-it" data-i="${i}" role="option"><span>${ico[it.tipo] || "•"}</span>${esc(it.nombre)}</button>`).join("");
        caja.hidden = false; activa = -1;
      } catch (e) { cerrar(); }
    }, 180);
  });
  caja.addEventListener("mousedown", (e) => e.preventDefault());   // que no se pierda el foco antes del clic
  caja.addEventListener("click", (e) => { const b = e.target.closest(".sug-it"); if (b) elegir(items[+b.dataset.i]); });
  inp.addEventListener("keydown", (e) => {
    if (caja.hidden) return;
    const bs = caja.querySelectorAll(".sug-it");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      activa = (activa + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length;
      bs.forEach((b, i) => b.classList.toggle("on", i === activa));
    } else if (e.key === "Enter" && activa >= 0) { e.preventDefault(); e.stopImmediatePropagation(); elegir(items[activa]); }
    else if (e.key === "Escape") cerrar();
  });
  inp.addEventListener("blur", () => setTimeout(cerrar, 150));
}

/* búsquedas recientes (en este dispositivo) */
function guardarReciente(o, d) {
  if (!o || !d) return;
  const r = leer("ir.recientes", []).filter((x) => !(x.o === o && x.d === d));
  r.unshift({ o, d });
  guardar("ir.recientes", r.slice(0, 4));
  pintarRecientes();
}
function pintarRecientes() {
  const r = leer("ir.recientes", []);
  const cont = $("ir-ejemplos");
  const lista = r.length ? r : [{ o: "EPI Gijón", d: "Candás" }, { o: "Hospital de Cabueñes", d: "Xivares" }, { o: "Candás", d: "Universidad Laboral" }];
  const corto = (x) => x === MI_UBIC ? "📍 Aquí" : x.replace(/ \((estación|parada de bus)\)$/, "").split(" · ")[0];
  cont.innerHTML = (r.length ? `<span class="ir-ej-t">Recientes</span>` : `<span class="ir-ej-t">Ejemplos</span>`) +
    lista.map((x, i) => `<button type="button" data-i="${i}">${esc(corto(x.o))} → ${esc(corto(x.d))}</button>`).join("");
  for (const b of cont.querySelectorAll("button")) b.onclick = () => {
    const x = lista[+b.dataset.i];
    ELEGIDO.o = ELEGIDO.d = null;
    if (x.o === MI_UBIC) { $("ir-d").value = x.d; usarGps(); return; }
    GPS = null; $("ir-o").value = x.o; $("ir-d").value = x.d; planificar();
  };
}

function pintarPlan(p) {
  const box = $("ir-resultado");
  if (!p) { box.innerHTML = ""; return; }
  if (p.cargando) { box.innerHTML = `<div class="ir-cargando">El horario del día aún se está cargando. Prueba en unos segundos.</div>`; return; }
  if (!p.ok) {
    box.innerHTML = `<div class="ir-error">${esc(p.error || "No encontré una ruta.")}${p.cual ? " Prueba con el nombre de una estación, una parada o un sitio conocido, o elige una de las sugerencias." : ""}</div>` +
      (p.avisos || []).map((a) => `<div class="ir-aviso">${esc(a)}</div>`).join("");
    return;
  }
  const dur = p.duracion != null ? `${Math.round(p.duracion)} min de viaje` : "";
  const en = p.sale_en != null ? Math.round(p.sale_en) : 0;
  const salir = p.solo_urbano ? "" : en >= 1
    ? `<div class="ir-salir">Sal a las <b class="num">${p.sale_hm || hm(p.sale)}</b> <span>(en ${en} min)</span></div>`
    : `<div class="ir-salir ya">Sal <b>ya</b></div>`;
  let h = `<div class="ir-cab">
      <div class="ir-od"><span class="ir-de">${esc(p.origen.nombre)}</span><span class="ir-fl">→</span><span class="ir-a">${esc(p.destino.nombre)}</span></div>
      ${salir}
      <div class="ir-tot">Llegas a las <b class="num">${p.llega_hm || "--:--"}</b>${dur ? ` · ${dur}` : ""}</div>
    </div><ol class="ir-etapas">`;
  for (const e of p.etapas) {
    if (e.tipo === "andar")
      h += `<li class="et andar"><span class="et-ico">🚶</span><div class="et-cuerpo">
        <div class="et-t">Andar ${Math.max(1, Math.round(e.min))} min <span class="et-sub2">· ${e.metros} m</span></div>
        <div class="et-sub">${esc(e.desde)} → ${esc(e.hasta)}</div></div></li>`;
    else if (e.tipo === "bus") {
      const sale = e.sale_en != null
        ? `<span class="et-min ok">pasa en ${e.sale_en} min</span>`
        : `<span class="et-min aprox">cada pocos minutos</span>`;
      h += `<li class="et bus"><span class="et-ico">🚌</span><div class="et-cuerpo">
        <div class="et-t"><span class="bus-chip" style="background:${esc(e.color)}">${esc(e.linea)}</span> hacia ${esc(e.destino)} ${sale}</div>
        <div class="et-sub">Sube en <b>${esc(e.subir)}</b> · baja en <b>${esc(e.bajar)}</b> · ${e.paradas} paradas (~${Math.round(e.min)} min)</div></div></li>`;
    } else if (e.tipo === "tren") {
      h += `<li class="et tren"><span class="et-ico">🚆</span><div class="et-cuerpo">
        <div class="et-t">Tren <b>C-4</b> → ${esc(nombreCorto(e.destino || ""))}${e.retraso >= 1 ? ` <span class="tag ${e.retraso <= 5 ? "warn" : "bad"}" style="margin:0 0 0 4px">+${Math.round(e.retraso)} min</span>` : ""}</div>
        <div class="et-horas num"><span>${hmS(e.sale)} <b>${esc(nombreCorto(e.desde))}</b></span><span class="et-fl">→</span><span>${hm(e.llega)} <b>${esc(nombreCorto(e.hasta))}</b></span></div>
        ${e.espera_estacion >= 3 ? `<div class="et-sub">Esperas ${Math.round(e.espera_estacion)} min en la estación</div>` : ""}
        ${(e.motivos || []).map((m) => `<div class="et-cruce">⇄ ${esc(m.texto)} <span class="et-min warn2">+${Math.round(m.min)}</span></div>`).join("")}
      </div></li>`;
    }
  }
  h += `</ol>`;
  if (p.alternativas && p.alternativas.length)
    h += `<div class="ir-alt"><div class="ir-alt-t">Si no te da tiempo</div>` + p.alternativas.map((a) =>
      `<div class="ir-alt-f num"><span>Tren de las <b>${a.sale_hm}</b>${a.retraso >= 1 && a.con_datos ? ` <span class="tag warn" style="margin:0">+${Math.round(a.retraso)}</span>` : ""}</span>
        <span>${a.salir_hm ? `sal ${a.salir_hm} · ` : ""}llegas <b>${a.llega_hm}</b></span></div>`).join("") + `</div>`;
  (p.avisos || []).forEach((a) => { h += `<div class="ir-aviso">${esc(a)}</div>`; });
  h += `<p class="ir-nota">El tren lleva la hora real (con cruces en vía única) y la ruta se recalcula sola cada minuto mientras la miras. El bus urbano usa los minutos en directo de EMTUSA cuando los hay.</p>`;
  box.innerHTML = h;
}

$("ir-buscar").onclick = () => planificar();
$("ir-gps").onclick = usarGps;
$("ir-o").addEventListener("input", () => { if ($("ir-o").value !== MI_UBIC) GPS = null; });
$("ir-swap").onclick = () => {
  const a = $("ir-o").value, eraGps = GPS && a === MI_UBIC;
  $("ir-o").value = $("ir-d").value; $("ir-d").value = eraGps ? "" : a; GPS = null;
  [ELEGIDO.o, ELEGIDO.d] = [ELEGIDO.d, eraGps ? null : ELEGIDO.o];
};
prepararSugerencias($("ir-o"), "o");
prepararSugerencias($("ir-d"), "d");
for (const inp of [$("ir-o"), $("ir-d")])
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); planificar(); } });
pintarRecientes();

function prepararIphone() {
  const url = LINEA && LINEA.url_movil;
  const esMovil = /iPhone|iPad|Android/i.test(navigator.userAgent);
  $("btn-iphone").hidden = !url || esMovil;
  if (!url) return;
  $("btn-iphone").onclick = () => {
    $("modal-iphone").hidden = false;
    $("url-movil").textContent = url;
    if (!$("qr").childElementCount) {
      if (window.QRCode) new QRCode($("qr"), { text: url, width: 200, height: 200 });
      else $("qr").innerHTML = `<div style="color:#333;padding:20px;max-width:200px">Escribe esta dirección en Safari:</div>`;
    }
  };
  $("cerrar-iphone").onclick = () => { $("modal-iphone").hidden = true; };
  $("modal-iphone").onclick = (e) => { if (e.target.id === "modal-iphone") $("modal-iphone").hidden = true; };
}

(async function inicio() {
  const v = leer("ventana", 180);
  for (const x of document.querySelectorAll("#ventana button")) x.setAttribute("aria-pressed", +x.dataset.v === v);
  await cargarLinea();
  iniciarSelectores();
  prepararIphone();
  const hash = location.hash.slice(1);
  await cargarEstado();
  irA(["inicio", "ir", "viaje", "estacion", "mapa", "malla", "cruces", "precision", "info"].includes(hash) ? hash : "inicio");
  // cada 3 s se pregunta si hay datos nuevos (casi gratis si no los hay); con la app en segundo plano, no
  setInterval(() => { if (!document.hidden) cargarEstado(); }, 3000);
  setInterval(() => { if (tabActual === "viaje" && R && document.activeElement !== $("andar")) pintarViaje(); }, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) cargarEstado(); });
  window.addEventListener("pageshow", () => cargarEstado());
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  setInterval(() => { if (tabActual === "precision") cargarPrecision(); }, 300000);
  setInterval(() => {
    $("reloj").textContent = new Date(ahoraSeg() * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (tabActual === "mapa") pintarTrenesMapa();
  }, 1000);
})();
