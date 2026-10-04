export function riverTrend(readings,now=Date.now()){
  const points=readings.filter(r=>Number.isFinite(Date.parse(r.time??r.observed_at))&&(r.value??r.gage_height_ft)!=null).map(r=>({time:new Date(r.time??r.observed_at).toISOString(),value:Number(r.value??r.gage_height_ft)})).filter(r=>Number.isFinite(r.value)&&Date.parse(r.time)<=now).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const last=points.at(-1);if(!last)return {status:'unavailable',points:[]};
  const target=Date.parse(last.time)-3600000;
  const baseline=points.filter(r=>Math.abs(Date.parse(r.time)-target)<=15*60000).sort((a,b)=>Math.abs(Date.parse(a.time)-target)-Math.abs(Date.parse(b.time)-target))[0];
  const fresh=now-Date.parse(last.time)<=30*60000;
  return {status:!fresh?'stale':baseline?'available':'insufficient_history',latestFt:last.value,observedAt:last.time,changeFt:baseline?last.value-baseline.value:null,windowMinutes:baseline?(Date.parse(last.time)-Date.parse(baseline.time))/60000:null,points};
}
