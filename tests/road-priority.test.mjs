import test from 'node:test';
import assert from 'node:assert/strict';
import {planRoute} from '../lib/routing.mjs';
import {bboxGeometry} from '../lib/geo.mjs';
import {demoSnapshot} from '../lib/demo.mjs';
import {planEvacuation} from '../lib/evacuation.mjs';

test('an unexposed road detour wins even when the exposed shortcut has lower weighted distance',()=>{
  const nodes=[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.002,0]},{id:'c',coordinates:[0,.01]},{id:'d',coordinates:[.002,.01]}];
  const edges=[['a','b','shortcut'],['a','c','west'],['c','d','north'],['d','b','east']].map(([from,to,name])=>({id:name,from,to,name}));
  const graph={nodes,edges},heat={category:'heat',title:'Heat warning',geometry:bboxGeometry([.0005,-.0001,.0015,.0001])};
  const shortest=planRoute(graph,[0,0],[.002,0],[heat]);
  const route=planRoute(graph,[0,0],[.002,0],[heat],[],{preferLowExposure:true});
  assert.deepEqual(shortest.streets,['shortcut']);
  assert.deepEqual(route.streets,['west','north','east']);
  assert.equal(route.exposureKm,0);assert.ok(route.distanceKm>shortest.weightedCost);
  assert.deepEqual(route.geometry.coordinates,nodes.filter(n=>['a','c','d','b'].includes(n.id)).sort((a,b)=>['a','c','d','b'].indexOf(a.id)-['a','c','d','b'].indexOf(b.id)).map(n=>n.coordinates));
});
test('normal app rejects fictional grid packs instead of drawing them as street routes',()=>{
  const snapshot=demoSnapshot();snapshot.requireMappedRoads=true;
  const request={start:snapshot.demoOrigins.flood,hazard:'flood'};
  const result=planEvacuation(snapshot,request);
  assert.equal(result.status,'incomplete_data');assert.equal(result.alternatives.length,0);
  assert.match(result.message,/Fictional grid/);
  delete snapshot.requireMappedRoads;
  assert.equal(planEvacuation(snapshot,request,{offline:true,requireMappedRoads:true}).status,'incomplete_data');
});
