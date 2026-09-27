/* Service worker: lets the app open and show the last downloaded news, history and weekly summaries offline.
   Strategy: always try the network first (so data is fresh), fall back to the saved copy when offline. */
const CACHE = "mmw-v1";
const SHELL = [
  "./", "index.html", "style.css", "app.js", "manifest.webmanifest", "watchlist.json",
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png",
];
const NETWORK_TIMEOUT_MS = 6000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Only same-origin GETs; CSV downloads always go to the network (not needed offline).
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.endsWith(".csv")) return;

  const key = url.origin + url.pathname; // ignore query strings
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const res = await withTimeout(fetch(req), NETWORK_TIMEOUT_MS);
      if (res.ok) cache.put(key, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(key) || (req.mode === "navigate" && await cache.match("index.html"));
      if (hit) return hit;
      throw err;
    }
  })());
});
