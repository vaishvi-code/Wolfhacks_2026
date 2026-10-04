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

test('destination midway along a long road ends on that road, not a distant intersection',()=>{
  const graph={nodes:[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.02,0]}],edges:[{id:'ab',from:'a',to:'b',name:'Main Street',oneway:true}]};
  const route=planRoute(graph,[.005,0],[.015,.0001],[],[],{checkAccess:true});
  assert.deepEqual(route.geometry.coordinates,[[.005,0],[.015,0]]);
  assert.ok(route.snapDistances.end<.012);
  assert.equal(route.access.endEdgeId,'ab');
  assert.throws(()=>planRoute(graph,[.015,0],[.005,.0001],[]),/No connected route/);
});

test('distinct roads without edge IDs are never treated as the same road',()=>{
  const graph={nodes:[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.002,0]},{id:'c',coordinates:[.002,.002]},{id:'d',coordinates:[0,.002]}],edges:[{from:'a',to:'d',name:'West'},{from:'d',to:'c',name:'North'},{from:'c',to:'b',name:'East'}]};
  const route=planRoute(graph,[0,0],[.002,0],[]);
  assert.deepEqual(route.geometry.coordinates,[[0,0],[0,.002],[.002,.002],[.002,0]]);
});
