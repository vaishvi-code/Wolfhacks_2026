import test from 'node:test';
import assert from 'node:assert/strict';
import {bboxGeometry,distanceToGeometryKm} from '../lib/geo.mjs';
import {destinationCandidates,planEvacuation,warningClearanceKm,evacuationIncidents} from '../lib/evacuation.mjs';
import {demoSnapshot} from '../lib/demo.mjs';

test('clearance measures nearest boundary, not center, for polygons and collections',()=>{
  const polygon=bboxGeometry([0,0,.1,.1]);
  assert.equal(distanceToGeometryKm([.05,.05],polygon),0);
  assert.equal(distanceToGeometryKm([.1,.05],polygon),0);
  assert.ok(distanceToGeometryKm([.101,.05],polygon)<.12);
  const second=bboxGeometry([.2,0,.3,.1]);
  assert.equal(distanceToGeometryKm([.19,.05],{type:'MultiPolygon',coordinates:[polygon.coordinates,second.coordinates]}),distanceToGeometryKm([.19,.05],second));
  assert.equal(distanceToGeometryKm([.19,.05],{type:'GeometryCollection',geometries:[polygon,second]}),distanceToGeometryKm([.19,.05],second));
  const hole={type:'Polygon',coordinates:[polygon.coordinates[0],bboxGeometry([.02,.02,.08,.08]).coordinates[0]]};
  assert.ok(distanceToGeometryKm([.05,.05],hole)>3);
  assert.ok(Number.isNaN(distanceToGeometryKm([0,0],null)));
});

test('a resource just outside the warning is excluded by default',()=>{
  const s=demoSnapshot(),[x,y]=s.region.center;
  s.facilities.push({id:'border',kind:'library',name:'Boundary library',coordinates:[x+.0361,y-.012]});
  assert.ok(destinationCandidates(s,'flood',Date.now(),0).some(f=>f.id==='border'));
  assert.ok(!destinationCandidates(s,'flood').some(f=>f.id==='border'));
  assert.equal(planEvacuation(s,{start:s.demoOrigins.flood,hazard:'flood',destinationId:'border'}).status,'destination_unavailable');
});

test('online and offline plans enforce the selected distance without a closer fallback',()=>{
  const s=demoSnapshot(),request={start:s.demoOrigins.flood,hazard:'flood',minimumClearanceKm:2};
  const online=planEvacuation(s,request),offline=planEvacuation(structuredClone(s),request,{offline:true});
  assert.equal(online.status,'routes_found');
  const incidents=evacuationIncidents(s,'flood');
  for(const r of online.alternatives){assert.ok(warningClearanceKm(r.destination.coordinates,incidents)>=2);assert.ok(warningClearanceKm(r.access.end,incidents)>=2);assert.ok(r.warningClearanceKm>=2);}
  assert.deepEqual(offline.alternatives.map(r=>r.destination.id),online.alternatives.map(r=>r.destination.id));
  const none=planEvacuation(s,{...request,minimumClearanceKm:20});
  assert.equal(none.status,'no_destination');assert.deepEqual(none.alternatives,[]);
  assert.throws(()=>planEvacuation(s,{...request,minimumClearanceKm:'2'}),/clearance/);
});

test('facility clearance cannot hide a road arrival too close to the warning',()=>{
  const s=demoSnapshot(),[x,y]=s.region.center;
  // Facility is 1.1 km east of boundary; its only road ends 0.75 km away.
  s.incidents.find(i=>i.category==='flood').geometry=bboxGeometry([x,y-.02,x+.036,y+.01]);
  s.facilities=[{id:'remote',kind:'clinic',coordinates:[x+.0482,y-.012]}];
  s.roads={nodes:[{id:'a',coordinates:[x+.036,y+.016]},{id:'b',coordinates:[x+.0443,y+.016]},{id:'c',coordinates:[x+.0443,y-.012]}],edges:[{id:'ab',from:'a',to:'b'},{id:'bc',from:'b',to:'c'}],simulation:false};
  const req={hazard:'flood',start:s.roads.nodes[0].coordinates};
  assert.equal(destinationCandidates(s,'flood').length,1);
  assert.equal(planEvacuation(s,req).status,'no_route');
  assert.equal(planEvacuation(s,{...req,minimumClearanceKm:0}).status,'routes_found');
});
