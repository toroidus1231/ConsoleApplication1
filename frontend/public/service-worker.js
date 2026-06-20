/* Service worker for the offline physical checklist (Contracts §6.5, Module 19).
 *
 * Strategy:
 *   * Precache the app shell at install so the checklist UI loads with no WiFi.
 *   * Navigation requests: network-first, fall back to the cached shell
 *     (index.html) so client-side routes like /checklist work offline.
 *   * Static assets (JS/CSS/img): stale-while-revalidate.
 *   * GET /api/v1/devices and GET /api/v1/checklist/*: network-first with a
 *     cache fallback, so the device list and a previously opened checklist are
 *     available offline.
 *   * Checklist POST submissions are NOT handled here — the page queues them in
 *     IndexedDB (offline.js) and flushes on the window 'online' event. The SW
 *     also nudges clients to flush when it regains connectivity via sync.
 *
 * This file is authored in src/ and served verbatim from /service-worker.js
 * (a copy lives in public/). Keep it framework-free — it runs as a worker.
 */
/* eslint-disable no-restricted-globals */

const VERSION = "cx-v1";
const SHELL_CACHE = `${VERSION}-shell`;
const ASSET_CACHE = `${VERSION}-assets`;
const API_CACHE = `${VERSION}-api`;

// The shell entry point; hashed assets are cached on demand at runtime.
const SHELL_URLS = ["/", "/index.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_URLS).catch(() => undefined))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => !k.startsWith(VERSION))
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

function isApiCacheable(url) {
  if (!url.pathname.startsWith("/api/v1/")) return false;
  // Only cache the offline-relevant GETs (§6.5: checklist + device list).
  return (
    url.pathname === "/api/v1/devices" ||
    url.pathname.startsWith("/api/v1/checklist/")
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return; // let POST/PATCH hit the network

  const url = new URL(request.url);

  // Never intercept the SSE stream — it must stream live from the network.
  if (url.pathname === "/api/v1/system/events") return;

  // App navigation: network-first, fall back to cached shell for offline routes.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then((c) => c.put("/index.html", copy));
          return resp;
        })
        .catch(() => caches.match("/index.html").then((r) => r || caches.match("/")))
    );
    return;
  }

  // Offline-relevant API GETs: network-first, cache fallback.
  if (isApiCacheable(url)) {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(API_CACHE).then((c) => c.put(request, copy));
          return resp;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Built static assets: stale-while-revalidate.
  if (url.origin === self.location.origin && /\.(js|css|woff2?|png|svg|jpg|jpeg|gif|ico)$/.test(url.pathname)) {
    event.respondWith(
      caches.open(ASSET_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((resp) => {
            cache.put(request, resp.clone());
            return resp;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
  }
});

// When a Background Sync fires (or a client posts SYNC_CHECKLIST), ask all
// open clients to flush their IndexedDB checklist queue (offline.js).
async function notifyClientsToSync() {
  const clients = await self.clients.matchAll({ includeUncontrolled: true });
  clients.forEach((client) => client.postMessage({ type: "FLUSH_CHECKLIST_QUEUE" }));
}

self.addEventListener("sync", (event) => {
  if (event.tag === "sync-checklist") {
    event.waitUntil(notifyClientsToSync());
  }
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SYNC_CHECKLIST") {
    notifyClientsToSync();
  }
});
