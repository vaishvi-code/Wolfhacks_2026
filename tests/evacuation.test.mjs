import test from 'node:test';
import assert from 'node:assert/strict';
import {demoSnapshot} from '../lib/demo.mjs';
import {planEvacuation,destinationCandidates,evacuationIncidents} from '../lib/evacuation.mjs';
import {pointInGeometry,bboxGeometry,segmentIntersectsGeometry,distanceKm} from '../lib/geo.mjs';
import {edgeRisk,createRoutePlanner} from '../lib/routing.mjs';
const now=Date.now();
function live(){const s=demoSnapshot('raleigh',now);s.mode='live';s.roads.simulation=false;s.incidents.forEach(i=>i.simulation=false);s.sources=['nws-raleigh','osm-raleigh'].map(id=>({id,status:'live',lastSuccess:new Date(now).toISOString()}));return s;}

for(const region of ['raleigh','wilmington','asheville'])for(const hazard of ['flood','hurricane','heat'])test(`${region} ${hazard}: starts inside one warning and suggests ranked resources outside it`,()=>{
  const s=demoSnapshot(region,now),plan=planEvacuation(s,{hazard,start:s.demoOrigins[hazard]},{now});
  assert.equal(plan.status,'routes_found');assert.equal(plan.originInside,true);assert.deepEqual(plan.otherHazardsChecked,[]);assert.equal(plan.selectedWarnings.length,1);
  assert.equal(plan.alternatives.length,3);assert.ok(plan.alternatives[0].weightedCost<=plan.alternatives[1].weightedCost);
  const area=s.incidents.find(i=>i.category===hazard).geometry;
  for(const r of plan.alternatives){assert.ok(!pointInGeometry(r.destination.coordinates,area));assert.ok(r.distanceKm>0);assert.equal(r.geometry.type,'LineString');assert.equal(r.destination.availability,'Unverified');assert.ok(r.geometry.coordinates.length>1);let outside=false;for(const p of r.geometry.coordinates){if(!pointInGeometry(p,area))outside=true;else assert.equal(outside,false,'Never re-enter the warning after exiting');}}
});
test('fictional flood corridor cannot be used on a live graph or a live incident',()=>{
  const s=demoSnapshot(),request={hazard:'flood',start:s.demoOrigins.flood};assert.equal(planEvacuation(s,request).status,'routes_found');s.roads.simulation=false;assert.equal(planEvacuation(s,request).status,'no_route');s.roads.simulation=true;s.incidents.find(i=>i.category==='flood').simulation=false;assert.equal(planEvacuation(s,request).status,'no_route');
});
test('an unverified blockage also excludes a fictional dry corridor',()=>{const s=demoSnapshot();s.reports=[{kind:'blocked_road',coordinates:s.demoOrigins.flood,expires:new Date(now+600000).toISOString()}];assert.equal(planEvacuation(s,{hazard:'flood',start:s.demoOrigins.flood},{now}).status,'no_route');});
test('live routes check hidden hazards and never invent an exit through flood exclusions',()=>{const s=live();const p=planEvacuation(s,{hazard:'flood',start:s.demoOrigins.flood},{now});assert.equal(p.status,'no_route');assert.deepEqual(p.otherHazardsChecked.sort(),['heat','hurricane']);assert.deepEqual(evacuationIncidents(s,'hurricane',now).length,3);const candidates=destinationCandidates(s,'hurricane',now);assert.ok(!candidates.some(f=>f.id==='demo-raleigh-f2'));});
test('no matching warning is not presented as a safe route or evacuation order',()=>{const s=live();s.incidents=[];const p=planEvacuation(s,{hazard:'heat',start:s.region.center},{now});assert.equal(p.status,'no_warning');assert.equal(p.alternatives.length,0);assert.match(p.message,/does not establish safe/);});
test('outside-warning origins get a resource access route, not an inferred evacuation need',()=>{const s=demoSnapshot();const p=planEvacuation(s,{hazard:'heat',start:s.region.center});assert.equal(p.status,'routes_found');assert.equal(p.originInside,false);assert.match(p.message,/has not established a need/);});
test('live planner rejects missing, failed, stale, and invalid source timestamps',()=>{const request={hazard:'hurricane',start:live().demoOrigins.hurricane};for(const change of [s=>s.sources.pop(),s=>s.sources[0].status='unavailable',s=>s.sources[0].lastSuccess='invalid',s=>s.sources[0].lastSuccess=new Date(now-16*60000).toISOString(),s=>s.sources[1].lastSuccess=new Date(now-26*3600000).toISOString()]){const s=live();change(s);const p=planEvacuation(s,request,{now});assert.ok(['incomplete_data','stale_data'].includes(p.status));assert.equal(p.alternatives.length,0);}});
test('live offline packs have a 30-minute maximum age even if source timestamps look current',()=>{const s=live();s.fetchedAt=new Date(now-31*60000).toISOString();assert.equal(planEvacuation(s,{hazard:'heat',start:s.demoOrigins.heat},{now,offline:true}).status,'stale_data');});
test('any live warning missing a boundary stops the route, including a hidden category',()=>{const s=live();s.incidents.find(i=>i.category==='flood').geometry=null;assert.equal(planEvacuation(s,{hazard:'hurricane',start:s.demoOrigins.hurricane},{now}).status,'incomplete_data');});
test('destination selection rejects resources inside a warning and gives a requested eligible resource',()=>{const s=demoSnapshot(),req={hazard:'flood',start:s.demoOrigins.flood};assert.equal(planEvacuation(s,{...req,destinationId:'demo-raleigh-f2'}).status,'destination_unavailable');const p=planEvacuation(s,{...req,destinationId:'demo-raleigh-f5'});assert.equal(p.alternatives.length,1);assert.equal(p.alternatives[0].destination.id,'demo-raleigh-f5');});
test('no destinations and no road graph produce explicit outcomes',()=>{const s=demoSnapshot();s.facilities=[];assert.equal(planEvacuation(s,{hazard:'heat',start:s.demoOrigins.heat}).status,'no_destination');const another=demoSnapshot();another.roads=null;assert.equal(planEvacuation(another,{hazard:'heat',start:another.demoOrigins.heat}).status,'incomplete_data');});
test('coordinates must belong to coverage and hazard must be one of the supported choices',()=>{const s=demoSnapshot();assert.throws(()=>planEvacuation(s,{hazard:'flood',start:[0,0]}),/coverage area/);assert.throws(()=>planEvacuation(s,{hazard:'earthquake',start:s.region.center}),/Choose flood/);});
test('generic OSM shelters and explicitly private resources are not evacuation suggestions',()=>{const s=demoSnapshot();s.facilities=[{id:'picnic',kind:'shelter',coordinates:s.region.center},{id:'private',kind:'library',access:'private',coordinates:s.region.center},{id:'public',kind:'library',access:'yes',coordinates:s.region.center}];assert.deepEqual(destinationCandidates(s,'heat').map(f=>f.id),['public']);});
test('snapping cannot bypass a flood exclusion between the person and the road',()=>{const g={nodes:[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.002,0]}],edges:[{from:'a',to:'b',name:'Road'}]},f={category:'flood',score:90,geometry:bboxGeometry([-.0001,.0005,.0001,.0015])};assert.throws(()=>createRoutePlanner(g,[0,.002],[f],[],{checkAccess:true}),/connection from your location/);assert.ok(edgeRisk([0,.002],[0,0],[f]).blocked);});

