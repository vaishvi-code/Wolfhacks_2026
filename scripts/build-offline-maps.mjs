// Run after installing the official go-pmtiles CLI; map extraction is a build step.
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {REGIONS} from '../lib/config.mjs';
const build=process.env.MAP_BUILD||'20261003',cli=process.env.PMTILES_CLI||'pmtiles';
if(!/^\d{8}$/.test(build))throw new Error('MAP_BUILD must be YYYYMMDD.');
await mkdir('public/maps',{recursive:true});
const catalog={version:build,source:`https://build.protomaps.com/${build}.pmtiles`,attribution:'© OpenStreetMap contributors · Protomaps',regions:{}};
for(const region of Object.values(REGIONS)){
  const [w,s,e,n]=region.bbox,bbox=[w-.05,s-.05,e+.05,n+.05].map(n=>Number(n.toFixed(4))),file=`public/maps/${region.id}.pmtiles`;
  if(!process.argv.includes('--catalog-only'))await new Promise((resolve,reject)=>{const child=spawn(cli,['extract',catalog.source,file,`--bbox=${bbox.join(',')}`,'--maxzoom=15','--download-threads=4'],{stdio:'inherit'});child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(new Error(`Map extraction failed (${code}).`)));});
  const bytes=await readFile(file);
  if(bytes.length>40*1024*1024)throw new Error(`${region.name} map exceeds the download size limit.`);
  catalog.regions[region.id]={url:`/maps/${region.id}.pmtiles`,name:region.name,version:build,bbox,maxDataZoom:15,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
}
await writeFile('public/maps/catalog.json',JSON.stringify(catalog,null,2)+'\n');
console.log('City map catalog written.');
