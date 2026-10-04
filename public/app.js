import {initNavigation} from './navigation.js';
import {initGuidance} from './guidance.js';
import {savePack,loadPack,loadCityMap,ensureOfflineShell} from './offline.js';
import {roadFeatures} from './offline-map.js';
import {createCityBasemap} from './city-map.js';
import {downloadCityMap,megabytes} from './map-download.js';
import {initConditions} from './conditions.js';
import {affectingEvents} from '/shared/route-events.mjs';
import {requestRoute} from './route-request.js';
import {initPreparedness} from './preparedness.js';
import {destinationCandidates,evacuationIncidents,planEvacuation} from '/shared/evacuation.mjs';
import {pointInGeometry,bboxGeometry,distanceKm} from '/shared/geo.mjs';

const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',locate:'M12 2v4m0 12v4M2 12h4m12 0h4 M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0',flood:'M2 8q3-4 6 0t6 0 8 0M2 14q3-4 6 0t6 0 8 0M2 20q3-4 6 0t6 0 8 0',hurricane:'M20 5C10-3 0 7 8 14c5 4 12-1 8-6-5-6-14 2-12 10m0 0c6 8 20 1 15-7',heat:'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5 M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',arrow:'M4 12h16m-6-6 6 6-6 6',layers:'m12 3 10 6-10 6L2 9 12 3ZM2 14l10 6 10-6',map:'m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16',route:'M6 4a2 2 0 1 1 0 4 2 2 0 0 1 0-4Zm12 12a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM6 8v6a4 4 0 0 0 4 4h6M10 6h6a3 3 0 0 1 0 6h-4',shield:'m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6l9-4Zm-5 10 3 3 7-7',flask:'M9 2h6m-5 0v7L4 19q-1 3 3 3h10q4 0 3-3L14 9V2M7 15h10',refresh:'M20 7V2l-3 3a9 9 0 1 0 3 12M20 7h-5',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',volume:'M3 9h4l5-5v16l-5-5H3V9Zm13-2q6 5 0 10m3-14q9 9 0 18',alert:'m12 3 10 18H2L12 3Zm0 6v5m0 3v1',plus:'M12 4v16M4 12h16',sparkles:'m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7',check:'m5 12 4 4L19 6'};
const icon=name=>`<svg class="icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.pin}"/></svg>`;
const hydrate=()=>document.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});
const META={flood:{name:'Flood',short:'flood',color:'#32779a',hint:'Explore a route beyond the flood warning. Never drive through floodwater.',url:'https://www.weather.gov/safety/flood-during'},hurricane:{name:'Hurricane',short:'hurricane',color:'#8a679b',hint:'Explore a route beyond the wind warning. Follow official evacuation orders.',url:'https://www.weather.gov/safety/hurricane'},heat:{name:'Extreme heat',short:'heat',color:'#ba7331',hint:'Look for a resource outside the heat warning. Confirm cooling and opening hours.',url:'https://www.weather.gov/safety/heat'}};
const state={region:'raleigh',mode:'demo',hazard:'flood',snapshot:null,config:null,origin:null,originKind:null,plan:null,routeIndex:0,showComparison:false,offline:false,epoch:0,revision:0,busy:false,picking:null,brief:null,stream:null,fingerprint:''};
// Reopening offline should return to the area and mode that were downloaded.
try{const saved=JSON.parse(localStorage.getItem('terrawatch-offline-area')||'null');if(saved&&['raleigh','wilmington','asheville'].includes(saved.region)&&['live','demo'].includes(saved.mode)&&Object.hasOwn(META,saved.hazard)){state.region=saved.region;state.mode=saved.mode;state.hazard=saved.hazard;}}catch{}
$('region').value=state.region;
const latlng=p=>[p[1],p[0]],formatTime=value=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'Unavailable';
const safeUrl=url=>{try{const u=new URL(url);return u.protocol==='https:'?esc(u.href):null;}catch{return null;}};
const map=L.map('map',{zoomControl:false}).setView([35.7796,-78.6382],12);
const basemap=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
let basemapFallback=false;
const cityBasemap=createCityBasemap(map,{onStatus:message=>{$('basemap-status').textContent=message;},onFallback:failed=>{basemapFallback=failed;if(failed&&!state.offline)basemap.addTo(map);else if(map.hasLayer(basemap))map.removeLayer(basemap);renderOfflineMap();}});
L.control.zoom({position:'topright'}).addTo(map);
map.createPane('offlineStreets');map.getPane('offlineStreets').style.zIndex=250;
const offlineStreets=L.layerGroup().addTo(map),streetRenderer=L.canvas({pane:'offlineStreets',padding:.3});
let renderedRoads=null;
function renderOfflineMap(){
  const graph=state.offline&&basemapFallback?state.snapshot?.roads:null;
  if(state.offline&&map.hasLayer(basemap))map.removeLayer(basemap);
  if(state.snapshot)cityBasemap.update(state.region,state.offline);
  if(graph===renderedRoads)return;
  renderedRoads=graph;offlineStreets.clearLayers();
  if(!graph)return;
  L.geoJSON(roadFeatures(graph),{pane:'offlineStreets',renderer:streetRenderer,style:{color:'#6c7875',weight:2,opacity:.85},onEachFeature:(feature,layer)=>layer.bindTooltip(feature.properties.name)}).addTo(offlineStreets);
  const [w,s,e,n]=state.snapshot.region.bbox;
  L.rectangle([[s,w],[n,e]],{pane:'offlineStreets',interactive:false,color:'#697b73',weight:1,dashArray:'6 6',fill:false}).addTo(offlineStreets);
}
const warnings=L.layerGroup().addTo(map),resources=L.layerGroup().addTo(map),personal=L.layerGroup().addTo(map),routeLayer=L.layerGroup().addTo(map),observations=L.layerGroup().addTo(map);
new ResizeObserver(()=>map.invalidateSize()).observe($('map'));
let toastTimer,guidance,preparedness,conditionsUI,navigation;
let reportLocation=null,reportEpoch=null,reportGpsVersion=0;
const reportPreview=L.layerGroup().addTo(map);
function setReportLocation(point,label){reportLocation=[...point];reportEpoch=state.epoch;$('report-location-status').textContent=label;$('report-error').textContent='';reportPreview.clearLayers();marker(point,'report-pin','Selected hazard location').addTo(reportPreview);}

