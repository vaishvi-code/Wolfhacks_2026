import type { Status, RouteResponse, Route, Destinations } from './api'
export const distance=(meters:number)=>`${(meters/1000).toFixed(2)} km`
export function StatusBar({status,offline,saved,checking}:{status:Status|null,offline:boolean,saved:boolean,checking:boolean}) {
  const sources=Object.values(status?.data_state.sources||{})
  const freshness=[...new Set(sources.map(s=>s.fetch_freshness.toUpperCase()))]
  return <div className="trustbar" role="status"><span className="status-dot"/>
    <strong>{checking?'UPDATING':!status?'UNAVAILABLE':offline?'OFFLINE':saved?'CACHED VIEW':status?.demo?'DEMO · SYNTHETIC':status?.data_state.mode||'CONNECTING'}</strong>
    <span>{status?.data_state.hazard_coverage_state.toUpperCase()||'Waiting for local backend'}</span>
    {freshness.filter(f=>f!=='CURRENT').map(f=><span key={f}>{f}</span>)}
    {status&&<span className="updated">{status.demo?'Scenario time':'Snapshot'} · {status.data_state.snapshot_created_at?new Date(status.data_state.snapshot_created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'No downloaded snapshot'}</span>}
  </div>
}
export function HazardStatus({status,saved}:{status:Status,saved:boolean}) {
  return <section className="hazard-status" aria-label="Hazard conditions">{status.hazard_categories.map(h=><div key={h.id} className={`hazard-chip ${h.id}`}><span className="hazard-symbol" aria-hidden="true">{h.id==='heat'?'☀':h.id==='flood'?'≈':h.id==='storm_surge'?'≋':'◉'}</span><div><strong>{h.label}</strong><span>{saved?'Downloaded: ':h.data_origin==='cached'?'Cached: ':''}{h.status.toLowerCase()}{h.active_count>0?` · ${h.active_count}`:''}</span></div></div>)}</section>
}
function RouteCard({title,route,safer}:{title:string,route:Route|null,safer?:boolean}){
  const events=[...new Set(route?.road_risks.flatMap(r=>r.contributions.map(c=>c.evidence.event||c.hazard_type))||[])]
  return <article className={`route-card ${safer?'safer':''}`}><h3><i aria-hidden="true"/>{title}</h3>{route?<><strong className="distance">{distance(route.total_distance_m)}</strong><p>{route.affected_edge_count} intersecting {route.affected_edge_count===1?'segment':'segments'}</p><span>{events.length?events.join(' · '):'No mapped hazard intersections'}</span></>:<p>No route available</p>}</article>
}
export function RouteComparison({result}:{result:RouteResponse}) {
  const {baseline,safer,avoided_edge_count}=result.comparison
  const delta=baseline.route&&safer.route?safer.route.total_distance_m-baseline.route.total_distance_m:null
  const evidence=new Map<string,{event:string,source:string,freshness:string,reason:string,count:number}>()
  for(const road of baseline.route?.road_risks||[]) for(const c of road.contributions){const old=evidence.get(c.hazard_id);evidence.set(c.hazard_id,{event:c.evidence.event||c.hazard_type,source:c.source,freshness:c.freshness,reason:c.reason,count:(old?.count||0)+1})}
  return <section aria-label="Route comparison"><div className="section-heading"><h2>Your route options</h2><span>Distance in km</span></div>
    <p className="small route-target">To {result.destination.name||result.destination.id}</p><div className="route-cards"><RouteCard title="Normal route" route={baseline.route}/><RouteCard title="Safer route" route={safer.route} safer/></div>
    <p className="route-benefit">{avoided_edge_count===null?'Route comparison unavailable':`${avoided_edge_count} affected ${avoided_edge_count===1?'segment':'segments'} avoided`}{delta!==null?` · ${delta>=0?'+':''}${distance(delta)}`:''}</p>
    <p className="small">“Safer” reflects supplied hazards and routing policy, not verified physical safety.</p>
    <details className="evidence"><summary>Why this route?</summary>{[...evidence].map(([id,e])=><article key={id}><strong>{e.event}</strong><p>{e.source} · {e.freshness} · {e.count} affected {e.count===1?'segment':'segments'}</p><p>{e.reason}</p><span>{result.comparison.avoided_hazard_ids?.includes(id)?'Avoided by alternate route':'Evidence remains on route or does not require avoidance'}</span></article>)}{!evidence.size&&<p>No mapped hazard contribution explains a route change. Coverage may be limited.</p>}{result.reevaluation&&<p>Reevaluation: {result.reevaluation.reason_codes.join(' · ')}</p>}</details>
  </section>
}
export function DestinationList({data,selected,onSelect}:{data:Destinations|null,selected:string,onSelect:(id:string)=>void}) {
  return <div className="destination-list">{!data?<p className="small">Find locations to compare available routes.</p>:!data.candidates.some(c=>c.destination)?<p role="status">No destinations available in the downloaded area.</p>:data.candidates.map((item,index)=>{const d=item.destination;if(!d)return null
    return <label key={`${d.id}:${index}`} className={`destination ${selected===d.id?'chosen':''}`}><input type="radio" name="destination" value={d.id} checked={selected===d.id} disabled={!item.eligible} onChange={()=>onSelect(d.id)}/><div><strong>{d.name||'Unnamed location'}</strong><span className="classification">{d.classification==='official_cooling_center'?'Official cooling center':'Potential relief location'}</span><p>{d.categories.join(', ')||'Facility'} · {item.comparison?.safer.route?distance(item.comparison.safer.route.total_distance_m):'Route unavailable'}</p><p>{d.source} · designation {d.designation_freshness}</p>{item.comparison?.safer.route&&<p>{item.comparison.safer.route.affected_edge_count} route segments intersect supplied hazards</p>}{!item.eligible&&<p className="error-text">Excluded: {item.exclusion_reasons.join(', ')}</p>}<span className="small">Hours, cooling and capacity unverified.</span></div></label>
  })}</div>
}
