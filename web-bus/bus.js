"use strict";
/* Autobuses de Gijón (EMTUSA) · interfaz. Posición de los buses en vivo + llegadas.
   Leaflet solo para el mapa. Sin más dependencias. */
let RED = null, PARADAS = {}, LINEAS = {}, TRAYECTOS = {}, POR_PARADA = {}, tab = "vivo";
let mapa = null, capaParadas = null, capaRuta = null, capaBuses = null, marcaYo = null;
let filtroLinea = "", ultimaPos = null, busTimer = null, ultBuses = 0;
const busMarks = {};   // clave -> {m, lat, lon, hdg}
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const guardar = (k, v) => { try { localStorage.setItem("bus." + k, JSON.stringify(v)); } catch (e) {} };
const leer = (k, d) => { try { const v = localStorage.getItem("bus." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
const favs = () => leer("favs", []);
const colorHex = (c) => "#" + String(c || "888888").replace("#", "");
const hm = (m) => { if (m == null) return "--:--"; m = Math.round(m); return String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(((m % 60) + 60) % 60).padStart(2, "0"); };
const nombreCorto = (n) => (n || "").replace("Gijón-Sanz Crespo", "Gijón").replace("-Apeadero", "");
function toast(t) { const e = $("toast"); e.textContent = t; e.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => e.hidden = true, 2600); }

async function cargarRed() {
  for (;;) {
    try {
      const j = await (await fetch("/api/bus/red")).json();
      if (j.lineas) { RED = j; return j; }
    } catch (e) {}
    $("chip-txt").textContent = "Conectando…";
    await new Promise((r) => setTimeout(r, 1500));
  }
}
function indexar() {
  for (const p of RED.paradas) PARADAS[p.id] = p;
  for (const l of RED.lineas) LINEAS[l.codigo] = l;
  TRAYECTOS = RED.trayectos;
  for (const k in TRAYECTOS) {
    const l = RED.lineas.find((x) => x.id === TRAYECTOS[k].linea);
    for (const pid of TRAYECTOS[k].paradas) {
      (POR_PARADA[pid] = POR_PARADA[pid] || []);
      if (l && !POR_PARADA[pid].some((x) => x.codigo === l.codigo)) POR_PARADA[pid].push({ codigo: l.codigo, color: l.color });
    }
  }
  $("pie").textContent = `${RED.lineas.length} líneas · ${RED.paradas.length} paradas · datos oficiales de EMTUSA` +
    (RED.generado ? ` · red actualizada ${new Date(RED.generado).toLocaleDateString("es-ES")}` : "");
}

/* ---------------- badges de línea ---------------- */
function badge(codigo, color) { return `<span class="lb" style="background:${colorHex(color)};color:${textoSobre(colorHex(color))}">${esc(codigo)}</span>`; }
function badgesParada(lineas) { return `<div class="lineas-badges">${(lineas || []).map((l) => badge(l.codigo, l.color)).join("")}</div>`; }

/* ================================================================= BUSES EN VIVO */
function iconoBus(v) {
  const r = v.rumbo;
  return L.divIcon({
    className: "bus-marca", iconSize: [28, 28], iconAnchor: [14, 14],
    html: `<div class="bm${r == null ? " sin-rumbo" : ""}${v.quieto_s > 150 ? " quieto" : ""}"><span class="bm-fl" style="border-bottom-color:${esc(v.color)};transform:translate(-50%,-50%) rotate(${r || 0}deg) translateY(-16px)"></span>
      <span class="bm-l" style="background:${esc(v.color)};color:${textoSobre(v.color)}">${esc(v.linea)}</span></div>`,
  });
}
/* texto blanco o negro según lo claro que sea el color de la línea (la 12 es amarilla) */
function textoSobre(c) {
  const h = String(c || "").replace("#", "");
  if (h.length < 6) return "#fff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.62 ? "#111" : "#fff";
}
/* Movimiento continuo. EMTUSA solo renueva la posición de cada bus cada ~30 s, así que entre dos
   lecturas se le hace avanzar por su recorrido a la velocidad que llevaba (sin pasar de la próxima
   parada, donde puede detenerse). Cuando llega la posición real, se corrige con suavidad. */
const INTERVALO_BUS = 5000;
let animando = false, ultFrame = 0;
function km(a, b) { const dx = (b[1] - a[1]) * 80.8, dy = (b[0] - a[0]) * 111.2; return Math.hypot(dx, dy); }
function objetivo(b, t) {
  const v = b.v;
  if (!v.vel || !v.camino || v.camino.length < 2) return [v.lat, v.lon];
  const edad = Math.min(45, (v.quieto_s || 0) + (t - b.tv) / 1000);
  let d = v.vel * edad / 60;                             // km recorridos desde la última lectura
  let p = [v.lat, v.lon];
  for (let i = 1; i < v.camino.length && d > 0; i++) {
    const q = v.camino[i], l = km(p, q);
    if (l >= d) { const f = d / l; return [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f]; }
    d -= l; p = q;
  }
  return p;
}
function animarBuses() {
  if (animando) return;
  animando = true;
  const paso = (t) => {
    if (document.hidden || tab !== "vivo") { animando = false; return; }
    const dt = t - ultFrame;
    if (dt >= 33) {                                        // ~30 imágenes por segundo basta
      ultFrame = t;
      const k = 1 - Math.exp(-Math.min(dt, 200) / 450);    // corrección suave hacia donde debe estar
      for (const key in busMarks) {
        const b = busMarks[key];
        const o = objetivo(b, t);
        const salto = Math.abs(o[0] - b.cur[0]) + Math.abs(o[1] - b.cur[1]);
        if (salto < 2e-7) continue;
        b.cur = salto > 0.006 ? o : [b.cur[0] + (o[0] - b.cur[0]) * k, b.cur[1] + (o[1] - b.cur[1]) * k];
        b.m.setLatLng(b.cur);
      }
    }
    requestAnimationFrame(paso);
  };
  requestAnimationFrame(paso);
}
async function tickBuses() {
  if (!mapa || !capaBuses) return;
  let j;
  try { j = await (await fetch("/api/bus/coordenadas")).json(); }
  catch (e) { estadoChip(false); return; }
  if (!j || !j.vehiculos || j.disponible === false) {
    // EMTUSA no responde: decirlo claro en vez de «0 buses en circulación»
    estadoChip(false);
    $("vivo-cont").innerHTML = "EMTUSA no da posiciones ahora";
    return;
  }
  const vis = j.vehiculos.filter((v) => !filtroLinea || v.linea === filtroLinea);
  ultBuses = j.vehiculos.length;
  const cuenta = {};
  for (const v of j.vehiculos) cuenta[v.linea] = (cuenta[v.linea] || 0) + 1;
  tickBuses._cuenta = cuenta;
  pintarChipsLineas(cuenta);
  const vivos = new Set(), t = performance.now();
  // (el reloj de las animaciones es el de requestAnimationFrame, que es el mismo que performance.now)
  // si el mapa lleva un rato sin mirarse, se colocan directamente (sin viajes largos por la ciudad)
  const saltar = t - (tickBuses._t || 0) > 3 * INTERVALO_BUS;
  tickBuses._t = t;
  for (const v of vis) {
    const key = v.linea + "-" + v.bus, pos = [v.lat, v.lon];
    vivos.add(key);
    let b = busMarks[key];
    if (!b) {
      const m = L.marker(pos, { icon: iconoBus(v), zIndexOffset: 500, keyboard: false }).addTo(capaBuses);
      m.on("click", () => popupBus(key));
      b = busMarks[key] = { m, cur: pos, v, tv: t, firma: "" };
    } else {
      b.v = v; b.tv = t;
      // si el mapa llevaba un rato sin mirarse, se coloca directamente (sin viajes largos por la ciudad)
      if (saltar) { b.cur = objetivo(b, t); b.m.setLatLng(b.cur); }
    }
    const firma = `${v.rumbo}|${v.quieto_s > 150}|${v.color}`;
    if (firma !== b.firma) {
      b.firma = firma;
      const fl = b.m._icon && b.m._icon.querySelector(".bm-fl"), bm = b.m._icon && b.m._icon.querySelector(".bm");
      if (bm) {
        if (v.rumbo != null) fl.style.transform = `translate(-50%,-50%) rotate(${v.rumbo}deg) translateY(-16px)`;
        bm.classList.toggle("sin-rumbo", v.rumbo == null);
        bm.classList.toggle("quieto", v.quieto_s > 150);
      } else b.m.setIcon(iconoBus(v));
    }
    if (b.m._popup && b.m._popup.isOpen()) b.m.setPopupContent(htmlPopupBus(v));
  }
  for (const key in busMarks) if (!vivos.has(key)) { capaBuses.removeLayer(busMarks[key].m); delete busMarks[key]; }
  animarBuses();
  estadoChip(true);
  const n = vis.length;
  $("vivo-cont").innerHTML = filtroLinea
    ? `<b>${n}</b> ${n === 1 ? "bus" : "buses"} de la línea <b>${esc(filtroLinea)}</b> <button class="vp-x" data-fl="" title="Ver todas">×</button>`
    : `<b>${n}</b> buses en circulación`;
  $("mapa-pie").textContent = (j.viejo ? "EMTUSA tarda en responder: posiciones de hace unos segundos · " : "") +
    `Actualizado ${new Date((j.ts || Date.now() / 1000) * 1000).toLocaleTimeString("es-ES")} · se mueven solos, sin recargar`;
}
function htmlPopupBus(v) {
  const q = v.quieto_s || 0;
  const donde = v.en_parada ? `En la parada <b>${esc(v.en_parada.nombre)}</b>` :
    v.proxima ? `Próxima parada: <b>${esc(v.proxima.nombre)}</b>` : "";
  return `<div class="pop-bus"><div class="pop-cab">${badge(v.linea, v.color)}<b>${esc(v.nombre_linea || "")}</b></div>
    <div class="pop-fila">→ ${esc(v.destino || "")}</div>
    ${donde ? `<div class="pop-donde">${donde}</div>` : ""}
    ${q > 150 ? `<div class="pop-quieto">Sin moverse desde hace ${Math.round(q / 60)} min</div>` : ""}
    <div class="pop-sub">Bus nº ${esc(v.bus)} · viene de ${esc(v.origen || "—")}</div>
    <div class="pop-btns">
      ${v.proxima ? `<button class="pop-btn" data-parada="${v.proxima.id}">Llegadas en ${esc(v.proxima.nombre)}</button>` : ""}
      <button class="pop-btn" data-verlinea="${esc(v.linea)}">Ver recorrido de la línea ${esc(v.linea)}</button></div></div>`;
}
function popupBus(key) {
  const b = busMarks[key]; if (!b) return;
  b.m.bindPopup(htmlPopupBus(b.v), { className: "bus-pop", autoPanPadding: [20, 60] }).openPopup();
}

function estadoChip(ok) {
  const c = $("chip");
  if (ok) { c.className = "chip ok"; $("chip-txt").textContent = ultBuses ? `${ultBuses} buses en vivo` : "Tiempo real"; }
  else { c.className = "chip no"; $("chip-txt").textContent = "Sin tiempo real"; }
}
function arrancarBuses() {
  clearInterval(busTimer);
  tickBuses();
  busTimer = setInterval(() => { if (!document.hidden && tab === "vivo") tickBuses(); }, INTERVALO_BUS);
}

/* ---------------- llegadas: agrupadas por línea y destino ---------------- */
/* EMTUSA da cada bus por separado; se juntan los de la misma línea y sentido: «7 min, luego 19 y 31». */
function agrupar(llegadas) {
  const g = new Map();
  for (const x of llegadas || []) {
    if (x.minutos == null) continue;
    const k = x.linea + "|" + x.destino;
    if (!g.has(k)) g.set(k, { ...x, mins: [] });
    g.get(k).mins.push(x.minutos);
  }
  return [...g.values()].sort((a, b) => a.mins[0] - b.mins[0]);
}
const horaEn = (min, ts) => { const d = new Date(((ts || Date.now() / 1000) + min * 60) * 1000); return d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }); };
function txtMin(m) { return m <= 0 ? "llegando" : m === 1 ? "1 min" : m + " min"; }
function filasLlegadas(llegadas, ts, max) {
  const gs = agrupar(llegadas).slice(0, max || 99);
  return gs.map((x) => {
    const m = x.mins[0], ya = m <= 0;
    const luego = x.mins.slice(1, 3);
    return `<div class="lleg ${m <= 2 ? "inminente" : ""}" data-verlinea="${esc(x.linea)}" title="Ver la línea ${esc(x.linea)} en el mapa">${badge(x.linea, x.color)}
      <span class="destino">${esc(x.destino)}${luego.length ? `<small>luego en ${luego.join(" y ")} min</small>` : ""}</span>
      <span class="min">${ya ? "ya" : m}<small>${ya ? "" : " min"}</small><em>${ya ? "llegando" : horaEn(m, ts)}</em></span></div>`;
  }).join("");
}

