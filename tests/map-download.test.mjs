import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {BlobMapSource,downloadCityMap} from '../public/map-download.js';
import {parseByteRange} from '../lib/http-range.mjs';

const digest=bytes=>webcrypto.subtle.digest('SHA-256',bytes);
function fixture(){const bytes=new Uint8Array(256);bytes.set(new TextEncoder().encode('PMTiles'));bytes[7]=3;return {bytes,entry:{url:'/maps/raleigh.pmtiles',version:'test',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),bbox:[-79,35,-78,36],maxDataZoom:15}};}
test('complete city download verifies hash and reads offline ranges from its Blob',async()=>{
  const {bytes,entry}=fixture(),progress=[];
  const asset=await downloadCityMap(entry,{fetchImpl:async()=>new Response(bytes),digest,onProgress:(n,total)=>progress.push([n,total])});
  assert.equal(asset.bytes,256);assert.equal(asset.sha256,entry.sha256);assert.deepEqual(progress.at(-1),[256,256]);
  const source=new BlobMapSource(asset.blob,'saved-map');assert.equal(source.getKey(),'saved-map');
  assert.deepEqual(new Uint8Array((await source.getBytes(0,8)).data),bytes.slice(0,8));
  assert.equal((await source.getBytes(250,100)).data.byteLength,6);
  await assert.rejects(source.getBytes(256,1),/byte range/);
  await assert.rejects(source.getBytes(0,8,AbortSignal.abort()),{name:'AbortError'});
});
test('partial, corrupt, oversized and failed map downloads cannot become ready',async()=>{
  const {bytes,entry}=fixture();
  for(const response of [new Response(bytes.slice(0,128)),new Response(bytes,{status:206}),new Response(new Uint8Array(300)),new Response('failure',{status:503})])await assert.rejects(downloadCityMap(entry,{fetchImpl:async()=>response,digest}));
  const changed=bytes.slice();changed[100]=1;
  await assert.rejects(downloadCityMap(entry,{fetchImpl:async()=>new Response(changed),digest}),/integrity/);
  await assert.rejects(downloadCityMap({...entry,bytes:100000000},{fetchImpl:()=>{throw Error('should not fetch');},digest}),/manifest/);
  await assert.rejects(downloadCityMap({...entry,url:'https://example.com/map.pmtiles'},{digest}),/manifest/);
});
test('HTTP byte ranges handle PMTiles initial reads, suffixes and invalid ranges',()=>{
  assert.deepEqual(parseByteRange('bytes=0-16383',8000),{start:0,end:7999});
  assert.deepEqual(parseByteRange('bytes=100-',8000),{start:100,end:7999});
  assert.deepEqual(parseByteRange('bytes=-20',8000),{start:7980,end:7999});
  assert.equal(parseByteRange(undefined,8000),null);
  for(const range of ['bytes=8000-','bytes=-0','bytes=20-10','bytes=0-1,3-4','junk'])assert.equal(parseByteRange(range,8000),false);
});
test('all bundled city maps match their manifest and cover routing bounds',async()=>{
  const catalog=JSON.parse(await readFile(new URL('../public/maps/catalog.json',import.meta.url),'utf8'));
  const {REGIONS}=await import('../lib/config.mjs');
  for(const [id,region]of Object.entries(REGIONS)){
    const entry=catalog.regions[id],bytes=await readFile(new URL(`../public${entry.url}`,import.meta.url));
    assert.equal(bytes.length,entry.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),entry.sha256);
    assert.equal(bytes.toString('ascii',0,7),'PMTiles');assert.equal(bytes[7],3);
    assert.ok(entry.bbox[0]<region.bbox[0]&&entry.bbox[1]<region.bbox[1]&&entry.bbox[2]>region.bbox[2]&&entry.bbox[3]>region.bbox[3]);
  }
});
