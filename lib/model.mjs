import { geometryCenter, pointInGeometry, distanceKm } from './geo.mjs';
export const CATEGORIES = ['flood', 'hurricane', 'heat'];
export function classifyWeather(event = '') {
  if (/flood|storm surge/i.test(event)) return 'flood';
  if (/hurricane|tropical storm|tropical depression|extreme wind/i.test(event)) return 'hurricane';
  if (/heat/i.test(event)) return 'heat';
  return null;
}
export function weatherPriority(severity, urgency='Unknown') {
  const base = {Extreme:90,Severe:70,Moderate:45,Minor:20}[severity];
  if (base === undefined) return {score:null,reason:'NWS has not specified a comparable severity.'};
  const extra = {Immediate:10,Expected:5}[urgency] || 0;
  return {score:base+extra,reason:`NWS ${severity.toLowerCase()} severity (${base}) + ${urgency.toLowerCase()} urgency (${extra}). A planning heuristic, not a probability of harm.`};
}
export function priorityLabel(score) { return score == null ? 'Unknown' : score>=70 ? 'High' : score>=40 ? 'Elevated' : 'Monitor'; }
export function normalizeWeather(data, now=Date.now()) {
  return (data.features||[]).flatMap(f=>{
    const p=f.properties||{}, category=classifyWeather(p.event);
    if (!category || p.status!=='Actual' || !Number.isFinite(Date.parse(p.ends||p.expires)) || Date.parse(p.ends||p.expires)<=now) return [];
    const {score,reason}=weatherPriority(p.severity,p.urgency);
    return [{id:p.id||f.id,source:'nws',category,title:p.event,place:p.areaDesc,score,reason,severity:p.severity,urgency:p.urgency,certainty:p.certainty,time:p.sent,expires:p.ends||p.expires,
      geometry:f.geometry,center:geometryCenter(f.geometry),description:p.description||'',instruction:p.instruction||'',url:f.id,zones:p.affectedZones||[],agency:p.senderName,footprint:f.geometry?'Official alert area':'Boundary unavailable',simulation:false}];
  });
}
export function parseReadings(data, stationId) {
  const readings=new Map();
  for (const f of data.features||[]) {
    const p=f.properties||{};
    if (p.monitoring_location_id!==`USGS-${stationId}` || p.parameter_code!=='00065' || p.statistic_id!=='00011' || p.unit_of_measure!=='ft') continue;
    if (p.value===null || p.value===undefined || String(p.value).trim()==='') continue;
    const value=Number(p.value), time=Date.parse(p.time);
    if (!Number.isFinite(value) || value<=-9999 || !Number.isFinite(time)) continue;
    readings.set(time,{time:new Date(time).toISOString(),value,provisional:p.approval_status!=='Approved'});
  }
  return [...readings.values()].sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
}
export function riverSignal(station, readings, now=Date.now()) {
  const latest=readings.at(-1), target=latest ? Date.parse(latest.time)-86400000 : 0;
  const at24=readings.filter(r=>Math.abs(Date.parse(r.time)-target)<=3600000).sort((a,b)=>Math.abs(Date.parse(a.time)-target)-Math.abs(Date.parse(b.time)-target))[0];
  const previous=latest ? readings.filter(r=>Date.parse(r.time)<Date.parse(latest.time) && Math.abs(Date.parse(r.time)-(Date.parse(latest.time)-3600000))<=20*60000).at(-1) : null;
  const fresh=!!latest && now-Date.parse(latest.time)<=7200000 && Date.parse(latest.time)<=now+60000;
  const change=at24 ? latest.value-at24.value : null;
  const rate=previous ? (latest.value-previous.value)/((Date.parse(latest.time)-Date.parse(previous.time))/3600000) : null;
  return {id:`river-${station.id}`,source:'usgs',name:station.name,coordinates:station.coordinates,time:latest?.time||null,level:latest?.value??null,change,rate,fresh,readings,
    url:`https://waterdata.usgs.gov/monitoring-location/USGS-${station.id}/`,note:'Provisional gage height relative to the station datum. A rise is not a flood stage or an inundation boundary.'};
}
export function exposure(facility, incidents) {
  const hits=incidents.filter(i=>i.geometry && pointInGeometry(facility.coordinates,i.geometry));
  const score=hits.length ? Math.max(...hits.map(i=>i.score??0)) : null;
  return {...facility,score,risk:hits.length ? priorityLabel(score) : 'Not assessed',incidentIds:hits.map(i=>i.id),reason:hits.length ? `Inside ${hits.map(i=>i.title).join(', ')}.` : 'No mapped alert overlap. This does not verify conditions or opening status.'};
}
export function actionsFor(snapshot) {
  return snapshot.incidents.map(i=>{
    const facilities=snapshot.facilities.filter(f=>i.geometry && pointInGeometry(f.coordinates,i.geometry));
    const nearby=i.center ? snapshot.facilities.filter(f=>distanceKm(f.coordinates,i.center)<5).length : 0;
    return {incidentId:i.id,title:i.category==='heat'?'Prioritize cooling outreach':i.category==='flood'?'Review flood exposure':'Prepare storm response',score:i.score,category:i.category,place:i.place,
      affectedFacilities:facilities.length,nearbyFacilities:nearby,facilityNames:facilities.slice(0,3).map(f=>f.name),reason:i.reason,
      action:i.category==='heat'?'Contact community facilities to confirm cooling access and opening hours.':i.category==='flood'?'Check official warnings and verify road conditions before planning access.':'Check official wind warnings and confirm resource locations with local officials.'};
  }).sort((a,b)=>(b.score??-1)-(a.score??-1));
}
