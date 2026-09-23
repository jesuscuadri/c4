"use strict";
/* C-4 en tiempo real · interfaz web (sin dependencias salvo Leaflet para el mapa) */

let LINEA = null, R = null, tRecibido = 0, PREC = null;
let tabActual = "viaje", mapa = null, capaTrenes = null, marcas = {}, cajonTren = null;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------------------------------------------------------- utilidades */
const hm = (m) => {
  if (m == null || isNaN(m)) return "--:--";
  m = Math.round(m);
  return String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(((m % 60) + 60) % 60).padStart(2, "0");
};
const ahora = () => (R ? R.ahora + (Date.now() - tRecibido) / 60000 : 0);
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
      const j = await (await fetch("/api/linea")).json();
      if (!j.cargando) { LINEA = j; return; }
      $("chip-txt").textContent = "Preparando el horario…";
      $("avisos").innerHTML = j.error
        ? `<div class="aviso bad"><span>⚠</span><div><b>No se pudo cargar el horario de Renfe.</b> Se reintenta solo. (${esc(j.error)})</div></div>`
        : `<div class="aviso info"><span>ℹ</span><div>Descargando el horario oficial de Renfe. La primera vez tarda unos segundos…</div></div>`;
    } catch (e) { $("chip-txt").textContent = "Conectando…"; }
    await new Promise((r) => setTimeout(r, 1500));
  }
}
async function cargarEstado() {
  try {
    const j = await (await fetch("/api/estado")).json();
    if (j.cargando) { $("chip-txt").textContent = "Leyendo el tiempo real…"; return; }
    if (LINEA && j.fecha && j.fecha !== LINEA.fecha) { await cargarLinea(); iniciarSelectores(); }
    R = j; tRecibido = Date.now();
    pintarTodo();
  } catch (e) {
    const c = $("chip"); c.className = "chip sin_conexion"; $("chip-txt").textContent = "El programa no responde";
  }
}
async function cargarPrecision() {
  try { PREC = await (await fetch("/api/precision")).json(); pintarPrecision(); } catch (e) { /* nada */ }
}

