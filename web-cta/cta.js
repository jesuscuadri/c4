"use strict";
/* Autobuses del Consorcio de Transportes de Asturias (de momento, urbano de Avilés), con el horario
   oficial. El Consorcio no publica dónde va cada autobús: las horas son las del horario y la posición
   de los buses en el mapa es la que les toca según el horario (y así se dice). */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const RED_ID = new URLSearchParams(location.search).get("red") || "aviles";
const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const hm = (m) => { m = Math.floor(m + 1e-6); return String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(((m % 60) + 60) % 60).padStart(2, "0"); };
const ahoraMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60; };
function leer(k, def) { try { const v = localStorage.getItem("cta." + RED_ID + "." + k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } }
function guardar(k, v) { try { localStorage.setItem("cta." + RED_ID + "." + k, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } }
function toast(t) { const x = $("toast"); x.textContent = t; x.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => { x.hidden = true; }, 2500); }

let D = null, PORPARADA = {}, VIAJES_VAR = {}, mapa = null, capaLineas = null, capaParadas = null, capaBuses = null, marcasBus = {}, filtro = null, tab = "mapa", marcaYo = null;
const linea = (c) => D.lineas[c] || { codigo: c, nombre: "", color: "#666" };
const lb = (c) => `<span class="lb" style="background:${linea(c).color}">${esc(linea(c).codigo)}</span>`;
const nomParada = (id) => (D.paradas[id] || ["?"])[0];

/* ------------------------------------------------------------ datos */
async function cargar() {
  for (let i = 0; ; i++) {
    try {
      const j = await (await fetch("/api/cta/red?red=" + encodeURIComponent(RED_ID))).json();
      if (j.error && !j.cargando) { $("avisos").innerHTML = `<div class="aviso bad"><span>⚠</span><div>${esc(j.error)}</div></div>`; return false; }
      if (!j.cargando) { D = j; break; }
      $("chip-txt").textContent = "Preparando horarios…";
      $("avisos").innerHTML = `<div class="aviso info"><span>ℹ</span><div>Descargando el horario oficial del Consorcio. La primera vez del día tarda unos segundos…${j.error ? " (" + esc(j.error) + ")" : ""}</div></div>`;
    } catch (e) {
      $("chip-txt").textContent = "Despertando el servidor…";
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  PORPARADA = {}; VIAJES_VAR = {};
  D.variantes.forEach((v, vi) => v.paradas.forEach((s, i) => (PORPARADA[s] = PORPARADA[s] || []).push([vi, i])));
  for (const [vi, sal, p] of D.viajes) (VIAJES_VAR[vi] = VIAJES_VAR[vi] || []).push([sal, p]);
  return true;
}

/* Próximos autobuses en una parada (según horario), agrupados por línea y destino */
function proximos(stop, horizonte = 150) {
  const now = ahoraMin(), grupos = {};
  for (const [vi, i] of PORPARADA[stop] || []) {
    const v = D.variantes[vi];
    if (i === v.paradas.length - 1 && v.paradas[0] !== stop) continue;   // aquí termina: no sale
    for (const [sal, p] of VIAJES_VAR[vi] || []) {
      for (const base of [0, -1440]) {            // también los de después de medianoche
        const t = sal + base + D.patrones[p][i];
        if (t < now - 0.5 || t > now + horizonte) continue;
        const k = v.linea + "|" + v.destino;
        (grupos[k] = grupos[k] || { linea: v.linea, destino: v.destino, t: [] }).t.push(t);
      }
    }
  }
  const out = Object.values(grupos);
  for (const g of out) g.t = [...new Set(g.t.map((x) => Math.round(x * 10) / 10))].sort((a, b) => a - b);
  return out.sort((a, b) => a.t[0] - b.t[0]);
}
const enMin = (t) => { const m = Math.floor(t - ahoraMin()); return m <= 0 ? "ya" : m < 60 ? m + " min" : hm(t); };
function llegadasHtml(stop, max = 6, mini = false) {
  const g = proximos(stop).slice(0, max);
  if (!g.length) return `<div class="${mini ? "mini-sin" : "vacio"}">No pasan más autobuses en las próximas horas.</div>`;
  return `<div class="llegadas${mini ? " mini" : ""}">` + g.map((x) => `<div class="lleg${x.t[0] - ahoraMin() < 3 ? " inminente" : ""}" data-lin="${esc(x.linea)}">
      ${lb(x.linea)}<span class="destino">${esc(x.destino)}${x.t.length > 1 ? `<small>luego ${x.t.slice(1, 3).map(hm).join(", ")}</small>` : ""}</span>
      <span class="min">${enMin(x.t[0])}<em>${hm(x.t[0])}</em></span></div>`).join("") + `</div>`;
}

/* ------------------------------------------------------------ hoja de parada */
function favs() { return leer("favs", []); }
function abrirParada(id) {
  const p = D.paradas[id];
  if (!p) return;
  const es = favs().includes(id);
  const lins = [...new Set((PORPARADA[id] || []).map(([vi]) => D.variantes[vi].linea))];
  $("hoja-caja").innerHTML = `<div class="hoja-cab"><h3>${esc(p[0])}<span class="parada-sentido">${esc(p[1])}</span></h3>
      <button class="fav-btn${es ? " on" : ""}" id="h-fav" title="Favorita">${es ? "★" : "☆"}</button>
      <button class="cerrar" id="h-cerrar" aria-label="Cerrar">×</button></div>
    <div class="lineas-badges">${lins.map(lb).join("")}</div>
    <h4 class="tr-tit">Próximos autobuses <span class="horario-tag">según horario</span></h4>
    <div id="h-lleg">${llegadasHtml(id, 10)}</div>
    <button class="pop-btn" id="h-mapa" style="margin-top:12px">🗺️ Ver en el mapa</button>`;
  $("hoja").hidden = false;
  $("h-cerrar").onclick = cerrarHoja;
  $("h-fav").onclick = () => {
    let f = favs();
    f = f.includes(id) ? f.filter((x) => x !== id) : [...f, id];
    guardar("favs", f); abrirParada(id); if (tab === "favoritos") pintarFavs();
  };
  $("h-mapa").onclick = () => { cerrarHoja(); irA("mapa"); setTimeout(() => mapa && mapa.setView([p[2], p[3]], 17), 200); };
  abrirParada._id = id;
}
function cerrarHoja() { $("hoja").hidden = true; abrirParada._id = null; abrirLinea._c = null; }
$("hoja-fondo").onclick = cerrarHoja;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarHoja(); });

/* hoja de línea: sus recorridos y cuándo pasa por cada parada */
function abrirLinea(c, vsel) {
  const vars = D.variantes.map((v, i) => [v, i]).filter(([v]) => v.linea === c);
  if (!vars.length) return;
  // recorridos distintos por destino (el más largo de cada uno)
  const porDest = {};
  for (const [v, i] of vars) if (!porDest[v.destino] || v.paradas.length > D.variantes[porDest[v.destino]].paradas.length) porDest[v.destino] = i;
  const opciones = Object.values(porDest);
  const vi = opciones.includes(vsel) ? vsel : opciones[0];
  const v = D.variantes[vi], now = ahoraMin();
  // próximo viaje de este recorrido en cada parada
  const filas = v.paradas.map((s, i) => {
    let mejor = null;
    for (const [sal, p] of VIAJES_VAR[vi] || []) { const t = sal + D.patrones[p][i]; if (t >= now - 0.5 && (mejor == null || t < mejor)) mejor = t; }
    return `<div class="lp" data-parada="${esc(s)}"><span class="lp-n">${esc(nomParada(s))}<small>${esc((D.paradas[s] || [])[1] || "")}</small></span><span class="lp-h">${mejor != null ? hm(mejor) : "—"}</span></div>`;
  }).join("");
  const salidas = (VIAJES_VAR[vi] || []).map(([s]) => s).filter((s) => s >= now - 0.5).slice(0, 6);
  const L = linea(c);
  $("hoja-caja").innerHTML = `<div class="hoja-cab"><h3>${lb(c)} ${esc(L.nombre)}</h3><button class="cerrar" id="h-cerrar" aria-label="Cerrar">×</button></div>
    <div class="var-sel">${opciones.map((i) => `<button type="button" data-var="${i}" class="${i === vi ? "on" : ""}">→ ${esc(D.variantes[i].destino)}</button>`).join("")}</div>
    <div class="prox-lin">Próximas salidas desde ${esc(nomParada(v.paradas[0]))}: <b>${salidas.length ? salidas.map(hm).join(" · ") : "ninguna más hoy"}</b> <span class="horario-tag">horario</span></div>
    <div class="lin-paradas" style="--c:${L.color}">${filas}</div>
    <button class="pop-btn" id="h-mapa" style="margin-top:12px">🗺️ Ver la línea en el mapa</button>`;
  $("hoja").hidden = false;
  $("h-cerrar").onclick = cerrarHoja;
  for (const b of document.querySelectorAll("[data-var]")) b.onclick = () => abrirLinea(c, +b.dataset.var);
  $("h-mapa").onclick = () => { cerrarHoja(); filtro = c; irA("mapa"); pintarChips(); pintarLineas(true); };
  abrirLinea._c = c;
}
document.addEventListener("click", (ev) => {
  const p = ev.target.closest("[data-parada]");
  if (p) { abrirParada(p.dataset.parada); return; }
  const l = ev.target.closest("[data-linea-card]");
  if (l) abrirLinea(l.dataset.lineaCard);
});

/* ------------------------------------------------------------ mapa */
function iniciarMapa() {
  if (mapa || !window.L) return;
  const pts = Object.values(D.paradas).map((p) => [p[2], p[3]]);
  mapa = L.map("mapa", { zoomControl: false, zoomSnap: 0.25 }).fitBounds(L.latLngBounds(pts), { padding: [20, 20] });
  L.control.zoom({ position: "bottomright" }).addTo(mapa);
  const tono = matchMedia("(prefers-color-scheme: dark)").matches ? "Dark" : "Light";
  const esri = (srv) => `https://server.arcgisonline.com/ArcGIS/rest/services/${srv}/MapServer/tile/{z}/{y}/{x}`;
  L.layerGroup([
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Base`), { maxZoom: 19, maxNativeZoom: 16, attribution: "Mapa &copy; Esri, HERE, Garmin, &copy; OpenStreetMap" }),
    L.tileLayer(esri(`Canvas/World_${tono}_Gray_Reference`), { maxZoom: 19, maxNativeZoom: 16, zIndex: 3 }),
  ]).addTo(mapa);
  mapa.attributionControl.setPrefix(false);
  capaLineas = L.layerGroup().addTo(mapa);
  capaParadas = L.layerGroup();
  capaBuses = L.layerGroup().addTo(mapa);
  pintarLineas(false);
  for (const [id, p] of Object.entries(D.paradas)) {
    L.circleMarker([p[2], p[3]], { radius: 5, color: "#555", weight: 1.5, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(esc(p[0]), { direction: "top", offset: [0, -4] })
      .on("click", () => abrirParada(id)).addTo(capaParadas);
  }
  const ajustar = () => { if (mapa.getZoom() >= 14.5) capaParadas.addTo(mapa); else capaParadas.remove(); };
  mapa.on("zoomend", ajustar); ajustar();
  pintarBuses();
}
function pintarLineas(encuadrar) {
  if (!capaLineas) return;
  capaLineas.clearLayers();
  const vistos = new Set(), pts = [];
  for (const v of D.variantes) {
    if (filtro && v.linea !== filtro) continue;
    const geo = v.forma || v.paradas.map((s) => D.paradas[s]).filter(Boolean).map((p) => [p[2], p[3]]);
    const clave = v.linea + JSON.stringify(geo[0]) + geo.length;
    if (vistos.has(clave)) continue;
    vistos.add(clave);
    L.polyline(geo, { color: linea(v.linea).color, weight: filtro ? 5 : 3.5, opacity: filtro ? 0.95 : 0.6, interactive: false }).addTo(capaLineas);
    pts.push(...geo);
  }
  if (encuadrar && pts.length) mapa.fitBounds(L.latLngBounds(pts), { padding: [30, 30] });
}
/* posición de cada autobús según el horario (entre dos paradas, en proporción al tiempo) */
function busesAhora() {
  const now = ahoraMin(), out = [];
  for (const [vi, sal, p] of D.viajes) {
    for (const base of [0, -1440]) {
      const off = D.patrones[p], dt = now - (sal + base);
      if (dt < 0 || dt > off[off.length - 1]) continue;
      const v = D.variantes[vi];
      let i = 0;
      while (i < off.length - 2 && off[i + 1] <= dt) i++;
      const a = D.paradas[v.paradas[i]], b = D.paradas[v.paradas[i + 1]];
      if (!a || !b) continue;
      const f = off[i + 1] > off[i] ? Math.min(1, Math.max(0, (dt - off[i]) / (off[i + 1] - off[i]))) : 0;
      out.push({ id: vi + "-" + sal, linea: v.linea, destino: v.destino, lat: a[2] + (b[2] - a[2]) * f, lon: a[3] + (b[3] - a[3]) * f,
                 sig: v.paradas[i + 1], llega: sal + base + off[i + 1] });
    }
  }
  return out;
}
function pintarBuses() {
  if (!mapa) return;
  const bs = busesAhora(), vivos = new Set();
  const cuenta = {};
  for (const b of bs) cuenta[b.linea] = (cuenta[b.linea] || 0) + 1;
  pintarChips(cuenta);
  for (const b of bs) {
    if (filtro && b.linea !== filtro) continue;
    vivos.add(b.id);
    let m = marcasBus[b.id];
    const html = `<div class="bm prog sin-rumbo"><div class="bm-l" style="background:${linea(b.linea).color}">${esc(linea(b.linea).codigo)}</div></div>`;
    if (!m) {
      m = marcasBus[b.id] = L.marker([b.lat, b.lon], { icon: L.divIcon({ html, className: "", iconSize: [28, 28], iconAnchor: [14, 14] }), zIndexOffset: 500 })
        .bindPopup("").addTo(capaBuses);
    } else m.setLatLng([b.lat, b.lon]);
    m.setPopupContent(`<div class="pop-bus"><div class="pop-cab">${lb(b.linea)} <b>→ ${esc(b.destino)}</b></div>
      <div class="pop-donde">Próxima parada: <b>${esc(nomParada(b.sig))}</b> (${hm(b.llega)})</div>
      <div class="pop-sub">Posición según el horario: el Consorcio no publica dónde va cada bus.</div>
      <button class="pop-btn" onclick="abrirLinea('${esc(b.linea)}')">Ver la línea</button></div>`);
  }
  for (const id of Object.keys(marcasBus)) if (!vivos.has(id)) { capaBuses.removeLayer(marcasBus[id]); delete marcasBus[id]; }
  $("vivo-cont").innerHTML = `<b>${bs.filter((b) => !filtro || b.linea === filtro).length}</b> buses según horario`;
}
function pintarChips(cuenta) {
  cuenta = cuenta || pintarChips._c || {};
  pintarChips._c = cuenta;
  const total = Object.values(cuenta).reduce((a, b) => a + b, 0);
  $("chips-lineas").innerHTML = `<button class="chip-l todas${filtro ? "" : " on"}" data-fil="">Todas <i>${total}</i></button>` +
    Object.entries(D.lineas).map(([c, l]) => `<button class="chip-l${filtro === c ? " on" : ""}${cuenta[c] ? "" : " vacia"}" style="--c:${l.color}" data-fil="${esc(c)}">${esc(l.codigo)} <i>${cuenta[c] || 0}</i></button>`).join("");
}
$("chips-lineas").addEventListener("click", (e) => {
  const b = e.target.closest("[data-fil]");
  if (!b) return;
  filtro = b.dataset.fil || null;
  pintarLineas(!!filtro);
  if (!filtro && mapa) mapa.fitBounds(L.latLngBounds(Object.values(D.paradas).map((p) => [p[2], p[3]])), { padding: [20, 20] });
  pintarBuses();
});
$("yo").onclick = () => {
  if (!navigator.geolocation) return toast("Este dispositivo no da la ubicación");
  navigator.geolocation.getCurrentPosition((pos) => {
    const yo = [pos.coords.latitude, pos.coords.longitude];
    if (!marcaYo) marcaYo = L.marker(yo, { icon: L.divIcon({ className: "", html: `<div class="yo-punto"></div>`, iconSize: [18, 18] }) }).addTo(mapa);
    else marcaYo.setLatLng(yo);
    mapa.setView(yo, 16);
  }, () => toast("No se pudo obtener tu ubicación"), { enableHighAccuracy: true, timeout: 10000 });
};

/* ------------------------------------------------------------ listas */
function tarjetaParada(id, extra) {
  const p = D.paradas[id];
  const lins = [...new Set((PORPARADA[id] || []).map(([vi]) => D.variantes[vi].linea))];
  return `<div class="parada" data-parada="${esc(id)}"><div class="parada-cab"><span class="parada-nom">${esc(p[0])}<span class="parada-sentido">${esc(p[1])}</span></span>${extra ? `<span class="parada-met">${extra}</span>` : ""}</div>
    <div class="lineas-badges">${lins.map(lb).join("")}</div>${llegadasHtml(id, 3, true)}</div>`;
}
function pintarFavs() {
  const f = favs().filter((id) => D.paradas[id]);
  $("fav-lista").innerHTML = f.length ? f.map((id) => tarjetaParada(id)).join("")
    : `<div class="vacio">Aún no tienes paradas favoritas. Abre una parada y pulsa ☆.</div>`;
}
function pintarLineasLista() {
  $("lineas-tit").textContent = "Líneas de " + D.nombre;
  $("lineas-lista").innerHTML = Object.entries(D.lineas).map(([c, l]) => `<button class="linea-card" data-linea-card="${esc(c)}">
      <span class="linea-code" style="background:${l.color}">${esc(l.codigo)}</span><span class="linea-desc">${esc(l.nombre)}</span></button>`).join("");
}
$("q").addEventListener("input", () => {
  const q = norm($("q").value.trim());
  if (q.length < 2) { $("buscar-lista").innerHTML = ""; return; }
  const ids = Object.entries(D.paradas).filter(([, p]) => norm(p[0] + " " + p[1]).includes(q)).slice(0, 15).map(([id]) => id);
  $("buscar-lista").innerHTML = ids.length ? ids.map((id) => tarjetaParada(id)).join("") : `<div class="vacio">No encontré esa parada.</div>`;
});
$("loc").onclick = () => {
  if (!navigator.geolocation) return toast("Este dispositivo no da la ubicación");
  $("loc").disabled = true; $("loc").textContent = "📍 Buscando…";
  navigator.geolocation.getCurrentPosition((pos) => {
    const la = pos.coords.latitude, lo = pos.coords.longitude;
    const d = (p) => Math.hypot((p[3] - lo) * 80.6, (p[2] - la) * 111.2) * 1000;
    const ids = Object.entries(D.paradas).sort((a, b) => d(a[1]) - d(b[1])).slice(0, 6);
    $("cerca-lista").innerHTML = ids.map(([id, p]) => tarjetaParada(id, Math.round(d(p)) + " m")).join("");
    if (d(ids[0][1]) > 5000) $("cerca-lista").insertAdjacentHTML("afterbegin", `<div class="aviso info"><span>ℹ</span><div>Estás lejos de ${esc(D.nombre)}: estas son sus paradas más cercanas a ti.</div></div>`);
    $("loc").disabled = false; $("loc").textContent = "📍 Actualizar";
  }, () => { $("loc").disabled = false; $("loc").textContent = "📍 Buscar paradas cerca de mí"; toast("No se pudo obtener tu ubicación"); },
  { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
};

/* ------------------------------------------------------------ navegación */
function irA(t) {
  tab = t;
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", b.dataset.tab === t);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "tab-" + t;
  history.replaceState(null, "", location.pathname + location.search + "#" + t);
  if (t === "mapa") { iniciarMapa(); setTimeout(() => mapa && mapa.invalidateSize(), 60); }
  if (t === "favoritos") pintarFavs();
  if (t === "lineas") pintarLineasLista();
}
for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => irA(b.dataset.tab);

(async function inicio() {
  if (!(await cargar())) return;
  document.title = `Autobuses de ${D.nombre} · horarios`;
  $("titulo").textContent = "Autobuses de " + D.nombre;
  $("subtitulo").textContent = (D.operador ? D.operador + " · " : "") + "horario oficial";
  $("pill").style.background = D.color;
  document.documentElement.style.setProperty("--ac", D.color);
  $("chip").className = "chip no";
  $("chip-txt").textContent = "Horario oficial";
  $("avisos").innerHTML = `<div class="aviso info"><span>ℹ</span><div>Horas del <b>horario oficial</b> del Consorcio. Todavía no hay datos abiertos en directo de dónde va cada autobús, así que pueden pasar unos minutos antes o después.</div></div>`;
  $("pie").textContent = `Datos: horario oficial del Consorcio de Transportes de Asturias (GTFS) · ${D.fecha.split("-").reverse().join("/")} · ${Object.keys(D.lineas).length} líneas · ${Object.keys(D.paradas).length} paradas`;
  const h = location.hash.slice(1);
  irA(["mapa", "cerca", "buscar", "lineas", "favoritos"].includes(h) ? h : "mapa");
  setInterval(() => {
    if (document.hidden) return;
    if (tab === "mapa") pintarBuses();
    if (abrirParada._id && !$("hoja").hidden && $("h-lleg")) $("h-lleg").innerHTML = llegadasHtml(abrirParada._id, 10);
  }, 5000);
  setInterval(() => { if (!document.hidden && tab === "favoritos") pintarFavs(); }, 30000);
})();
