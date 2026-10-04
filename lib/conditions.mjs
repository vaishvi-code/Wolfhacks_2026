export function riverTrend(readings,now=Date.now()){
  const points=readings.filter(r=>Number.isFinite(Date.parse(r.time??r.observed_at))&&(r.value??r.gage_height_ft)!=null).map(r=>({time:new Date(r.time??r.observed_at).toISOString(),value:Number(r.value??r.gage_height_ft)})).filter(r=>Number.isFinite(r.value)&&Date.parse(r.time)<=now).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const last=points.at(-1);if(!last)return {status:'unavailable',points:[]};
  const target=Date.parse(last.time)-3600000;
  const baseline=points.filter(r=>Math.abs(Date.parse(r.time)-target)<=15*60000).sort((a,b)=>Math.abs(Date.parse(a.time)-target)-Math.abs(Date.parse(b.time)-target))[0];
  const fresh=now-Date.parse(last.time)<=30*60000;
  return {status:!fresh?'stale':baseline?'available':'insufficient_history',latestFt:last.value,observedAt:last.time,changeFt:baseline?last.value-baseline.value:null,windowMinutes:baseline?(Date.parse(last.time)-Date.parse(baseline.time))/60000:null,points};
}

// Send only bounded, city-specific observations to the language model.
export function conditionEvidence(snapshot,now=Date.now()){
  const c=snapshot.conditions,live=snapshot.mode==='live';
  if(!c)return {source:'unavailable',status:'unavailable',river:null,hourly:[],roadReports:[]};
  const trend=live?c.trend:null;
  const number=value=>value!=null&&Number.isFinite(Number(value))?Number(value):null;
  const stamp=value=>Number.isFinite(Date.parse(value))?new Date(value).toISOString():null;
  return {
    source:c.source,status:c.status,checkedAt:stamp(c.checkedAt),stationId:live?snapshot.region.gauge:null,
    river:trend?{status:trend.observedAt&&now-Date.parse(trend.observedAt)>30*60000?'stale':trend.status,latestFt:number(trend.latestFt),observedAt:stamp(trend.observedAt),changeFt:number(trend.changeFt),windowMinutes:number(trend.windowMinutes),points:(trend.points||[]).slice(-120).map(p=>({time:stamp(p.time),valueFt:number(p.value)})).filter(p=>p.time&&p.valueFt!==null)}:null,
    hourly:live?(c.hourly||[]).slice(-6).map(h=>({time:stamp(h.bucket),averageFt:number(h.average_ft),minimumFt:number(h.min_ft),maximumFt:number(h.max_ft),observations:number(h.observations)})).filter(h=>h.time&&h.averageFt!==null):[],
    roadReports:(c.events||[]).filter(e=>e.region===snapshot.region.id&&e.mode===snapshot.mode&&(snapshot.mode==='demo'||!e.simulation)).slice(0,20).map(e=>({kind:e.kind,description:String(e.description||'').slice(0,600),status:e.status,simulation:!!e.simulation,createdAt:stamp(e.createdAt),expiresAt:stamp(e.expires),active:e.status!=='resolved'&&Date.parse(e.expires)>now}))
  };
}
