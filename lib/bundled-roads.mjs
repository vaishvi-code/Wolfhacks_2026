import {readFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {REGIONS} from './config.mjs';
const root=new URL('./road-packs/',import.meta.url),loaded=new Map();
export async function loadBundledRoads(regionId,now=Date.now()){
  if(!Object.hasOwn(REGIONS,regionId))throw new Error('Unknown road-pack region.');
  if(!loaded.has(regionId))loaded.set(regionId,(async()=>{
    const manifest=JSON.parse(await readFile(new URL('manifest.json',root),'utf8')),entry=manifest.regions[regionId];
    if(!entry||entry.file!==`${regionId}.json.gz`||!Number.isFinite(Date.parse(entry.fetchedAt)))throw new Error('Invalid bundled road manifest.');
    const bytes=await readFile(new URL(entry.file,root));
    if(bytes.length!==entry.bytes||createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw new Error('Bundled road integrity check failed.');
    const data=JSON.parse(gunzipSync(bytes));
    if(!data.roads?.edges?.length||!data.roads.nodes?.length||data.roads.simulation||data.roads.fetchedAt!==entry.fetchedAt||!Array.isArray(data.facilities))throw new Error('Bundled real road data is invalid.');
    data.roads.basis='osm';data.roads.bundled=true;
    return {data,entry};
  })().catch(error=>{loaded.delete(regionId);if(error.code==='ENOENT')return null;throw error;}));
  const pack=await loaded.get(regionId);if(!pack)return null;
  return {data:pack.data,source:{id:`osm-v2-${regionId}`,name:'OpenStreetMap · bundled roads & resources',status:now-Date.parse(pack.entry.fetchedAt)<86400000?'cached':'stale',lastSuccess:pack.entry.fetchedAt,bundled:true,detail:`Bundled city street network downloaded ${pack.entry.fetchedAt}. Road conditions and resource availability are unverified.`}};
}
