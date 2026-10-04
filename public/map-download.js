export const MAX_MAP_BYTES=40*1024*1024;
export const megabytes=bytes=>(bytes/1024/1024).toFixed(1)+' MB';

// PMTiles reads ranges from the saved Blob, with no fetch calls or object URLs.
export class BlobMapSource {
  constructor(blob,key){this.blob=blob;this.key=key;}
  getKey(){return this.key;}
  async getBytes(offset,length,signal){
    if(signal?.aborted)throw new DOMException('Map read cancelled.','AbortError');
    if(!Number.isSafeInteger(offset)||!Number.isSafeInteger(length)||offset<0||length<=0||offset>=this.blob.size)throw new Error('Invalid map byte range.');
    return {data:await this.blob.slice(offset,Math.min(offset+length,this.blob.size)).arrayBuffer()};
  }
}
export async function downloadCityMap(entry,{fetchImpl=fetch,onProgress=()=>{},signal=AbortSignal.timeout(180000),digest=bytes=>crypto.subtle.digest('SHA-256',bytes)}={}){
  if(!Number.isSafeInteger(entry.bytes)||entry.bytes<127||entry.bytes>MAX_MAP_BYTES||!/^\/maps\/[a-z]+\.pmtiles$/.test(entry.url))throw new Error('Invalid offline map manifest.');
  const response=await fetchImpl(entry.url,{signal,cache:'no-store'});
  if(response.status!==200)throw new Error('City map download failed. Reconnect and try again.');
  const reader=response.body.getReader(),parts=[];let received=0;
  try{
    while(true){const {value,done}=await reader.read();if(done)break;received+=value.byteLength;if(received>entry.bytes)throw new Error('City map size changed. Refresh the app and try again.');parts.push(value);onProgress(received,entry.bytes);}
    if(received!==entry.bytes)throw new Error('City map download was incomplete. Try again.');
    const blob=new Blob(parts,{type:'application/octet-stream'}),bytes=await blob.arrayBuffer();
    const header=new Uint8Array(bytes,0,8);
    if(new TextDecoder().decode(header.slice(0,7))!=='PMTiles'||header[7]!==3)throw new Error('Downloaded map is not a supported PMTiles archive.');
    const hash=Array.from(new Uint8Array(await digest(bytes)),b=>b.toString(16).padStart(2,'0')).join('');
    if(hash!==entry.sha256)throw new Error('Map integrity check failed. Download again.');
    return {blob,version:entry.version,bytes:received,bbox:entry.bbox,maxDataZoom:entry.maxDataZoom,sha256:hash,savedAt:new Date().toISOString()};
  }catch(error){await reader.cancel().catch(()=>{});throw error;}
  finally{reader.releaseLock();}
}
