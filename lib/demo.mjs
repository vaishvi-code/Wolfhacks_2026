import { REGIONS } from './config.mjs';
import { geometryCenter,bboxGeometry,segmentIntersectsGeometry } from './geo.mjs';
import { riverSignal } from './model.mjs';
function poly(x,y,dx,dy) {return {type:'Polygon',coordinates:[[[x-dx,y-dy],[x+dx*.7,y-dy*.8],[x+dx,y+dy*.5],[x+dx*.1,y+dy],[x-dx,y+dy*.6],[x-dx,y-dy]]]};}
export function demoSnapshot(regionId='raleigh', now=Date.now()) {
  const region=REGIONS[regionId], [x,y]=region.center;
  const specs=[['flood','Flash flood warning',90,poly(x+.018,y-.005,.018,.018),'East & southeast planning area'],['hurricane','Tropical storm warning',75,poly(x-.042,y+.033,.022,.022),'Northwest planning area'],['heat','Extreme heat warning',80,poly(x+.015,y+.038,.032,.014),'Northern planning area']];
  const incidents=specs.map(([category,title,score,geometry,place])=>({id:`demo-${regionId}-${category}`,source:'scenario',category,title,score,geometry,place,center:geometryCenter(geometry),severity:'Simulated',certainty:'Scenario',time:new Date(now-480000).toISOString(),expires:new Date(now+86400000).toISOString(),onset:new Date(now+({flood:3,hurricane:12,heat:6}[category])*3600000).toISOString(),effective:new Date(now-480000).toISOString(),ends:new Date(now+86400000).toISOString(),simulation:true,
    reason:`Fictional ${score}/100 scenario priority. This does not describe current conditions.`,description:`A fictional ${category} scenario for ${region.name}. Boundaries, road conditions, resource locations, and readings are illustrative.`,
    instruction:category==='flood'?'Scenario exercise: explore a route using simulated dry roads while avoiding the red flooded patches. Never drive through floodwater in real conditions.':category==='heat'?'Scenario exercise: explore a resource beyond the heat warning and confirm that cooling and access are available.':'Scenario exercise: explore a route beyond the wind warning. Follow local evacuation directions in real conditions.',agency:'WayAhead demo',footprint:'Simulated hazard boundary'}));
  const facilities=[
    ['1','Oakwood community hub','community_centre',-.012,.012],['2','Eastside medical center','hospital',.024,-.006],['3','Northside library','library',.012,.036],['4','Westside response station','fire_station',-.048,.024],['5','Southside resource center','community_centre',-.024,-.036],['6','Greenway health clinic','clinic',.036,.024]
  ].map(([id,name,kind,dx,dy])=>({id:`demo-${regionId}-f${id}`,name,kind,coordinates:[x+dx,y+dy],simulation:true,availability:'Unverified',source:'Fictional scenario'}));
  const nodes=[], edges=[];
  for(let row=0;row<41;row++) for(let col=0;col<49;col++) nodes.push({id:`${row}-${col}`,coordinates:[x+(col-24)*.003,y+(row-20)*.003]});
  for(let row=0;row<41;row++) for(let col=0;col<49;col++) {
    if(col<48) edges.push({id:`h-${row}-${col}`,from:`${row}-${col}`,to:`${row}-${col+1}`,name:`Scenario avenue ${row+1}`,oneway:false});
    if(row<40) edges.push({id:`v-${row}-${col}`,from:`${row}-${col}`,to:`${row+1}-${col}`,name:`Scenario street ${col+1}`,oneway:false});
  }
  // A warning is distinct from measured inundation. This demo explicitly invents
  // dry roads and two flooded patches; none of this road-condition evidence is live.
  const demoFloodedAreas=[bboxGeometry([x+.020,y-.015,x+.025,y-.009]),bboxGeometry([x+.005,y-.010,x+.009,y-.006])].map((geometry,i)=>({id:`demo-water-${regionId}-${i}`,category:'flood',title:'Simulated flooded area',score:100,simulation:true,geometry}));
  const {roads,demoRouting}=scenarioRoads({nodes,edges,fetchedAt:new Date(now).toISOString()},incidents,demoFloodedAreas,'synthetic');
  const readings=Array.from({length:105},(_,j)=>({time:new Date(now-(104-j)*900000).toISOString(),value:2.1+Math.max(0,j-50)*.058+Math.sin(j/8)*.08,provisional:true}));
  const sensor={...riverSignal({id:'demo',name:'Scenario river gauge',coordinates:[x+.024,y-.012]},readings,now),simulation:true,url:null};
  return {region,mode:'demo',demoRouting,fetchedAt:new Date(now).toISOString(),demoOrigins:{flood:[x+.012,y],hurricane:[x-.048,y+.036],heat:[x+.012,y+.036]},incidents,facilities,sensor,weather:{temperatureF:94,humidity:68,windMph:18,time:new Date(now).toISOString(),station:'Simulated weather',simulation:true},
    roads,demographics:null,sources:[{id:'scenario',name:'Demonstration dataset',status:'simulated',lastSuccess:new Date(now).toISOString(),detail:'All hazards, facilities, roads, and observations in this mode are fictional.'}]};
}

function scenarioRoads(graph,incidents,demoFloodedAreas,basis){
  const byId=new Map(graph.nodes.map(n=>[n.id,n.coordinates])),flood=incidents.find(i=>i.category==='flood'),dryRoads=[],closedRoads=[];
  // Copy edges so simulated passability can never leak into the live OSM graph.
  const edges=graph.edges.map(original=>{
    const edge={...original};delete edge.demoPassages;
    const a=byId.get(edge.from),b=byId.get(edge.to);
    if(a&&b&&segmentIntersectsGeometry(a,b,flood.geometry)){
      const closed=demoFloodedAreas.some(i=>segmentIntersectsGeometry(a,b,i.geometry));
      if(!closed)edge.demoPassages=[flood.id];(closed?closedRoads:dryRoads).push([a,b]);
    }
    return edge;
  });
  return {roads:{...graph,edges,simulation:true,demoFloodedAreas,basis},demoRouting:{version:2,basis,roadTimestamp:graph.fetchedAt,
    floodRoads:{type:'FeatureCollection',features:[{type:'Feature',properties:{closed:false},geometry:{type:'MultiLineString',coordinates:dryRoads}},{type:'Feature',properties:{closed:true},geometry:{type:'MultiLineString',coordinates:closedRoads}}]},
    floodedAreas:{type:'FeatureCollection',features:demoFloodedAreas.map(i=>({type:'Feature',properties:{name:i.title},geometry:i.geometry}))}}};
}

export function demoWithOSM(snapshot,osm){
  if(!osm.data?.roads?.edges?.length)return {...snapshot,sources:[...snapshot.sources,osm.source],demoRouting:{...snapshot.demoRouting,fallbackReason:osm.source.detail||'OpenStreetMap is unavailable.'}};
  const {roads,demoRouting}=scenarioRoads(osm.data.roads,snapshot.incidents,snapshot.roads.demoFloodedAreas,'osm');
  return {...snapshot,roads,demoRouting,facilities:osm.data.facilities,
    incidents:snapshot.incidents.map(i=>({...i,description:`A fictional ${i.category} scenario for ${snapshot.region.name}, on real OpenStreetMap streets. Warning boundaries, road conditions, and readings are simulated. Resource locations are mapped, but their availability is unverified.`})),
    sources:[{...snapshot.sources[0],detail:'Disaster boundaries, flooded patches, dry-road assumptions, and sensor readings are simulated. Street geometry and resource locations come from OpenStreetMap.'},osm.source]};
}