test('the reported custom flood point and a slight move from the example both route',()=>{
  const s=demoSnapshot();
  for(const start of [[-78.62016,35.79015],[-78.6261,35.7797],[-78.615,35.775]]){
    const p=planEvacuation(s,{hazard:'flood',start,destinationId:'demo-raleigh-f6'});
    assert.equal(p.status,'routes_found',p.message);assert.equal(p.originInside,true);
    const r=p.alternatives[0];assert.equal(r.destination.id,'demo-raleigh-f6');assert.ok(r.snapDistances.start<.17);assert.equal(r.access.verified,false);assert.deepEqual(r.geometry.coordinates[0],r.access.start);
  }
});
for(const region of ['raleigh','wilmington','asheville'])test(`${region}: custom points throughout the flood warning can use dry roads, never flooded patches`,()=>{
  const s=demoSnapshot(region),[x,y]=s.region.center,flood=s.incidents.find(i=>i.category==='flood'),patches=s.roads.demoFloodedAreas;let checked=0;
  for(let row=0;row<5;row++)for(let col=0;col<6;col++){
    const start=[x+.0023+col*.0051,y-.0191+row*.0067];
    if(!pointInGeometry(start,flood.geometry)||patches.some(i=>pointInGeometry(start,i.geometry)))continue;
    const p=planEvacuation(s,{hazard:'flood',start});assert.equal(p.status,'routes_found',JSON.stringify({start,message:p.message}));checked++;
    const r=p.alternatives[0],line=[start,...r.geometry.coordinates,r.destination.coordinates];
    for(const patch of patches)for(let i=1;i<line.length;i++)assert.ok(!segmentIntersectsGeometry(line[i-1],line[i],patch.geometry),'Neither access gaps nor the road path may cross simulated flooding');
  }
  assert.ok(checked>=20,'Sample custom points across the warning, not just the default origin');
});
test('a point inside simulated floodwater gets a specific no-route outcome',()=>{const s=demoSnapshot(),[x,y]=s.region.center,p=planEvacuation(s,{hazard:'flood',start:[x+.022,y-.012]});assert.equal(p.status,'no_route');assert.match(p.message,/red simulated flooded area/);assert.deepEqual(p.alternatives,[]);});
test('live mode cannot enable demo passages even if a graph is mistakenly tagged simulated',()=>{const s=live();s.roads.simulation=true;s.incidents.forEach(i=>i.simulation=true);assert.equal(planEvacuation(s,{hazard:'flood',start:s.demoOrigins.flood},{now}).status,'no_route');});
test('obsolete offline demo packs request an update instead of reusing the broken network',()=>{const s=demoSnapshot();delete s.demoRouting;const p=planEvacuation(s,{hazard:'flood',start:s.demoOrigins.flood},{offline:true});assert.equal(p.status,'incomplete_data');assert.match(p.message,/older road network/);});
test('starting midway along a long road works without a nearby intersection node',()=>{
  const g={nodes:[{id:'a',coordinates:[0,0]},{id:'b',coordinates:[.02,0]}],edges:[{from:'a',to:'b',name:'Long road',oneway:true}]};
  const find=createRoutePlanner(g,[.01,.0001],[],[],{checkAccess:true}),r=find([.02,0]);
  assert.deepEqual(r.geometry.coordinates[0],[.01,0]);assert.ok(r.snapDistances.start<.012);assert.ok(Math.abs(r.distanceKm-distanceKm([.01,0],[.02,0]))<.0001);assert.throws(()=>find([0,0]),/No connected route/);
});
