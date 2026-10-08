"use strict";
/* Autobuses del Consorcio de Transportes de Asturias (urbanos de Avilés, Oviedo y Mieres, e interurbanos), con el horario
   oficial. El Consorcio no publica dónde va cada autobús: las horas son las del horario y la posición
   de los buses en el mapa es la que les toca según el horario (y así se dice). */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const RED_ID = new URLSearchParams(location.search).get("red") || "aviles";
const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const hm = (m) => { m = Math.floor(m + 1e-6); return String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(((m % 60) + 60) % 60).padStart(2, "0"); };
const ahoraMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60; };
const ahoraViaje = () => (DIA ? -1 : ahoraMin());     // mañana: desde las 00:00
function leer(k, def) { try { const v = localStorage.getItem("cta." + RED_ID + "." + k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } }
function guardar(k, v) { try { localStorage.setItem("cta." + RED_ID + "." + k, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } }
function toast(t) { const x = $("toast"); x.textContent = t; x.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => { x.hidden = true; }, 2500); }

let D = null, INTER = false, LOCS = [], FORMAS = {}, PORPARADA = {}, VIAJES_VAR = {}, mapa = null, capaLineas = null, capaParadas = null, capaBuses = null, marcasBus = {}, filtro = null, tab = "mapa", marcaYo = null;
const linea = (c) => D.lineas[c] || { codigo: c, nombre: "", color: "#666" };
const lb = (c) => `<span class="lb" style="background:${linea(c).color}">${esc(linea(c).codigo)}</span>`;
const nomParada = (id) => (D.paradas[id] || ["?"])[0];
/* en los interurbanos, hay paradas donde solo se baja (o se sube) y tramos que no se pueden hacer */
const puedeSubir = (v, i) => i < v.paradas.length - 1 && (!v.perm || !!v.perm[i]);
const puedeBajar = (v, i, j) => j > i && (!v.perm || (v.perm[i] && j >= v.perm[i][0] && j <= v.perm[i][1]));
const viaDe = (c) => { const l = linea(c); return INTER ? l.nombre + (l.via ? " · " + l.via : "") : ""; };

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
  INTER = D.tipo === "interurbano";
  HOY = { D, ...indexar(D) };
  usar(HOY);
  return true;
}
/* los índices que se calculan de un horario (también el de mañana) */
function indexar(D) {
  const PORPARADA = {}, VIAJES_VAR = {};
  D.variantes.forEach((v, vi) => v.paradas.forEach((s, i) => (PORPARADA[s] = PORPARADA[s] || []).push([vi, i])));
  for (const [vi, sal, p] of D.viajes) (VIAJES_VAR[vi] = VIAJES_VAR[vi] || []).push([sal, p]);
  // pueblos y ciudades (para buscar «de Oviedo a Llanes»), los de más paradas primero
  const locs = {};
  for (const [id, p] of Object.entries(D.paradas)) if (p[1]) (locs[p[1]] = locs[p[1]] || []).push(id);
  const alias = D.alias || {};     // «Langreo» también se encuentra como «Llangréu»
  const LOCS = Object.entries(locs).map(([nombre, ids]) => ({ nombre, ids, alias: alias[nombre] || "", n: norm(nombre + " " + (alias[nombre] || "")) }))
    .sort((a, b) => b.ids.length - a.ids.length);
  return { PORPARADA, VIAJES_VAR, LOCS };
}
function usar(b) { D = b.D; PORPARADA = b.PORPARADA; VIAJES_VAR = b.VIAJES_VAR; LOCS = b.LOCS; }
/* Hoy / Mañana (solo en la pestaña Viaje): mañana trae su propio horario */
let HOY = null, MANANA = null, DIA = 0;
async function setDia(d) {
  if (d === DIA) return true;
  if (d === 1 && !MANANA) {
    $("iv-res").innerHTML = `<div class="vacio">Preparando el horario de mañana…<br><small>La primera vez puede tardar un minuto; luego es instantáneo.</small></div>`;
    for (let i = 0; i < 90; i++) {
      try {
        const j = await (await fetch("/api/cta/red?red=" + encodeURIComponent(RED_ID) + "&dia=manana")).json();
        if (j.error && !j.cargando) { toast("No hay horario de mañana todavía"); return false; }
        if (!j.cargando) { MANANA = { D: j, ...indexar(j) }; break; }
      } catch (e) { /* reintenta */ }
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (!MANANA) { toast("No se pudo cargar el horario de mañana"); return false; }
  }
  DIA = d;
  usar(d ? MANANA : HOY);
  document.querySelectorAll("[data-dia]").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.dia === d));
  return true;
}

