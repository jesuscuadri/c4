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
const ahora = () => (!R ? 0 : R.ts ? R.ahora + (Date.now() / 1000 - R.ts) / 60 : R.ahora + (Date.now() - tRecibido) / 60000);
const edadDatos = () => (R && R.ts ? Date.now() / 1000 - R.ts : 0);  // segundos desde que el servidor calculó
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
  try {
    const j = await pedir("/api/estado");
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
  }
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
                sin_conexion: "Sin conexión con Renfe · usando horario" }[R.calidad];
  const corto = { directo: "En directo", congelado: "Sin datos Renfe", sin_conexion: "Sin conexión" }[R.calidad];
  $("chip-txt").innerHTML = viejo ? `<span class="txt">Actualizando…</span><span class="corto">Actualizando…</span>`
    : `<span class="txt">${txt}</span><span class="corto">${corto}</span>`;
  c.title = `Última lectura ${R.actualizado}` + (R.ts_feed ? ` · datos de Renfe de las ${R.ts_feed}` : "");
  let h = "";
  if (viejo)
    h += `<div class="aviso warn"><span>⏳</span><div><b>Datos de las ${esc(R.actualizado.slice(0, 5))}.</b> El servidor se está despertando o no hay conexión: en cuanto responda se actualiza solo.</div></div>`;
  if (R.calidad === "congelado")
    h += `<div class="aviso warn"><span>⚠</span><div><b>Los datos en tiempo real de Renfe están parados</b> (último dato ${R.ts_feed || "?"}). Mientras tanto se calcula con el horario, así que las esperas por cruces con trenes retrasados no se ven.</div></div>`;
  if (R.calidad === "sin_conexion")
    h += `<div class="aviso bad"><span>⚠</span><div><b>No hay conexión con Renfe.</b> ${esc(R.error || "")} Se muestra el horario oficial.</div></div>`;
  for (const a of R.avisos || []) h += `<div class="aviso info"><span>ℹ</span><div><b>Aviso de Renfe:</b> ${esc(a)}</div></div>`;
  $("avisos").innerHTML = h;
  $("pie").textContent = `Datos: Renfe (horario oficial GTFS y tiempo real) · actualizado ${R.actualizado} · ` +
    `${LINEA.n_trenes} trenes hoy · cruces ${R.modo_cruces} · ${R.tramos_aprendidos} tramos con tiempos aprendidos · v${LINEA.version}`;
}

