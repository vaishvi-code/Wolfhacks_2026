const CACHE='terrawatch-shell-v10';
const SHELL=['/','/index.html','/styles.css','/app.js','/guidance.js','/offline.js','/offline-map.js','/route-request.js','/preparedness.js','/vendor/leaflet.js','/vendor/leaflet.css','/icons/logo.svg','/icons/icon-192.png','/icons/icon-512.png','/icons/apple-touch-icon.png','/manifest.webmanifest','/shared/routing.mjs','/shared/geo.mjs','/shared/evacuation.mjs'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  // OSM tile policy prohibits bulk offline tile downloads. Only local assets are cached.
  if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
  event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));}return response;}).catch(()=>caches.match(event.request)));
});
