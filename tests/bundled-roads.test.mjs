import test from 'node:test';
import assert from 'node:assert/strict';
import {loadBundledRoads} from '../lib/bundled-roads.mjs';
import {DisasterService} from '../lib/service.mjs';
import {Providers} from '../lib/providers.mjs';
import {Store} from '../lib/store.mjs';
import {planEvacuation} from '../lib/evacuation.mjs';

for(const id of ['raleigh','wilmington','asheville'])test(`${id}: empty-server demo and offline download use bundled real streets without provider requests`,async()=>{
  const store=new Store(':memory:');try{
    const service=new DisasterService(store,undefined,{demoRoads:'real'});
    service.providers.osm=()=>{throw new Error('Demo must not call a provider.');};
    const snapshot=await service.snapshot(id,'demo'),pack=await service.snapshot(id,'demo',true);
    assert.equal(snapshot.roadInfo.basis,'osm');assert.equal(snapshot.roadInfo.bundled,true);assert.equal(snapshot.roads,undefined);
    assert.ok(pack.roads.nodes.length>10000);assert.ok(pack.roads.edges.length>10000);
    assert.ok(pack.facilities.length>0);assert.ok(pack.facilities.every(f=>!f.simulation));
    const bundled=await loadBundledRoads(id);
    assert.equal(pack.roadInfo.fetchedAt,bundled.source.lastSuccess);
    assert.ok(bundled.data.roads.edges.every(e=>!e.demoPassages),'Demo conditions cannot leak into the real bundle');
    assert.equal(planEvacuation(pack,{start:pack.demoOrigins.flood,hazard:'flood'},{offline:true,requireMappedRoads:true}).status,'routes_found');
  }finally{store.close();}
});
test('bundled ages remain original and stale live-road sources do not pass freshness checks',async()=>{
  const bundle=await loadBundledRoads('raleigh'),future=Date.parse(bundle.source.lastSuccess)+26*3600000;
  const stale=await loadBundledRoads('raleigh',future);
  assert.equal(stale.source.status,'stale');assert.equal(stale.source.lastSuccess,bundle.source.lastSuccess);
  const store=new Store(':memory:');try{
    const service=new DisasterService(store,undefined,{demoRoads:'real'}),s=await service.snapshot('raleigh','demo',true);
    s.mode='live';s.sources=[{id:'nws-raleigh',status:'live',lastSuccess:new Date(future).toISOString()},stale.source];
    const p=planEvacuation(s,{start:s.demoOrigins.flood,hazard:'flood'},{now:future});
    assert.equal(p.status,'incomplete_data');assert.deepEqual(p.alternatives,[]);
  }finally{store.close();}
});
test('a fresh bundle serves live base streets on an empty cache without a provider fetch',async()=>{
  const store=new Store(':memory:');try{
    const bundle=await loadBundledRoads('raleigh');
    const p=new Providers(store,{roadPackLoader:async()=>({...bundle,source:{...bundle.source,status:'cached'}})});
    p.cached=()=>{throw new Error('Fresh bundled streets must not trigger a network loader.');};
    const result=await p.osm({id:'raleigh'});assert.equal(result.source.bundled,true);
    assert.equal(store.get('osm-v2-raleigh'),null,'Loading a bundle must not reset its timestamp in the cache');
  }finally{store.close();}
});
test('provider outage can retain bundled geometry without renewing stale dates',async()=>{
  const store=new Store(':memory:');try{
    const bundle=await loadBundledRoads('raleigh'),stale={...bundle,source:{...bundle.source,status:'stale'}};
    const p=new Providers(store,{roadPackLoader:async()=>stale});let calls=0;
    p.cached=async()=>{calls++;return {data:null,source:{status:'unavailable',lastSuccess:null}};};
    const result=await p.osm({id:'raleigh'});
    assert.equal(calls,1);assert.equal(result.source.status,'stale');assert.equal(result.source.lastSuccess,bundle.source.lastSuccess);
    assert.equal(result.data.roads.fetchedAt,bundle.data.roads.fetchedAt);
  }finally{store.close();}
});
