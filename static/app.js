const $=id=>document.getElementById(id);
let state,route=null,showHazards=true,offline=false;
const colors={clear:'#ced6c7',caution:'#d9a143',high:'#df805a',blocked:'#b75551'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=t=>t?new Date(t).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Not yet fetched';
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,6000);}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok){const e=new Error(data.error||data.status||'Request failed');e.data=data;throw e;}return data;}
function xy(n){return [85+(n.lon+78.674)/.045*650,535-(n.lat-35.756)/.040*450];}
function drawMap(){
 if(!state)return;const nodes=Object.fromEntries(state.nodes.map(n=>[n.id,n]));
 let svg=`<defs><pattern id="grid" width="35" height="35" patternUnits="userSpaceOnUse"><path d="M35 0L0 0 0 35" fill="none" stroke="#e5e9db"/></pattern></defs><rect width="820" height="620" fill="#eef1e6"/><rect width="820" height="620" fill="url(#grid)"/><path d="M610 -20C560 110 675 200 600 320S640 530 510 650" fill="none" stroke="#d4e0d9" stroke-width="42"/><path d="M610 -20C560 110 675 200 600 320S640 530 510 650" fill="none" stroke="#b9d0c8" stroke-width="2"/><path d="M50 200Q190 140 210 230T120 380L30 360Z" fill="#e0e7cc"/><path d="M350 20Q490 40 475 160L310 170Z" fill="#e1e8ce"/><text x="45" y="55" fill="#8d9983" font-size="10" letter-spacing="2">RALEIGH / ILLUSTRATIVE NETWORK</text><text x="60" y="288" fill="#a0ab92" font-size="9">WEST DISTRICT</text><text x="350" y="313" fill="#9ba58f" font-size="21" letter-spacing="4">RALEIGH</text><text x="620" y="370" fill="#95aaa0" font-size="10" transform="rotate(-70 620 370)">ILLUSTRATIVE WATERWAY</text>`;
 if(showHazards)for(const h of state.hazards){const[x,y]=xy(h),r=h.radius/4450*450;svg+=`<circle cx="${x}" cy="${y}" r="${r+9}" fill="${colors[h.severity]}" opacity=".10"/><circle cx="${x}" cy="${y}" r="${r}" fill="${colors[h.severity]}" opacity=".16" stroke="${colors[h.severity]}" stroke-dasharray="4 4"/>`;}
 for(const e of state.edges){const[x1,y1]=xy(nodes[e.a]),[x2,y2]=xy(nodes[e.b]);svg+=`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#fafbf6" stroke-width="12"/><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${showHazards?colors[e.risk]:colors.clear}" stroke-width="5" ${showHazards&&e.risk==='blocked'?'stroke-dasharray="6 5"':''}/>`;}
 if(route){const points=route.nodes.map(id=>xy(nodes[id]).join(',')).join(' ');svg+=`<polyline points="${points}" fill="none" stroke="#fff" stroke-width="12" stroke-linejoin="round"/><polyline points="${points}" fill="none" stroke="#2b6953" stroke-width="6" stroke-linejoin="round"/>`;}
 for(const n of state.nodes){const[x,y]=xy(n);svg+=`<circle class="node-hit" data-node="${n.id}" cx="${x}" cy="${y}" r="9" fill="transparent"><title>Use grid ${n.id} as start</title></circle>`;}
 for(const d of state.destinations){const[x,y]=xy(nodes[d.node]);svg+=`<rect x="${x-13}" y="${y-13}" width="26" height="26" rx="7" fill="#fff" stroke="#aebaa4"/><text x="${x}" y="${y+5}" text-anchor="middle" font-size="17" fill="#2b6953">＋</text><rect x="${x-72}" y="${y-39}" width="144" height="20" rx="4" fill="#ffffffee"/><text x="${x}" y="${y-25}" text-anchor="middle" font-size="9" fill="#506449">${esc(d.name)}</text>`;}
 const[sx,sy]=xy(nodes[$('start').value]);svg+=`<circle cx="${sx}" cy="${sy}" r="16" fill="#2b6953" opacity=".15"/><circle cx="${sx}" cy="${sy}" r="8" fill="#2b6953" stroke="#fff" stroke-width="3"/><rect x="${sx-29}" y="${sy+20}" width="58" height="20" rx="4" fill="#19342f"/><text x="${sx}" y="${sy+34}" fill="#fff" text-anchor="middle" font-size="9">START</text><text x="760" y="55" font-size="12" fill="#63715c">N ↑</text>`;
 $('map').innerHTML=svg;$('map').querySelectorAll('[data-node]').forEach(el=>el.addEventListener('click',()=>{$('start').value=el.dataset.node;resetRoute('Starting point updated. Calculate a new route.');}));
}
function resetRoute(message='Choose a starting point to find a sample route.'){route=null;drawMap();$('route-result').textContent=message;}
function renderWeather(w){$('weather').innerHTML=`<p class="small muted">${esc(w.status)} · ${time(w.updated)}</p>`+(w.alerts.length?w.alerts.map(a=>`<div class="weather-alert"><strong>${esc(a.event)}</strong><p>${esc(a.headline)}</p><p>${esc(a.instruction||'')}</p><small>Expires ${time(a.expires)}</small></div>`).join(''):`<h3>${w.updated?'No alerts in the last fetched response.':'Check official conditions.'}</h3><p class="muted small">${w.updated?'This does not establish that roads are clear.':'Refresh to request Raleigh alerts from the National Weather Service.'}</p>`);}
function renderState(){
 $('hazard-count').textContent=`${state.hazards.length} locations`;
 $('hazard-list').innerHTML=state.hazards.map(h=>`<div class="hazard-row"><span class="hazard-icon">${h.kind==='Flooding'?'≈':'⌁'}</span><div><p>${esc(h.note||h.kind)}</p><small>${esc(h.source)} · ${esc(h.confidence)}${h.expires?`<br>Expires ${time(h.expires)}`:''}</small></div><span class="badge">${esc(h.severity)}</span></div>`).join('');renderWeather(state.weather);drawMap();
}
async function load(){
 try{state=await api('/api/state');localStorage.setItem('wayfinder-state',JSON.stringify(state));offline=false;}catch{try{state=JSON.parse(localStorage.getItem('wayfinder-state'));}catch{}offline=true;}
 $('connection').textContent=offline?'● Cached / offline':'● Local server connected';
 if(!state){toast('Cannot connect. Start the Python server and reload.');return;}
 const old=$('start').value,options=state.nodes.map(n=>`<option value="${n.id}">Grid ${n.id} · ${n.lat.toFixed(3)}, ${n.lon.toFixed(3)}</option>`).join('');$('start').innerHTML=options;$('report-location').innerHTML=options;$('start').value=old||'1-2';
 if($('destination').options.length===1)for(const d of state.destinations)$('destination').add(new Option(d.name+' (sample)',d.id));renderState();
}
function offlineRoute(start,target){
 const found=[];
 for(const d of state.destinations.filter(d=>target==='auto'||d.id===target)){
  const costs={[start]:0},previous={},pending=new Set(state.nodes.map(n=>n.id));
  while(pending.size){const current=[...pending].reduce((a,b)=>(costs[a]??Infinity)<(costs[b]??Infinity)?a:b);if(!Number.isFinite(costs[current]))break;pending.delete(current);if(current===d.node)break;
   for(const e of state.edges.filter(e=>e.risk!=='blocked'&&(e.a===current||e.b===current))){const next=e.a===current?e.b:e.a,cost=costs[current]+e.distance*({clear:1,caution:3,high:12}[e.risk]);if(cost<(costs[next]??Infinity)){costs[next]=cost;previous[next]={parent:current,edge:e};}}
  }
  if(costs[d.node]!==undefined){const nodes=[d.node],edges=[];while(nodes[0]!==start){const p=previous[nodes[0]];nodes.unshift(p.parent);edges.unshift(p.edge);}found.push({nodes,edges,cost:costs[d.node],distance:edges.reduce((s,e)=>s+e.distance,0),destination:d,caution:edges.filter(e=>e.risk==='caution').length,high_risk:edges.filter(e=>e.risk==='high').length});}
 }
 if(!found.length)throw new Error('No viable route in the cached network.');return found.sort((a,b)=>a.cost-b.cost)[0];
}
async function findRoute(){
 if(!state)return;$('route-button').disabled=true;
 try{if(!offline)await load();route=offline?offlineRoute($('start').value,$('destination').value):await api('/api/route',{start:$('start').value,destination:$('destination').value});$('route-result').innerHTML=`<div class="eyebrow">${offline?'CACHED SAMPLE ROUTE':'LOWER-RISK SAMPLE ROUTE'}</div><h3>${esc(route.destination.name)}</h3><div class="route-stats"><div><strong>${(route.distance/1000).toFixed(1)}</strong><span>kilometers</span></div><div><strong>${route.edges.length}</strong><span>road segments</span></div></div><small>${route.caution} caution · ${route.high_risk} high-risk segments<br>Blocked roads excluded. ${offline?'Based on cached conditions. ':''}Shelter availability is illustrative.</small>`;drawMap();}
 catch(e){resetRoute(e.message);toast(e.message);}finally{$('route-button').disabled=false;}
}
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b===button));$('map-view').hidden=button.dataset.view!=='map';$('sensors-view').hidden=button.dataset.view!=='sensors';}));
$('start').addEventListener('change',()=>resetRoute('Starting point updated. Calculate a new route.'));
$('destination').addEventListener('change',()=>resetRoute('Destination updated. Calculate a new route.'));
$('route-button').addEventListener('click',findRoute);
$('hazard-toggle').addEventListener('click',()=>{showHazards=!showHazards;$('hazard-toggle').textContent=`Hazards ${showHazards?'on':'off'}`;$('hazard-toggle').setAttribute('aria-pressed',String(showHazards));drawMap();});
$('reset-map').addEventListener('click',()=>resetRoute());
$('locate').addEventListener('click',()=>{
 if(!state)return;if(!navigator.geolocation){toast('Geolocation is unavailable in this browser.');return;}
 navigator.geolocation.getCurrentPosition(pos=>{const lat=pos.coords.latitude,lon=pos.coords.longitude;if(lat<35.752||lat>35.800||lon< -78.680||lon> -78.623){toast('Your location is outside the sample network. Choose a grid point.');return;}const n=state.nodes.reduce((a,b)=>Math.hypot(b.lat-lat,(b.lon-lon)*.81)<Math.hypot(a.lat-lat,(a.lon-lon)*.81)?b:a);$('start').value=n.id;resetRoute('Location snapped to a sample grid point. Calculate a new route.');},()=>toast('Location unavailable. Select a sample starting point.'),{timeout:10000});
});
$('report-button').addEventListener('click',()=>{if(!state)return;$('report-location').value=$('start').value;$('report-dialog').showModal();});
$('close-dialog').addEventListener('click',()=>$('report-dialog').close());
$('report-form').addEventListener('submit',async event=>{event.preventDefault();const n=state.nodes.find(n=>n.id===$('report-location').value),button=event.submitter;button.disabled=true;try{state=await api('/api/reports',{lat:n.lat,lon:n.lon,kind:$('kind').value,severity:$('severity').value,note:$('note').value});localStorage.setItem('wayfinder-state',JSON.stringify(state));resetRoute('Conditions changed. Calculate a new route.');renderState();$('report-dialog').close();$('note').value='';toast('Report saved. Sample road risk updated.');}catch(e){toast(offline?'Reports require the local server. Your draft is still here.':e.message);}finally{button.disabled=false;}});
$('refresh').addEventListener('click',async()=>{const b=$('refresh');b.disabled=true;b.textContent='Fetching…';try{state.weather=await api('/api/refresh',{});localStorage.setItem('wayfinder-state',JSON.stringify(state));renderWeather(state.weather);toast('NWS conditions refreshed.');}catch(e){if(e.data&&state){state.weather=e.data;renderWeather(e.data);}toast(e.message);}finally{b.disabled=false;b.textContent='Refresh ↻';}});
function renderSensors(data){
 $('sensor-status').textContent=data.status;$('sensor-updated').textContent=`Last successful USGS fetch: ${time(data.fetched)} · Ingestion interval: ${data.poll_seconds}s · ${data.stations.reduce((s,r)=>s+r.length,0)} retained observations`;
 $('sensor-cards').innerHTML=data.stations.length?data.stations.map(series=>{const last=series[series.length-1],first=series[0],stale=Date.now()-new Date(last.observed)>90*60*1000,values=series.map(r=>r.value),min=Math.min(...values),max=Math.max(...values),range=max-min||1,points=series.map((r,i)=>`${10+i/Math.max(series.length-1,1)*270},${75-(r.value-min)/range*60}`).join(' ');return `<article class="card"><div class="eyebrow">USGS ${esc(last.site)} · ${stale?'STALE OBSERVATION':'RECENT OBSERVATION'}</div><h3>${esc(last.name)}</h3><div class="sensor-value">${last.value.toFixed(2)} <small>${esc(last.unit)}</small></div><p class="muted small">Gage height · observed ${time(last.observed)}</p><svg class="sparkline" viewBox="0 0 290 90" role="img" aria-label="Gauge height trend, range ${min.toFixed(2)} to ${max.toFixed(2)}"><line x1="10" x2="280" y1="76" y2="76" stroke="#dce1d8"/><polyline points="${points}" fill="none" stroke="#2b6953" stroke-width="2"/><circle cx="${series.length>1?280:10}" cy="${75-(last.value-min)/range*60}" r="3" fill="#2b6953"/></svg><p class="small">${last.value-first.value>=0?'+':''}${(last.value-first.value).toFixed(2)} ${esc(last.unit)} over ${Math.max(0,Math.round((new Date(last.observed)-new Date(first.observed))/60000))} minutes</p><a class="small" href="https://waterdata.usgs.gov/monitoring-location/USGS-${encodeURIComponent(last.site)}/" target="_blank" rel="noreferrer">Station details ↗</a></article>`;}).join(''):'<div class="card"><h3>Waiting for sensor observations.</h3><p class="muted">No readings available yet. The pipeline retries automatically; no synthetic readings are substituted.</p></div>';
}
async function loadSensors(){try{const data=await api('/api/sensors');localStorage.setItem('wayfinder-sensors',JSON.stringify(data));renderSensors(data);}catch{try{const data=JSON.parse(localStorage.getItem('wayfinder-sensors'));if(data)renderSensors({...data,status:'Offline · cached sensor data'});else $('sensor-status').textContent='Sensor feed unavailable';}catch{}}}
load();loadSensors();setInterval(loadSensors,10000);
window.addEventListener('online',load);window.addEventListener('offline',()=>{offline=true;$('connection').textContent='● Cached / offline';});
if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
