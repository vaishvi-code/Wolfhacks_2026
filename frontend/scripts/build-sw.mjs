// Precache the complete built shell, including hashed JS/CSS. Never cache API responses.
import { readdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
async function files(dir){const entries=await readdir(dir,{withFileTypes:true});const result=[];for(const e of entries){const path=`${dir}/${e.name}`;if(e.isDirectory())result.push(...await files(path));else if(e.name!=='sw.js')result.push(path)}return result}
const paths=await files('dist')
const hash=createHash('sha256');for(const path of paths.sort())hash.update(await readFile(path))
const name=`wayfinder-shell-${hash.digest('hex').slice(0,12)}`
const assets=paths.map(path=>'/'+path.slice(5))
await writeFile('dist/sw.js',`
const CACHE=${JSON.stringify(name)};
const ASSETS=${JSON.stringify(assets)};
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('wayfinder-shell-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
 if(event.request.mode==='navigate'){
  event.respondWith(fetch(event.request).catch(()=>caches.open(CACHE).then(cache=>cache.match('/index.html'))));return;
 }
 if(ASSETS.includes(url.pathname))event.respondWith(caches.open(CACHE).then(async cache=>(await cache.match(url.pathname))||fetch(event.request)));
});
`)
console.log(`Precached ${assets.length} shell files in ${name}. API responses and tiles excluded.`)
