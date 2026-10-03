// Read-only integration smoke test against public data, not simulated feeds.
const base=process.env.APP_URL||'http://127.0.0.1:4173';
let failed=false;
for(const region of ['raleigh','wilmington','asheville']){
  try{
    const response=await fetch(`${base}/api/snapshot?region=${region}&mode=live`,{signal:AbortSignal.timeout(90000)});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const s=await response.json();
    const report={region,mode:s.mode,alerts:s.stats.alerts,resources:s.stats.resources,roadSegments:s.roadInfo.segments,gauge:s.sensor?.name,observed:s.sensor?.time,readings:s.sensor?.readings.length,sources:s.sources.map(x=>`${x.name}: ${x.status}`)};
    console.log(JSON.stringify(report,null,2));
    if(s.mode!=='live'||s.facilities.some(f=>f.simulation)||s.incidents.some(i=>i.simulation))throw new Error('Demo data leaked into live mode');
    if(s.sources.some(s=>['unavailable','stale'].includes(s.status)))failed=true;
  }catch(error){failed=true;console.error(`${region}: ${error.message}`);}
}
process.exitCode=failed?1:0;