/* ---------------------------------------------------------------- mi viaje */
function iniciarSelectores() {
  const opts = LINEA.estaciones.map((e) => `<option value="${e.k}">${esc(e.nombre)}${e.cruce ? " ·" : ""}</option>`).join("");
  for (const id of ["o", "d", "est"]) $(id).innerHTML = opts;
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
const fiabilidad = (t) => ({ "posición": ["ok", "Posición en directo"], "Renfe": ["ok", "Dato de Renfe"],
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
      <table class="tabla num"><thead><tr><th>Sale</th><th>Llega</th><th>Tren</th><th>Destino</th></tr></thead><tbody>` +
      j.trenes.map((x) => `<tr><td class="e">${hm(x.sale)}</td><td>${hm(x.llega)}</td><td>${x.num}</td><td>${esc(nombreCorto(x.destino))}</td></tr>`).join("") +
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
    const falta = Math.round(sal - now);
    const cuando = falta <= 0 ? "sale ahora" : falta < 60 ? `sale en ${falta} min` : `sale a las ${hm(sal)}`;
    const cambiaSal = Math.abs(sal - t.prog_d[jo]) >= 1, cambiaLle = Math.abs(lle - t.prog_a[jd]) >= 1;
    const salirCasa = sal - andar, margen = salirCasa - now;
    const perdido = andar > 0 && margen < -0.5;
    const destacado = !perdido && primero;
    if (destacado) primero = false;
    let casa = "";
    if (andar > 0) casa = perdido
      ? `<div class="casa perdido">🚶 Andando (${andar} min) ya no llegas a este tren</div>`
      : `<div class="casa${margen <= 3 ? " prisa" : ""}">🚶 Sal de casa ${margen < 1 ? "<b>ya</b>" : `a las <b class="num">${hm(salirCasa)}</b> (en ${Math.round(margen)} min)`}</div>`;
    let aviso = "";
    if (t.con_datos && dif >= 1) aviso = `<div class="dif">La app oficial dirá <b class="num">${hm(app)}</b>. Llegarás sobre las <b class="num">${hm(lle)}</b> (<b>+${Math.round(dif)} min</b>).</div>`;
    else if (t.con_datos && dif <= -1) aviso = `<div class="dif bien">Llegarás antes de lo que dice la app oficial (${hm(app)}): recupera ${Math.round(-dif)} min con los márgenes del horario.</div>`;
    const lis = antes.map((m) => `<li class="antes">Antes de tu estación: ${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)
      .concat(mot.map((m) => `<li>${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)).join("");
    const fia = fiabilidad(t);
    const texto = `Voy en el tren ${t.num} de la C-4 (sale de ${nombreCorto(est(o).nombre)} a las ${hm(sal)}). Llego a ${nombreCorto(est(d).nombre)} sobre las ${hm(lle)}.`;
    return `<div class="tv${destacado ? " primero" : ""}${perdido ? " perdido" : ""}" data-tren="${t.id}" data-jo="${jo}" data-jd="${jd}">
      <div class="tv-cab">
        <div>
          <div class="tv-tren">Tren ${t.num}<span class="tag ${t.dir > 0 ? "ida" : "vta"}">→ ${esc(destinoCorto(t))}</span>${t.con_datos ? tagRetraso(t.retraso) : `<span class="tag gris">${t.situacion.startsWith("Aún") ? "Programado" : "Sin datos"}</span>`}</div>
          <div class="tv-sit">${esc(t.situacion)}${t.via ? ` · vía ${esc(t.via)}` : ""}${fia ? ` · <span class="fia ${fia[0]}">${fia[1]}</span>` : ""}</div>
        </div>
        <div class="tv-grande"><div class="h num">${hm(lle)}</div><div class="l">llegada · ${cuando}</div></div>
      </div>${casa}
      <div class="cmp num">
        <span></span><span class="c">Horario</span><span class="c">App oficial</span><span class="c">Estimado</span>
        <span class="c" style="align-self:center">Sale</span><span class="${cambiaSal ? "x" : ""}">${hm(t.prog_d[jo])}</span><span>${hm(t.adif_d[jo])}</span><span class="e">${hm(sal)}</span>
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
  $("est-info").innerHTML = e.cruce
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
    return `<table class="tabla num"><thead><tr><th>Sale</th><th>Destino</th><th>Tren</th><th>Estado</th></tr></thead><tbody>` +
      filas.slice(0, 8).map(({ t, j, s }) => {
        const cr = (R.cruces || []).find((c) => c.k === k && (c.ida.id === t.id || c.vuelta.id === t.id));
        const aqui = t.parado && t.j0 === j;
        const mot = t.motivos.find((m) => m.j === j);
        const retr = s - t.prog_d[j];
        return `<tr class="clic${aqui ? " aqui" : ""}" data-tren="${t.id}" data-jo="${j}">
          <td><span class="e">${hm(s)}</span>${Math.abs(retr) >= 1 ? `<br><span style="color:var(--mut);text-decoration:line-through">${hm(t.prog_d[j])}</span>` : ""}</td>
          <td>${esc(destinoCorto(t))}${aqui ? `<br><span class="tag ok" style="margin:0">En andén${t.via ? " · vía " + esc(t.via) : ""}</span>` : ""}</td>
          <td>${t.num}</td>
          <td>${t.con_datos || retr >= 1 ? tagRetraso(retr) : '<span class="tag gris">Horario</span>'}${cr ? `<br><span style="font-size:12px;color:var(--tx2)">Cruza aquí con el ${cr.ida.id === t.id ? cr.vuelta.num : cr.ida.num}</span>` : ""}${mot ? `<br><span style="font-size:12px;color:var(--warn)">Espera ${Math.round(mot.min)} min</span>` : ""}</td></tr>`;
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
  for (let j = Math.max(0, t.j0 - 1); j < n; j++) {
    const a = t.est_a[j], d = t.est_d[j];
    if (a == null) continue;
    if (j >= t.j0 && now < a) {
      if (j === 0) return t.con_datos ? km(0) : null;
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
function retrasoActual(t) {
  const j = Math.min(t.j0, t.k.length - 1);
  return t.est_a[j] != null ? Math.max(0, t.est_a[j] - t.prog_a[j]) : 0;
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
  // Planos sin clave: OpenStreetMap (oscurecido con un filtro en modo oscuro) y satélite de Esri
  const planos = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, className: "base-osm",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const satelite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imágenes &copy; Esri, Maxar, Earthstar Geographics",
  });
  (leer("capa", "planos") === "satelite" ? satelite : planos).addTo(mapa);
  L.control.layers({ "Plano": planos, "Satélite": satelite }, null, { position: "topright" }).addTo(mapa);
  mapa.on("baselayerchange", (e) => guardar("capa", e.name === "Satélite" ? "satelite" : "planos"));

  // Vía: borde + línea para que se lea sobre cualquier fondo
  L.polyline(LINEA.trazado, { color: oscuroMapa() ? "#000" : "#fff", weight: 9, opacity: 0.55, interactive: false }).addTo(mapa);
  L.polyline(LINEA.trazado, { color: "#e93cac", weight: 5, opacity: 0.95, interactive: false }).addTo(mapa);
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
  mapa.on("zoomend", ajustarEtiquetas);
  mapa.on("dragstart", () => { if (seguir) { seguir = false; pintarFichaTren(); } });
  mapa.on("click", () => seleccionarTren(null));
  crearControlesMapa();
  mapa.fitBounds(L.latLngBounds(LINEA.trazado), { padding: [20, 20] });
  ajustarEtiquetas();
}

function crearControlesMapa() {
  const Ctl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const d = L.DomUtil.create("div", "mapa-botones");
      d.innerHTML = `<button type="button" id="m-yo" title="Mi estación más cercana">📍<span class="txt-l" id="m-yo-txt"> Cerca de mí</span></button>
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
}

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
    }).bindTooltip(`<b>Cruce en ${esc(c.estacion)} · ${hm(c.hora)}</b><br>${c.ida.num} (→ Cudillero/Avilés) y ${c.vuelta.num} (→ Gijón)` +
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
  if (!t) { f.hidden = true; f._html = null; return; }
  const j = Math.min(t.j0, t.k.length - 1), sig = est(t.k[j]).nombre;
  const r = Math.round(retrasoActual(t));
  const proxCruce = (R.cruces || []).find((c) => (c.ida.id === t.id || c.vuelta.id === t.id) && c.hora >= ahora() - 0.5);
  f.hidden = false;
  const html = `<div class="ft-cab"><span class="ft-num" style="background:${colorDirHex(t.dir)}">${t.num}</span>
      <div><b>→ ${esc(nombreCorto(t.destino))}</b><div class="pp-sub">${esc(t.situacion)}</div></div>
      <button type="button" class="ft-x" id="ft-cerrar" aria-label="Cerrar">×</button></div>
    <div class="ft-datos num">
      <div><span class="pp-sub">Próxima</span><b>${esc(nombreCorto(sig))} ${hm(t.est_a[j])}</b></div>
      <div><span class="pp-sub">Llega a ${esc(nombreCorto(t.destino))}</span><b>${hm(t.est_a[t.k.length - 1])}</b></div>
      <div><span class="pp-sub">Retraso</span><b>${t.con_datos ? (r > 0 ? "+" + r + " min" : "en hora") : "sin datos"}</b></div>
    </div>
    ${proxCruce ? `<div class="ft-cruce">⇄ Cruza con el ${proxCruce.ida.id === t.id ? proxCruce.vuelta.num : proxCruce.ida.num} en ${esc(proxCruce.estacion)} a las ${hm(proxCruce.hora)}</div>` : ""}
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
  const now = ahora(), vivos = new Set();
  if (pintarTrenesMapa._cruces !== R) {  // datos nuevos (cada 15 s): cruces y ventanas de estación
    pintarCrucesMapa();
    pintarTrenesMapa._cruces = R;
    for (const [e, m] of etiquetasEst) if (m._abierto) m.setPopupContent(popupEstacion(e.k));
  }
  for (const t of R.trenes) {
    const x = kmTren(t, now);
    if (x == null) continue;
    const ll = latlonKm(x);
    if (!ll) continue;
    vivos.add(t.id);
    const r = Math.round(retrasoActual(t));
    const rc = !t.con_datos ? "gris" : r <= 0 ? "ok" : r <= 5 ? "warn" : "bad";
    const ang = Math.round(rumbo(x, t.dir));
    const sel = t.id === trenSel;
    const html = `<div class="tren-m ${t.dir > 0 ? "ida" : "vta"}${sel ? " sel" : ""}${t.con_datos ? "" : " sindatos"}">
      <div class="tm-punto" style="background:${colorDirHex(t.dir)}"><svg class="tm-flecha" width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true"><path transform="rotate(${ang})" d="M6 0 L-4 -5 L-1.5 0 L-4 5 Z" fill="#fff"/></svg></div>
      <div class="tm-etq"><b>${t.num}</b><span class="tm-r ${rc}">${t.con_datos ? (r > 0 ? "+" + r : "✓") : "?"}</span></div></div>`;
    let m = marcas[t.id];
    if (!m) {
      m = marcas[t.id] = L.marker(ll, { icon: L.divIcon({ html, className: "", iconSize: null }), zIndexOffset: 1000, riseOnHover: true })
        .on("click", (ev) => { L.DomEvent.stopPropagation(ev); seleccionarTren(t.id); }).addTo(capaTrenes);
      m._html = html;
    } else {
      m.setLatLng(ll);
      if (m._html !== html) { m.setIcon(L.divIcon({ html, className: "", iconSize: null })); m._html = html; }
    }
    m.setZIndexOffset(sel ? 3000 : 1000);
    if (sel) {
      if (!pintarTrenesMapa._rutaT || now - pintarTrenesMapa._rutaT > 0.25 || pintarTrenesMapa._rutaId !== t.id) {
        pintarRutaSel(t, x); pintarTrenesMapa._rutaT = now; pintarTrenesMapa._rutaId = t.id;
      }
      if (seguir) mapa.panTo(ll, { animate: true, duration: 0.8 });
    }
  }
  for (const id of Object.keys(marcas)) if (!vivos.has(id)) { capaTrenes.removeLayer(marcas[id]); delete marcas[id]; }
  if (trenSel && !vivos.has(trenSel)) seleccionarTren(null);
  pintarFichaTren();
}

/* ---------------------------------------------------------------- malla */
function pintarMalla() {
  const cont = $("malla"), E = LINEA.estaciones, now = ahora();
  const vent = leer("ventana", 180), verProg = $("ver-prog").checked;
  const W = Math.max(760, cont.clientWidth || 900), top = 12, filaH = 19, left = 128, right = 12;
  const H = top + (E.length - 1) * filaH + 34;
  const t0 = now - vent * 0.2, t1 = now + vent * 0.8;
  const x = (t) => left + ((t - t0) / (t1 - t0)) * (W - left - right), y = (k) => top + k * filaH;
  const paso = vent <= 90 ? 10 : vent <= 180 ? 15 : 30;
  let s = `<svg width="${W}" height="${H}" style="display:block" role="img" aria-label="Malla de circulación">`;
  for (let t = Math.ceil(t0 / paso) * paso; t <= t1; t += paso)
    s += `<line x1="${x(t)}" y1="${top}" x2="${x(t)}" y2="${H - 22}" stroke="var(--bd)"/><text x="${x(t)}" y="${H - 6}" text-anchor="middle">${hm(t)}</text>`;
  for (const e of E)
    s += `<line x1="${left}" y1="${y(e.k)}" x2="${W - right}" y2="${y(e.k)}" stroke="var(--bd${e.cruce ? "2" : ""})" stroke-width="${e.cruce ? 1.2 : 0.5}"/>` +
      `<text x="${left - 8}" y="${y(e.k) + 4}" text-anchor="end" class="${e.cruce ? "est-cruce" : ""}">${esc(nombreCorto(e.nombre).slice(0, 19))}</text>`;
  s += `<clipPath id="recorte"><rect x="${left}" y="0" width="${W - left - right}" height="${H}"/></clipPath><g clip-path="url(#recorte)">`;
  const camino = (t, A, D, desde) => {
    const p = [];
    for (let j = desde; j < t.k.length; j++) {
      if (A[j] != null) p.push([x(A[j]), y(t.k[j])]);
      if (D[j] != null && Math.abs(D[j] - A[j]) > 0.01) p.push([x(D[j]), y(t.k[j])]);
    }
    return p.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("");
  };
  for (const t of R.trenes) {
    const fin = t.est_a[t.k.length - 1] ?? t.prog_a[t.k.length - 1];
    if (Math.max(fin, t.prog_a[t.k.length - 1]) < t0 || Math.min(t.prog_d[0], t.est_d[0] ?? 1e9) > t1) continue;
    if (verProg) s += `<path d="${camino(t, t.prog_a, t.prog_d, 0)}" fill="none" stroke="var(--mut)" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>`;
    if (!t.fin && !t.cancelado) {
      const c = colorDir(t.dir);
      s += `<path d="${camino(t, t.est_a, t.est_d, Math.max(0, t.j0 - 1))}" fill="none" stroke="${c}" stroke-width="2.4" opacity="${t.con_datos ? 1 : 0.6}" style="cursor:pointer" data-tren="${t.id}"><title>Tren ${t.num} → ${esc(t.destino)} · ${esc(t.situacion)}</title></path>`;
      const kx = kmTren(t, now);
      if (kx != null) {
        let kk = 0; // posición vertical: interpolar entre estaciones por km
        for (let i = 0; i < E.length - 1; i++) if (E[i].km <= kx && kx <= E[i + 1].km) { kk = i + (kx - E[i].km) / Math.max(1e-9, E[i + 1].km - E[i].km); break; }
        if (kx >= E[E.length - 1].km) kk = E.length - 1;
        const yy = top + kk * filaH;
        s += `<circle cx="${x(now)}" cy="${yy}" r="4.5" fill="${c}"/><text x="${x(now) + 7}" y="${yy + 4}" style="fill:${c};font-weight:700">${t.num}</text>`;
      }
    }
  }
  for (const c of R.cruces || []) {
    if (c.hora < t0 || c.hora > t1) continue;
    const malo = c.ida.retraso_extra > 0.5 || c.vuelta.retraso_extra > 0.5;
    s += `<circle cx="${x(c.hora)}" cy="${y(c.k)}" r="5" fill="none" stroke="var(--${malo ? "warn" : "mut"})" stroke-width="2"><title>Cruce ${c.ida.num} / ${c.vuelta.num} en ${esc(c.estacion)} · ${hm(c.hora)}</title></circle>`;
  }
  s += `</g><line x1="${x(now)}" y1="${top - 6}" x2="${x(now)}" y2="${H - 22}" stroke="var(--c4)" stroke-width="1.6"/>` +
    `<text x="${x(now)}" y="${top - 1}" text-anchor="middle" style="fill:var(--c4);font-weight:700;font-size:10px"></text></svg>`;
  cont.innerHTML = s;
}

/* ---------------------------------------------------------------- cruces */
function pintarCruces() {
  const now = ahora();
  const lista = (R.cruces || []).filter((c) => c.hora >= now - 2).slice(0, 30);
  if (!lista.length) { $("lista-cruces").innerHTML = `<div class="vacio">No quedan cruces hoy.</div>`; return; }
  const celda = (x) => `<span class="e">${x.num}</span><br><span style="font-size:12.5px;color:var(--tx2)">llega ${hm(x.llega)}${x.sale != null ? ` · sale ${hm(x.sale)}` : ""}</span>` +
    (x.retraso_extra > 0.5 ? `<br><span class="tag warn" style="margin:2px 0 0">espera +${Math.round(x.retraso_extra)} min</span>` : x.espera >= 1 ? `<br><span style="font-size:12px;color:var(--mut)">parado ${Math.round(x.espera)} min (previsto)</span>` : "");
  $("lista-cruces").innerHTML = `<table class="tabla num"><thead><tr><th>Hora</th><th>Estación</th><th><span class="bola" style="background:var(--ida)"></span> Hacia Cudillero/Avilés</th><th><span class="bola" style="background:var(--vta)"></span> Hacia Gijón</th><th>Nota</th></tr></thead><tbody>` +
    lista.map((c) => {
      const malo = c.ida.retraso_extra > 0.5 || c.vuelta.retraso_extra > 0.5;
      return `<tr style="${malo ? "background:color-mix(in srgb,var(--warn) 7%,transparent)" : ""}">
        <td class="e">${hm(c.hora)}</td><td>${esc(c.estacion)}</td>
        <td class="clic" data-tren="${c.ida.id}" style="cursor:pointer">${celda(c.ida)}</td>
        <td class="clic" data-tren="${c.vuelta.id}" style="cursor:pointer">${celda(c.vuelta)}</td>
        <td>${c.info === "movido" ? `<span class="tag warn" style="margin:0">Trasladado</span><br><span style="font-size:12px;color:var(--tx2)">en horario: ${esc(c.programado)}</span>` : '<span class="tag gris" style="margin:0">Según horario</span>'}</td></tr>`;
    }).join("") + `</tbody></table>`;
}

/* ---------------------------------------------------------------- precisión */
function tarjetaAprendizaje() {
  if (!APREN || APREN.cargando) return "";
  const kpi = (v, l) => `<div class="kpi"><div class="v num">${v}</div><div class="l">${l}</div></div>`;
  const ns = APREN.sesgos_n || 0;
  let s = `<div class="card apr-card"><div class="card-cab"><h3>🧠 Aprende de sus errores</h3></div>
    <p class="sub" style="margin:0 0 10px">El programa se corrige solo: guarda cada predicción, la compara con lo que pasó de verdad y ajusta lo que falla. Cuanto más se usa, más afina.</p>
    <div class="kpis">${kpi(APREN.tramos || 0, "tramos con tiempo real aprendido")}${kpi(ns, "estaciones con sesgo corregido")}</div>`;
  if (APREN.sesgos && APREN.sesgos.length)
    s += `<div class="apr-lista">` + APREN.sesgos.slice(0, 8).map((x) =>
      `<div class="apr-fila"><span>${esc(x.estacion)}</span><b class="num ${x.min > 0 ? "mas" : "menos"}">${x.min > 0 ? "+" : ""}${x.min} min</b></div>`).join("") +
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
  const kpi = (v, l) => `<div class="kpi"><div class="v num">${v}</div><div class="l">${l}</div></div>`;
  let h = apr + `<div class="kpis">` +
    kpi(`${tot.error_nuestro.toFixed(1)} min`, "Error medio de este programa (7 días)") +
    kpi(`${tot.error_adif.toFixed(1)} min`, "Error medio de la app oficial (7 días)") +
    kpi(`${tot.acierto_nuestro}%`, "Llegadas acertadas a ±1 min (este programa)") +
    kpi(`${tot.acierto_adif}%`, "Llegadas acertadas a ±1 min (app oficial)") + `</div>`;
  const fila = (hz, x) => {
    if (!x) return `<tr><td>${hz} min antes</td><td colspan="5" style="color:var(--mut)">sin datos</td></tr>`;
    const max = Math.max(x.error_nuestro, x.error_adif, 0.1);
    const barra = (v, c) => `<div class="barra"><i style="width:${(100 * v) / max}%;background:${c}"></i></div>`;
    return `<tr><td>${hz} min antes</td><td>${x.n}</td><td><b>${x.error_nuestro.toFixed(1)}</b>${barra(x.error_nuestro, "var(--c4)")}</td>
      <td>${x.error_adif.toFixed(1)}${barra(x.error_adif, "var(--mut)")}</td><td>${x.acierto_nuestro}% / ${x.acierto_adif}%</td>
      <td>${x.sesgo_nuestro > 0 ? "+" : ""}${x.sesgo_nuestro.toFixed(1)}</td></tr>`;
  };
  const tabla = (g, titulo) => `<h3 style="font-size:14px;margin:14px 0 6px">${titulo}</h3><div class="scroll-x"><table class="tabla num">
    <thead><tr><th>Antelación</th><th>Llegadas</th><th>Error medio (min) · este programa</th><th>App oficial</th><th>Acierto ±1 min</th><th>Sesgo</th></tr></thead>
    <tbody>${["5", "10", "20", "30"].map((hz) => fila(hz, g[hz])).join("")}</tbody></table></div>`;
  h += tabla(sem, "Últimos 7 días") + (th ? tabla(hoy, "Hoy") : "");
  h += `<p class="sub" style="margin-top:10px">Sesgo positivo: el programa tiende a decir que se llega más tarde de lo real (pesimista); negativo, optimista. <b>Ya no hay que tocar nada a mano:</b> el propio programa aprende ese sesgo por estación y lo corrige solo (arriba, «Aprende de sus errores»).</p>`;
  el.innerHTML = h;
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
    <li><b>Retraso por posición.</b> Para la C-4, Renfe a menudo da la posición pero no el retraso. El programa lo calcula viendo cuándo sale de cada estación.</li>
    <li><b>Aprendizaje.</b> Con el tiempo sustituye los tiempos teóricos entre estaciones por los que observa de verdad (necesita 5 observaciones por tramo).</li>
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
    const hEst = j === 0 ? t.est_d[j] : t.est_a[j];
    const hProg = j === 0 ? t.prog_d[j] : t.prog_a[j];
    const hApp = j === 0 ? t.adif_d[j] : t.adif_a[j];
    const notas = mot.map((m) => `<div style="color:var(--warn);font-size:12.5px">${esc(m.texto)} (+${Math.round(m.min)} min)</div>`).join("") +
      (cr && !mot.some((m) => m.tipo === "cruce") ? `<div style="font-size:12.5px;color:var(--tx2)">Cruce con el ${cr.ida.id === t.id ? cr.vuelta.num : cr.ida.num}${cr.info === "movido" ? " (trasladado)" : ""}</div>` : "") +
      (!t.para[j] ? `<div style="font-size:12px;color:var(--mut)">sin parada</div>` : "") +
      (j === t.j0 && !t.fin ? `<div style="font-size:12.5px;font-weight:700;color:var(--c4)">◀ ${esc(t.situacion)}</div>` : "");
    filas.push(`<tr class="${pasado ? "pasado" : ""}${j === jo || j === jd ? " aqui" : ""}">
      <td>${e.cruce ? "<b>" : ""}${esc(e.nombre)}${e.cruce ? "</b>" : ""}${notas}</td>
      <td>${hm(hProg)}</td><td>${pasado ? "" : hm(hApp)}</td><td class="e">${pasado ? "" : hm(hEst)}</td></tr>`);
  }
  $("cajon").innerHTML = `<div class="cajon-cab"><div><h2>Tren ${t.num}</h2>
      <div style="color:var(--tx2)">${esc(t.origen)} → ${esc(t.destino)}</div></div>
      <button class="cerrar" id="cerrar" aria-label="Cerrar">×</button></div>
    <div class="datos"><span class="tag ${t.dir > 0 ? "ida" : "vta"}" style="margin:0">${t.dir > 0 ? "Hacia Cudillero/Avilés" : "Hacia Gijón"}</span>
      ${t.con_datos ? tagRetraso(retrasoActual(t)).replace('class="tag', 'style="margin:0" class="tag') : '<span class="tag gris" style="margin:0">Sin datos en tiempo real</span>'}
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
    if (j < 0 || !t.para[j] || t.j0 > j) continue;
    const sale = t.est_d[j];
    if (sale == null || sale < now - 0.5) continue;
    out.push({ num: t.num, dir: t.dir, destino: t.destino, sale });
  }
  return out.sort((a, b) => a.sale - b.sale).slice(0, n);
}
function pintarInicio() {
  if (!R || !LINEA) return;
  const viejo = edadDatos() > 90;
  const meta = { directo: ["ok", "En directo"], congelado: ["warn", "Renfe no actualiza"], sin_conexion: ["bad", "Sin conexión con Renfe"] };
  const [cls, lbl] = viejo ? ["warn", "Actualizando…"] : (meta[R.calidad] || ["", "En directo"]);
  const prox = (R.cruces || []).find((c) => c.hora >= R.ahora - 1);
  let h = `<div class="dash">`;
  h += `<div class="hero hero-${cls}">
      <div class="hero-top"><span class="pill-linea">C4</span>
        <div><div class="hero-tit">Cercanías C-4 · Gijón–Cudillero</div>
        <div class="hero-sub">${esc(lbl)}${R.actualizado ? " · actualizado " + esc(R.actualizado.slice(0, 5)) : ""}</div></div>
        <span class="hero-dot"></span></div>
      <div class="hero-kpis">
        <div class="hk"><div class="hk-n num">${R.en_circulacion || 0}</div><div class="hk-l">trenes en circulación</div></div>
        <div class="hk"><div class="hk-n num">${R.con_posicion || 0}</div><div class="hk-l">localizados en vivo</div></div>
        <div class="hk"><div class="hk-n num">${prox ? hm(prox.hora) : "—"}</div><div class="hk-l">${prox ? "próx. cruce · " + esc(nombreCorto(prox.estacion)) : "sin cruces próximos"}</div></div>
      </div></div>`;
  if ((R.tramos_aprendidos || 0) + (R.sesgos_corregidos || 0) > 0)
    h += `<div class="apr-strip" onclick="irA('precision')">🧠 <span>Aprendiendo de los datos: <b>${R.tramos_aprendidos || 0}</b> tramos y <b>${R.sesgos_corregidos || 0}</b> estaciones ajustadas a partir de errores</span><span class="cta-fl">›</span></div>`;
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
      const sal = t.est_d[jo], lle = t.est_a[jd], falta = Math.round(sal - now);
      return `<div class="mini-tren" data-tren="${t.id}" data-jo="${jo}" data-jd="${jd}">
        <div class="mt-l"><b>Tren ${t.num}</b><span class="tag ${t.dir > 0 ? "ida" : "vta"}">→ ${esc(destinoCorto(t))}</span>${t.con_datos ? tagRetraso(t.retraso) : ""}</div>
        <div class="mt-r"><span class="mt-when">${falta <= 0 ? "sale ya" : falta < 60 ? "en " + falta + " min" : hm(sal)}</span><span class="mt-lle num">${hm(lle)}</span></div></div>`;
    }).join("") : `<div class="vacio">No quedan trenes hoy en este trayecto.</div>`;
  }
  h += `</div>`;
  h += `<div class="card"><div class="card-cab"><h3>Cerca de ti</h3></div>
      <button class="btn-grande2" id="inicio-cerca">📍 Estaciones y buses cerca de mí</button>
      <div id="inicio-cerca-res"></div></div>`;
  const tools = [["ir", "Ir a…", "🧭"], ["mapa", "Mapa en vivo", "🗺️"], ["/bus/", "Bus en vivo", "🚌"],
                 ["malla", "Malla", "📈"], ["cruces", "Cruces", "⇄"], ["precision", "Precisión", "🎯"],
                 ["estacion", "Estación", "🚉"], ["info", "Cómo funciona", "ℹ️"]];
  h += `<div class="card"><div class="card-cab"><h3>Todo</h3></div><div class="tools">` +
    tools.map(([t, n, ic]) => t[0] === "/" ? `<a class="tool" href="${t}">${ic}<span>${n}</span></a>`
      : `<button class="tool" onclick="irA('${t}')">${ic}<span>${n}</span></button>`).join("") + `</div></div>`;
  h += `</div>`;
  $("inicio").innerHTML = h;
  const b = $("inicio-cerca"); if (b) b.onclick = inicioCerca;
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
    let h = `<div class="cerca-est"><div class="ce-cab">🚉 <b>${esc(e.nombre)}</b> <span class="sub">a ${dk(e) < 1 ? Math.round(dk(e) * 1000) + " m" : dk(e).toFixed(1) + " km"}</span></div>`;
    h += sal.length ? sal.map((s) => `<div class="ce-fila"><span class="tag ${s.dir > 0 ? "ida" : "vta"}">→ ${esc(nombreCorto(s.destino))}</span><span class="ce-mid">Tren ${s.num}</span><b class="num">${hm(s.sale)}</b></div>`).join("") : `<div class="vacio">Sin trenes próximos.</div>`;
    h += `</div>`;
    b.disabled = false; b.textContent = "📍 Actualizar mi posición";
    res.innerHTML = h + `<div class="vacio" id="cb">Buscando buses cerca…</div>`;
    try {
      const j = await pedir(`/api/bus/cercanas?lat=${yo[0]}&lon=${yo[1]}`, 9000);
      const el = $("cb"); if (!el) return;
      el.outerHTML = (j.paradas && j.paradas.length)
        ? `<div class="cerca-bus"><div class="ce-cab">🚌 Paradas de bus cerca</div>` +
          j.paradas.slice(0, 4).map((p) => `<div class="ce-fila"><span class="ce-mid" style="flex:1">${esc(p.nombre)}</span><span class="sub">${p.metros} m</span></div>`).join("") +
          `<a class="link" href="/bus/" style="display:inline-block;margin-top:8px">Abrir el mapa de buses en vivo ›</a></div>`
        : "";
    } catch (err) { const el = $("cb"); if (el) el.remove(); }
  }, () => {
    b.disabled = false; b.textContent = "📍 Estaciones y buses cerca de mí";
    res.innerHTML = `<div class="vacio">No se pudo obtener la ubicación.</div>`;
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

function irA(tab) {
  tabActual = tab;
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", b.dataset.tab === tab);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "tab-" + tab;
  history.replaceState(null, "", "#" + tab);
  if (tab === "mapa") {
    const nuevo = !mapa;
    iniciarMapa();
    setTimeout(() => { if (mapa) { mapa.invalidateSize(); if (nuevo) mapa.fitBounds(L.latLngBounds(LINEA.trazado), { padding: [16, 16] }); pintarTrenesMapa(); } }, 60);
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

async function planificar() {
  const o = $("ir-o").value.trim(), d = $("ir-d").value.trim();
  if (!d) { $("ir-d").focus(); return; }
  let url = "/api/ir?destino=" + encodeURIComponent(d);
  if (GPS && (!o || o === MI_UBIC)) url += `&olat=${GPS[0]}&olon=${GPS[1]}`;
  else if (o) url += "&origen=" + encodeURIComponent(o);
  else { $("ir-o").focus(); return; }
  $("ir-resultado").innerHTML = `<div class="ir-cargando">Buscando la mejor combinación…</div>`;
  $("ir-buscar").disabled = true;
  try { pintarPlan(await pedir(url, 16000)); }
  catch (e) { pintarPlan({ ok: false, error: "No pude conectar con el servidor. Prueba otra vez en un momento." }); }
  finally { $("ir-buscar").disabled = false; }
}

function usarGps() {
  if (!navigator.geolocation) { aviso("Este dispositivo no permite la ubicación."); return; }
  const inp = $("ir-o"); inp.value = "Localizando…";
  navigator.geolocation.getCurrentPosition((pos) => {
    GPS = [pos.coords.latitude, pos.coords.longitude]; inp.value = MI_UBIC;
    if ($("ir-d").value.trim()) planificar();
  }, (err) => {
    inp.value = ""; GPS = null;
    aviso(err.code === 1 ? "Permiso de ubicación denegado." : "No se pudo obtener la ubicación.");
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

function pintarPlan(p) {
  const box = $("ir-resultado");
  if (!p) { box.innerHTML = ""; return; }
  if (p.cargando) { box.innerHTML = `<div class="ir-cargando">El horario del día aún se está cargando. Prueba en unos segundos.</div>`; return; }
  if (!p.ok) {
    box.innerHTML = `<div class="ir-error">${esc(p.error || "No encontré una ruta.")}</div>` +
      (p.avisos || []).map((a) => `<div class="ir-aviso">${esc(a)}</div>`).join("");
    return;
  }
  const dur = p.duracion != null ? `${Math.round(p.duracion)} min` : "";
  let h = `<div class="ir-cab">
      <div class="ir-od"><span class="ir-de">${esc(p.origen.nombre)}</span><span class="ir-fl">→</span><span class="ir-a">${esc(p.destino.nombre)}</span></div>
      <div class="ir-tot">${p.solo_urbano ? "" : `Sales ${hm(p.sale)} · `}llegas <b>${p.llega_hm || "--:--"}</b>${dur ? ` · ${dur}` : ""}</div>
    </div><ol class="ir-etapas">`;
  for (const e of p.etapas) {
    if (e.tipo === "andar")
      h += `<li class="et andar"><span class="et-ico">🚶</span><div class="et-cuerpo">
        <div class="et-t">Andar ${e.min} min <span class="et-sub2">· ${e.metros} m</span></div>
        <div class="et-sub">${esc(e.desde)} → ${esc(e.hasta)}</div></div></li>`;
    else if (e.tipo === "bus") {
      const sale = e.sale_en != null
        ? `<span class="et-min ok">sale en ${e.sale_en} min</span>`
        : `<span class="et-min aprox">según horario</span>`;
      h += `<li class="et bus"><span class="et-ico">🚌</span><div class="et-cuerpo">
        <div class="et-t"><span class="bus-chip" style="background:${esc(e.color)}">${esc(e.linea)}</span> hacia ${esc(e.destino)} ${sale}</div>
        <div class="et-sub">Sube en <b>${esc(e.subir)}</b> · baja en <b>${esc(e.bajar)}</b> · ${e.paradas} paradas (~${e.min} min)</div></div></li>`;
    } else if (e.tipo === "tren") {
      h += `<li class="et tren"><span class="et-ico">🚆</span><div class="et-cuerpo">
        <div class="et-t">Tren <b>C-4</b> ${esc(e.num)}${e.retraso > 0 ? ` <span class="tm-r warn">+${e.retraso}</span>` : ""}</div>
        <div class="et-horas"><span>${hm(e.sale)} <b>${esc(e.desde)}</b></span><span class="et-fl">→</span><span>${hm(e.llega)} <b>${esc(e.hasta)}</b></span></div>
        ${e.espera_estacion > 0 ? `<div class="et-sub">Espera en la estación ${Math.round(e.espera_estacion)} min hasta la salida</div>` : ""}
        ${(e.motivos || []).map((m) => `<div class="et-cruce">⇄ ${esc(m.texto)} <span class="et-min warn2">+${m.min}</span></div>`).join("")}
      </div></li>`;
    }
  }
  h += `</ol>`;
  (p.avisos || []).forEach((a) => { h += `<div class="ir-aviso">${esc(a)}</div>`; });
  h += `<p class="ir-nota">El tren lleva la hora real (con cruces en vía única). El bus urbano usa los minutos en directo de EMTUSA cuando los hay; si no, una estimación por horario.</p>`;
  box.innerHTML = h;
}

$("ir-buscar").onclick = planificar;
$("ir-gps").onclick = usarGps;
$("ir-o").addEventListener("input", () => { if ($("ir-o").value !== MI_UBIC) GPS = null; });
$("ir-swap").onclick = () => {
  const a = $("ir-o").value, eraGps = GPS && a === MI_UBIC;
  $("ir-o").value = $("ir-d").value; $("ir-d").value = eraGps ? "" : a; GPS = null;
};
for (const inp of [$("ir-o"), $("ir-d")])
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); planificar(); } });
for (const b of document.querySelectorAll("#ir-ejemplos button"))
  b.onclick = () => { GPS = null; $("ir-o").value = b.dataset.o; $("ir-d").value = b.dataset.d; planificar(); };

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
  setInterval(cargarEstado, 15000);
  setInterval(() => { if (tabActual === "viaje" && R && document.activeElement !== $("andar")) pintarViaje(); }, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) cargarEstado(); });
  window.addEventListener("pageshow", () => cargarEstado());
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  setInterval(() => { if (tabActual === "precision") cargarPrecision(); }, 300000);
  setInterval(() => {
    $("reloj").textContent = new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (tabActual === "mapa") pintarTrenesMapa();
  }, 1000);
})();
