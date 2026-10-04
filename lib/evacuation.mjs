import {bboxGeometry,pointInGeometry,validCoordinate,distanceKm,segmentIntersectsGeometry,distanceToGeometryKm} from './geo.mjs';
import {createRoutePlanner,edgeRisk} from './routing.mjs';

export const HAZARDS=['flood','hurricane','heat'];
export const DEFAULT_CLEARANCE_KM=1;
export function warningClearanceKm(point,incidents){return Math.min(...incidents.map(i=>distanceToGeometryKm(point,i.geometry)));}
const active=(i,now)=>!i.expires||Date.parse(i.expires)>now;
export function evacuationIncidents(snapshot,hazard,now=Date.now()) {
  if(!HAZARDS.includes(hazard))throw new Error('Choose flood, hurricane, or heat.');
  // A demo is one independent scenario. Live routing also considers other known warnings.
  return snapshot.incidents.filter(i=>active(i,now)&&(snapshot.mode!=='demo'||i.category===hazard));
}
export function destinationCandidates(snapshot,hazard,now=Date.now(),minimumClearanceKm=DEFAULT_CLEARANCE_KM) {
  const incidents=evacuationIncidents(snapshot,hazard,now);
  // OSM amenity=shelter commonly means a picnic/bus shelter, not an evacuation site.
  const kinds=['community_centre','library','hospital','clinic'];
  const eligible=snapshot.facilities.filter(f=>kinds.includes(f.kind)&&!['no','private'].includes(f.access)&&!incidents.some(i=>pointInGeometry(f.coordinates,i.geometry))&&warningClearanceKm(f.coordinates,incidents)>=minimumClearanceKm);
  // OSM can tag several buildings at one facility. Keep a stable mapped
  // representative after eligibility checks, without averaging locations.
  const groups=new Map(),unique=[];
  for(const facility of [...eligible].sort((a,b)=>String(a.id).localeCompare(String(b.id)))){
    const name=facility.name?.normalize('NFKC').toLowerCase().replace(/[.]/g,'').replace(/\s+/g,' ').trim();
    if(!name||name.startsWith('unnamed ')){unique.push(facility);continue;}
    const key=`${facility.kind}:${name}`,nearby=groups.get(key)||[];
    if(nearby.some(existing=>distanceKm(existing.coordinates,facility.coordinates)<=.1))continue;
    nearby.push(facility);groups.set(key,nearby);unique.push(facility);
  }
  return unique;
}
export function planEvacuation(snapshot,{start,hazard,destinationId=null,minimumClearanceKm=DEFAULT_CLEARANCE_KM},options={}) {
  if(!Number.isFinite(minimumClearanceKm)||minimumClearanceKm<0||minimumClearanceKm>20)throw new Error('Choose a warning clearance between 0 and 20 km.');
  const now=options.now??Date.now(),incidents=evacuationIncidents(snapshot,hazard,now);
  if(!validCoordinate(start)||!pointInGeometry(start,bboxGeometry(snapshot.region.bbox)))throw new Error('Set your location inside the selected coverage area.');
  const selected=incidents.filter(i=>i.category===hazard),originInside=selected.some(i=>pointInGeometry(start,i.geometry));
  const base={hazard,origin:start,originInside,mode:snapshot.mode,createdAt:new Date(now).toISOString(),snapshotAt:snapshot.fetchedAt,alternatives:[],selectedWarnings:selected.map(i=>({id:i.id,title:i.title,url:i.url||null,instruction:i.instruction,expires:i.expires})),otherHazardsChecked:[...new Set(incidents.filter(i=>i.category!==hazard).map(i=>i.category))]};
  const outcome=(status,message,extra={})=>({...base,status,message,...extra});
  if((options.requireMappedRoads||snapshot.requireMappedRoads)&&snapshot.roads?.basis!=='osm'&&snapshot.roadInfo?.basis!=='osm')return outcome('incomplete_data','Real street data is unavailable in this pack. Reconnect, refresh until OpenStreetMap roads load, and save a new offline pack. Fictional grid roads cannot be used for a street route.');
  if(snapshot.mode==='demo'&&snapshot.demoRouting?.version!==2)return outcome('incomplete_data','This saved demo uses an older road network. Reconnect, refresh conditions, and save a new offline pack.');
  if(snapshot.mode==='live'){
    const required=['nws-','osm-'].map(prefix=>snapshot.sources.find(s=>s.id.startsWith(prefix)));
    if(required.some(s=>!s||!['live','cached'].includes(s.status)))return outcome('incomplete_data','Current warning and road data are required. Refresh conditions or follow official evacuation directions.');
    if(required.some((s,index)=>!Number.isFinite(Date.parse(s.lastSuccess))||now-Date.parse(s.lastSuccess)>(index===0?15*60000:25*3600000)))return outcome('stale_data','The warning or road data is too old to calculate a new exit route. Reconnect and refresh.');
    if(options.offline&&(!Number.isFinite(Date.parse(snapshot.fetchedAt))||now-Date.parse(snapshot.fetchedAt)>30*60000))return outcome('stale_data','This offline pack is older than 30 minutes. Reconnect for a new route.');
  }
  if(incidents.some(i=>!i.geometry))return outcome('incomplete_data','A relevant warning has no mapped boundary. The app cannot establish a route outside it.');
  if(!selected.length)return outcome('no_warning',`No active mapped ${hazard==='heat'?'heat':hazard} warning was returned for this area. That does not establish safe conditions or recommend evacuation.`);
  let candidates=destinationCandidates(snapshot,hazard,now,minimumClearanceKm);
  if(destinationId){candidates=candidates.filter(f=>f.id===destinationId);if(!candidates.length)return outcome('destination_unavailable',`That destination is unavailable, overlaps a warning, or is less than ${minimumClearanceKm} km beyond its boundary. Choose another location.`);}
  if(!candidates.length)return outcome('no_destination',`No eligible resource at least ${minimumClearanceKm} km beyond the mapped warnings was found in this coverage area. Check official evacuation destinations; a larger coverage area may be needed.`);
  if(!snapshot.roads?.nodes?.length)return outcome('incomplete_data','The road network is unavailable. Download current road data before planning.');
  if(snapshot.mode==='demo'&&snapshot.roads.simulation&&hazard==='flood'&&snapshot.roads.demoFloodedAreas?.some(i=>pointInGeometry(start,i.geometry)))return outcome('no_route','This point is inside a red simulated flooded area. No dry access route can be established. Choose a point outside the red patch to test another demo route.');
  let findRoute;
  try{findRoute=createRoutePlanner(snapshot.roads,start,incidents,snapshot.reports||[],{now,preferLowExposure:true,allowDemoPassages:snapshot.mode==='demo',checkAccess:true,requireOutside:true,exitGeometries:selected.map(i=>i.geometry)});}
  catch(error){return outcome('no_route',error.message);}
  const routes=[];
  for(const destination of candidates){
    try{
      const result=findRoute(destination.coordinates);
      // Check where the road actually stops too, not just the facility marker.
      const warningClearance=Math.min(warningClearanceKm(destination.coordinates,incidents),warningClearanceKm(result.access.end,incidents));
      if(warningClearance<minimumClearanceKm)continue;
      routes.push({...result,warningClearanceKm:warningClearance,destination:{...destination,availability:'Unverified'},originInside,outsideSelectedArea:true,straightLineKm:distanceKm(start,destination.coordinates)});
    }catch{/* Unreachable destinations are excluded, never replaced with straight lines. */}
  }
  routes.sort((a,b)=>a.exposureCost-b.exposureCost||a.distanceKm-b.distanceKm);
  if(!routes.length)return outcome('no_route',hazard==='flood'&&originInside?`No usable exit to a resource at least ${minimumClearanceKm} km beyond the warning boundaries was established. The app will not route through excluded flood segments. Follow official instructions; never drive through floodwater.`:`No connected route with an arrival at least ${minimumClearanceKm} km beyond the warning boundaries was found. Roads may be excluded or coverage may be too small. Follow official evacuation directions.`);
  const alternatives=routes.slice(0,3);
  addComparisons(snapshot,start,incidents,selected,alternatives,now);
  return outcome('routes_found',originInside?'A planning route reaches a mapped resource outside the warning areas. Confirm the destination is open and follow official directions.':'Your location is outside the selected mapped warning. This is a resource-access route; the app has not established a need to evacuate.',{
    alternatives,minimumClearanceKm,evaluatedDestinations:candidates.length,reachableDestinations:routes.length,
    explanation:`Destinations and their road arrival points must be at least ${minimumClearanceKm} km beyond all warning boundaries checked. This planning preference is not an official safety distance. Eligible routes prioritize least mapped hazard exposure, then shortest road distance. These checks cannot establish actual road safety.`,
    notice:snapshot.mode==='demo'?'Simulated disaster. Flood exits use invented dry-road conditions that are never used with live warnings.':'Resource opening, shelter status, road passability, and turn restrictions are unverified.'
  });
}

