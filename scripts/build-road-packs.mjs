// Build-time public OSM downloads. No provider request is needed to use demo packs.
import {DatabaseSync} from 'node:sqlite';
import {mkdir,writeFile} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {Store} from '../lib/store.mjs';
import {Providers} from '../lib/providers.mjs';
import {REGIONS} from '../lib/config.mjs';
const memory=new Store(':memory:'),provider=new Providers(memory);
let cached;
if(process.argv.includes('--use-local-cache'))cached=new DatabaseSync('data/terrawatch.sqlite',{readOnly:true});
await mkdir('lib/road-packs',{recursive:true});
const manifest={version:1,attribution:'© OpenStreetMap contributors',regions:{}};
try{
  for(const region of Object.values(REGIONS)){
    const row=cached?.prepare('SELECT value FROM cache WHERE key=?').get(`osm-v2-${region.id}`);
    const data=row?JSON.parse(row.value):(await provider.osm(region,{ignoreBundle:true})).data;
    if(!data?.roads?.edges?.length||!data.roads.nodes.length||data.roads.simulation||!Number.isFinite(Date.parse(data.roads.fetchedAt)))throw new Error(`Real road download failed for ${region.name}. Retry the build; fictional data cannot be bundled.`);
    const bytes=gzipSync(Buffer.from(JSON.stringify(data)),{level:9});
    await writeFile(`lib/road-packs/${region.id}.json.gz`,bytes);
    manifest.regions[region.id]={file:`${region.id}.json.gz`,fetchedAt:data.roads.fetchedAt,bbox:region.bbox,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),nodes:data.roads.nodes.length,edges:data.roads.edges.length,facilities:data.facilities.length};
    console.log(`${region.name}: ${data.roads.edges.length} real road segments, ${(bytes.length/1048576).toFixed(1)} MB compressed, fetched ${data.roads.fetchedAt}`);
  }
  await writeFile('lib/road-packs/manifest.json',JSON.stringify(manifest,null,2)+'\n');
}finally{cached?.close();memory.close();}
