import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Status, type Destinations, type RouteResponse, type Location } from './api'
import { loadSaved, saveView, needsRevalidation } from './storage'
import MapView from './MapView'
import { StatusBar, HazardStatus, RouteComparison, DestinationList } from './components'

export default function App(){
  const [initial]=useState(loadSaved)
  const [status,setStatus]=useState<Status|null>(initial?.status||null)
  const [origin,setOrigin]=useState<Location>(initial?.origin||[35.77,-78.64])
  const [coordinates,setCoordinates]=useState(initial?.origin.join(', ')||'35.770, -78.640')
  const [locationLabel,setLocationLabel]=useState(initial?'Downloaded starting point':'Configured starting point')
  const [destinations,setDestinations]=useState<Destinations|null>(initial?.destinations||null)
  const [route,setRoute]=useState<RouteResponse|null>(initial?.route||null)
  const [selected,setSelected]=useState(initial?.route?.destination.id||initial?.destinations?.selected_destination_id||'')
  const [saved,setSaved]=useState(Boolean(initial)),[routeStale,setRouteStale]=useState(Boolean(initial?.route))
  const [online,setOnline]=useState(navigator.onLine),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[storageError,setStorageError]=useState(false)
  const [now,setNow]=useState(Date.now()),[install,setInstall]=useState<Event & {prompt:()=>Promise<void>}|null>(null)
  const busyRef=useRef(false), ready=useRef(false)
  const offline=!online||Boolean(status?.offline)
  const revalidate=Boolean(status&&needsRevalidation(status,now))
  const select=useCallback((id:string)=>{if(busyRef.current)return;setSelected(id);setRoute(null);setRouteStale(false)},[])
  async function run(action:()=>Promise<void>){
    if(busyRef.current)return
    busyRef.current=true;setBusy(true);setError('');setNotice('')
    try{await action()}catch(e){setError(e instanceof Error?e.message:'Unable to complete this request.');setSaved(true)}
    finally{busyRef.current=false;setBusy(false)}
  }
  async function loadDestinations(point:Location){
    const data=await api.destinations(point);setDestinations(data)
    setSelected(current=>data.candidates.some(c=>c.eligible&&c.destination?.id===current)?current:data.selected_destination_id||'')
  }
  async function update(action:'refresh'|'offline'|'reconnect'|'reset'){
    await run(async()=>{
      const next=action==='refresh'?await api.refresh():await api.demo(action)
      setStatus(next);setSaved(false);setRouteStale(Boolean(route))
      if(action==='reset'){setRoute(null);setRouteStale(false);setOrigin(next.origin);setCoordinates(next.origin.join(', '));setLocationLabel('Demo starting point');await loadDestinations(next.origin);return}
      await loadDestinations(origin)
      if(route&&action!=='offline'){
        const nextRoute=await api.reevaluate(route.route_id)
        setRoute(nextRoute);setRouteStale(false)
        setNotice(!nextRoute.destination_eligible?'Destination is no longer eligible. Choose another location.':nextRoute.reevaluation?.route_updated?'Route updated · a new hazard affects the previous route.':'Route reevaluated against the latest available evidence.')
      } else if(action==='offline') setNotice('Offline demonstration: Python loaded the local GraphML and cached hazard snapshot. Your existing route remains visible.')
      if(next.data_state.hazard_coverage_state!=='available')setNotice('Some hazard evidence is limited or unavailable. Inspect source status before relying on this comparison.')
    })
  }
  useEffect(()=>{
    if(ready.current)return;ready.current=true
    void run(async()=>{
      const next=await api.status();setStatus(next);setSaved(false)
      const same=initial?.status.workspace_id===next.workspace_id
      const point=same?initial!.origin:next.origin
      setOrigin(point);setCoordinates(point.join(', '));setLocationLabel(same?'Downloaded starting point':next.demo?'Demo starting point':'Configured starting point')
      if(!same){setRoute(null);setDestinations(null);setSelected('')}
      await loadDestinations(point)
    })
  },[])
  useEffect(()=>{
    const lost=()=>{setOnline(false);setSaved(true);setRouteStale(true);setNotice('Connection lost. Downloaded geometry remains visible. New calculations need a reachable local backend.')
      if(!busyRef.current)void api.offline().then(next=>setStatus(next)).catch(()=>{/* Saved browser view remains available. */})
    }
    const connected=()=>{setOnline(true);void update(status?.demo?'reconnect':'refresh')}
    window.addEventListener('offline',lost);window.addEventListener('online',connected)
    return()=>{window.removeEventListener('offline',lost);window.removeEventListener('online',connected)}
  },[status,route,origin])
  useEffect(()=>{
    if(status&&!busy&&!saved){setStorageError(!saveView({version:1,saved_at:new Date().toISOString(),status,origin,destinations,route}))}
  },[status,origin,destinations,route,busy,saved])
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),30000);return()=>clearInterval(timer)},[])
  useEffect(()=>{const failed=()=>setError('Offline app caching could not be installed. This page may not reopen without a connection.');window.addEventListener('pwa-error',failed);return()=>window.removeEventListener('pwa-error',failed)},[])
  useEffect(()=>{const handler=(event:Event)=>{event.preventDefault();setInstall(event as Event & {prompt:()=>Promise<void>})};window.addEventListener('beforeinstallprompt',handler);return()=>window.removeEventListener('beforeinstallprompt',handler)},[])
  function setLocation(point:Location,label:string){setOrigin(point);setCoordinates(point.join(', '));setLocationLabel(label);setRoute(null);setRouteStale(false);setDestinations(null);void run(()=>loadDestinations(point))}
  function enterCoordinates(){const parts=coordinates.split(',');const values=parts.map(Number);if(parts.some(p=>!p.trim())||values.length!==2||!values.every(Number.isFinite)||Math.abs(values[0])>90||Math.abs(values[1])>180){setError('Enter latitude, longitude as two valid numbers.');return}setLocation(values as Location,'Entered coordinates')}
  function locate(){if(!navigator.geolocation){setError('Location is unavailable. Enter coordinates or use the configured starting point.');return}
    navigator.geolocation.getCurrentPosition(p=>setLocation([p.coords.latitude,p.coords.longitude],'Browser location'),()=>setError('Location permission was denied or a position could not be found. Enter coordinates or use the configured starting point.'),{enableHighAccuracy:true,timeout:10000,maximumAge:30000})}
  return <div className="app-shell">
    <a className="skip-link" href="#route-controls">Skip to route controls</a>
    <header className="app-header"><div className="brand"><svg viewBox="0 0 40 40" aria-hidden="true"><path d="M20 3 36 35 20 28 4 35Z" fill="currentColor"/><path d="m20 12 6 15-6-3Z" fill="#f8faf6"/></svg><div><strong>wayfinder<span>NC</span></strong><p>Know your way. Understand the risk.</p></div></div><div className="header-actions">{install?<button className="subtle" onClick={()=>void install.prompt()}>Install app</button>:<details className="install-help"><summary>Install</summary><p>Use your browser’s Install app menu. On iPhone: Share → Add to Home Screen. Requires HTTPS or localhost.</p></details>}<span className="edition">FIELD EDITION / 01</span></div></header>
    <StatusBar status={status} offline={offline} saved={saved||revalidate} checking={busy}/>
    <main className="workspace">
      <section className="map-area" aria-label="Navigation overview">
        {status?<MapView status={status} origin={origin} destinations={destinations} route={route} selected={selected} onSelect={select}/>:<div className="map-empty"><strong>Your map will appear here</strong><p>Connect to the local backend to download the road area and hazard evidence.</p></div>}
        <div className="map-heading"><span className="eyebrow">{status?.demo?'SYNTHETIC DEMO AREA':'DOWNLOADED ROAD AREA'}</span><h1>A clearer way forward.</h1><p>{status?.demo?'Raleigh coordinates · fictional roads & events':'Local roads, supplied hazards, informed choices'}</p></div>
        <div className="map-legend" aria-label="Map legend"><span><i className="user-key"/>Your location</span><span><i className="normal-key"/>Normal</span><span><i className="safer-key"/>Safer</span><span><i className="hazard-key"/>Hazard area</span><span>C / P · official / potential</span></div>
        <div className="map-note">Local road geometry · no downloaded basemap tiles</div>
      </section>
      <aside className="control-panel" id="route-controls" aria-label="Plan and compare routes">
        {status?.demo&&<div className="demo-banner"><strong>DEMO MODE</strong><span>All events and destinations are synthetic. No live disaster conditions.</span></div>}
        {error&&<div className="message error" role="alert">{error}{!status&&<button disabled={busy} onClick={()=>void run(async()=>{const next=await api.status();setStatus(next);setOrigin(next.origin);setCoordinates(next.origin.join(', '));setSaved(false);await loadDestinations(next.origin)})}>Retry connection</button>}</div>}
        {notice&&<div className="message notice" role="status">{notice}</div>}
        {status&&(saved||revalidate||routeStale)&&<div className="message warning">{offline?'Offline · ':''}Downloaded view. {routeStale?'Displayed route has not been revalidated. ':''}Freshness labels describe the last backend evaluation; conditions may have changed.</div>}
        {storageError&&<div role="alert" className="message warning">Browser storage is unavailable or full. This view could not be saved for offline use.</div>}
        <section className="location-section"><span className="eyebrow">01 / START HERE</span><div className="section-heading"><h2>Your starting point</h2><button className="text-button" disabled={busy} onClick={locate}>Use my location</button></div><p className="location-label"><span className="location-dot"/>{locationLabel}</p><div className="coordinate-row"><label className="sr-only" htmlFor="coordinates">Latitude, longitude</label><input id="coordinates" value={coordinates} onChange={e=>setCoordinates(e.target.value)} placeholder="35.770, -78.640"/><button disabled={busy||!status} onClick={enterCoordinates}>Set</button></div></section>
        {status&&<><div className="section-heading"><h2>Conditions nearby</h2><span>Official source evidence</span></div><HazardStatus status={status} saved={saved||revalidate}/><p className="small">No applicable alert does not mean safe. Unmapped alerts remain in source context.</p></>}
        <section className="relief-section"><span className="eyebrow">02 / CHOOSE A DESTINATION</span><div className="section-heading"><h2>Find a place for relief</h2><button className="text-button" disabled={busy||!status} onClick={()=>void run(()=>loadDestinations(origin))}>Find locations</button></div><DestinationList data={destinations} selected={selected} onSelect={select}/><button className="primary" disabled={busy||!selected||!status} onClick={()=>void run(async()=>{const r=await api.route(origin,selected);setRoute(r);setRouteStale(false);setSaved(false);setStatus(await api.status())})}>{busy?'Working with local backend…':route?'Recalculate route':'Compare routes'}<span aria-hidden="true">↗</span></button></section>
        {route&&<><RouteComparison result={route}/>{route.destination_eligible===false&&<p role="alert" className="message error">Destination is no longer eligible. Displayed lines are for comparison only.</p>}</>}
        <section className="monitor"><div className="section-heading"><h2>Keep conditions in view</h2><button disabled={busy||!status||Boolean(status?.demo&&status.offline)} onClick={()=>void update('refresh')}>Refresh & check route</button></div><p className="small">Refresh uses source data and rechecks your selected route. This app does not provide turn-by-turn guidance.</p>
        {status&&!status.demo&&<button disabled={busy} onClick={()=>void run(async()=>{setStatus(await api.offline());setSaved(false);setRouteStale(true);setNotice('Using the local road graph and downloaded hazard snapshot. Refresh to check sources again.')})}>Use downloaded data</button>}
        {status?.demo&&<div className="demo-controls"><button disabled={busy||status.offline} onClick={()=>void update('offline')}>Simulate offline</button><button disabled={busy} onClick={()=>void update('reconnect')}>Reconnect + new hazard</button><button disabled={busy} onClick={()=>void update('reset')}>Reset demo</button></div>}
        {status&&<details className="source-details"><summary>Data sources & limitations</summary><p>Last backend evaluation: {new Date(status.data_state.evaluated_at).toLocaleString()}{status.demo?' (fixed scenario clock)':''}</p>{Object.entries(status.data_state.sources).map(([name,s])=><p key={name}><strong>{name}</strong> · {s.status} · {s.fetch_freshness}<br/>Fetched: {s.fetched_at?new Date(s.fetched_at).toLocaleString():'Unavailable'}{Boolean(s.issues?.length)&&<span> · Source reports limited data</span>}</p>)}{status.data_state.cache_error!=null&&<p>Local snapshot unavailable or could not be read.</p>}<p>{status.context.length} contextual records supplied, including observations and alerts without usable geometry.</p>{status.context.filter(c=>typeof c.event==='string').map((c,i)=><p key={i}>{String(c.event)} · {String(c.freshness||'unknown')} · {c.used_for_routing?'Mapped evidence':String(c.exclusion_reason||'Context only')}</p>)}<p>Offline without a reachable Python backend: view downloaded data only. New routing and reevaluation are unavailable. Roads and route geometry are saved; internet map tiles are not.</p></details>}</section>
        <footer>Built for clarity, not certainty.<br/>Follow official instructions and local conditions.</footer>
      </aside>
    </main>
  </div>
}
