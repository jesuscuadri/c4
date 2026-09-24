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
function badge(codigo, color) { return `<span class="lb" style="background:${colorHex(color)}">${esc(codigo)}</span>`; }
function badgesParada(lineas) { return `<div class="lineas-badges">${(lineas || []).map((l) => badge(l.codigo, l.color)).join("")}</div>`; }

/* ================================================================= BUSES EN VIVO */
function bearing(a, b) {
  const toR = Math.PI / 180, y = Math.sin((b[1] - a[1]) * toR) * Math.cos(b[0] * toR);
  const x = Math.cos(a[0] * toR) * Math.sin(b[0] * toR) - Math.sin(a[0] * toR) * Math.cos(b[0] * toR) * Math.cos((b[1] - a[1]) * toR);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}
function iconoBus(v, hdg) {
  return L.divIcon({
    className: "bus-marca", iconSize: [28, 28], iconAnchor: [14, 14],
    html: `<div class="bm"><span class="bm-fl" style="border-bottom-color:${esc(v.color)};transform:translate(-50%,-50%) rotate(${hdg}deg) translateY(-16px)"></span>
      <span class="bm-l" style="background:${esc(v.color)}">${esc(v.linea)}</span></div>`,
  });
}
async function tickBuses() {
  if (!mapa || !capaBuses) return;
  let j;
  try { j = await (await fetch("/api/bus/coordenadas")).json(); }
  catch (e) { return; }
  if (!j || !j.vehiculos) { estadoChip(false); return; }
  const vis = j.vehiculos.filter((v) => !filtroLinea || v.linea === filtroLinea);
  ultBuses = j.vehiculos.length;
  const vivos = new Set();
  for (const v of vis) {
    const key = v.linea + "-" + v.bus, pos = [v.lat, v.lon];
    vivos.add(key);
    let b = busMarks[key];
    if (!b) {
      const m = L.marker(pos, { icon: iconoBus(v, 0), zIndexOffset: 500, keyboard: false }).addTo(capaBuses);
      m.on("click", () => popupBus(v, m));
      b = busMarks[key] = { m, lat: v.lat, lon: v.lon, hdg: 0, v };
    } else {
      const movido = Math.abs(v.lat - b.lat) + Math.abs(v.lon - b.lon);
      if (movido > 1e-5) b.hdg = bearing([b.lat, b.lon], pos);
      b.lat = v.lat; b.lon = v.lon; b.v = v;
      b.m.setLatLng(pos);
      const fl = b.m._icon && b.m._icon.querySelector(".bm-fl");
      if (fl) fl.style.transform = `translate(-50%,-50%) rotate(${b.hdg}deg) translateY(-16px)`;
      if (b.m._popup && b.m._popup.isOpen()) b.m.setPopupContent(htmlPopupBus(v));
    }
  }
  for (const key in busMarks) if (!vivos.has(key)) { capaBuses.removeLayer(busMarks[key].m); delete busMarks[key]; }
  estadoChip(true);
  const n = vis.length;
  $("vivo-cont").innerHTML = `<b>${n}</b> ${filtroLinea ? "de la línea " + esc(filtroLinea) : "autobuses"} en circulación`;
  $("mapa-pie").textContent = `Actualizado ${new Date((j.ts || Date.now() / 1000) * 1000).toLocaleTimeString("es-ES")} · se refresca solo cada pocos segundos`;
}
function htmlPopupBus(v) {
  return `<div class="pop-bus"><div class="pop-cab">${badge(v.linea, v.color)}<b>${esc(v.nombre_linea || "")}</b></div>
    <div class="pop-fila">→ ${esc(v.destino || "")}</div>
    <div class="pop-sub">Bus nº ${esc(v.bus)} · procede de ${esc(v.origen || "—")}</div>
    <button class="pop-btn" data-verlinea="${esc(v.linea)}">Ver recorrido de la línea</button></div>`;
}
function popupBus(v, m) { m.bindPopup(htmlPopupBus(v), { className: "bus-pop" }).openPopup(); }

function estadoChip(ok) {
  const c = $("chip");
  if (ok) { c.className = "chip ok"; $("chip-txt").textContent = ultBuses ? `${ultBuses} buses en vivo` : "Tiempo real"; }
  else { c.className = "chip no"; $("chip-txt").textContent = "Sin tiempo real"; }
}
function arrancarBuses() {
  clearInterval(busTimer);
  tickBuses();
  busTimer = setInterval(() => { if (!document.hidden && tab === "vivo") tickBuses(); }, 7000);
}

