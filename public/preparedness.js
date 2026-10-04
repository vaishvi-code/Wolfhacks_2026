const COMMON=[
  ['supplies','Pack drinking water, food, a flashlight, batteries, and first-aid supplies.'],
  ['medical','Gather medications, medical supplies, and essential documents.'],
  ['contact','Agree on a family contact and meeting plan, including pets and accessibility needs.'],
  ['power','Charge your phone and power bank; save your city’s offline pack.'],
  ['alerts','Sign up for local emergency alerts and review official evacuation instructions.']
];
const SPECIFIC={
  flood:[['higher','Identify higher ground and an official destination outside flood-prone areas.'],['roads','Check road closures and alternate routes; never plan to cross floodwater.'],['documents','Keep important documents and essential supplies in waterproof containers.']],
  hurricane:[['shelter','Confirm an official hurricane shelter and how you will reach it before conditions worsen.'],['home','Secure loose outdoor objects and prepare window protection before the storm arrives.'],['zone','Check your evacuation zone and transport arrangements; follow local orders.']],
  heat:[['cooling','Confirm an air-conditioned place or cooling center, its hours, and transport.'],['water','Prepare drinking water and arrange breaks from outdoor activity.'],['checkin','Arrange check-ins with vulnerable neighbors or relatives and plan for a power outage.']]
};
const SOURCES={flood:'https://www.ready.gov/floods',hurricane:'https://www.nhc.noaa.gov/prepare/',heat:'https://www.weather.gov/safety/heat-ww'};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function checklist(hazard){return [...COMMON,...(SPECIFIC[hazard]||[])];}
export function alertTiming(alert,now=Date.now()){
  const onset=Date.parse(alert.onset),end=Date.parse(alert.ends||alert.expires);
  if(Number.isFinite(end)&&end<=now)return {status:'ended',label:'Saved alert has ended'};
  if(!Number.isFinite(onset))return {status:'unknown',label:'Expected start not specified'};
  if(onset<=now)return {status:'started',label:'Reported start time has passed'};
  const minutes=Math.ceil((onset-now)/60000),hours=Math.floor(minutes/60);
  return {status:'upcoming',label:`Expected in ${hours?`${hours}h `:''}${minutes%60}m`};
}
const when=value=>Number.isFinite(Date.parse(value))?new Date(value).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}):'Not specified';
export function initPreparedness({state,toast}){
  const $=id=>document.getElementById(id);let key='',checked={},lastRegion=null,seen=new Set();
  function render(){
    const nextKey=`terrawatch-prep-v1:${state.hazard}`;
    if(nextKey!==key){key=nextKey;try{checked=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{checked={};}
      $('prep-title').textContent=`Prepare for ${state.hazard==='heat'?'extreme heat':state.hazard}`;
      $('prep-items').innerHTML=checklist(state.hazard).map(([id,text])=>`<label class="prep-item"><input type="checkbox" data-prep="${id}" ${checked[id]?'checked':''}><span>${escape(text)}</span></label>`).join('');
      $('prep-source').href=SOURCES[state.hazard];
    }
    const items=checklist(state.hazard),done=items.filter(([id])=>checked[id]).length;
    $('prep-progress').textContent=`${done} of ${items.length} completed`;$('prep-meter').max=items.length;$('prep-meter').value=done;
    const snapshot=state.snapshot;
    $('alert-location').textContent=`${snapshot?.region.name||state.region} · regional alerts`;
    if(!snapshot){$('upcoming-alerts').textContent='Loading official alert information…';return;}
    const matching=snapshot.incidents.filter(a=>a.category===state.hazard);
    const active=matching.filter(a=>alertTiming(a).status!=='ended').sort((a,b)=>(Date.parse(a.onset)||0)-(Date.parse(b.onset)||0));
    const source=snapshot.sources.find(s=>s.id.startsWith('nws-'));
    const stale=state.offline||state.mode==='live'&&(!source||!['live','cached'].includes(source.status)||!Number.isFinite(Date.parse(source.lastSuccess))||Date.now()-Date.parse(source.lastSuccess)>15*60000);
    $('alert-freshness').textContent=state.mode==='demo'?'Fictional timing for this exercise, not a forecast.':`${stale?'Saved or unavailable data — reconnect to check updates. ':'Updated while this app is open. '}Last warning check: ${when(source?.lastSuccess)}. Regional timing may differ at your location.`;
    $('upcoming-alerts').innerHTML=active.length?active.map(a=>{
      const timing=alertTiming(a),url=a.url&&/^https:\/\/(api\.)?weather\.gov\//.test(a.url)?a.url:null;
      return `<article class="prep-alert ${timing.status}"><div><strong>${escape(a.title)}</strong><span class="prep-alert-label">${state.mode==='demo'?'Demo · ':stale?'Saved · ':''}${escape(timing.label)}</span></div><p>${escape(a.place||snapshot.region.name)}</p><dl><div><dt>Expected start</dt><dd>${escape(when(a.onset))}</dd></div><div><dt>${a.ends?'Expected end':'Alert expires'}</dt><dd>${escape(when(a.ends||a.alertExpires||a.expires))}</dd></div></dl>${a.certainty?`<p>Certainty: ${escape(a.certainty)}</p>`:''}${a.instruction?`<p>${escape(a.instruction)}</p>`:''}${url?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">Read official alert ↗</a>`:''}</article>`;
    }).join(''):`<p class="prep-empty">${stale&&state.mode==='live'?'Current alerts could not be verified.':matching.length?'The saved alerts have ended.':'No matching issued alert was returned.'} This does not rule out future hazards. Prepare now and check official forecasts.</p>`;
    const regionKey=`${state.region}:${state.mode}`;
    if(lastRegion===regionKey&&!stale&&state.mode==='live'&&active.some(a=>!seen.has(a.id)))toast('New weather alert for your region. Check the preparation panel.');
    if(lastRegion!==regionKey||!stale){lastRegion=regionKey;seen=new Set(snapshot.incidents.map(a=>a.id));}
  }
  $('prep-items').addEventListener('change',event=>{const id=event.target.dataset.prep;if(!id)return;checked[id]=event.target.checked;try{localStorage.setItem(key,JSON.stringify(checked));}catch{toast('Checklist changes could not be saved on this device.',true);}render();});
  $('prep-reset').onclick=()=>{checked={};try{localStorage.removeItem(key);}catch{}key='';render();};
  setInterval(render,60000);
  return {render};
}
