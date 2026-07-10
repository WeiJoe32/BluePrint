/* ============================================================================
   BluePrint — service worker
   ----------------------------------------------------------------------------
   Cache-first for the static app shell only. Everything else (the main page,
   /login, /marker, and all /api/* calls — especially the streaming NDJSON
   POST /api/generate and /api/refine) goes straight to the network, never
   through the cache. Flask serves this file at /sw.js (root scope) even
   though it lives at static/js/sw.js on disk.
   ========================================================================== */

"use strict";

// BUMP THIS on every release that changes app.js / style.css / index markup —
// cache-first serving means a stale version here ships stale code to clients.
var CACHE_VERSION = "blueprint-v3";

var PRECACHE_URLS = [
  "/static/launch.html",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/manifest.webmanifest",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
  "/static/img/apple-touch-icon.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then(function (cache) { return cache.addAll(PRECACHE_URLS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(
          names
            .filter(function (name) { return name !== CACHE_VERSION; })
            .map(function (name) { return caches.delete(name); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  // Never intercept anything but plain GETs, and never touch the API —
  // the streaming NDJSON endpoints must always hit the network directly.
  if (event.request.method !== "GET") return;
  if (new URL(event.request.url).pathname.startsWith("/api/")) return;

  var url = new URL(event.request.url);
  if (!url.pathname.startsWith("/static/")) return; // "/", "/login", "/marker" — always network

  event.respondWith(
    caches.match(event.request).then(function (cached) {
      if (cached) return cached;
      return fetch(event.request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(CACHE_VERSION).then(function (cache) { cache.put(event.request, copy); });
        }
        return response;
      });
    })
  );
});
