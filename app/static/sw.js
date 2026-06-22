// 韭菜健檢 Service Worker v2 — 強化版 offline cache + push 預備
const CACHE_VERSION = "leek-check-v2";
const ASSETS_TO_CACHE = [
  "/",
  "/app/static/manifest.json",
  "/app/static/icon-192.png",
  "/app/static/icon-512.png",
];

// Inline offline fallback HTML(Streamlit Cloud 不服務 .html static,只能 inline)
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>韭菜健檢 — 離線中</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0f766e">
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;
background:linear-gradient(135deg,#0f766e 0%,#0a1a1f 35%,#16181d 100%);color:#fff;
min-height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;padding:24px}
.icon{font-size:4rem;margin-bottom:12px}
h1{font-size:1.8rem;margin:0 0 12px;font-weight:800}
.sub{color:#5eead4;font-size:1rem;margin-bottom:8px}
.hint{color:#94a3b8;font-size:.85rem;max-width:320px;text-align:center;line-height:1.5}
.retry{margin-top:24px;background:#14b8a6;color:#16181d;border:none;padding:12px 24px;
border-radius:10px;font-weight:700;font-size:1rem;cursor:pointer}
.retry:hover{background:#5eead4}
</style></head><body>
<div class="icon">📡</div>
<h1>韭菜健檢 — 暫時離線</h1>
<div class="sub">沒網路連線</div>
<p class="hint">韭菜健檢需要連線才能抓即時股價跟分析資料。<br>確認 WiFi / 行動網路後重試。</p>
<button class="retry" onclick="location.reload()">↺ 重新連線</button>
<script>
window.addEventListener("online",()=>location.replace("/"));
setInterval(()=>{if(navigator.onLine)location.replace("/")},5000);
</script></body></html>`;

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
  if (event.request.method !== "GET") return;
  // Streamlit websocket / 內部 endpoint 不 cache(避免 stale state)
  const url = new URL(event.request.url);
  if (url.pathname.includes("/_stcore/") ||
      url.pathname.includes("/healthz") ||
      url.protocol === "ws:" || url.protocol === "wss:") {
    return;
  }
  event.respondWith(
    fetch(event.request).catch(async () => {
      const cached = await caches.match(event.request);
      if (cached) return cached;
      if (event.request.mode === "navigate") {
        // Inline offline page(避開 Streamlit Cloud .html 不被服務問題)
        return new Response(OFFLINE_HTML, {
          headers: {"Content-Type": "text/html; charset=utf-8"},
        });
      }
      return caches.match("/");
    })
  );
});

// Web Push 通知處理(未來推 alpha 訊號用)
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "韭菜健檢";
  const options = {
    body: data.body || "你關注的個股有新動態",
    icon: "/app/static/icon-192.png",
    badge: "/app/static/icon-192.png",
    data: data.url || "/",
    vibrate: [100, 50, 100],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data || "/"));
});