function addComparisons(snapshot,start,incidents,selected,routes,now){
  const graph=snapshot.roads,demo=snapshot.mode==='demo'&&graph.simulation;
  const checked=demo&&incidents.some(i=>i.category==='flood'&&i.simulation)?[...incidents,...(graph.demoFloodedAreas||[])]:incidents;
  try{
    // Same origin road segment, destination road node, driving access, and one-way
    // rules. Hazard restrictions alone are removed for this explanatory baseline.
    const shortest=createRoutePlanner(graph,start,[],[],{now,allowDemoPassages:false,accessEdgeId:routes[0].access.edgeId});
    const nodes=new Map(graph.nodes.map(n=>[n.id,n.coordinates])),edges=new Map(graph.edges.map(e=>[e.id,e]));
    for(const route of routes){
      try{
        const baseline=shortest(route.destination.coordinates,{destinationEdgeId:route.access.endEdgeId}),excluded=[],reasons=new Set();let exposureKm=0,excludedKm=0;
        for(const segment of baseline.segments){
          const edge=edges.get(segment.edgeId),[a,b]=segment.coordinates;
          const risk=edgeRisk(nodes.get(edge.from),nodes.get(edge.to),checked,snapshot.reports||[],now,demo?edge.demoPassages||[]:[]);
          const reentry=selected.some(i=>!pointInGeometry(a,i.geometry)&&segmentIntersectsGeometry(a,b,i.geometry));
          if(risk.penalty>1||risk.blocked)exposureKm+=segment.distanceKm;
          if(risk.blocked||reentry){excludedKm+=segment.distanceKm;excluded.push(segment.coordinates);}
          risk.reasons.forEach(reason=>reasons.add(reason));if(reentry)reasons.add('Re-enters a selected warning area');
        }
        const samePath=baseline.geometry.coordinates.length===route.geometry.coordinates.length&&baseline.geometry.coordinates.every((p,i)=>distanceKm(p,route.geometry.coordinates[i])<.000001);
        route.comparison={status:'available',samePath,extraDistanceKm:Math.max(0,route.distanceKm-baseline.distanceKm),
          shortest:{comparisonOnly:true,geometry:baseline.geometry,distanceKm:baseline.distanceKm,exposureKm,excludedKm,reasons:[...reasons],excludedGeometry:{type:'MultiLineString',coordinates:excluded}},
          explanation:'Shortest by road distance in the loaded network, using the same road access points and one-way rules. It ignores hazard restrictions and is shown for comparison only. Distances exclude unverified access gaps.'};
      }catch{route.comparison={status:'unavailable',explanation:'A shortest-route comparison could not be calculated for this destination.'};}
    }
  }catch{for(const route of routes)route.comparison={status:'unavailable',explanation:'The shortest-route comparison is unavailable; the planning route still passed its configured checks.'};}
}
