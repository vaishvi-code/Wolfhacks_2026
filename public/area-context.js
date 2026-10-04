const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const link=url=>{try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password?esc(u.href):null;}catch{return null;}};
export function areaContextCards(data){
  return [['flood','Flood-prone areas','Mapped flood zones from NC OneMap / NC Emergency Management. These are reference flood zones, not current inundation.'],['tracts','Census tracts','Census TIGERweb boundaries identify the tracts around this city. Boundaries alone do not contain population counts.'],['epa','Environmental facilities','EPA ECHO lists regulated facilities. A listing does not establish a spill, contamination, or current danger.']].map(([kind,title,note])=>{
    const item=data[kind],count=item.data?.features.length;
    return `<article class="area-context-card"><h3>${title}</h3><strong>${count==null?'Unavailable':`${count}${item.data.partial?'+':''} mapped features`}</strong><p>${note}</p><p class="small-note">${esc(item.source.name)} · ${esc(item.source.status)}${item.source.lastSuccess?' · retrieved '+esc(new Date(item.source.lastSuccess).toLocaleString()):''}${item.data?.partial?' · partial result':''}</p></article>`;
  }).join('');
}
export function initAreaContext({state,api,createCityBasemap,isOnline=()=>navigator.onLine}){
  const panel=document.getElementById('area-context-panel'),cache=new Map();
  let busyRegion=null,error='',renderedKey='',map=null,basemap=null,observer=null;
  function dispose(){observer?.disconnect();basemap?.remove?.();map?.remove();map=null;observer=null;basemap=null;}
  function render(){
    const region=state.region,data=cache.get(region),online=isOnline(),regionInfo=state.snapshot?.region.id===region?state.snapshot.region:state.config?.regions?.find(r=>r.id===region),center=data?.center||regionInfo?.center,key=JSON.stringify([region,data?.checkedAt,busyRegion,error,online,center]);
    if(key===renderedKey)return;renderedKey=key;dispose();
    panel.innerHTML=`<div class="conditions-heading"><h2>Area context</h2><button class="button secondary" id="load-area-context" ${busyRegion||!online?'disabled':''}>${busyRegion===region?'Loading area data…':data?'Refresh area data':'Load area data'}</button></div><p class="small-note">Explore flood zones, Census tracts and environmental facilities for ${esc(state.snapshot?.region.name||region)}. These reference layers apply to the real city, including when you try a demo.</p>${error?`<p role="alert">${esc(error)}</p>`:''}${!online?'<p class="small-note">Connect to load or refresh area data.</p>':''}${data?`<div class="area-context-cards">${areaContextCards(data)}</div><div id="area-context-map" class="area-context-map" role="region" aria-label="Reference flood zones, Census tracts and EPA facilities"></div><p class="small-note">Blue: flood zones · purple: Census tracts · orange: EPA facilities. Use the map layer control to show each layer.</p><details><summary>Environmental facilities (${data.epa.data?.features.length??0})</summary>${(data.epa.data?.features||[]).slice(0,20).map(f=>`<p>${esc(f.properties.name)} · ${esc(f.properties.city)}${/^\d+$/.test(f.properties.id)?` · <a href="https://echo.epa.gov/detailed-facility-report?fid=${f.properties.id}" target="_blank" rel="noopener noreferrer">EPA record</a>`:''}</p>`).join('')||'<p>No facility records available in this result.</p>'}</details><details><summary>Related public datasets</summary><p class="small-note">Data.gov catalog results describe datasets; they do not contain the measurements themselves.</p>${data.catalog.data?data.catalog.data.map(d=>`<p>${link(d.url)?`<a href="${link(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.title)}</a>`:esc(d.title)} · ${esc(d.publisher)}${d.modified?' · updated '+esc(d.modified):''}</p>`).join('')||'<p>No matching datasets returned.</p>':`<p>${data.catalog.source.status==='needs-key'?'Dataset discovery is not configured.':esc(data.catalog.source.detail)}</p><a href="https://catalog.data.gov/" target="_blank" rel="noopener noreferrer">Browse Data.gov</a>`}</details><p class="small-note">Ask WayAhead about these loaded sources, such as “What flood zones are mapped here?” or “Which environmental facilities are listed?”</p>`:''}`;
    panel.querySelector('#load-area-context').onclick=()=>load();
    if(!data||!center)return;
    map=L.map('area-context-map',{scrollWheelZoom:false}).setView([center[1],center[0]],12);
    basemap=createCityBasemap(map);void basemap.update(region,!online);
    const overlays={};
    for(const [kind,title,color]of [['flood','Flood zones','#32779a'],['tracts','Census tracts','#8a679b'],['epa','EPA facilities','#bc692e']]){
      if(!data[kind].data)continue;
      const layer=L.geoJSON(data[kind].data,{style:{color,weight:1.5,fillOpacity:kind==='flood'?.18:0},pointToLayer:(feature,latlng)=>L.circleMarker(latlng,{radius:5,color,fillOpacity:.8}),onEachFeature:(feature,l)=>{const p=feature.properties;l.bindPopup(kind==='flood'?`Reference flood zone ${esc(p.zone)}${p.subtype?' · '+esc(p.subtype):''}`:kind==='tracts'?`Census tract ${esc(p.name)} · GEOID ${esc(p.geoid)}`:`${esc(p.name)}<br>EPA-regulated facility · ${esc(p.city)}`);}});
      overlays[title]=layer;if(kind==='flood')layer.addTo(map);
    }
    L.control.layers(null,overlays,{collapsed:false}).addTo(map);
    observer=new ResizeObserver(()=>map?.invalidateSize());observer.observe(panel.querySelector('#area-context-map'));
  }
  async function load(){
    if(busyRegion||!isOnline())return;
    const requested=state.region;busyRegion=requested;error='';render();
    try{const result=await api(`/api/area-context?region=${requested}`);cache.set(requested,result);}catch(e){if(requested===state.region)error=e.message;}finally{busyRegion=null;render();}
  }
  function show(){render();if(!cache.has(state.region)&&!error)return load();}
  return {render,show};
}