function toast(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
async function api(path,body){const r=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(path==='/api/evacuate'?8000:95000)});const data=await r.json();if(!r.ok)throw new Error(data.error||'The request failed.');return data;}
const context=()=>({region:state.region,mode:state.mode,hazard:state.hazard,minimumClearanceKm:Number($('warning-clearance').value)});
const selectedWarnings=()=>state.snapshot?evacuationIncidents(state.snapshot,state.hazard).filter(i=>i.category===state.hazard):[];
const candidates=()=>state.snapshot?destinationCandidates(state.snapshot,state.hazard,Date.now(),context().minimumClearanceKm):[];
const selectedRoute=()=>state.plan?.alternatives?.[state.routeIndex];
function fingerprint(s){return JSON.stringify({incidents:evacuationIncidents(s,state.hazard).map(i=>[i.id,i.geometry,i.score]),roadVersion:s.roadInfo?.basis==='osm'?s.roadInfo?.fetchedAt:null,facilities:s.facilities.map(f=>[f.id,f.coordinates,f.access]),reports:(s.reports||[]).filter(r=>Date.parse(r.expires)>Date.now()),sources:s.sources.filter(s=>/^(nws|osm)-/.test(s.id)).map(s=>[s.id,['live','cached'].includes(s.status)?'available':s.status])});}
function clearBrief(){guidance?.reset();}
function invalidate(message=''){if(!message)state.routeChange=null;state.revision++;state.plan=null;state.routeIndex=0;state.showComparison=false;state.busy=false;$('planner-error').textContent=message;clearBrief();renderResult();updateControls();}
function updateControls(){
  preparedness?.render();
  conditionsUI?.render();
  const m=META[state.hazard];document.documentElement.style.setProperty('--accent',m.color);
  document.querySelectorAll('[data-hazard]').forEach(el=>{const yes=el.dataset.hazard===state.hazard;el.classList.toggle('selected',yes);el.setAttribute('aria-pressed',yes);});
  for(const mode of ['live','demo']){$(`mode-${mode}`).classList.toggle('selected',state.mode===mode);$(`mode-${mode}`).setAttribute('aria-pressed',state.mode===mode);}
  $('use-demo-location').hidden=state.mode!=='demo';$('map-watermark').hidden=state.mode!=='demo';$('hazard-hint').textContent=m.hint;$('map-title').textContent=`Your ${m.short} exit plan`;$('legend-label').textContent=`${m.name} warning`;$('safety-link').href=m.url;$('safety-link').textContent=`Read ${m.short} guidance ↗`;$('guidance-title').textContent=`${m.name} guidance`;
  $('demo-road-key').hidden=state.mode!=='demo'||state.hazard!=='flood';
  if(state.mode==='demo'&&state.hazard==='flood')$('hazard-hint').textContent='Demo: green roads are simulated dry roads. Avoid red flooded areas.';
  $('quick-route').disabled=!state.snapshot||state.busy;$('quick-route').textContent=state.busy?'Finding…':'Find route';$('quick-route').disabled=!state.origin||!state.snapshot||state.busy;
  $('dock-location').textContent=state.origin?'Change GPS':'Use GPS';
  document.querySelector('.map-result-shortcut').hidden=!state.plan;
  $('open-route-result').textContent=selectedRoute()?selectedRoute().distanceKm.toFixed(1)+' km · View route':'View route result';
  $('find-route').disabled=!state.origin||!state.snapshot||state.busy;$('find-route').innerHTML=state.busy?'Comparing routes…':`Find a route ${icon('arrow')}`;
  $('map-prompt').hidden=!!state.origin;$('location-card').classList.toggle('set',!!state.origin);
  $('location-title').textContent=state.origin?state.originKind==='example'?'Example starting location':state.originKind==='gps'?'Your GPS location':'Your chosen location':'Choose a starting point';
  $('location-coordinates').textContent=state.origin?`${state.origin[1].toFixed(5)}, ${state.origin[0].toFixed(5)}`:'Use GPS or tap the map.';
  $('location-step-number').classList.toggle('complete',!!state.origin);
  if(state.origin){const inside=selectedWarnings().some(i=>pointInGeometry(state.origin,i.geometry));$('location-status').textContent=inside?`Inside ${state.mode==='demo'?'the example':'a mapped'} ${m.short} warning area.`:'Outside the selected mapped warning; local conditions remain unverified.';}
  else $('location-status').textContent='Location is used only when you choose it.';
}
function renderDestinations(){const prior=$('destination').value,items=candidates().sort((a,b)=>state.origin?distanceKm(state.origin,a.coordinates)-distanceKm(state.origin,b.coordinates):a.name.localeCompare(b.name));$('destination').innerHTML='<option value="">Suggest a nearby resource</option>'+items.map(f=>`<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');if(items.some(f=>f.id===prior))$('destination').value=prior;}
function renderStatus(){
  if(!state.snapshot)return;const s=state.snapshot,count=selectedWarnings().length;
  $('mode-banner').hidden=state.mode==='demo'&&!state.offline;
  $('mode-banner').classList.toggle('live',state.mode==='live');
  const realStreets=s.roadInfo?.basis==='osm';
  $('mode-banner').innerHTML=state.mode==='demo'?`${icon('download')} <span><strong>Saved offline data</strong> · Conditions will not update without internet.</span>`:`${icon('shield')} <span><strong>${state.offline?'Saved offline alerts':'Live alerts'}</strong> · ${count?count+' matching warning'+(count===1?'':'s'):'No matching warning returned.'} Follow official directions.</span>`;
  $('map-watermark').textContent=realStreets?'SIMULATED DISASTER':'FICTIONAL DEMO';
  const roadDate=realStreets&&s.roadInfo.fetchedAt?new Date(s.roadInfo.fetchedAt).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):formatTime(s.fetchedAt);
  $('map-region').textContent=s.region.name;$('map-status').textContent=`${state.offline?'Saved routing data · '+(realStreets?'real streets':'fictional roads'):s.roadInfo?.bundled?'Bundled real streets':realStreets?`${s.roadInfo.segments.toLocaleString()} OSM road segments`:'Illustrative roads'} · ${roadDate}${state.mode==='live'?' · road conditions unverified':''}`;
  if(state.offline&&s.offlineSavedAt)$('offline-save-status').textContent=`Offline · ${s.region.name} · ${s.mode} conditions saved ${formatTime(s.offlineSavedAt)}. Warnings will not update without internet.`;
  updateControls();
}
function marker(p,cls,text){return L.marker(latlng(p),{icon:L.divIcon({className:cls,html:icon(cls==='origin-pin'?'locate':cls==='report-pin'?'alert':'pin'),iconSize:[30,30],iconAnchor:[15,15]}),title:text,keyboard:true});}
function detail(title,body){$('map-detail').hidden=false;$('map-detail').innerHTML=`<button class="close-detail icon-button" aria-label="Close map detail">×</button><h3>${esc(title)}</h3>${body}`;$('map-detail').querySelector('button').onclick=()=>$('map-detail').hidden=true;}
function choosePoint(e){if(!state.picking)return false;const p=[e.latlng.lng,e.latlng.lat];if(state.picking==='report'){if(!insideCoverage(p)){toast('Choose a point inside this coverage area.',true);return true;}setReportLocation(p,'Location selected on the map.');cancelPick();$('report-dialog').showModal();}else setOrigin(p,'map');return true;}
function renderMap(){
  renderOfflineMap();warnings.clearLayers();resources.clearLayers();personal.clearLayers();routeLayer.clearLayers();observations.clearLayers();
  $('comparison-key').hidden=!state.showComparison||!selectedRoute()?.comparison?.shortest;
  if(!state.snapshot)return;
  for(const w of selectedWarnings())if(w.geometry)L.geoJSON(w.geometry,{style:{color:META[state.hazard].color,weight:2,fillOpacity:.18}}).on('click',e=>{if(choosePoint(e))return;detail(w.title,`<p>${esc(w.description)}</p><p>${esc(w.instruction)}</p>${safeUrl(w.url)?`<a href="${safeUrl(w.url)}" target="_blank" rel="noopener noreferrer">Read official warning ↗</a>`:''}`);}).addTo(warnings);
  if(state.mode==='demo'&&state.hazard==='flood'&&state.snapshot.demoRouting){
    L.geoJSON(state.snapshot.demoRouting.floodRoads,{interactive:false,style:f=>({color:f.properties.closed?'#b55043':'#4a927c',weight:f.properties.closed?2.5:1.5,opacity:.6,dashArray:f.properties.closed?'3 4':null})}).addTo(warnings);
    L.geoJSON(state.snapshot.demoRouting.floodedAreas,{interactive:false,style:{color:'#b55043',weight:1.5,fillOpacity:.38}}).addTo(warnings);
  }
  const current=selectedRoute();let shown=state.plan?.alternatives?.length?state.plan.alternatives.map(r=>r.destination):state.origin?candidates().sort((a,b)=>distanceKm(state.origin,a.coordinates)-distanceKm(state.origin,b.coordinates)).slice(0,5):[];
  for(const f of shown)marker(f.coordinates,f.id===current?.destination.id?'destination-pin':'facility-pin',f.name).on('click',e=>{if(choosePoint(e))return;detail(f.name,`<p>${esc(f.kind.replaceAll('_',' '))} · opening and shelter status unverified.</p><p>Outside mapped warning areas checked for this plan.</p><button class="button secondary" data-destination="${esc(f.id)}">Choose this destination</button>`);$('map-detail').querySelector('[data-destination]').onclick=()=>{$('destination').value=f.id;invalidate();renderMap();$('map-detail').hidden=true;};}).addTo(resources);
  if(state.origin)marker(state.origin,'origin-pin',state.originKind==='example'?'Example location':'Your location').addTo(personal);
  for(const r of state.snapshot.reports||[])if(Date.parse(r.expires)>Date.now()&&(r.kind!=='heat_concern'||state.hazard==='heat'))marker(r.coordinates,'report-pin','Unverified road observation').on('click',e=>{if(choosePoint(e))return;detail(r.kind.replaceAll('_',' '),`<p>${esc(r.description)}</p><p>Unverified · expires ${formatTime(r.expires)}</p>`);}).addTo(observations);
  if(current){
    if(state.showComparison&&current.comparison?.shortest){const shortest=current.comparison.shortest;L.geoJSON(shortest.geometry,{interactive:false,style:{color:'#fff',weight:9,opacity:.95}}).addTo(routeLayer);L.geoJSON(shortest.geometry,{interactive:false,style:{color:'#67717b',weight:5,dashArray:'7 7',opacity:1}}).addTo(routeLayer);L.geoJSON(shortest.excludedGeometry,{interactive:false,style:{color:'#ba463b',weight:6,dashArray:'4 5',opacity:1}}).addTo(routeLayer);}
    L.geoJSON(current.geometry,{style:{color:'#fff',weight:9,opacity:.9}}).addTo(routeLayer);L.geoJSON(current.geometry,{style:{color:'#176450',weight:5,opacity:1}}).addTo(routeLayer);if(current.access){for(const [p,q] of [[state.origin,current.access.start],[current.access.end,current.destination.coordinates]])if(distanceKm(p,q)>.001)L.polyline([latlng(p),latlng(q)],{color:'#367fa9',weight:3,dashArray:'4 6',interactive:false}).bindTooltip('Unverified access gap').addTo(routeLayer);}personal.bringToFront?.();}
}
function resetView(){if(state.snapshot){const [w,s,e,n]=state.snapshot.region.bbox;map.fitBounds([[s,w],[n,e]],{padding:[20,20]});}}
function fitRoute(){const r=selectedRoute();if(r){const points=[...r.geometry.coordinates,state.origin,r.destination.coordinates];if(state.showComparison&&r.comparison?.shortest)points.push(...r.comparison.shortest.geometry.coordinates);if(state.mode==='demo'&&state.hazard==='flood')for(const warning of selectedWarnings())if(warning.geometry?.type==='Polygon')points.push(...warning.geometry.coordinates[0]);map.fitBounds(points.map(latlng),{padding:[45,45],maxZoom:14});}}
function comparisonHTML(route){
  const c=route.comparison;if(!c||c.status!=='available')return '<p class="small-note">The shortest-route comparison is unavailable for this destination.</p>';
  const b=c.shortest,excluded=b.excludedKm>.0001;
  const reason=c.samePath?'The checks chose the same road route. No detour is needed under the available data.':`${c.extraDistanceKm<.05?'Similar road distance.':`${c.extraDistanceKm.toFixed(1)} km extra by road.`} ${excluded?'The shortest route includes segments excluded by the current checks.':'The route choice accounts for warning exposure and avoids re-entering the selected warning.'}`;
  return `<section class="route-comparison" aria-label="Route comparison"><div class="comparison-heading"><h3>Why this route?</h3><span>Same destination · same road access</span></div><div class="comparison-grid"><div class="comparison-card chosen"><span class="comparison-label"><i class="legend-route"></i> With hazard checks</span><strong>${route.distanceKm.toFixed(1)} <small>km</small></strong><span>${route.exposureKm.toFixed(1)} km in caution segments</span><span class="comparison-outcome">No excluded road segments</span></div><div class="comparison-card"><span class="comparison-label"><i class="legend-shortest"></i> Shortest by distance</span><strong>${b.distanceKm.toFixed(1)} <small>km</small></strong><span>${b.exposureKm.toFixed(1)} km in caution / excluded segments</span><span class="comparison-outcome ${excluded?'excluded':''}">${excluded?`${b.excludedKm.toFixed(1)} km excluded by checks`:'No excluded road segments'}</span></div></div><p class="comparison-reason">${reason}</p><div class="comparison-footer"><span>Shortest route is for comparison only. Neither route is verified safe.</span><button class="text-button" id="toggle-comparison" aria-pressed="${state.showComparison}">${state.showComparison?'Hide':'Show'} shortest on map</button></div></section>`;
}
function renderResult(){
  const plan=state.plan,r=selectedRoute(),m=META[state.hazard];
  if(!plan){$('route-result').innerHTML=`<div class="result-empty"><span class="icon-box">${icon('route')}</span><div><h2>${state.origin?'Ready to plan your route':'Your route will appear here'}</h2><p>${state.origin?'Tap Find a route to compare destinations.':'Set a location, then find a route.'}</p></div></div>`;return;}
  if(!r){const headings={no_warning:'No matching warning in this view',no_route:'An exit route could not be established',no_destination:'No destination found outside the warnings',incomplete_data:'More information is needed',stale_data:'Refresh before planning',destination_unavailable:'Choose another destination'};$('route-result').innerHTML=`<div class="no-route"><span class="icon-box">${icon('alert')}</span><div><span class="tag neutral">${esc(m.name)}</span><h2>${esc(headings[plan.status]||'Route unavailable')}</h2><p>${esc(plan.message)}</p><a href="${m.url}" target="_blank" rel="noopener noreferrer">Official ${m.short} guidance ↗</a></div></div>`;return;}
  const connector=(r.snapDistances.start+r.snapDistances.end)*1000;
  $('route-result').innerHTML=`<div class="result-heading"><div><span class="tag">${state.mode==='demo'?'DEMO · ':''}SUGGESTED RESOURCE</span><h2>${esc(r.destination.name)}</h2></div><div class="route-distance">${r.distanceKm.toFixed(1)} <small>km by road</small></div></div>
    <div class="route-detail-line"><span>${icon('pin')} ${r.warningClearanceKm.toFixed(1)} km beyond warning boundaries</span><span>${icon('alert')} Open status unverified</span></div>
    <p>${plan.originInside?'Planning route only. Confirm the destination is open.':'Outside this warning. This is a resource route.'}</p><div class="route-story"><div><span>Your starting point</span><strong>${plan.originInside?`Inside the ${m.short} warning`:'Outside the selected warning'}</strong></div><div><span>Along this road route</span><strong>${r.exposureKm.toFixed(1)} km in caution segments</strong></div></div>
    <details class="route-disclosure"><summary>Why this route?</summary>${comparisonHTML(r)}</details>
    <p class="route-caveat">${esc(state.mode==='demo'&&state.hazard==='flood'?'Demo route · fictional road conditions.':'Road passability is unverified. Follow official directions.')} ${connector>1?`${Math.round(connector)} m of unverified access shown dashed.`:''}</p>
    ${plan.otherHazardsChecked.length?`<p class="route-other">Also checked for known ${esc(plan.otherHazardsChecked.join(' and '))} warnings along the route. The map remains focused on ${esc(m.short)}.</p>`:''}
    ${plan.alternatives.length>1?`<div class="route-alternatives" aria-label="Suggested destinations">${plan.alternatives.map((a,i)=>`<button class="alternative ${i===state.routeIndex?'active':''}" data-route="${i}" aria-pressed="${i===state.routeIndex}"><strong>${esc(a.destination.name)}</strong><span>${a.distanceKm.toFixed(1)} km · ${i===0?'Least mapped exposure':'Alternative'}</span></button>`).join('')}</div>`:''}
    <div class="result-actions"><button class="text-button" id="route-details">How this route was chosen ${icon('arrow')}</button><button class="text-button" id="save-route">${icon('download')} Save route</button></div>`;
  $('route-result').querySelectorAll('[data-route]').forEach(b=>b.onclick=()=>{state.routeIndex=Number(b.dataset.route);renderResult();renderMap();fitRoute();});$('route-details').onclick=showRouteDetails;$('save-route').onclick=()=>download('wayahead-route.geojson',JSON.stringify({type:'Feature',geometry:r.geometry,properties:{destination:r.destination.name,mode:state.mode,hazard:state.hazard,createdAt:r.createdAt,distanceKm:r.distanceKm,disclaimer:r.note,simulation:r.simulation}},null,2),'application/geo+json');
  if($('toggle-comparison'))$('toggle-comparison').onclick=()=>{state.showComparison=!state.showComparison;renderResult();renderMap();fitRoute();};
}
function insideCoverage(p){return state.snapshot&&pointInGeometry(p,bboxGeometry(state.snapshot.region.bbox));}
function setOrigin(p,kind){if(!insideCoverage(p)){toast('That location is outside this coverage area. Select the matching city or choose a point inside its map.',true);return;}state.origin=p;state.originKind=kind;$('origin-lat').value=p[1].toFixed(6);$('origin-lon').value=p[0].toFixed(6);cancelPick();invalidate();renderDestinations();renderMap();map.panTo(latlng(p));}
function cancelPick(){state.picking=null;$('map-instruction').hidden=true;$('map').style.cursor='';$('pick-location').setAttribute('aria-pressed','false');}
function startPick(kind){if($('route-options-dialog').open)$('route-options-dialog').close();selectFeature('route');state.picking=kind;$('map-detail').hidden=true;$('map-instruction').hidden=false;$('map-instruction').textContent=`Choose ${kind==='report'?'the observation location':'your starting point'} on the map. Esc to cancel.`;$('map').style.cursor='crosshair';$('map-prompt').hidden=true;$('pick-location').setAttribute('aria-pressed',kind==='origin');$('map').scrollIntoView({behavior:'smooth',block:'center'});}
function acceptSnapshot(s){const next=fingerprint(s),previous=selectedRoute(),priorIds=new Set((state.snapshot?.reports||[]).map(e=>e.id)),affected=affectingEvents(previous,(s.reports||[]).filter(e=>!priorIds.has(e.id)));if((state.plan||state.busy)&&next!==state.fingerprint)invalidate('Conditions changed. Find a new route before using the plan.');if(affected.length)state.routeChange={events:affected,recalculated:false,previous};state.fingerprint=next;state.snapshot=s;renderDestinations();renderStatus();renderMap();conditionsUI?.render();}
async function loadSnapshot({reset=false,refresh=false}={}){
  const epoch=++state.epoch,ctx=context();if(state.busy)invalidate();state.stream?.close();state.stream=null;if(reset){state.snapshot=null;state.origin=null;state.originKind=null;invalidate();cancelPick();$('map-detail').hidden=true;renderMap();$('mode-banner').hidden=false;$('mode-banner').textContent='Loading your planner…';}
  $('refresh').disabled=true;
  try{let s;try{if(!navigator.onLine)throw new Error('No internet connection.');s=refresh?await api('/api/refresh',ctx):await api(`/api/snapshot?region=${ctx.region}&mode=${ctx.mode}`);if(epoch!==state.epoch)return;state.offline=false;}catch(error){s=await loadPack(ctx.region,ctx.mode);if(!s)throw error;if(epoch!==state.epoch)return;state.offline=true;toast('Using saved map data and conditions. Warnings will not update offline.');}
    acceptSnapshot(s);if(reset)resetView();if(ctx.mode==='live'&&!state.offline){state.stream=new EventSource(`/api/stream?region=${ctx.region}`);state.stream.addEventListener('snapshot',event=>{if(epoch===state.epoch){state.offline=false;acceptSnapshot(JSON.parse(event.data));}});}
  }catch(error){if(epoch===state.epoch){$('mode-banner').hidden=false;$('mode-banner').textContent='Conditions could not be loaded. Try refreshing or use a saved offline pack.';toast(error.message,true);}}
  finally{if(epoch===state.epoch){$('refresh').disabled=false;updateControls();}}
}
async function findRoute(){
  if($('route-options-dialog').open)$('route-options-dialog').close();
  selectFeature('route');
  if(!state.origin||!state.snapshot||state.busy)return;const revision=state.revision,epoch=state.epoch;const body={...context(),start:[...state.origin],destinationId:$('destination').value||null};state.busy=true;state.plan=null;renderResult();renderMap();$('planner-error').textContent='';updateControls();
  try{const result=await requestRoute({body,snapshot:state.snapshot,offline:state.offline,online:navigator.onLine,api,loadPack,planEvacuation});if(revision!==state.revision||epoch!==state.epoch)return;
    const plan=result.plan;
    if(result.offline){state.offline=true;state.stream?.close();if(result.snapshot){state.snapshot=result.snapshot;state.fingerprint=fingerprint(result.snapshot);renderDestinations();}renderStatus();}
    // Provider recovery can replace a fallback while a user is planning. Display
    // the exact snapshot used by this result, including its source labels.
    if(plan.mapSnapshot){state.snapshot=plan.mapSnapshot;state.fingerprint=fingerprint(plan.mapSnapshot);renderDestinations();renderStatus();}
    state.plan=plan;state.routeIndex=0;if(state.routeChange&&plan.status==='routes_found')state.routeChange.recalculated=true;renderResult();renderMap();fitRoute();if(!mobilePlanner.matches&&!document.body.classList.contains('map-fullscreen'))$('route-result').scrollIntoView({behavior:'smooth',block:'nearest'});}catch(error){if(revision===state.revision&&epoch===state.epoch){$('planner-error').textContent=error.message;toast(error.message,true);}}
  finally{if(revision===state.revision&&epoch===state.epoch){state.busy=false;updateControls();}}
}
function showInfo(title,html){$('info-title').textContent=title;$('info-content').innerHTML=html;$('info-dialog').showModal();}
function showMethodology(){showInfo('How routes are checked',`<div class="brief-content"><p>Choose one disaster to focus the map and guidance. Live routes still check other known warnings and unverified road reports.</p><p>Resources must be outside the warnings checked. A single road-network search compares eligible destinations and ranks them by least mapped hazard exposure first, then road distance. Wind overlaps cost 5×; heat overlaps cost 2×. Flood footprints rated 70 or higher exclude roads. Once outside the selected warning, a route cannot re-enter it.</p><p>Roads near reports of flooding, fallen trees, or blockages are temporarily excluded. Reports remain unverified and expire after six hours.</p><p>The lowest calculated cost is not proof of safety. This prototype does not know water depth, bridge condition, all closures, turn restrictions, shelter designation, capacity, or opening hours. Follow local evacuation directions.</p><p>Coverage is limited to the selected city area, not a statewide evacuation corridor. Heat warnings can cover every nearby resource; in that case no destination is suggested. The flood demo has simulated dry roads and red flooded patches. Starting points join road segments; dashed access gaps remain unverified.</p></div>`);}
function showRouteDetails(){const r=selectedRoute();if(!r)return;showInfo('About your suggested route',`<div class="brief-content"><p>${esc(state.plan.explanation)}</p><p>Compared ${state.plan.evaluatedDestinations} mapped destinations; ${state.plan.reachableDestinations} had a route under these rules. ${r.excludedSegments} segments in the loaded network were excluded.</p><p><strong>Road names along the route</strong><br>${r.streets.map(esc).join(' → ')}</p><p>These are road names, not turn-by-turn directions.</p><p>${esc(r.note)}</p><p>${esc(state.plan.notice)}</p><p>${esc(r.comparison?.explanation||'')}</p><p>Calculated ${formatTime(r.createdAt)}. Warning conditions can change.</p></div>`);}
function showData(){const s=state.snapshot;if(!s){toast('Load conditions first.',true);return;}const i=state.config?.integrations||{};showInfo('Data & help',`<p class="muted">The map shows your selected disaster. Source status and optional services are available here when you need them.</p><div class="data-grid">${s.sources.map(source=>`<div class="data-card"><div class="data-top"><strong>${esc(source.name)}</strong><span class="tag neutral">${esc(source.status)}</span></div><p>${esc(source.detail||'Public data source')}</p><small>Last successful check: ${formatTime(source.lastSuccess)}</small></div>`).join('')}</div><p class="data-note"><strong>${s.roadInfo.segments.toLocaleString()} road segments · ${s.facilities.length} mapped resources</strong><br>${s.roadInfo.basis==='osm'?'Street geometry and locations: OpenStreetMap via Overpass.':'Illustrative road grid and fictional resource locations.'} ${state.mode==='demo'?'Disaster conditions and supporting readings below are simulated. Switch to Live conditions to inspect NOAA and USGS feeds.':'NOAA and USGS public feeds work without an API key.'}</p><h3 class="section-title">Supporting observations</h3><p class="data-note">${s.sensor?`${esc(s.sensor.name)}: ${Number.isFinite(s.sensor.level)?s.sensor.level.toFixed(2)+' ft':'unavailable'}, observed ${formatTime(s.sensor.time)}. Gage height is not flood stage and does not establish road flooding.`:'No river observation available.'}</p><p class="data-note">${s.weather?`${esc(s.weather.station)}: ${Number.isFinite(s.weather.temperatureF)?Math.round(s.weather.temperatureF)+' °F':'temperature unavailable'}, observed ${formatTime(s.weather.time)}.`:'No weather observation available.'} These readings provide context; routing uses warning boundaries and road reports.</p><h3 class="section-title">Optional services</h3>${[['Gemini guidance',i.gemini?.configured?'Configured':'Local template active'],['ElevenLabs voice',i.elevenlabs?.keyConfigured?'Key configured · choose a narrator':'Browser voice available'],['Tiger Data',i.tiger?.status||'not-configured'],['Databricks ingest bridge',i.databricks?.status||'not-configured']].map(([name,status])=>`<div class="integration-row"><strong>${name}</strong><span>${esc(status)}</span></div>`).join('')}<p class="data-note">A mapped library, clinic, or community center is not a confirmed evacuation shelter. Confirm access with local authorities. Precise starting coordinates are sent to this app’s server for routing, not to the optional AI summary service.</p><div class="data-actions"><button class="button secondary" id="export-map">Export selected map data</button><button class="text-button" id="data-methodology">How routing works</button></div>`);$('export-map').onclick=()=>{const features=[...selectedWarnings().filter(i=>i.geometry).map(i=>({type:'Feature',geometry:i.geometry,properties:{name:i.title,category:i.category,simulation:!!i.simulation}})),...candidates().map(f=>({type:'Feature',geometry:{type:'Point',coordinates:f.coordinates},properties:{name:f.name,kind:f.kind,availability:'Unverified',simulation:!!f.simulation}}))];download('wayahead-selected-map.geojson',JSON.stringify({type:'FeatureCollection',features},null,2),'application/geo+json');};$('data-methodology').onclick=()=>{$('info-dialog').close();showMethodology();};}
function download(name,content,type='text/plain'){const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

document.querySelectorAll('[data-hazard]').forEach(b=>b.onclick=()=>{if(state.hazard===b.dataset.hazard)return;state.hazard=b.dataset.hazard;if(state.originKind==='example'){state.origin=null;state.originKind=null;}cancelPick();invalidate();$('destination').value='';$('map-detail').hidden=true;state.fingerprint=state.snapshot?fingerprint(state.snapshot):'';renderDestinations();renderStatus();renderMap();resetView();});
for(const mode of ['live','demo'])$(`mode-${mode}`).onclick=()=>{if(state.mode===mode)return;state.mode=mode;loadSnapshot({reset:true});};
$('region').onchange=()=>{state.region=$('region').value;loadSnapshot({reset:true});};
$('use-demo-location').onclick=()=>{const p=state.snapshot?.demoOrigins?.[state.hazard];if(p)setOrigin(p,'example');};
$('use-location').onclick=()=>{if(!navigator.geolocation){toast('GPS is unavailable. Choose your location on the map.',true);return;}const epoch=state.epoch,revision=state.revision;$('use-location').disabled=true;navigator.geolocation.getCurrentPosition(pos=>{$('use-location').disabled=false;if(epoch!==state.epoch||revision!==state.revision)return;setOrigin([pos.coords.longitude,pos.coords.latitude],'gps');},()=>{$('use-location').disabled=false;toast('Location was not available. Choose a point on the map or enter coordinates.',true);},{enableHighAccuracy:true,timeout:15000,maximumAge:30000});};
$('map-use-location').onclick=()=>$('use-location').click();$('map-pick-location').onclick=()=>startPick('origin');$('quick-route').onclick=()=>{if(state.origin)findRoute();else startPick('origin');};
$('pick-location').onclick=()=>startPick('origin');map.on('click',choosePoint);document.addEventListener('keydown',e=>{if(e.key==='Escape'){cancelPick();updateControls();}});
$('coordinate-form').onsubmit=e=>{e.preventDefault();setOrigin([Number($('origin-lon').value),Number($('origin-lat').value)],'manual');};
$('warning-clearance').onchange=()=>{invalidate();$('destination').value='';renderDestinations();renderMap();};
$('destination').onchange=()=>{invalidate();renderMap();};$('find-route').onclick=findRoute;$('reset-map').onclick=resetView;
$('refresh').onclick=()=>loadSnapshot({refresh:true});
$('save-offline').onclick=async()=>{
  const epoch=state.epoch,ctx=context(),button=$('save-offline');button.disabled=true;
  $('offline-save-status').textContent='Downloading streets and conditions…';button.textContent='Downloading…';
  try{
    const pack=await api(`/api/offline?region=${ctx.region}&mode=${ctx.mode}`);
    if(pack.roadInfo?.basis!=='osm')throw new Error('Real street data is unavailable. Refresh until OpenStreetMap streets load before saving.');
    if(!pack.roads?.nodes?.length||!pack.roads?.edges?.length)throw new Error('Street data is unavailable. Try saving again when roads have loaded.');
    await ensureOfflineShell();
    const catalog=await (await fetch('/maps/catalog.json',{cache:'no-store'})).json(),entry=catalog.regions[ctx.region];
    let mapAsset=await loadCityMap(ctx.region);
    if(!mapAsset?.blob||mapAsset.sha256!==entry.sha256)mapAsset=await downloadCityMap(entry,{onProgress:(received,total)=>{if(epoch===state.epoch){$('offline-save-status').textContent=`Downloading city map: ${megabytes(received)} / ${megabytes(total)}`;button.textContent=`Downloading ${Math.round(received/total*100)}%`;}}});
    $('offline-save-status').textContent='Saving map and routing data on this device…';
    pack.offlineSavedAt=new Date().toISOString();pack.offlineMapVersion=mapAsset.version;await savePack(pack,mapAsset);
    try{localStorage.setItem('terrawatch-offline-area',JSON.stringify({region:ctx.region,mode:ctx.mode,hazard:ctx.hazard}));}catch{}
    // Persistence is best effort; storage failures above never report success.
    navigator.storage?.persist?.().catch(()=>{});
    if(epoch===state.epoch){cityBasemap.refresh();renderOfflineMap();$('offline-save-status').textContent=`Available offline: ${pack.region.name} · ${megabytes(mapAsset.bytes)} map + ${pack.mode} routing data · saved ${formatTime(pack.offlineSavedAt)}. Map includes streets, buildings and labels within this city area.`;toast('City map and routing data saved for offline use.');}
  }catch(error){if(epoch===state.epoch)$('offline-save-status').textContent='Save failed: '+error.message;toast(error.message,true);}
  finally{button.disabled=false;button.innerHTML=icon('download')+' Save offline';}
};
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());$('open-data').onclick=showData;$('methodology').onclick=showMethodology;
$('open-report').onclick=()=>{$('report-mode').textContent=state.mode==='demo'?'DEMO REPORT':'LIVE APP OBSERVATION';$('report-error').textContent='';reportLocation=null;reportEpoch=null;reportPreview.clearLayers();$('report-location-status').textContent='Choose where you saw the hazard.';$('report-dialog').showModal();};
$('report-use-location').onclick=()=>{
  if(!navigator.geolocation){$('report-error').textContent='GPS is unavailable. Choose the hazard on the map.';return;}
  const epoch=state.epoch,version=++reportGpsVersion,button=$('report-use-location');button.disabled=true;$('report-error').textContent='';$('report-location-status').textContent='Finding your current location…';
  navigator.geolocation.getCurrentPosition(position=>{if(version!==reportGpsVersion||epoch!==state.epoch)return;button.disabled=false;const point=[position.coords.longitude,position.coords.latitude];if(!insideCoverage(point)){$('report-error').textContent='Your location is outside this city area. Choose the correct area or select a point on the map.';$('report-location-status').textContent='Choose where you saw the hazard.';return;}setReportLocation(point,'Current location selected.');},()=>{if(version!==reportGpsVersion||epoch!==state.epoch)return;button.disabled=false;$('report-location-status').textContent='Choose where you saw the hazard.';$('report-error').textContent='Could not get your location. Choose the hazard on the map.';},{enableHighAccuracy:true,timeout:15000,maximumAge:30000});
};
$('report-dialog').addEventListener('close',()=>{reportGpsVersion++;$('report-use-location').disabled=false;});
$('pick-report-location').onclick=()=>{$('report-dialog').close();startPick('report');};
$('report-form').onsubmit=async e=>{e.preventDefault();if(!reportLocation||reportEpoch!==state.epoch){$('report-error').textContent='Choose the hazard location using GPS or the map.';return;}const epoch=state.epoch,button=e.submitter;button.disabled=true;try{await api('/api/reports',{...context(),kind:$('report-kind').value,coordinates:reportLocation,description:$('report-description').value});$('report-dialog').close();$('report-form').reset();reportLocation=null;reportPreview.clearLayers();if(epoch===state.epoch){await loadSnapshot();toast('Observation saved as unverified. It expires after 6 hours.');}}catch(error){$('report-error').textContent=error.message;}finally{button.disabled=false;}};
setInterval(()=>{if(state.snapshot){if(state.mode==='live'&&state.plan&&Date.now()-Date.parse(state.plan.createdAt)>15*60000){invalidate('This plan has expired. Refresh conditions and calculate a new route.');renderMap();}const fp=fingerprint(state.snapshot);if(fp!==state.fingerprint){invalidate('A warning or observation changed. Find a new route.');state.fingerprint=fp;renderStatus();renderDestinations();renderMap();}}},30000);
window.addEventListener('offline',()=>{state.stream?.close();loadSnapshot();});
window.addEventListener('online',()=>{if(state.offline)toast('Connection restored. Refresh conditions before planning.');});
guidance=initGuidance({state,api,context,toast,download});
preparedness=initPreparedness({state,toast});
conditionsUI=initConditions({state,api,context,selectedRoute,acceptSnapshot,findRoute,toast});
navigation=initNavigation({map,state,selectedRoute,selectFeature,fitRoute});
hydrate();updateControls();api('/api/config').then(c=>state.config=c).catch(()=>{});loadSnapshot({reset:true});
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});

// Keep feature navigation independent of data mode and saved routing state.
function selectFeature(name){
  if(!['route','prepare','updates'].includes(name))return;
  if(name!=='route')navigation?.leave();
  document.querySelectorAll('[data-view]').forEach(button=>{const selected=button.dataset.view===name;button.setAttribute('aria-selected',selected);button.tabIndex=selected?0:-1;});
  for(const feature of ['route','prepare','updates'])$('view-'+feature).hidden=feature!==name;
  document.querySelector('.emergency-dock').hidden=name!=='route';
  if(name==='route')requestAnimationFrame(()=>map.invalidateSize());
}
const featureTabs=[...document.querySelectorAll('[data-view]')];
featureTabs.forEach((button,index)=>{
  button.onclick=()=>{selectFeature(button.dataset.view);document.querySelector('.feature-tabs').scrollIntoView({block:'start'});};
  button.onkeydown=event=>{let next;if(event.key==='ArrowRight')next=(index+1)%featureTabs.length;else if(event.key==='ArrowLeft')next=(index+featureTabs.length-1)%featureTabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=featureTabs.length-1;else return;event.preventDefault();featureTabs[next].focus();selectFeature(featureTabs[next].dataset.view);};
});
document.querySelectorAll('a[href="#route-planner"],a[href="#evacuation-map"],a[href="#route-settings"]').forEach(link=>link.addEventListener('click',()=>selectFeature('route')));

const mobilePlanner=matchMedia('(max-width:600px)');
const settingsHome=$('route-settings').parentElement,resultHome=$('route-result').parentElement;
function arrangeMobilePlanner(){
  if($('route-options-dialog').open)$('route-options-dialog').close();
  if($('route-summary-dialog').open)$('route-summary-dialog').close();
  if(mobilePlanner.matches){$('route-options-content').append($('route-settings'));$('route-summary-content').append($('route-result'));}
  else{settingsHome.prepend($('route-settings'));resultHome.append($('route-result'));}
  requestAnimationFrame(()=>map.invalidateSize());
}
mobilePlanner.addEventListener('change',arrangeMobilePlanner);arrangeMobilePlanner();
$('open-route-options').onclick=()=>$('route-options-dialog').showModal();
$('open-route-result').onclick=()=>$('route-summary-dialog').showModal();
$('dock-location').onclick=()=>$('use-location').click();
$('dock-pick').onclick=()=>startPick('origin');
