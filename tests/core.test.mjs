import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyWeather,normalizeWeather,parseReadings,riverSignal,exposure,actionsFor} from '../lib/model.mjs';
import {pointInGeometry,segmentIntersectsGeometry,segmentDistanceKm,bboxGeometry} from '../lib/geo.mjs';
import {planRoute,parseRoads,edgeRisk} from '../lib/routing.mjs';
import {demoSnapshot} from '../lib/demo.mjs';
import {Store} from '../lib/store.mjs';
import {Providers} from '../lib/providers.mjs';
import {DisasterService} from '../lib/service.mjs';
import {generateBrief} from '../lib/integrations.mjs';
const square=bboxGeometry([0,0,1,1]);
test('geometry supports boundaries, holes, multipolygons, and collection gaps',()=>{
  assert.ok(pointInGeometry([.5,.5],square));assert.ok(pointInGeometry([0,.5],square));assert.ok(!pointInGeometry([2,.5],square));
  const hole={...square,coordinates:[...square.coordinates,[[.2,.2],[.8,.2],[.8,.8],[.2,.8],[.2,.2]]]};
  assert.ok(!pointInGeometry([.5,.5],hole));assert.ok(pointInGeometry([.1,.1],hole));
  assert.ok(pointInGeometry([.5,.5],{type:'MultiPolygon',coordinates:[square.coordinates]}));
  assert.ok(!pointInGeometry([1.5,.5],{type:'GeometryCollection',geometries:[square,bboxGeometry([2,0,3,1])]}));
});
test('road crossing a polygon is detected even when both endpoints are outside',()=>{
  assert.ok(segmentIntersectsGeometry([-1,.5],[2,.5],square));assert.ok(!segmentIntersectsGeometry([-1,2],[2,2],square));
  assert.ok(segmentDistanceKm([.5,.001],[0,0],[1,0])<.12);
});
test('only the three requested hazard families are classified',()=>{
  assert.equal(classifyWeather('Tropical Storm Warning'),'hurricane');assert.equal(classifyWeather('Storm Surge Warning'),'flood');assert.equal(classifyWeather('Extreme Heat Warning'),'heat');assert.equal(classifyWeather('Earthquake'),null);assert.equal(classifyWeather('Severe Thunderstorm Warning'),null);
});
test('NWS test alerts and expired alerts cannot become current hazards',()=>{
  const now=Date.now(),fixture=(extra={})=>({id:'https://api.weather.gov/alerts/example',geometry:square,properties:{event:'Flash Flood Warning',severity:'Severe',urgency:'Immediate',status:'Actual',sent:new Date(now-1000).toISOString(),expires:new Date(now+60000).toISOString(),...extra}});
  const normalized=normalizeWeather({features:[fixture(),fixture({status:'Test'}),fixture({expires:new Date(now-1).toISOString()}),fixture({event:'Earthquake'})]},now);
  assert.equal(normalized.length,1);assert.equal(normalized[0].score,80);assert.equal(normalized[0].simulation,false);
});
test('unknown NWS severity stays unrated',()=>{const [i]=normalizeWeather({features:[{geometry:null,properties:{event:'Heat Advisory',severity:'Unknown',status:'Actual',expires:new Date(Date.now()+60000).toISOString()}}]});assert.equal(i.score,null);assert.equal(i.geometry,null);});
test('USGS parser validates station, parameter, units, timestamp and value; negative datum is valid',()=>{
  const prop={monitoring_location_id:'USGS-123',parameter_code:'00065',statistic_id:'00011',unit_of_measure:'ft',time:'2026-10-03T12:00:00Z',value:'-0.5',approval_status:'Approved'};
  const rows=parseReadings({features:[prop,{...prop,value:'1.2'},{...prop,value:null},{...prop,value:''},{...prop,value:'-999999'},{...prop,parameter_code:'00060'},{...prop,unit_of_measure:'m'},{...prop,time:'invalid'}].map(properties=>({properties}))},'123');
  assert.equal(rows.length,1);assert.equal(rows[0].value,1.2);assert.equal(rows[0].provisional,false);
  assert.equal(parseReadings({features:[{properties:prop}]},'123')[0].value,-.5);
});
test('river changes require real historical coverage and freshness is measured from observation time',()=>{
  const now=Date.now(),station={id:'123',name:'Test',coordinates:[0,0]},latest={time:new Date(now).toISOString(),value:2};
  const single=riverSignal(station,[latest],now);assert.equal(single.change,null);assert.equal(single.rate,null);assert.equal(single.fresh,true);
  const old=riverSignal(station,[{...latest,time:new Date(now-3*3600000).toISOString()}],now);assert.equal(old.fresh,false);
  const full=riverSignal(station,[{time:new Date(now-24*3600000).toISOString(),value:1},{time:new Date(now-3600000).toISOString(),value:1.5},latest],now);assert.equal(full.change,1);assert.equal(full.rate,.5);
});
const graph={nodes:[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.002,0]},{id:'c',coordinates:[.002,.002]},{id:'d',coordinates:[0,.002]}],edges:[{from:'a',to:'b',name:'direct',oneway:false},{from:'b',to:'c',name:'east',oneway:false},{from:'a',to:'d',name:'west',oneway:false},{from:'d',to:'c',name:'north',oneway:false}]};
const flooded={id:'f',title:'Flood Warning',category:'flood',score:80,geometry:bboxGeometry([.0008,-.0001,.0012,.0001])};
test('routing detours around a crossed flood segment and reports real graph length',()=>{const route=planRoute(graph,[0,0],[.002,0],[flooded]);assert.equal(route.excludedSegments,1);assert.deepEqual(route.streets,['west','north','east']);assert.ok(route.distanceKm>.6);});
test('routing respects one-way direction and cannot invent a route',()=>{const g={nodes:graph.nodes.slice(0,2),edges:[{from:'a',to:'b',oneway:true,name:'one way'}]};assert.throws(()=>planRoute(g,[.002,0],[0,0],[]),/No connected route/);assert.throws(()=>planRoute(graph,[1,1],[.002,0],[]),/500 m/);});
test('unverified road reports create local temporary exclusions',()=>{const now=Date.now(),report={coordinates:[.001,0],kind:'blocked_road',expires:new Date(now+60000).toISOString()};assert.ok(edgeRisk([0,0],[.002,0],[],[report],now).blocked);assert.ok(!edgeRisk([0,0],[.002,0],[],[{...report,expires:new Date(now-1).toISOString()}],now).blocked);assert.ok(!edgeRisk([0,0],[.002,0],[],[{...report,coordinates:[1,1]}],now).blocked);});
test('wind and heat add costs while expired hazards are ignored',()=>{assert.equal(edgeRisk([0,0],[.002,0],[{...flooded,category:'heat'}]).penalty,2);assert.equal(edgeRisk([0,0],[.002,0],[{...flooded,category:'hurricane'}]).penalty,5);assert.equal(edgeRisk([0,0],[.002,0],[{...flooded,expires:new Date(0).toISOString()}]).blocked,false);});
test('OSM graph excludes restricted ways and models reversed one-way roads',()=>{
  const way={type:'way',id:1,nodes:[1,2],geometry:[{lon:0,lat:0},{lon:.002,lat:0}],tags:{highway:'residential',oneway:'-1'}};
  const g=parseRoads({elements:[way,{...way,id:2,tags:{highway:'footway'}},{...way,id:3,tags:{highway:'residential',access:'private'}}]});assert.equal(g.edges.length,1);assert.equal(g.edges[0].from,'2');assert.equal(g.edges[0].to,'1');assert.ok(g.edges[0].oneway);
});
test('demo is region-specific with exactly three fictional hazards and viable detour workflow',()=>{
  const s=demoSnapshot('raleigh');assert.deepEqual(s.incidents.map(i=>i.category).sort(),['flood','heat','hurricane']);assert.ok(s.facilities.every(f=>f.simulation));
  const result=planRoute(s.roads,[s.region.center[0]-.036,s.region.center[1]-.012],s.facilities[0].coordinates,s.incidents);assert.ok(result.distanceKm>0);assert.ok(result.simulation);assert.ok(result.excludedSegments>0);
  assert.notDeepEqual(demoSnapshot('wilmington').region.center,s.region.center);
});
test('resource exposure is a polygon intersection; outside is not labeled safe',()=>{
  assert.equal(exposure({coordinates:[.5,.5]},[{id:'a',title:'Warning',score:80,geometry:square}]).risk,'High');assert.equal(exposure({coordinates:[2,2]},[{geometry:square}]).risk,'Not assessed');
  const s=demoSnapshot();assert.equal(actionsFor(s)[0].affectedFacilities,1);
});
test('SQLite persistence separates report modes and deduplicates sensor observations',()=>{
  const store=new Store(':memory:'),now=new Date().toISOString(),expires=new Date(Date.now()+60000).toISOString();
  store.addReport({id:'x',region:'raleigh',mode:'demo',expires});store.addReport({id:'y',region:'raleigh',mode:'live',expires});
  assert.equal(store.reports('raleigh','demo').length,1);assert.equal(store.reports('wilmington','demo').length,0);
  store.saveReadings('123',[{time:now,value:1,provisional:true},{time:now,value:2,provisional:false}]);assert.equal(store.readings('123').length,1);assert.equal(store.readings('123')[0].value,2);
  assert.equal(store.pending('tiger','123').length,1);store.delivered('tiger','123',now);assert.equal(store.pending('tiger','123').length,0);assert.equal(store.pending('databricks','123').length,1);store.close();
});
test('provider outage preserves old timestamps and explicitly marks stale data',async()=>{
  const store=new Store(':memory:'),p=new Providers(store);store.set('fixture',{value:42});const prior=store.get('fixture').updated;
  const result=await p.cached('fixture','Fixture',-1,()=>Promise.reject(new Error('offline')));assert.equal(result.source.status,'stale');assert.equal(result.source.lastSuccess,prior);assert.equal(result.data.value,42);
  const missing=await p.cached('missing','Missing',0,()=>Promise.reject(new Error('offline')));assert.equal(missing.source.status,'unavailable');assert.equal(missing.data,null);store.close();
});
test('empty successful feeds replace earlier data and do not masquerade as an outage',async()=>{const store=new Store(':memory:'),p=new Providers(store);store.set('fixture',[1]);const result=await p.cached('fixture','Fixture',-1,async()=>[]);assert.deepEqual(result.data,[]);assert.equal(result.source.status,'live');store.close();});
test('offline packs contain a graph; ordinary snapshots omit the large graph',async()=>{const store=new Store(':memory:'),service=new DisasterService(store,undefined,{demoRoads:'synthetic'});assert.equal((await service.snapshot('raleigh','demo')).roads,undefined);assert.ok((await service.snapshot('raleigh','demo',true)).roads.edges.length);store.close();});
test('key-free briefing remains an explicitly labeled template',async()=>{const old=process.env.GEMINI_API_KEY;delete process.env.GEMINI_API_KEY;try{const result=await generateBrief(demoSnapshot());assert.equal(result.ai,false);assert.match(result.text,/FICTIONAL DEMONSTRATION/);assert.match(result.notice,/GEMINI_API_KEY/);}finally{if(old)process.env.GEMINI_API_KEY=old;}});