/* ---------------- ficha de parada (llegadas) ---------------- */
let hojaParada = null, hojaTimer = null, marcaParada = null;
function abrirParada(id) {
  hojaParada = id; $("hoja").hidden = false; pintarHoja(true);
  clearInterval(hojaTimer); hojaTimer = setInterval(() => { if (!document.hidden) pintarHoja(false); }, 15000);
}
function cerrarHoja() { $("hoja").hidden = true; hojaParada = null; clearInterval(hojaTimer); }
async function pintarHoja(reset) {
  const id = hojaParada, p = PARADAS[id];
  if (!p) return;
  const esFav = favs().includes(id);
  if (reset) {
    $("hoja-caja").innerHTML =
      `<div class="hoja-cab"><h3>${esc(p.nombre)}<small class="parada-sentido">${esc(sentidoParada(id))}</small></h3>
        <button class="fav-btn ${esFav ? "on" : ""}" id="hoja-fav" title="Favorita">${esFav ? "★" : "☆"}</button>
        <button class="cerrar" id="hoja-x" aria-label="Cerrar">×</button></div>
       ${badgesParada(POR_PARADA[id])}
       <div id="hoja-cont"><div class="vacio">Cargando llegadas…</div></div>
       <button class="pop-btn" id="hoja-mapa" style="margin-top:12px">Ver la parada en el mapa</button>`;
    $("hoja-x").onclick = cerrarHoja;
    $("hoja-fav").onclick = () => { alternarFav(id); pintarHoja(true); if (tab === "favoritos") pintarFav(); };
    $("hoja-mapa").onclick = () => verParadaEnMapa(id);
  }
  try {
    const j = await (await fetch("/api/bus/parada/" + id)).json();
    const cont = $("hoja-cont"); if (!cont || hojaParada !== id) return;
    if (j.error) {
      cont.innerHTML = `<div class="aviso warn" style="margin:10px 0 0"><span>⚠</span><div>EMTUSA no está dando llegadas en directo ahora mismo. Prueba en unos segundos.</div></div>`;
      return;
    }
    if (!agrupar(j.llegadas).length) { cont.innerHTML = `<div class="vacio">No viene ningún bus en los próximos minutos.</div>`; return; }
    cont.innerHTML = `<div class="llegadas">${filasLlegadas(j.llegadas, j.ts)}</div>
      <p class="sub" style="margin:10px 0 0">Datos de EMTUSA de las ${esc(((j.llegadas || [])[0] || {}).actualizado || "")}. Se actualiza solo. Toca una línea para verla en el mapa.</p>`;
  } catch (e) { const c = $("hoja-cont"); if (c) c.innerHTML = `<div class="vacio">No se pudieron cargar las llegadas.</div>`; }
}
function verParadaEnMapa(id) {
  const p = PARADAS[id]; if (!p) return;
  cerrarHoja();
  irA("vivo");
  setTimeout(() => {
    if (!mapa) return;
    mapa.setView([p.lat, p.lon], 17);
    if (marcaParada) mapa.removeLayer(marcaParada);
    marcaParada = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="parada-foco"></div>`, iconSize: [26, 26] }), interactive: false }).addTo(mapa);
  }, 120);
}
function alternarFav(id) {
  let f = favs();
  f = f.includes(id) ? f.filter((x) => x !== id) : [...f, id];
  guardar("favs", f);
  toast(f.includes(id) ? "Parada guardada" : "Parada quitada");
}

/* Hacia dónde van los buses de una parada (hay muchas con el mismo nombre, una en cada acera) */
function sentidoParada(id) {
  const c = {};
  for (const t of Object.values(TRAYECTOS)) {
    const i = t.paradas.indexOf(id);
    if (i >= 0 && i < t.paradas.length - 1) c[t.destino] = (c[t.destino] || 0) + 1;
  }
  const ds = Object.keys(c).sort((a, b) => c[b] - c[a]).slice(0, 3);
  return ds.length ? "Hacia " + ds.join(", ") : "Final de línea";
}
/* ---------------- tarjeta de parada (con los próximos buses dentro) ---------------- */
function tarjetaParada(p, extra, info) {
  let lleg = "";
  if (info) {
    lleg = info.error ? `<div class="mini-sin">Sin datos en directo ahora</div>`
      : agrupar(info.llegadas).length ? `<div class="llegadas mini">${filasLlegadas(info.llegadas, info.ts, 4)}</div>`
      : `<div class="mini-sin">Ningún bus en los próximos minutos</div>`;
  }
  return `<div class="parada" data-parada="${p.id}">
    <div class="parada-cab"><span class="parada-nom">${esc(p.nombre)}</span>
      ${extra || (p.metros != null ? `<span class="parada-met">${p.metros} m${p.metros >= 60 ? ` · ${Math.max(1, Math.round(p.metros / 75))} min a pie` : ""}</span>` : "")}</div>
    <div class="parada-sentido">${esc(sentidoParada(p.id))}</div>
    ${info ? lleg : badgesParada(POR_PARADA[p.id] || p.lineas)}</div>`;
}
/* pinta una lista de paradas y después rellena sus llegadas (todas en una sola petición) */
async function listaConLlegadas(contId, paradas, vacio) {
  const cont = $(contId);
  if (!paradas.length) { cont.innerHTML = `<div class="vacio">${vacio}</div>`; return; }
  if (!cont.querySelector(".parada")) cont.innerHTML = paradas.map((p) => tarjetaParada(p)).join("");
  try {
    const j = await (await fetch("/api/bus/llegadas?ids=" + paradas.map((p) => p.id).join(","))).json();
    cont.innerHTML = paradas.map((p) => tarjetaParada(p, null, j.paradas[p.id] || j.paradas[String(p.id)])).join("") +
      `<p class="sub" style="margin:10px 2px 0">Actualizado ${new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · se refresca solo cada 30 s.</p>`;
  } catch (e) { cont.innerHTML = paradas.map((p) => tarjetaParada(p)).join(""); }
}
let listaTimer = null;
function refrescoLista(fn) { clearInterval(listaTimer); listaTimer = setInterval(() => { if (!document.hidden) fn(); }, 30000); }

/* ---------------- cerca de mí ---------------- */
let cercanas = [];
async function pintarCerca() {
  await listaConLlegadas("cerca-lista", cercanas, "No hay paradas a menos de 500 m.");
}
async function cargarCerca(pos) {
  try {
    const j = await (await fetch(`/api/bus/cercanas?lat=${pos[0]}&lon=${pos[1]}`)).json();
    cercanas = j.paradas || [];
    $("cerca-lista").innerHTML = "";
    await pintarCerca();
    refrescoLista(() => { if (tab === "cerca") pintarCerca(); });
  } catch (e) { toast("No se pudieron cargar las paradas"); }
}
function localizar() {
  const b = $("loc");
  if (!navigator.geolocation) { toast("Sin ubicación en este dispositivo"); return; }
  b.disabled = true; b.textContent = "📍 Buscando tu posición…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    ultimaPos = [pos.coords.latitude, pos.coords.longitude]; guardar("pos", ultimaPos);
    await cargarCerca(ultimaPos);
    b.disabled = false; b.textContent = "📍 Actualizar mi posición";
  }, () => { b.disabled = false; b.textContent = "📍 Buscar paradas cerca de mí"; toast("No se pudo obtener tu ubicación"); },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
}

/* ---------------- buscar ---------------- */
let buscarT = null;
function pintarBuscar() {
  const q = $("q").value.trim();
  clearTimeout(buscarT);
  if (q.length < 2) { $("buscar-lista").innerHTML = `<div class="vacio">Escribe al menos 2 letras.</div>`; return; }
  buscarT = setTimeout(async () => {
    try {
      const j = await (await fetch("/api/bus/buscar?q=" + encodeURIComponent(q))).json();
      $("buscar-lista").innerHTML = (j.paradas && j.paradas.length) ? j.paradas.map((p) => tarjetaParada(p)).join("") : `<div class="vacio">Ninguna parada coincide.</div>`;
    } catch (e) {}
  }, 250);
}

/* ---------------- favoritas ---------------- */
function pintarFav() {
  const f = favs().map((id) => PARADAS[id]).filter(Boolean);
  if (!f.length) { $("fav-lista").innerHTML = `<div class="vacio">Aún no has guardado paradas. Abre una parada y pulsa la estrella ☆.</div>`; return; }
  listaConLlegadas("fav-lista", f, "");
  refrescoLista(() => { if (tab === "favoritos") pintarFav(); });
}

/* ---------------- líneas ---------------- */
async function pintarLineas() {
  let cuenta = tickBuses._cuenta;
  if (!cuenta) {
    try { const j = await (await fetch("/api/bus/coordenadas")).json(); cuenta = {}; for (const v of j.vehiculos || []) cuenta[v.linea] = (cuenta[v.linea] || 0) + 1; } catch (e) { cuenta = {}; }
  }
  const ls = [...RED.lineas].sort((a, b) => (cuenta[b.codigo] ? 1 : 0) - (cuenta[a.codigo] ? 1 : 0) || a.id - b.id);
  $("lineas-lista").innerHTML = ls.map((l) => {
    const n = cuenta[l.codigo] || 0;
    return `<button class="linea-card${n ? "" : " sin-buses"}" data-linea="${esc(l.codigo)}">
      <span class="linea-code" style="background:${colorHex(l.color)};color:${textoSobre(l.color)}">${esc(l.codigo)}</span>
      <span class="linea-desc">${esc(l.nombre)}<small>${n ? `${n} ${n === 1 ? "bus" : "buses"} ahora` : "Ahora sin servicio"}</small></span></button>`;
  }).join("");
}
function abrirLinea(codigo) {
  const l = LINEAS[codigo]; if (!l) return;
  const trs = Object.values(TRAYECTOS).filter((t) => t.linea === l.id);
  $("hoja").hidden = false;
  $("hoja-caja").innerHTML = `<div class="hoja-cab">
      <span class="linea-code" style="background:${colorHex(l.color)}">${esc(l.codigo)}</span>
      <h3 style="flex:1">${esc(l.nombre)}</h3>
      <button class="cerrar" id="hoja-x">×</button></div>
    <button class="pop-btn" id="ver-en-mapa" style="margin:0 0 10px">Ver en el mapa en vivo</button>
    ${trs.map((t) => `<h4 class="tr-tit">→ ${esc(t.destino)}</h4>
      <div class="llegadas">${t.paradas.map((pid) => PARADAS[pid] ?
        `<div class="lleg" data-parada="${pid}" style="cursor:pointer"><span class="destino">${esc(PARADAS[pid].nombre)}</span><span class="min" style="font-size:13px">›</span></div>` : "").join("")}</div>`).join("")}`;
  $("hoja-x").onclick = cerrarHoja;
  $("ver-en-mapa").onclick = () => { cerrarHoja(); $("mapa-linea").value = codigo; irA("vivo"); setFiltroLinea(codigo); };
}

/* ---------------- mapa ---------------- */
const OSCURO = () => matchMedia("(prefers-color-scheme: dark)").matches;
const ZOOM_DETALLE = 14.5;   // desde aquí los buses llevan número y se ven las paradas
function estiloParada(enLinea, color) {
  const osc = OSCURO();
  return enLinea
    ? { radius: 5, color, weight: 2.5, fillColor: osc ? "#1a1a1f" : "#fff", fillOpacity: 1, opacity: 1 }
    : { radius: 3.5, color: osc ? "#8a8a94" : "#6b6b75", weight: 1.3, fillColor: osc ? "#e4e4e8" : "#fff", fillOpacity: 1, opacity: 1 };
}
function iniciarMapa() {
  if (mapa || !window.L) return;
  // vista inicial: toda la zona urbana donde circulan los buses (antes se cortaba por los lados en el móvil)
  // en el móvil (pantalla estrecha) el área entera queda diminuta: se enfoca el centro de la ciudad
  const estrecho = $("mapa").clientWidth < 600;
  mapa = L.map("mapa", { zoomControl: false, zoomSnap: 0.25 }).fitBounds(
    estrecho ? [[43.515, -5.690], [43.548, -5.630]] : [[43.506, -5.712], [43.551, -5.612]], { padding: [6, 6] });
  L.control.zoom({ position: "bottomright" }).addTo(mapa);
  mapa.attributionControl.setPrefix(false);
  // fondo limpio (lienzo gris de Esri, claro u oscuro), como en el mapa del tren
  const esri = (srv) => `https://server.arcgisonline.com/ArcGIS/rest/services/${srv}/MapServer/tile/{z}/{y}/{x}`;
  const tono = OSCURO() ? "Dark" : "Light";
  const sencillo = L.layerGroup([
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Base`), { maxZoom: 19, maxNativeZoom: 16, attribution: "Mapa &copy; Esri, HERE, Garmin, &copy; OpenStreetMap" }),
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Reference`), { maxZoom: 19, maxNativeZoom: 16, zIndex: 3 }),
  ]);
  const planos = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, className: "base-osm",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' });
  const satelite = L.tileLayer(esri("World_Imagery"), { maxZoom: 19, attribution: "Imágenes &copy; Esri" });
  const fondos = { "Sencillo": sencillo, "Detallado": planos, "Satélite": satelite };
  (fondos[leer("fondo", "Sencillo")] || sencillo).addTo(mapa);
  L.control.layers(fondos, null, { position: "topright" }).addTo(mapa);
  mapa.on("baselayerchange", (e) => guardar("fondo", e.name));
  capaRuta = L.layerGroup().addTo(mapa);
  // 565 paradas: en lienzo (canvas), mucho más ligero que un elemento por parada
  const lienzo = L.canvas({ padding: 0.3 });
  capaParadas = L.layerGroup();
  for (const p of RED.paradas)
    L.circleMarker([p.lat, p.lon], { renderer: lienzo, _pid: p.id, ...estiloParada(false) })
      .bindTooltip(p.nombre, { className: "bus-tt", direction: "top", offset: [0, -5] })
      .on("click", () => abrirParada(p.id)).addTo(capaParadas);
  capaBuses = L.layerGroup().addTo(mapa);
  const cont = mapa.getContainer();
  mapa.on("zoomstart movestart", () => cont.classList.add("sin-anim"));
  mapa.on("zoomend moveend", () => setTimeout(() => cont.classList.remove("sin-anim"), 60));
  mapa.on("zoomend", ajustarDetalle);
  $("mapa-linea").innerHTML = `<option value="">Todas las líneas</option>` +
    RED.lineas.map((l) => `<option value="${esc(l.codigo)}">Línea ${esc(l.codigo)} · ${esc(l.nombre).slice(0, 28)}</option>`).join("");
  pintarChipsLineas({});
  ajustarDetalle();
  arrancarBuses();
}
/* Con el mapa alejado: buses como puntos de color y sin paradas. Al acercar: número, flecha y paradas. */
function ajustarDetalle() {
  if (!mapa) return;
  const z = mapa.getZoom(), cerca = z >= ZOOM_DETALLE;
  // alejado: el número de línea más pequeño (sigue leyéndose); muy alejado: solo el punto de color
  mapa.getContainer().classList.toggle("lejos", !cerca && !filtroLinea);
  mapa.getContainer().classList.toggle("muy-lejos", z < 11.75 && !filtroLinea);
  const ver = cerca || !!filtroLinea;
  if (ver && !mapa.hasLayer(capaParadas)) capaParadas.addTo(mapa);
  if (!ver && mapa.hasLayer(capaParadas)) mapa.removeLayer(capaParadas);
}
/* Barra de líneas: el color de cada una y cuántos buses lleva ahora mismo. Un toque filtra. */
function pintarChipsLineas(cuenta) {
  const cont = $("chips-lineas");
  if (!cont || !RED) return;
  const total = Object.values(cuenta).reduce((a, b) => a + b, 0);
  const lineas = [...RED.lineas].sort((a, b) => (cuenta[b.codigo] ? 1 : 0) - (cuenta[a.codigo] ? 1 : 0) || a.id - b.id);
  const html = `<button class="chip-l todas${filtroLinea ? "" : " on"}" data-fl="">Todas${total ? `<i>${total}</i>` : ""}</button>` +
    lineas.map((l) => {
      const n = cuenta[l.codigo] || 0;
      return `<button class="chip-l${filtroLinea === l.codigo ? " on" : ""}${filtroLinea && filtroLinea !== l.codigo ? " apagada" : ""}${n ? "" : " vacia"}" data-fl="${esc(l.codigo)}" style="--c:${colorHex(l.color)};color:${textoSobre(l.color)}" title="${esc(l.nombre)}${n ? ` · ${n} en circulación` : " · ahora sin buses"}"><b>${esc(l.codigo)}</b>${n ? `<i>${n}</i>` : ""}</button>`;
    }).join("");
  if (cont._html !== html) { cont.innerHTML = html; cont._html = html; }
}
/* trazado por las calles (polilínea codificada); si no lo hay, de parada a parada */
function decodifica(p) {
  const pts = []; let i = 0, la = 0, lo = 0;
  while (i < p.length) {
    for (let k = 0; k < 2; k++) {
      let sh = 0, r = 0, b;
      do { b = p.charCodeAt(i++) - 63; r |= (b & 0x1f) << sh; sh += 5; } while (b >= 0x20);
      const d = r & 1 ? ~(r >> 1) : r >> 1;
      if (k === 0) la += d; else lo += d;
    }
    pts.push([la / 1e5, lo / 1e5]);
  }
  return pts;
}
function trazaTrayecto(t) {
  if (t.forma) return t._geo || (t._geo = decodifica(t.forma));
  return t.paradas.map((pid) => PARADAS[pid]).filter(Boolean).map((p) => [p.lat, p.lon]);
}
function setFiltroLinea(codigo) {
  filtroLinea = codigo;
  $("mapa-linea").value = codigo;
  capaRuta.clearLayers();
  const l = codigo && LINEAS[codigo];
  const usadas = new Set(); let pts = [];
  if (l) {
    const col = colorHex(l.color), osc = OSCURO();
    for (const t of Object.values(TRAYECTOS).filter((x) => x.linea === l.id)) {
      const traza = trazaTrayecto(t);
      if (traza.length > 1) {
        L.polyline(traza, { color: osc ? "#0b0b0e" : "#fff", weight: 7, opacity: .75, interactive: false, lineJoin: "round", lineCap: "round", smoothFactor: 0.5 }).addTo(capaRuta);
        L.polyline(traza, { color: col, weight: 4, opacity: .95, interactive: false, lineJoin: "round", lineCap: "round", smoothFactor: 0.5 }).addTo(capaRuta);
        pts = pts.concat(traza);
      }
      t.paradas.forEach((pid) => usadas.add(pid));
    }
    capaParadas.eachLayer((m) => {
      const en = usadas.has(m.options._pid);
      m.setStyle(en ? estiloParada(true, col) : { opacity: 0, fillOpacity: 0 });
      if (en) m.bringToFront();
    });
    if (pts.length) mapa.fitBounds(L.latLngBounds(pts), { padding: [30, 30] });
  } else {
    capaParadas.eachLayer((m) => m.setStyle(estiloParada(false)));
  }
  ajustarDetalle();
  pintarChipsLineas(tickBuses._cuenta || {});
  tickBuses();
}
function seguirme() {
  if (!navigator.geolocation) { toast("Sin ubicación en este dispositivo"); return; }
  navigator.geolocation.getCurrentPosition((pos) => {
    const yo = [pos.coords.latitude, pos.coords.longitude];
    if (!marcaYo) marcaYo = L.marker(yo, { icon: L.divIcon({ className: "", html: `<div class="yo-punto"></div>`, iconSize: [18, 18] }) }).addTo(mapa);
    else marcaYo.setLatLng(yo);
    mapa.setView(yo, 15);
  }, () => toast("No se pudo obtener tu ubicación"), { enableHighAccuracy: true, timeout: 10000 });
}

/* ---------------- tren + bus ---------------- */
const EST_GIJON = { lat: 43.5377, lon: -5.6760, nombre: "Gijón-Sanz Crespo" };
async function pintarTrenBus() {
  const cont = $("tren-bus");
  if (!cont.children.length) cont.innerHTML = `<div class="vacio">Cargando…</div>`;
  let trenHtml = "";
  try {
    const est = await (await fetch("/api/estado")).json();
    if (est && est.trenes) {
      const now = est.ahora;
      const hacia = est.trenes.filter((t) => !t.fin && t.dir < 0 && t.k.includes(0))
        .map((t) => ({ t, lle: t.est_a[t.k.indexOf(0)] }))
        .filter((x) => x.lle != null && x.lle > now - 1).sort((a, b) => a.lle - b.lle).slice(0, 3);
      trenHtml = `<div class="tb-tren"><h3>🚆 Próximas llegadas de la C-4 a Gijón</h3>` +
        (hacia.length ? hacia.map(({ t, lle }) => `<div class="tb-fila"><span class="tb-hora num">${hm(lle)}</span>
          <span style="flex:1">Tren ${t.num} · desde ${esc(nombreCorto(t.origen))}</span>
          ${t.retraso >= 1 ? `<span style="color:var(--warn);font-weight:700">+${Math.round(t.retraso)}</span>` : ""}</div>`).join("")
          : `<div class="sub">No quedan trenes hacia Gijón hoy.</div>`) + `</div>`;
    }
  } catch (e) { trenHtml = `<div class="aviso info" style="margin:0 0 12px"><span>ℹ</span><div>No se pudo leer el tren ahora mismo.</div></div>`; }
  cont.innerHTML = trenHtml + `<h3 style="font-size:14px;margin:4px 0 8px">🚌 Buses en las paradas junto a la estación</h3><div id="tb-paradas"><div class="vacio">Cargando…</div></div>`;
  try {
    const j = await (await fetch(`/api/bus/cercanas?lat=${EST_GIJON.lat}&lon=${EST_GIJON.lon}`)).json();
    await listaConLlegadas("tb-paradas", (j.paradas || []).slice(0, 4), "No hay paradas cerca de la estación.");
  } catch (e) {}
  refrescoLista(() => { if (tab === "tren") pintarTrenBus(); });
}

/* ---------------- navegación ---------------- */
function irA(t) {
  tab = t;
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", b.dataset.tab === t);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "tab-" + t;
  location.hash = t;
  if (t === "favoritos") pintarFav();
  if (t === "lineas") pintarLineas();
  if (t === "tren") pintarTrenBus();
  if (t === "vivo") { iniciarMapa(); setTimeout(() => { if (mapa) { mapa.invalidateSize(); arrancarBuses(); animarBuses(); } }, 60); }
  if (!["cerca", "favoritos", "tren"].includes(t)) clearInterval(listaTimer);
  if (t === "cerca") {
    if (cercanas.length) { pintarCerca(); refrescoLista(() => { if (tab === "cerca") pintarCerca(); }); }
    else {
      const g = leer("pos", null);
      if (g) cargarCerca(g);
      // si ya dio permiso de ubicación antes, se busca sola
      if (navigator.permissions) navigator.permissions.query({ name: "geolocation" }).then((r) => { if (r.state === "granted") localizar(); }).catch(() => {});
    }
  }
}

document.addEventListener("click", (ev) => {
  const fl = ev.target.closest("[data-fl]");
  if (fl) { const c = fl.dataset.fl; setFiltroLinea(c === filtroLinea ? "" : c); return; }
  const vl = ev.target.closest("[data-verlinea]");
  if (vl && !ev.target.closest(".parada")) { cerrarHoja(); $("mapa-linea").value = vl.dataset.verlinea; irA("vivo"); setFiltroLinea(vl.dataset.verlinea); return; }
  const par = ev.target.closest("[data-parada]");
  if (par) { abrirParada(+par.dataset.parada); return; }
  const lin = ev.target.closest("[data-linea]");
  if (lin) { abrirLinea(lin.dataset.linea); return; }
});
$("hoja-fondo").onclick = cerrarHoja;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarHoja(); });
for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => irA(b.dataset.tab);
$("loc").onclick = localizar;
$("q").oninput = pintarBuscar;
$("mapa-linea").onchange = () => setFiltroLinea($("mapa-linea").value);
$("seguir").onclick = seguirme;
document.addEventListener("visibilitychange", () => { if (!document.hidden && tab === "vivo") { tickBuses(); animarBuses(); } });

(async function () {
  await cargarRed();
  indexar();
  estadoChip(!!RED.hay_tiempo_real);
  const h = location.hash.slice(1);
  const mp = /^parada-(\d+)$/.exec(h);        // enlace directo a una parada (desde la app del tren)
  irA(["vivo", "cerca", "buscar", "lineas", "favoritos", "tren"].includes(h) ? h : "vivo");
  if (mp && PARADAS[+mp[1]]) {
    const p = PARADAS[+mp[1]];
    setTimeout(() => { if (mapa) mapa.setView([p.lat, p.lon], 16.5); abrirParada(p.id); }, 300);
  }
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