/* Próximos autobuses en una parada (según horario), agrupados por línea y destino */
function proximos(stop, horizonte = 150) {
  const now = ahoraMin(), grupos = {};
  for (const [vi, i] of PORPARADA[stop] || []) {
    const v = D.variantes[vi];
    if (i === v.paradas.length - 1 && v.paradas[0] !== stop) continue;   // aquí termina: no sale
    if (v.perm && !v.perm[i]) continue;                                    // aquí solo se baja
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
      ${lb(x.linea)}<span class="destino">${esc(x.destino)}${INTER ? `<small class="via">${esc(viaDe(x.linea))}</small>` : ""}${x.t.length > 1 ? `<small>luego ${x.t.slice(1, 3).map(hm).join(", ")}</small>` : ""}</span>
      <span class="min">${enMin(x.t[0])}<em>${hm(x.t[0])}</em></span></div>`).join("") + `</div>`;
}

/* ------------------------------------------------------------ hoja de parada */
function favs() { return leer("favs", []); }
function abrirParada(id) {
  const p = D.paradas[id];
  if (!p) return;
  const es = favs().includes(id);
  const lins = badges(id);
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
  $("hoja-caja").innerHTML = `<div class="hoja-cab"><h3>${lb(c)} ${esc(L.nombre)}${L.via ? ` <span class="via-l">${esc(L.via)}</span>` : ""}</h3><button class="cerrar" id="h-cerrar" aria-label="Cerrar">×</button></div>
    ${INTER && L.operador ? `<div class="prox-lin">${esc(L.operador)}</div>` : ""}
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
  if (p) { if (DIA && p.closest(".hoja")) return; abrirParada(p.dataset.parada); return; }   // las horas de una parada son de hoy
  const l = ev.target.closest("[data-linea-card]");
  if (l) abrirLinea(l.dataset.lineaCard);
});

