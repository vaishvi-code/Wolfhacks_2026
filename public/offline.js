const dbPromise=new Promise((resolve,reject)=>{const r=indexedDB.open('terrawatch',2);r.onupgradeneeded=()=>{for(const name of ['packs','maps'])if(!r.result.objectStoreNames.contains(name))r.result.createObjectStore(name);};r.onsuccess=()=>{r.result.onversionchange=()=>r.result.close();resolve(r.result);};r.onerror=()=>reject(r.error);});
// A single transaction prevents a partial download being advertised as ready.
export async function savePack(pack,mapAsset){const db=await dbPromise;return new Promise((resolve,reject)=>{const tx=db.transaction(mapAsset?['packs','maps']:['packs'],'readwrite');if(mapAsset)tx.objectStore('maps').put(mapAsset,pack.region.id);tx.objectStore('packs').put(pack,`${pack.region.id}:${pack.mode}`);tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(tx.error||new Error('Device storage could not save this area.'));});}
export async function loadPack(region,mode){const db=await dbPromise;return new Promise((resolve,reject)=>{const r=db.transaction('packs').objectStore('packs').get(`${region}:${mode}`);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
export async function loadCityMap(region){const db=await dbPromise;return new Promise((resolve,reject)=>{const r=db.transaction('maps').objectStore('maps').get(region);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
export async function ensureOfflineShell(){
  if(!('serviceWorker' in navigator))throw new Error('Offline reopening needs a supported browser over HTTPS.');
  let timer;
  try{
    const registration=await Promise.race([navigator.serviceWorker.ready,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Offline app setup did not finish. Reload and try again.')),12000);})]);
    clearTimeout(timer);
    await new Promise((resolve,reject)=>{
      const channel=new MessageChannel();
      const finish=(error)=>{clearTimeout(timer);channel.port1.close();error?reject(error):resolve();};
      timer=setTimeout(()=>finish(new Error('The offline app needs an update. Reload before saving.')),8000);
      channel.port1.onmessage=event=>finish(event.data?.ready?null:new Error('Offline app files are incomplete. Reload and save again.'));
      registration.active.postMessage({type:'CHECK_OFFLINE_SHELL'},[channel.port2]);
    });
  }finally{clearTimeout(timer);}
}
