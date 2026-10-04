const CACHE='terrawatch-shell-v14';
const SHELL=['/','/index.html','/styles.css','/app.js','/guidance.js','/offline.js','/offline-map.js','/city-map.js','/map-download.js','/maps/catalog.json','/route-request.js','/preparedness.js','/vendor/leaflet.js','/vendor/leaflet.css','/vendor/pmtiles.js','/vendor/protomaps-leaflet.js','/icons/logo.svg','/icons/icon-192.png','/icons/icon-512.png','/icons/apple-touch-icon.png','/manifest.webmanifest','/shared/routing.mjs','/shared/geo.mjs','/shared/evacuation.mjs'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('message',event=>{
  if(event.data?.type!=='CHECK_OFFLINE_SHELL'||!event.ports[0])return;
  event.waitUntil(caches.open(CACHE).then(async cache=>{const entries=await Promise.all(SHELL.map(path=>cache.match(path)));event.ports[0].postMessage({ready:entries.every(Boolean),version:CACHE});}).catch(()=>event.ports[0].postMessage({ready:false})));
});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  // OSM tile policy prohibits bulk offline tile downloads. Only local assets are cached.
  // Full city archives live in IndexedDB. Never cache partial range responses.
  if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/')||url.pathname.endsWith('.pmtiles')||event.request.headers.has('range'))return;
  event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));}return response;}).catch(()=>caches.match(event.request)));
});