/* ---------------- ficha de parada (llegadas) ---------------- */
let hojaParada = null, hojaTimer = null;
function abrirParada(id) {
  hojaParada = id; $("hoja").hidden = false; pintarHoja(true);
  clearInterval(hojaTimer); hojaTimer = setInterval(() => pintarHoja(false), 15000);
}
function cerrarHoja() { $("hoja").hidden = true; hojaParada = null; clearInterval(hojaTimer); }
async function pintarHoja(reset) {
  const id = hojaParada, p = PARADAS[id];
  if (!p) return;
  const esFav = favs().includes(id);
  if (reset) {
    $("hoja-caja").innerHTML =
      `<div class="hoja-cab"><h3>${esc(p.nombre)}</h3>
        <button class="fav-btn ${esFav ? "on" : ""}" id="hoja-fav" title="Favorita">${esFav ? "★" : "☆"}</button>
        <button class="cerrar" id="hoja-x" aria-label="Cerrar">×</button></div>
       ${badgesParada(POR_PARADA[id])}
       <div id="hoja-cont"><div class="vacio">Cargando llegadas…</div></div>`;
    $("hoja-x").onclick = cerrarHoja;
    $("hoja-fav").onclick = () => { alternarFav(id); pintarHoja(true); if (tab === "favoritos") pintarFav(); };
  }
  try {
    const j = await (await fetch("/api/bus/parada/" + id)).json();
    const cont = $("hoja-cont"); if (!cont) return;
    if (j.error) {
      cont.innerHTML = `<div class="aviso warn" style="margin:0"><span>⚠</span><div>No hay llegadas en directo ahora mismo.</div></div>`;
      return;
    }
    if (!j.llegadas || !j.llegadas.length) { cont.innerHTML = `<div class="vacio">No hay buses próximos ahora mismo.</div>`; return; }
    cont.innerHTML = `<div class="llegadas">` + j.llegadas.map((x) => {
      const min = x.minutos, inm = min != null && min <= 1;
      return `<div class="lleg ${inm ? "inminente" : ""}">${badge(x.linea, x.color)}
        <span class="destino">${esc(x.destino)}</span>
        <span class="met">${x.distancia != null && x.distancia >= 0 ? Math.round(x.distancia) + " m" : ""}</span>
        <span class="min">${min == null ? "--" : inm ? "ya" : min}<small>${min == null || inm ? "" : " min"}</small></span></div>`;
    }).join("") + `</div><p class="sub" style="margin:10px 0 0">Actualizado ${esc(j.llegadas[0].actualizado || "")}. Se refresca solo.</p>`;
  } catch (e) { const c = $("hoja-cont"); if (c) c.innerHTML = `<div class="vacio">No se pudieron cargar las llegadas.</div>`; }
}
function alternarFav(id) {
  let f = favs();
  f = f.includes(id) ? f.filter((x) => x !== id) : [...f, id];
  guardar("favs", f);
  toast(f.includes(id) ? "Parada guardada" : "Parada quitada");
}

/* ---------------- tarjeta de parada ---------------- */
function tarjetaParada(p, extra) {
  return `<div class="parada" data-parada="${p.id}">
    <div class="parada-cab"><span class="parada-nom">${esc(p.nombre)}</span>
      ${extra || (p.metros != null ? `<span class="parada-met">${p.metros} m</span>` : "")}</div>
    ${badgesParada(POR_PARADA[p.id] || p.lineas)}</div>`;
}

/* ---------------- cerca de mí ---------------- */
function pintarCerca(paradas) {
  $("cerca-lista").innerHTML = paradas.length ? paradas.map((p) => tarjetaParada(p)).join("") : `<div class="vacio">No hay paradas cerca.</div>`;
}
function localizar() {
  const b = $("loc");
  if (!navigator.geolocation) { toast("Sin ubicación en este dispositivo"); return; }
  b.disabled = true; b.textContent = "📍 Buscando tu posición…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    ultimaPos = [pos.coords.latitude, pos.coords.longitude]; guardar("pos", ultimaPos);
    try { const j = await (await fetch(`/api/bus/cercanas?lat=${ultimaPos[0]}&lon=${ultimaPos[1]}`)).json(); pintarCerca(j.paradas || []); }
    catch (e) { toast("No se pudieron cargar las paradas"); }
    b.disabled = false; b.textContent = "📍 Actualizar mi posición";
  }, () => { b.disabled = false; b.textContent = "📍 Buscar paradas cerca de mí"; toast("No se pudo obtener tu ubicación"); },
    { enableHighAccuracy: true, timeout: 10000 });
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
  const f = favs();
  $("fav-lista").innerHTML = f.length ? f.map((id) => PARADAS[id] ? tarjetaParada(PARADAS[id]) : "").join("")
    : `<div class="vacio">Aún no has guardado paradas. Abre una parada y pulsa la estrella.</div>`;
}

