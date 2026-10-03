import {distanceKm,segmentDistanceKm,segmentIntersectsGeometry,pointInGeometry,projectOnSegment} from './geo.mjs';
export function parseRoads(data) {
  const nodes=new Map(),edges=[];
  for(const way of data.elements||[]) {
    const t=way.tags||{};
    if(way.type!=='way'||!t.highway||!way.geometry||!way.nodes) continue;
    if(/^(footway|path|cycleway|steps|pedestrian|track|construction|proposed|bridleway|corridor)$/.test(t.highway)||['no','private'].includes(t.access)||['no','private'].includes(t.motor_vehicle)||t.motorcar==='no') continue;
    way.nodes.forEach((id,i)=>{const p=way.geometry[i]; if(p) nodes.set(String(id),{id:String(id),coordinates:[p.lon,p.lat]});});
    for(let i=1;i<way.nodes.length;i++) {
      let from=String(way.nodes[i-1]),to=String(way.nodes[i]);
      if(!nodes.has(from)||!nodes.has(to)) continue;
      if(t.oneway==='-1') [from,to]=[to,from];
      const oneway=['yes','1','true','-1'].includes(t.oneway) || (!['no','0','false'].includes(t.oneway) && (t.junction==='roundabout'||t.highway==='motorway'));
      edges.push({id:`${way.id}-${i}`,from,to,name:t.name||t.ref||t.highway,oneway,bridge:!!t.bridge&&t.bridge!=='no'});
    }
  }
  return {nodes:[...nodes.values()],edges,simulation:false,fetchedAt:new Date().toISOString()};
}
export function edgeRisk(a,b,incidents,reports=[],now=Date.now(),demoPassages=[]) {
  let penalty=1, blocked=false; const reasons=[];
  for(const i of incidents) {
    if(i.expires && Date.parse(i.expires)<=now) continue;
    if(!segmentIntersectsGeometry(a,b,i.geometry)) continue;
    if(i.category==='flood' && (i.score??0)>=70 && !(i.simulation && demoPassages.includes(i.id))) {blocked=true;reasons.push('Excluded by flood warning footprint (conservative planning rule)');}
    else if(i.category==='flood' && i.simulation && demoPassages.includes(i.id)) {penalty=Math.max(penalty,3);reasons.push('Fictional dry exit corridor in this demo only');}
    else {penalty=Math.max(penalty,i.category==='heat'?2:5);reasons.push(`${i.title} overlap`);}
  }
  for(const report of reports) {
    if(Date.parse(report.expires)<=now||segmentDistanceKm(report.coordinates,a,b)>.075) continue;
    if(['flooded_road','blocked_road','fallen_tree'].includes(report.kind)) {blocked=true;reasons.push('Excluded near an unverified community report');}
    else {penalty=Math.max(penalty,3);reasons.push('Unverified community report nearby');}
  }
  return {blocked,penalty,reasons};
}
class MinHeap {
  values=[];
  push(item){const a=this.values;a.push(item);let i=a.length-1;while(i>0){const p=(i-1)>>1;if(a[p][0]<=item[0])break;a[i]=a[p];i=p;}a[i]=item;}
  pop(){const a=this.values,first=a[0],last=a.pop();if(a.length){let i=0;while(i*2+1<a.length){let c=i*2+1;if(c+1<a.length&&a[c+1][0]<a[c][0])c++;if(a[c][0]>=last[0])break;a[i]=a[c];i=c;}a[i]=last;}return first;}
}
// Build the graph and run one search for all candidate destinations.
export function createRoutePlanner(graph,start,incidents,reports=[],options={}) {
  if(!graph?.nodes?.length||!graph.edges?.length) throw new Error('Road graph unavailable. Load local OSM data first.');
  const now=options.now??Date.now(),demo=graph.simulation&&options.allowDemoPassages!==false;
  // These extra flooded polygons are fictional obstacles, and never live evidence.
  const checkedIncidents=demo&&incidents.some(i=>i.category==='flood'&&i.simulation)?[...incidents,...(graph.demoFloodedAreas||[])]:incidents;
  const nearest=p=>graph.nodes.reduce((best,n)=>{const d=distanceKm(p,n.coordinates);return !best||d<best.distance?{...n,distance:d}:best;},null);
  const nodes=new Map(graph.nodes.map(n=>[n.id,n])),adj=new Map(),accessCandidates=[];let excluded=0,nearestRoad=Infinity;
  const leavesOrStaysOutside=(a,b)=>!(options.exitGeometries||[]).some(g=>!pointInGeometry(a,g)&&segmentIntersectsGeometry(a,b,g));
  const add=(source,target,name,risk,edgeId)=>{
    const a=nodes.get(source).coordinates,b=nodes.get(target).coordinates;
    if(!leavesOrStaysOutside(a,b))return;
    const distance=distanceKm(a,b),cost=distance*risk.penalty;
    if(!adj.has(source))adj.set(source,[]);adj.get(source).push({to:target,cost,distance,name,risk,edgeId});
  };
  for(const e of graph.edges){
    const a=nodes.get(e.from)?.coordinates,b=nodes.get(e.to)?.coordinates;if(!a||!b)continue;
    const projection=projectOnSegment(start,a,b);nearestRoad=Math.min(nearestRoad,projection.distance);
    const passages=demo?e.demoPassages||[]:[];
    const risk=edgeRisk(a,b,checkedIncidents,reports,now,passages);if(risk.blocked){excluded++;continue;}
    add(e.from,e.to,e.name,risk,e.id);if(!e.oneway)add(e.to,e.from,e.name,risk,e.id);
    // Connect to a road segment, not just its possibly distant intersection nodes.
    // Any gap remains explicitly unverified; demo exceptions apply only to tagged roads.
    if((!options.accessEdgeId||e.id===options.accessEdgeId)&&projection.distance<=.5&&leavesOrStaysOutside(start,projection.coordinates)&&(options.checkAccess===false||!edgeRisk(start,projection.coordinates,checkedIncidents,reports,now,passages).blocked))accessCandidates.push({...projection,edge:e,risk});
  }
  accessCandidates.sort((a,b)=>a.distance-b.distance);
  const access=accessCandidates[0];
  if(!access){
    if(nearestRoad>.5)throw new Error('Your location is more than 500 m from the loaded road network. Choose a closer point.');
    throw new Error('No usable road connection from your location was found. Nearby roads or the connection from your location cross an excluded area.');
  }
  const edge=access.edge;
  let from;
  if(access.t<1e-8)from={...nodes.get(edge.from),distance:access.distance};
  else if(access.t>1-1e-8)from={...nodes.get(edge.to),distance:access.distance};
  else{
    from={id:Symbol('origin'),coordinates:access.coordinates,distance:access.distance};nodes.set(from.id,from);
    add(from.id,edge.to,edge.name,access.risk,edge.id);if(!edge.oneway)add(from.id,edge.from,edge.name,access.risk,edge.id);
  }
  const queue=new MinHeap(),dist=new Map([[from.id,0]]),prev=new Map();queue.push([0,from.id]);
  while(queue.values.length){const [cost,id]=queue.pop();if(cost!==dist.get(id))continue;for(const edge of adj.get(id)||[]){const next=cost+edge.cost;if(next<(dist.get(edge.to)??Infinity)){dist.set(edge.to,next);prev.set(edge.to,{from:id,edge});queue.push([next,edge.to]);}}}
  return end=>{
  const to=nearest(end);
  if(to.distance>.5) throw new Error('The destination is more than 500 m from the loaded road network.');
  if(from.id===to.id) throw new Error('These locations use the same road access point. Select a different origin or destination.');
  if(options.checkAccess!==false && edgeRisk(to.coordinates,end,checkedIncidents,reports,now).blocked)throw new Error('The destination connection crosses an excluded area.');
  if(options.requireOutside && incidents.some(i=>pointInGeometry(to.coordinates,i.geometry)||segmentIntersectsGeometry(to.coordinates,end,i.geometry)))throw new Error('The destination road access point overlaps a warning area.');
  if(!dist.has(to.id)) throw new Error('No connected route is available under these exclusions. Do not infer that another road is passable.');
  const path=[to.id],steps=[];let current=to.id;
  while(current!==from.id){const p=prev.get(current);steps.unshift(p.edge);current=p.from;path.unshift(current);}
  return {geometry:{type:'LineString',coordinates:path.map(id=>nodes.get(id).coordinates)},distanceKm:steps.reduce((n,e)=>n+e.distance,0),weightedCost:dist.get(to.id),excludedSegments:excluded,
    segments:steps.map((e,i)=>({edgeId:e.edgeId,coordinates:[nodes.get(path[i]).coordinates,nodes.get(path[i+1]).coordinates],distanceKm:e.distance})),
    cautionSegments:steps.filter(e=>e.risk.penalty>1).length,exposureKm:steps.filter(e=>e.risk.penalty>1).reduce((sum,e)=>sum+e.distance,0),streets:[...new Set(steps.map(e=>e.name))],reasons:[...new Set(steps.flatMap(e=>e.risk.reasons))],snapDistances:{start:from.distance,end:to.distance},
    access:{start:from.coordinates,end:to.coordinates,edgeId:edge.id,verified:false},createdAt:new Date(now).toISOString(),simulation:!!graph.simulation,label:'Experimental planning route',note:'Road access, turn restrictions, water depth, bridge conditions, and destination opening are unverified. Dashed access gaps are not verified road routes. This is not turn-by-turn evacuation guidance.'};
  };
}
export function planRoute(graph,start,end,incidents,reports=[],options={}) {return createRoutePlanner(graph,start,incidents,reports,options)(end);}