/* ---------------------------------------------------------------- estado y avisos */
function pintarEstado() {
  const c = $("chip");
  c.className = "chip " + R.calidad;
  const txt = { directo: `En directo · ${R.con_posicion} trenes localizados`,
                congelado: "Renfe no actualiza · usando horario",
                sin_conexion: "Sin conexión con Renfe · usando horario" }[R.calidad];
  const corto = { directo: "En directo", congelado: "Sin datos Renfe", sin_conexion: "Sin conexión" }[R.calidad];
  $("chip-txt").innerHTML = `<span class="txt">${txt}</span><span class="corto">${corto}</span>`;
  c.title = `Última lectura ${R.actualizado}` + (R.ts_feed ? ` · datos de Renfe de las ${R.ts_feed}` : "");
  let h = "";
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
function pintarViaje() {
  const o = +$("o").value, d = +$("d").value;
  if (o === d) { $("viajes").innerHTML = `<div class="vacio">Elige dos estaciones distintas.</div>`; return; }
  const v = viajesEntre(o, d);
  if (!v.length) { $("viajes").innerHTML = `<div class="vacio">No quedan trenes directos hoy entre ${esc(est(o).nombre)} y ${esc(est(d).nombre)}.</div>`; return; }
  const now = ahora();
  $("viajes").innerHTML = v.map(({ t, jo, jd, mot, antes }, i) => {
    const sal = t.est_d[jo], lle = t.est_a[jd], app = t.adif_a[jd], dif = lle - app;
    const falta = Math.round(sal - now);
    const cuando = falta <= 0 ? "sale ahora" : falta < 60 ? `sale en ${falta} min` : `sale a las ${hm(sal)}`;
    const cambiaSal = Math.abs(sal - t.prog_d[jo]) >= 1, cambiaLle = Math.abs(lle - t.prog_a[jd]) >= 1;
    let aviso = "";
    if (t.con_datos && dif >= 1) aviso = `<div class="dif">La app oficial dirá <b class="num">${hm(app)}</b>. Llegarás sobre las <b class="num">${hm(lle)}</b> (<b>+${Math.round(dif)} min</b>).</div>`;
    else if (t.con_datos && dif <= -1) aviso = `<div class="dif bien">Llegarás antes de lo que dice la app oficial (${hm(app)}): recupera ${Math.round(-dif)} min con los márgenes del horario.</div>`;
    const lis = antes.map((m) => `<li class="antes">Antes de tu estación: ${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)
      .concat(mot.map((m) => `<li>${esc(m.texto)} (+${Math.round(m.min)} min)</li>`)).join("");
    return `<div class="tv ${i === 0 ? "primero" : ""}" data-tren="${t.id}" data-jo="${jo}" data-jd="${jd}">
      <div class="tv-cab">
        <div>
          <div class="tv-tren">Tren ${t.num}<span class="tag ${t.dir > 0 ? "ida" : "vta"}">→ ${esc(destinoCorto(t))}</span>${t.con_datos ? tagRetraso(t.retraso) : `<span class="tag gris">${t.situacion.startsWith("Aún") ? "Programado" : "Sin datos"}</span>`}</div>
          <div class="tv-sit">${esc(t.situacion)}${t.via ? ` · vía ${esc(t.via)}` : ""}</div>
        </div>
        <div class="tv-grande"><div class="h num">${hm(lle)}</div><div class="l">llegada · ${cuando}</div></div>
      </div>
      <div class="cmp num">
        <span></span><span class="c">Horario</span><span class="c">App oficial</span><span class="c">Estimado</span>
        <span class="c" style="align-self:center">Sale</span><span class="${cambiaSal ? "x" : ""}">${hm(t.prog_d[jo])}</span><span>${hm(t.adif_d[jo])}</span><span class="e">${hm(sal)}</span>
        <span class="c" style="align-self:center">Llega</span><span class="${cambiaLle ? "x" : ""}">${hm(t.prog_a[jd])}</span><span>${hm(app)}</span><span class="e">${hm(lle)}</span>
      </div>${aviso}${lis ? `<ul class="motivos">${lis}</ul>` : ""}</div>`;
  }).join("");
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
function iniciarMapa() {
  if (mapa || !window.L) {
    if (!window.L) $("mapa").innerHTML = `<div class="vacio">No se pudo cargar el mapa (hace falta internet para los planos de OpenStreetMap).</div>`;
    return;
  }
  const oscuro = matchMedia("(prefers-color-scheme: dark)").matches;
  mapa = L.map("mapa", { zoomControl: true, attributionControl: true, zoomSnap: 0.25 });
  L.tileLayer(`https://{s}.basemaps.cartocdn.com/${oscuro ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`, {
    maxZoom: 19, subdomains: "abcd",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  }).addTo(mapa);
  const traz = L.polyline(LINEA.trazado, { color: "#e93cac", weight: 5, opacity: 0.85 }).addTo(mapa);
  for (const e of LINEA.estaciones) {
    L.circleMarker([e.lat, e.lon], e.cruce
      ? { radius: 6.5, color: "#e93cac", weight: 3, fillColor: "#fff", fillOpacity: 1 }
      : { radius: 3.5, color: "#e93cac", weight: 2, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(`<b>${esc(e.nombre)}</b>${e.cruce ? "<br>Vía de cruce" : ""}`, { direction: "top", offset: [0, -4] })
      .on("click", () => { $("est").value = e.k; guardar("est", e.k); irA("estacion"); })
      .addTo(mapa);
  }
  capaTrenes = L.layerGroup().addTo(mapa);
  mapa.fitBounds(traz.getBounds(), { padding: [20, 20] });
}
function pintarTrenesMapa() {
  if (!mapa || !R) return;
  const now = ahora(), vivos = new Set();
  for (const t of R.trenes) {
    const x = kmTren(t, now);
    if (x == null) continue;
    const ll = latlonKm(x);
    if (!ll) continue;
    vivos.add(t.id);
    const r = Math.round(retrasoActual(t));
    const rc = r <= 0 ? "#15803d" : r <= 5 ? "#b45309" : "#c0262d";
    const html = `<div class="tren-ico" style="opacity:${t.con_datos ? 1 : 0.55}"><span class="p" style="background:${colorDirHex(t.dir)}">${t.num}</span>${t.con_datos ? `<span class="r" style="color:${rc}">+${r}</span>` : ""}</div>`;
    let m = marcas[t.id];
    if (!m) {
      m = marcas[t.id] = L.marker(ll, { icon: L.divIcon({ html, className: "", iconSize: null }), zIndexOffset: 500 })
        .on("click", () => abrirTren(t.id)).addTo(capaTrenes);
      m._html = html;
    } else {
      m.setLatLng(ll);
      if (m._html !== html) { m.setIcon(L.divIcon({ html, className: "", iconSize: null })); m._html = html; }
    }
    m.bindTooltip(`Tren ${t.num} → ${esc(t.destino)}<br>${esc(t.situacion)}`, { direction: "top", offset: [0, -12] });
  }
  for (const id of Object.keys(marcas)) if (!vivos.has(id)) { capaTrenes.removeLayer(marcas[id]); delete marcas[id]; }
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
function pintarPrecision() {
  const el = $("precision");
  if (!PREC) { el.innerHTML = `<div class="vacio">Cargando…</div>`; return; }
  const sem = PREC.semana || {}, hoy = PREC.hoy || {}, tot = sem.total, th = hoy.total;
  if (!tot) {
    el.innerHTML = `<div class="aviso info"><span>ℹ</span><div><b>Aún no hay mediciones.</b> Se van acumulando solas mientras el programa está abierto y Renfe da datos en directo. Con un par de días de uso ya verás si las estimaciones aciertan más que la app oficial.</div></div>`;
    return;
  }
  const kpi = (v, l) => `<div class="kpi"><div class="v num">${v}</div><div class="l">${l}</div></div>`;
  let h = `<div class="kpis">` +
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
  h += `<p class="sub" style="margin-top:10px">Sesgo positivo: el programa tiende a decir que llegará más tarde de lo que llega (pesimista). Negativo: optimista. Si ves un sesgo claro, ajusta <code>margen_cruce_min</code> o <code>recuperacion</code> en <code>config.json</code>.</p>`;
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
  if (tabActual === "viaje") pintarViaje();
  if (tabActual === "estacion") pintarEstacion();
  if (tabActual === "malla") pintarMalla();
  if (tabActual === "cruces") pintarCruces();
  if (tabActual === "info") pintarInfo();
  if (tabActual === "mapa") pintarTrenesMapa();
  pintarCajon();
}

document.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-tren]");
  if (el && !ev.target.closest("#cajon")) abrirTren(el.dataset.tren, el.dataset.jo, el.dataset.jd);
});
$("velo").onclick = cerrarTren;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarTren(); });
for (const b of document.querySelectorAll("nav.tabs button")) b.onclick = () => irA(b.dataset.tab);
$("o").onchange = $("d").onchange = () => { guardar("o", +$("o").value); guardar("d", +$("d").value); pintarViaje(); };
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
  irA(["viaje", "estacion", "mapa", "malla", "cruces", "precision", "info"].includes(hash) ? hash : "viaje");
  setInterval(cargarEstado, 15000);
  setInterval(() => { if (tabActual === "precision") cargarPrecision(); }, 300000);
  setInterval(() => {
    $("reloj").textContent = new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (tabActual === "mapa") pintarTrenesMapa();
  }, 1000);
})();