/* ---------------- líneas ---------------- */
function pintarLineas() {
  $("lineas-lista").innerHTML = RED.lineas.map((l) =>
    `<button class="linea-card" data-linea="${esc(l.codigo)}">
      <span class="linea-code" style="background:${colorHex(l.color)}">${esc(l.codigo)}</span>
      <span class="linea-desc">${esc(l.nombre)}</span></button>`).join("");
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
function iniciarMapa() {
  if (mapa || !window.L) return;
  mapa = L.map("mapa", { zoomControl: true, zoomSnap: 0.25 }).setView([43.5357, -5.6615], 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, className: "base-osm",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(mapa);
  capaRuta = L.layerGroup().addTo(mapa);
  capaParadas = L.layerGroup().addTo(mapa);
  capaBuses = L.layerGroup().addTo(mapa);
  for (const p of RED.paradas)
    L.marker([p.lat, p.lon], { _pid: p.id, icon: L.divIcon({ className: "", html: `<div class="parada-punto"></div>`, iconSize: [10, 10] }) })
      .bindTooltip(p.nombre, { className: "bus-tt" }).on("click", () => abrirParada(p.id)).addTo(capaParadas);
  const cont = mapa.getContainer();
  mapa.on("zoomstart movestart", () => cont.classList.add("sin-anim"));
  mapa.on("zoomend moveend", () => setTimeout(() => cont.classList.remove("sin-anim"), 60));
  $("mapa-linea").innerHTML = `<option value="">Todas las líneas</option>` +
    RED.lineas.map((l) => `<option value="${esc(l.codigo)}">Línea ${esc(l.codigo)} · ${esc(l.nombre).slice(0, 28)}</option>`).join("");
  arrancarBuses();
}
function trazaTrayecto(t) { return t.paradas.map((pid) => PARADAS[pid]).filter(Boolean).map((p) => [p.lat, p.lon]); }
function setFiltroLinea(codigo) {
  filtroLinea = codigo;
  capaRuta.clearLayers();
  if (!codigo) {
    capaParadas.eachLayer((m) => m.setOpacity(1));
    tickBuses();
    return;
  }
  const l = LINEAS[codigo]; if (!l) return;
  const trs = Object.values(TRAYECTOS).filter((t) => t.linea === l.id);
  const usadas = new Set(); let pts = [];
  for (const t of trs) {
    const traza = trazaTrayecto(t);
    if (traza.length > 1) { L.polyline(traza, { color: colorHex(l.color), weight: 5, opacity: .85 }).addTo(capaRuta); pts = pts.concat(traza); }
    t.paradas.forEach((pid) => usadas.add(pid));
  }
  capaParadas.eachLayer((m) => m.setOpacity(usadas.has(m.options._pid) ? 1 : .2));
  if (pts.length) mapa.fitBounds(L.latLngBounds(pts), { padding: [30, 30] });
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
  cont.innerHTML = `<div class="vacio">Cargando…</div>`;
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
  let busHtml = "";
  try {
    const j = await (await fetch(`/api/bus/cercanas?lat=${EST_GIJON.lat}&lon=${EST_GIJON.lon}`)).json();
    busHtml = `<h3 style="font-size:14px;margin:4px 0 8px">🚌 Paradas junto a la estación</h3>` +
      (j.paradas || []).slice(0, 4).map((p) => tarjetaParada(p, `<span class="parada-met">${p.metros} m</span>`)).join("");
  } catch (e) {}
  cont.innerHTML = trenHtml + busHtml + `<p class="sub" style="margin-top:10px">Toca una parada para ver los minutos exactos de cada bus.</p>`;
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
  if (t === "vivo") { iniciarMapa(); setTimeout(() => { if (mapa) { mapa.invalidateSize(); arrancarBuses(); } }, 60); }
  if (t === "cerca" && !$("cerca-lista").children.length) {
    const g = leer("pos", null);
    if (g) fetch(`/api/bus/cercanas?lat=${g[0]}&lon=${g[1]}`).then((r) => r.json()).then((j) => pintarCerca(j.paradas || [])).catch(() => {});
  }
}

document.addEventListener("click", (ev) => {
  const vl = ev.target.closest("[data-verlinea]");
  if (vl) { $("mapa-linea").value = vl.dataset.verlinea; irA("vivo"); setFiltroLinea(vl.dataset.verlinea); return; }
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
document.addEventListener("visibilitychange", () => { if (!document.hidden && tab === "vivo") tickBuses(); });

(async function () {
  await cargarRed();
  indexar();
  estadoChip(!!RED.hay_tiempo_real);
  const h = location.hash.slice(1);
  irA(["vivo", "cerca", "buscar", "lineas", "favoritos", "tren"].includes(h) ? h : "vivo");
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
