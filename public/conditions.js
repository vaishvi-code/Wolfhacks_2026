const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function riverHistoryChart(conditions,mode){
  if(mode!=='live')return '';
  const finite=n=>n!=null&&Number.isFinite(Number(n));
  const hourly=(conditions?.hourly||[]).filter(h=>Number.isFinite(Date.parse(h.bucket))&&finite(h.average_ft)).map(h=>({time:h.bucket,value:Number(h.average_ft),min:finite(h.min_ft)?Number(h.min_ft):Number(h.average_ft),max:finite(h.max_ft)?Number(h.max_ft):Number(h.average_ft),count:h.observations})).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const raw=(conditions?.trend?.points||[]).filter(p=>Number.isFinite(Date.parse(p.time))&&finite(p.value)).map(p=>({time:p.time,value:Number(p.value),min:Number(p.value),max:Number(p.value)})).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const points=hourly.length>=2?hourly:raw.length?raw:hourly;
  if(!points.length)return '<section class="river-history"><h3>River level history</h3><p class="small-note">No river history available yet.</p></section>';
  const aggregates=points===hourly,lo=Math.min(...points.map(p=>p.min)),hi=Math.max(...points.map(p=>p.max)),pad=Math.max((hi-lo)*.15,.05),bottom=lo-pad,top=hi+pad;
  const start=Date.parse(points[0].time),end=Date.parse(points.at(-1).time);
  const x=p=>end===start?310:58+(Date.parse(p.time)-start)/(end-start)*514,y=v=>162-(v-bottom)/(top-bottom)*132;
  const fmt=t=>new Date(t).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}),full=t=>new Date(t).toLocaleString();
  const axes=[bottom,(top+bottom)/2,top].map(v=>`<line x1="58" y1="${y(v)}" x2="572" y2="${y(v)}" class="river-grid"/><text x="49" y="${y(v)+4}" text-anchor="end">${v.toFixed(2)}</text>`).join('');
  const marks=points.map(p=>`<g><title>${escapeHtml(full(p.time))}: ${p.value.toFixed(2)} ft${aggregates?` average; min ${p.min.toFixed(2)}, max ${p.max.toFixed(2)} ft`:''}</title>${aggregates?`<line x1="${x(p)}" y1="${y(p.min)}" x2="${x(p)}" y2="${y(p.max)}" class="river-range"/>`:''}<circle cx="${x(p)}" cy="${y(p.value)}" r="3" class="river-point"/></g>`).join('');
  return `<section class="river-history"><h3>River level history</h3><p class="small-note">${aggregates?'Hourly average · bars show minimum and maximum':'Recorded observations'} · gage height in feet</p><svg viewBox="0 0 600 202" role="img" aria-label="River gage height history from ${escapeHtml(full(points[0].time))} to ${escapeHtml(full(points.at(-1).time))}"><text x="10" y="16">ft</text>${axes}<polyline points="${points.map(p=>`${x(p)},${y(p.value)}`).join(' ')}" class="river-line"/>${marks}<text x="58" y="188">${escapeHtml(fmt(points[0].time))}</text><text x="572" y="188" text-anchor="end">${escapeHtml(fmt(points.at(-1).time))}</text></svg><details><summary>View readings (${points.length})</summary><div class="reading-scroll"><table class="reading-table"><thead><tr><th>Observed</th><th>${aggregates?'Average':'Height'} (ft)</th>${aggregates?'<th>Min / max (ft)</th><th>Readings</th>':''}</tr></thead><tbody>${points.map(p=>`<tr><td>${escapeHtml(full(p.time))}</td><td>${p.value.toFixed(2)}</td>${aggregates?`<td>${p.min.toFixed(2)} / ${p.max.toFixed(2)}</td><td>${escapeHtml(p.count??'—')}</td>`:''}</tr>`).join('')}</tbody></table></div></details><p class="small-note">${conditions?.source==='tiger'?'History from Tiger Data.':'History from local observations.'} Gage height is not flood stage. Gaps may reflect missing observations.</p></section>`;
}

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
      ${riverHistoryChart(c,state.mode)}
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
