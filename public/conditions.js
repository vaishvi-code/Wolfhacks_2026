export function initConditions({state,api,context,selectedRoute,acceptSnapshot,findRoute,toast}){
  const el=document.getElementById('conditions-panel');
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=t=>new Date(t).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  let busy=false;
  function render(){
    const s=state.snapshot;if(!s){el.hidden=true;return;}el.hidden=false;
    const c=s.conditions,trend=c?.trend,events=c?.events||[],change=state.routeChange;
    let trendText=state.mode==='demo'?'':'River trend unavailable.';
    if(state.mode==='live'&&trend?.observedAt&&Date.now()-Date.parse(trend.observedAt)>30*60000)trendText=`River observations are stale · last seen ${time(trend.observedAt)}.`;
    else if(state.mode==='live'&&trend?.status==='available')trendText=`River ${trend.changeFt===0?'unchanged':trend.changeFt>0?'rose':'fell'} ${Math.abs(trend.changeFt).toFixed(2)} ft over ${Math.round(trend.windowMinutes)} min · observed ${time(trend.observedAt)}.`;
    else if(state.mode==='live'&&trend?.status==='stale')trendText=`River observations are stale · last seen ${time(trend.observedAt)}.`;
    else if(state.mode==='live'&&trend?.status==='insufficient_history')trendText='More observation history is needed to show a trend.';
    const origin=state.offline?'Saved snapshot':c?.source==='tiger'?'Tiger Data':'';
    el.innerHTML=`<div class="conditions-heading"><h2>Conditions & route updates</h2>${origin?`<span class="tag neutral">${esc(origin)}</span>`:''}</div>${trendText?`<p>${esc(trendText)}</p>`:''}${state.offline?'<p class="small-note">No new updates while offline.</p>':''}
      ${change?`<div class="route-change-alert" role="alert"><strong>${change.recalculated?'Route recalculated':'A road report affects your previous route'}</strong><p>${esc(change.events[0].simulation?'Simulated closure':'Unverified road report')} · ${esc(change.events[0].description)}</p>${change.recalculated?'<p>Previous route was cleared; this route was calculated with the new exclusion.</p>':'<button class="button primary" id="recalculate-change">Recalculate route</button>'}</div>`:''}
      ${state.mode==='demo'?`<button class="button secondary" id="simulate-closure" ${!selectedRoute()||state.offline||busy?'disabled':''}>${busy?'Recording closure…':'Demo: add a closure to my route'}</button>${events.some(e=>e.simulation&&Date.parse(e.expires)>Date.now())?`<button class="text-button" id="reset-demo-closures" ${state.offline||busy?'disabled':''}>Clear demo closures</button>`:''}<p class="small-note">Simulated event · expires after 10 minutes.</p>`:''}
      <details><summary>What changed? (${events.length})</summary>${events.length?events.slice(0,8).map(e=>`<div class="condition-event"><strong>${e.simulation?'Demo closure':'Unverified '+esc(e.kind.replaceAll('_',' '))}</strong><span>${time(e.createdAt)} · ${Date.parse(e.expires)<=Date.now()?'Expired':'expires '+time(e.expires)}</span><p>${esc(e.description)}</p></div>`).join(''):'<p>No road events recorded for this city and mode.</p>'}${c?.hourly?.length?`<h3>Hourly river history</h3>${c.hourly.map(h=>`<p>${time(h.bucket)} · average ${Number(h.average_ft).toFixed(2)} ft · ${Number(h.observations)} observations</p>`).join('')}`:''}</details>
      <p class="small-note">Checked ${c?.checkedAt?time(c.checkedAt):'unknown'}. ${c?.hourly?.length?`${c.hourly.length} hourly river summaries queried from Tiger.`:''}</p>`;
    el.querySelector('#recalculate-change')?.addEventListener('click',findRoute);
    el.querySelector('#reset-demo-closures')?.addEventListener('click',async()=>{const epoch=state.epoch;busy=true;render();try{const result=await api('/api/demo-reset',context());if(epoch===state.epoch){state.routeChange=null;acceptSnapshot(result.snapshot);toast('Simulated closures cleared. Recalculate to start the demo again.');}}catch(error){toast(error.message,true);}finally{busy=false;render();}});
    el.querySelector('#simulate-closure')?.addEventListener('click',async()=>{
      const route=selectedRoute();if(!route||busy)return;const epoch=state.epoch;busy=true;render();
      try{const result=await api('/api/demo-closure',{...context(),start:state.origin,destinationId:route.destination.id});if(epoch!==state.epoch)return;acceptSnapshot(result.snapshot);toast(result.storage==='tiger'?'Simulated closure stored and queried in Tiger. Recalculate your route.':'Simulated closure stored locally. Tiger is unavailable.');}
      catch(error){toast(error.message,true);}finally{busy=false;render();}
    });
  }
  return {render};
}