/* ------------------------------------------------------------ mapa */
function iniciarMapa() {
  if (mapa || !window.L) return;
  const pts = Object.values(D.paradas).map((p) => [p[2], p[3]]);
  mapa = L.map("mapa", { zoomControl: false, zoomSnap: 0.25, preferCanvas: INTER }).fitBounds(L.latLngBounds(pts), { padding: [20, 20] });
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
  if (INTER) {
    // cientos de líneas: solo se dibuja la elegida, y su recorrido se pide al abrirla
    if (!filtro) return;
    if (!FORMAS[filtro]) {
      const c = filtro;
      FORMAS[c] = "pidiendo";
      fetch("/api/cta/formas?red=" + encodeURIComponent(RED_ID) + "&linea=" + encodeURIComponent(c)).then((r) => r.json()).then((j) => {
        FORMAS[c] = j.formas || {};
        for (const [i, f] of Object.entries(FORMAS[c])) if (D.variantes[i]) D.variantes[i].forma = f;
        if (filtro === c) pintarLineas(encuadrar);
      }).catch(() => { FORMAS[c] = null; });
    }
  }
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
    m.setPopupContent(`<div class="pop-bus"><div class="pop-cab">${lb(b.linea)} <b>→ ${esc(b.destino)}</b></div>${INTER ? `<div class="pop-sub">${esc(viaDe(b.linea))}</div>` : ""}
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
  if (INTER) {     // demasiadas líneas para un botón cada una: la elegida (desde «Líneas» o un bus)
    const l = filtro && linea(filtro);
    $("chips-lineas").innerHTML = `<button class="chip-l todas${filtro ? "" : " on"}" data-fil="">Todas <i>${total}</i></button>` +
      (filtro ? `<button class="chip-l on" style="--c:${l.color}" data-fil="${esc(filtro)}">${esc(l.codigo)} ${esc(l.nombre)} <i>${cuenta[filtro] || 0}</i></button>` : "");
    return;
  }
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
/* las líneas de una parada (en los interurbanos, una por empresa: «ALSA» repetido no dice nada) */
function badges(id) {
  const lins = [...new Set((PORPARADA[id] || []).map(([vi]) => D.variantes[vi].linea))];
  if (!INTER) return lins;
  const vistos = new Set();
  return lins.filter((c) => !vistos.has(linea(c).codigo) && vistos.add(linea(c).codigo));
}
function tarjetaParada(id, extra) {
  const p = D.paradas[id];
  const lins = badges(id);
  return `<div class="parada" data-parada="${esc(id)}"><div class="parada-cab"><span class="parada-nom">${esc(p[0])}<span class="parada-sentido">${esc(p[1])}</span></span>${extra ? `<span class="parada-met">${extra}</span>` : ""}</div>
    <div class="lineas-badges">${lins.map(lb).join("")}</div>${llegadasHtml(id, 3, true)}</div>`;
}
function pintarFavs() {
  const f = favs().filter((id) => D.paradas[id]);
  $("fav-lista").innerHTML = f.length ? f.map((id) => tarjetaParada(id)).join("")
    : `<div class="vacio">Aún no tienes paradas favoritas. Abre una parada y pulsa ☆.</div>`;
}
function tarjetaLinea(c, l) {
  return `<button class="linea-card" data-linea-card="${esc(c)}">
      <span class="linea-code" style="background:${l.color}">${esc(l.codigo)}</span><span class="linea-desc">${esc(l.nombre)}${l.via ? `<small>${esc(l.via)}</small>` : ""}</span></button>`;
}
function pintarLineasLista() {
  $("lineas-tit").textContent = INTER ? "Líneas interurbanas" : "Líneas de " + D.nombre;
  if (!INTER) { $("lineas-lista").innerHTML = Object.entries(D.lineas).map(([c, l]) => tarjetaLinea(c, l)).join(""); return; }
  $("lin-buscar").hidden = false;
  const q = norm($("lin-q").value.trim());
  const lins = Object.entries(D.lineas).filter(([, l]) => !q || norm(l.nombre + " " + (l.via || "") + " " + l.codigo + " " + (l.operador || "")).includes(q));
  const porOp = {};
  for (const [c, l] of lins) (porOp[l.operador || "Otras"] = porOp[l.operador || "Otras"] || []).push([c, l]);
  const ops = Object.entries(porOp).sort((a, b) => b[1].length - a[1].length);
  $("lineas-lista").className = "";
  $("lineas-lista").innerHTML = lins.length ? ops.map(([op, ls]) => `<div class="op-tit">${esc(op)} · ${ls.length}</div>
      <div class="lineas-grid">${ls.slice(0, q ? 200 : 60).map(([c, l]) => tarjetaLinea(c, l)).join("")}</div>${!q && ls.length > 60 ? `<div class="vacio">…y ${ls.length - 60} más: busca por pueblo o ciudad.</div>` : ""}`).join("")
    : `<div class="vacio">Ninguna línea pasa por ahí.</div>`;
}
$("lin-q").addEventListener("input", () => pintarLineasLista());
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

/* ------------------------------------------------------------ viaje entre dos sitios (interurbanos) */
const IV = { de: null, a: null, campo: null, mas: 12 };
function sugerencias(q) {
  q = norm(q.trim());
  if (q.length < 2) return [];
  const empieza = (t) => t.startsWith(q) || t.includes(" " + q);
  const locs = LOCS.filter((l) => empieza(l.n)).slice(0, 6).map((l) => ({ tipo: "loc", nombre: l.nombre, ids: l.ids, txt: (l.alias ? l.alias + " · " : "") + l.ids.length + (l.ids.length === 1 ? " parada" : " paradas") }));
  const pars = Object.entries(D.paradas).filter(([, p]) => empieza(norm(p[0]))).slice(0, 8 - locs.length)
    .map(([id, p]) => ({ tipo: "parada", nombre: p[0] + (p[1] ? " (" + p[1] + ")" : ""), ids: [id], txt: "parada" }));
  return [...locs, ...pars];
}
function pintarSug() {
  const campo = IV.campo, inp = campo && $(campo === "de" ? "iv-de" : "iv-a");
  const lista = inp ? sugerencias(inp.value) : [];
  $("iv-sug").hidden = !lista.length;
  IV.sug = lista;
  $("iv-sug").innerHTML = lista.map((x, k) => `<button type="button" data-sug="${k}"><span class="s-ic">${x.tipo === "loc" ? "🏘️" : "🚏"}</span><span class="s-n">${esc(x.nombre)}<small>${esc(x.txt)}</small></span></button>`).join("");
}
function elegir(campo, x) {
  IV[campo] = x;
  $(campo === "de" ? "iv-de" : "iv-a").value = x.nombre;
  $("iv-sug").hidden = true; IV.campo = null;
  guardar("iv", { de: IV.de, a: IV.a });
  if (campo === "de" && !IV.a) $("iv-a").focus();
  else buscarViaje();
}
for (const c of ["de", "a"]) {
  const inp = $(c === "de" ? "iv-de" : "iv-a");
  inp.addEventListener("focus", () => { IV.campo = c; inp.select(); pintarSug(); });
  inp.addEventListener("input", () => { IV.campo = c; IV[c] = null; pintarSug(); });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && IV.sug && IV.sug.length) { e.preventDefault(); elegir(c, IV.sug[0]); } });
}
$("iv-sug").addEventListener("mousedown", (e) => e.preventDefault());     // que no se pierda el foco antes del clic
$("iv-sug").addEventListener("click", (e) => { const b = e.target.closest("[data-sug]"); if (b && IV.campo) elegir(IV.campo, IV.sug[+b.dataset.sug]); });
document.addEventListener("click", (e) => { if (!e.target.closest(".iv-campos,#iv-sug")) $("iv-sug").hidden = true; });
$("iv-cambiar").onclick = () => {
  [IV.de, IV.a] = [IV.a, IV.de];
  $("iv-de").value = IV.de ? IV.de.nombre : ""; $("iv-a").value = IV.a ? IV.a.nombre : "";
  guardar("iv", { de: IV.de, a: IV.a }); buscarViaje();
};
$("iv-yo").onclick = () => {
  if (!navigator.geolocation) return toast("Este dispositivo no da la ubicación");
  $("iv-yo").textContent = "📍 Buscando dónde estás…";
  navigator.geolocation.getCurrentPosition((pos) => {
    const la = pos.coords.latitude, lo = pos.coords.longitude;
    const d = (p) => Math.hypot((p[3] - lo) * 80.6, (p[2] - la) * 111.2) * 1000;
    const cerca = Object.entries(D.paradas).map(([id, p]) => [id, d(p)]).sort((a, b) => a[1] - b[1]);
    const ids = cerca.filter(([, m], k) => m < 900 || k < 3).slice(0, 12).map(([id]) => id);
    $("iv-yo").textContent = "📍 Salir desde donde estoy";
    if (cerca[0][1] > 5000) toast("No hay paradas cerca de ti: uso las más próximas");
    elegir("de", { tipo: "yo", nombre: "📍 Cerca de mí (" + nomParada(ids[0]) + ")", ids });
  }, () => { $("iv-yo").textContent = "📍 Salir desde donde estoy"; toast("No se pudo obtener tu ubicación"); },
  { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
};
/* Todos los autobuses de hoy que van de un sitio a otro: en cada uno, se sube en la primera parada
   del origen donde se puede subir y se baja en la última del destino donde se puede bajar */
function viajesEntre(de, a) {
  const O = new Set(de.ids), A = new Set(a.ids), now = ahoraViaje(), out = [];
  D.variantes.forEach((v, vi) => {
    let par = null;
    for (let i = 0; i < v.paradas.length && !par; i++) {
      if (!O.has(v.paradas[i]) || !puedeSubir(v, i)) continue;
      for (let j = v.paradas.length - 1; j > i; j--) if (A.has(v.paradas[j]) && puedeBajar(v, i, j)) { par = [i, j]; break; }
    }
    if (!par) return;
    for (const [sal, p] of VIAJES_VAR[vi] || []) for (const base of DIA ? [0] : [0, -1440]) {
      const off = D.patrones[p], ts = sal + base + off[par[0]], tl = sal + base + off[par[1]];
      if (ts < now - 30 || ts > now + 1440) continue;
      out.push({ vi, sal: sal + base, p, i: par[0], j: par[1], ts, tl });
    }
  });
  return out.sort((x, y) => x.ts - y.ts || x.tl - y.tl);
}
const durTxt = (m) => { m = Math.round(m); return m < 60 ? m + " min" : Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : ""); };
function buscarViaje() {
  const box = $("iv-res");
  if (!IV.de || !IV.a) { box.innerHTML = IV.de || IV.a ? "" : `<div class="vacio">Escribe de dónde sales y a dónde vas: un pueblo, una ciudad o una parada.</div>`; return; }
  const now = ahoraViaje(), todos = viajesEntre(IV.de, IV.a), dm = DIA ? "mañana" : "hoy";
  IV.res = todos;
  const prox = todos.filter((r) => r.ts >= now - 0.5);
  if (!todos.length) { box.innerHTML = `<div class="vacio">${DIA ? "Mañana" : "Hoy"} no hay autobuses directos de ${esc(IV.de.nombre)} a ${esc(IV.a.nombre)}.<br>Prueba con el pueblo de al lado o con una parada concreta.</div>`; return; }
  const ult = DIA ? [] : todos.filter((r) => r.ts < now - 0.5).slice(-1);
  const lista = [...ult, ...prox.slice(0, IV.mas)];
  box.innerHTML = `<div class="iv-res-cab"><span>${prox.length ? `<b>${prox.length}</b> ${prox.length === 1 ? "autobús" : "autobuses"} ${DIA ? "mañana" : "más hoy"}` : "No quedan más autobuses hoy"}</span><span class="horario-tag">horario oficial</span></div>` +
    lista.map((r) => {
      const v = D.variantes[r.vi], k = todos.indexOf(r), falta = r.ts - now;
      return `<button type="button" class="iv-viaje${falta < -0.5 ? " pasado" : ""}" data-iv="${k}">
        <span class="iv-h"><b>${hm(r.ts)}</b><i>→</i>${hm(r.tl)}<small class="iv-dur"> · ${durTxt(r.tl - r.ts)}</small></span><span class="iv-en">${falta < -0.5 ? "ya salió" : DIA ? "mañana" : enMin(r.ts)}</span>
        <span class="iv-lin">${lb(v.linea)}<span>${esc(viaDe(v.linea))}</span></span>
        <span class="iv-par">${esc(nomParada(v.paradas[r.i]))} → ${esc(nomParada(v.paradas[r.j]))}</span></button>`;
    }).join("") + (prox.length > IV.mas ? `<button type="button" class="iv-mas" id="iv-mas">Ver más autobuses</button>` : "");
  if ($("iv-mas")) $("iv-mas").onclick = () => { IV.mas += 20; buscarViaje(); };
}
$("iv-res").addEventListener("click", (e) => { const b = e.target.closest("[data-iv]"); if (b) abrirViaje(IV.res[+b.dataset.iv]); });
/* hoja de un autobús concreto: todas sus paradas, con el tramo del viaje resaltado */
function abrirViaje(r) {
  const v = D.variantes[r.vi], off = D.patrones[r.p], L = linea(v.linea);
  const filas = v.paradas.map((s, k) => {
    const cls = k < r.i || k > r.j ? " fuera" : " tramo" + (k === r.i ? " sube" : k === r.j ? " baja" : "");
    const loc = (D.paradas[s] || [])[1] || "";
    return `<div class="lp${cls}" data-parada="${esc(s)}"><span class="lp-n">${esc(nomParada(s))}<small>${esc(loc)}</small></span><span class="lp-h">${hm(r.sal + off[k])}</span></div>`;
  }).join("");
  $("hoja-caja").innerHTML = `<div class="hoja-cab"><h3>${lb(v.linea)} → ${esc(v.destino)}</h3><button class="cerrar" id="h-cerrar" aria-label="Cerrar">×</button></div>
    <div class="prox-lin">${esc(viaDe(v.linea))}${L.operador ? " · " + esc(L.operador) : ""} <span class="horario-tag">horario</span></div>
    <div class="prox-lin">Subes a las <b>${hm(r.ts)}</b> en ${esc(nomParada(v.paradas[r.i]))} y bajas a las <b>${hm(r.tl)}</b> en ${esc(nomParada(v.paradas[r.j]))} (${durTxt(r.tl - r.ts)}).</div>
    <div class="lin-paradas" style="--c:${L.color}">${filas}</div>
    <button class="pop-btn" id="h-mapa" style="margin-top:12px">🗺️ Ver la línea en el mapa</button>`;
  $("hoja").hidden = false;
  $("h-cerrar").onclick = cerrarHoja;
  $("h-mapa").onclick = () => { cerrarHoja(); filtro = v.linea; irA("mapa"); pintarChips(); pintarLineas(true); };
  const t = document.querySelector(".lp.sube");
  if (t) setTimeout(() => t.scrollIntoView({ block: "center" }), 50);
}

/* ------------------------------------------------------------ navegación */
async function irA(t) {
  if (t !== "viaje" && DIA) await setDia(0);
  tab = t;
  for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", b.dataset.tab === t);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "tab-" + t;
  history.replaceState(null, "", location.pathname + location.search + "#" + t);
  if (t === "mapa") { iniciarMapa(); setTimeout(() => mapa && mapa.invalidateSize(), 60); }
  if (t === "favoritos") pintarFavs();
  if (t === "lineas") pintarLineasLista();
  if (t === "viaje") buscarViaje();
}
for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => irA(b.dataset.tab);

(async function inicio() {
  if (!(await cargar())) return;
  const titulo = D.titulo || "Autobuses de " + D.nombre;
  document.title = titulo + " · horarios";
  $("titulo").textContent = titulo;
  $("subtitulo").textContent = (D.operador ? D.operador + " · " : "") + "horario oficial";
  $("pill").style.background = D.color;
  document.documentElement.style.setProperty("--ac-red", D.color);
  $("chip").className = "chip no";
  $("chip-txt").textContent = "Horario oficial";
  $("avisos").innerHTML = `<div class="aviso info"><span>ℹ</span><div>Horas del <b>horario oficial</b> del Consorcio. Todavía no hay datos abiertos en directo de dónde va cada autobús, así que pueden pasar unos minutos antes o después.</div></div>`;
  $("pie").textContent = `Datos: horario oficial del Consorcio de Transportes de Asturias (GTFS) · ${D.fecha.split("-").reverse().join("/")} · ${Object.keys(D.lineas).length} líneas · ${Object.keys(D.paradas).length} paradas`;
  if (INTER) {
    $("tab-btn-viaje").hidden = false;
    $("q").placeholder = "Nombre de la parada o del pueblo";
    const g = leer("iv", null);
    if (g) { IV.de = g.de; IV.a = g.a; $("iv-de").value = g.de ? g.de.nombre : ""; $("iv-a").value = g.a ? g.a.nombre : ""; }
    if (IV.de && IV.de.tipo === "yo") { IV.de = null; $("iv-de").value = ""; }     // la ubicación de otro día no vale
  }
  const h = location.hash.slice(1);
  const tabs = ["mapa", "cerca", "buscar", "lineas", "favoritos"].concat(INTER ? ["viaje"] : []);
  irA(tabs.includes(h) ? h : INTER ? "viaje" : "mapa");
  setInterval(() => {
    if (document.hidden) return;
    if (tab === "mapa") pintarBuses();
    if (abrirParada._id && !$("hoja").hidden && $("h-lleg")) $("h-lleg").innerHTML = llegadasHtml(abrirParada._id, 10);
  }, 5000);
  document.querySelectorAll("[data-dia]").forEach((b) => { b.onclick = async () => { if (await setDia(+b.dataset.dia)) buscarViaje(); }; });
  setInterval(() => { if (!document.hidden && tab === "viaje" && !DIA && $("iv-sug").hidden) buscarViaje(); }, 30000);
  setInterval(() => { if (!document.hidden && tab === "favoritos") pintarFavs(); }, 30000);
})();
