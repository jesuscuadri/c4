/* Service worker: la app abre al instante aunque el servidor esté arrancando.
   - Páginas y estilos: se piden a la red; si tarda más de 3 s, se usa la copia guardada.
   - Datos (/api/estado, /api/linea): igual, con 8 s; la app avisa si son datos antiguos.
   - Mapa (Leaflet): se guarda la primera vez. Las teselas del mapa no se guardan. */
const VERSION = "c4-v5";
const BASICOS = ["/", "/index.html", "/app.js", "/estilos.css", "/manifest.json", "/icono-180.png", "/icono-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(BASICOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function conLimite(promesa, ms) {
  return new Promise((ok, mal) => {
    const t = setTimeout(() => mal(new Error("tiempo")), ms);
    promesa.then((r) => { clearTimeout(t); ok(r); }, (e) => { clearTimeout(t); mal(e); });
  });
}

async function redPrimero(req, ms, guardar) {
  const cache = await caches.open(VERSION);
  const red = fetch(req).then((r) => {
    if (r.ok && guardar) cache.put(req, r.clone());
    return r;
  });
  try {
    return await conLimite(red, ms);
  } catch (e) {
    const copia = await cache.match(req, { ignoreSearch: false });
    if (copia) return copia;
    return red; // sin copia: esperamos a la red lo que haga falta
  }
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.origin === location.origin) {
    if (url.pathname === "/api/estado" || url.pathname === "/api/linea") {
      e.respondWith(redPrimero(e.request, 8000, true));
    } else if (url.pathname.startsWith("/api/")) {
      return; // precisión, mañana, ping: siempre a la red
    } else {
      e.respondWith(redPrimero(e.request, 3000, true));
    }
  } else if (url.hostname === "unpkg.com" || url.hostname === "cdnjs.cloudflare.com") {
    e.respondWith(caches.open(VERSION).then(async (c) => {
      const copia = await c.match(e.request);
      if (copia) return copia;
      const r = await fetch(e.request);
      if (r.ok) c.put(e.request, r.clone());
      return r;
    }));
  }
});
