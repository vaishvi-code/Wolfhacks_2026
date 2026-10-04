import test from 'node:test';
import assert from 'node:assert/strict';
import {demoSnapshot,demoWithOSM} from '../lib/demo.mjs';
import {planEvacuation} from '../lib/evacuation.mjs';
import {DisasterService} from '../lib/service.mjs';
import {Store} from '../lib/store.mjs';

function fixture(){
  const scenario=demoSnapshot(),[x,y]=scenario.region.center;
  const points={a:[x+.012,y-.012],c:[x+.052,y-.012],d:[x+.012,y+.023],e:[x+.052,y+.023]};
  const roads={nodes:Object.entries(points).map(([id,coordinates])=>({id,coordinates})),edges:[['a','c'],['a','d'],['d','e'],['e','c']].map(([from,to])=>({id:from+to,from,to,name:`Mapped ${from}-${to}`,oneway:false})),simulation:false,fetchedAt:new Date().toISOString()};
  const osm={data:{roads,facilities:[{id:'osm-test',kind:'clinic',name:'Mapped clinic',coordinates:points.c,simulation:false,source:'OpenStreetMap'}]},source:{id:'osm-v2-raleigh',name:'OpenStreetMap',status:'cached',lastSuccess:roads.fetchedAt}};
  return {scenario,osm,points};
}

test('real-street scenario keeps mapped resources, geometry, and source age without mutating live graph',()=>{
  const {scenario,osm}=fixture(),before=structuredClone(osm),s=demoWithOSM(scenario,osm);
  assert.equal(s.demoRouting.basis,'osm');assert.equal(s.roads.simulation,true);assert.equal(s.roads.fetchedAt,osm.source.lastSuccess);
  assert.deepEqual(s.facilities,osm.data.facilities);assert.deepEqual(s.roads.nodes,osm.data.roads.nodes);assert.deepEqual(osm,before);
  assert.ok(s.roads.edges.some(e=>e.demoPassages?.length));assert.ok(s.incidents.every(i=>i.simulation));assert.match(s.sources[0].detail,/simulated/);
});

test('shortest comparison exposes an excluded flood segment while chosen route detours on connected roads',()=>{
  const {scenario,osm,points}=fixture(),s=demoWithOSM(scenario,osm),p=planEvacuation(s,{hazard:'flood',start:points.a});
  assert.equal(p.status,'routes_found');const r=p.alternatives[0],c=r.comparison;
  assert.deepEqual(r.segments.map(s=>s.edgeId),['ad','de','ec']);assert.equal(c.status,'available');assert.equal(c.samePath,false);
  assert.equal(c.shortest.comparisonOnly,true);assert.ok(c.shortest.distanceKm<r.distanceKm);assert.ok(c.shortest.excludedKm>0);
  assert.deepEqual(c.shortest.geometry.coordinates,[points.a,points.c]);assert.deepEqual(c.shortest.excludedGeometry.coordinates,[[points.a,points.c]]);
  assert.deepEqual(r.geometry.coordinates[0],c.shortest.geometry.coordinates[0]);assert.deepEqual(r.geometry.coordinates.at(-1),c.shortest.geometry.coordinates.at(-1));
  assert.ok(Math.abs(c.extraDistanceKm-(r.distanceKm-c.shortest.distanceKm))<1e-9);
  const offline=planEvacuation(structuredClone(s),{hazard:'flood',start:points.a},{offline:true});assert.deepEqual(offline.alternatives[0].comparison,c);
});

test('comparison retains one-way restrictions and reports honestly when the same route wins',()=>{
  const {scenario,osm,points}=fixture();Object.assign(osm.data.roads.edges[0],{from:'c',to:'a',oneway:true});
  const p=planEvacuation(demoWithOSM(scenario,osm),{hazard:'flood',start:points.a}),r=p.alternatives[0];
  assert.equal(r.comparison.samePath,true);assert.equal(r.comparison.extraDistanceKm,0);assert.equal(r.comparison.shortest.excludedKm,0);
});

test('origin projection is identical in hazard and shortest comparisons',()=>{
  const {scenario,osm,points}=fixture(),start=[points.a[0],points.a[1]+.006];
  const p=planEvacuation(demoWithOSM(scenario,osm),{hazard:'flood',start}),r=p.alternatives[0];
  assert.deepEqual(r.geometry.coordinates[0],r.comparison.shortest.geometry.coordinates[0]);assert.equal(r.access.edgeId,'ad');
});

test('OSM outage keeps an explicitly fictional fallback and unsuccessful source status',()=>{
  const s=demoWithOSM(demoSnapshot(),{data:null,source:{id:'osm-v2-raleigh',status:'unavailable',detail:'Provider timed out.'}});
  assert.equal(s.demoRouting.basis,'synthetic');assert.equal(s.demoRouting.fallbackReason,'Provider timed out.');assert.ok(s.facilities.every(f=>f.simulation));assert.equal(s.sources[1].status,'unavailable');
});

test('service uses real streets in normal and offline demo responses',async()=>{
  const store=new Store(':memory:');try{
    const service=new DisasterService(store,undefined,{demoRoads:'real'}),{osm}=fixture();service.providers.osm=async()=>osm;
    const small=await service.snapshot('raleigh','demo'),pack=await service.snapshot('raleigh','demo',true);
    assert.equal(small.roads,undefined);assert.equal(small.roadInfo.basis,'osm');assert.equal(small.roadInfo.segments,4);assert.equal(pack.roads.edges.length,4);assert.equal(pack.facilities[0].id,'osm-test');
  }finally{store.close();}
});
