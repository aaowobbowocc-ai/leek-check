// 韭菜健檢 Service Worker — 基本 offline cache + 之後可加 push
const CACHE_VERSION = "leek-check-v1";
const ASSETS_TO_CACHE = [
  "/",
  "/app/static/manifest.json",
  "/app/static/icon-192.png",
  "/app/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(ASSETS_TO_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names.filter((n) => n !== CACHE_VERSION).map((n) => caches.delete(n))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // 只 cache GET 請求
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((res) => res || caches.match("/"))
    )
  );
});

// 之後可加 push 通知處理
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "韭菜健檢";
  const options = {
    body: data.body || "你關注的個股有新動態",
    icon: "/app/static/icon-192.png",
    badge: "/app/static/icon-192.png",
    data: data.url || "/",
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data || "/"));
});
