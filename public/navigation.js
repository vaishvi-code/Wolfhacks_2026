export function initNavigation({map,state,selectedRoute,selectFeature,fitRoute}){
  const $=id=>document.getElementById(id),panel=$('evacuation-map');
  function fullScreen(on){
    if(on)selectFeature('route');
    panel.classList.toggle('navigation-fullscreen',on);document.body.classList.toggle('map-fullscreen',on);
    $('expand-map').textContent=on?'Exit full screen':'Full screen';$('expand-map').setAttribute('aria-pressed',String(on));
    requestAnimationFrame(()=>map.invalidateSize());
  }
  $('expand-map').onclick=()=>fullScreen(!panel.classList.contains('navigation-fullscreen'));
  $('navigation-overview').onclick=()=>{if(selectedRoute())fitRoute();else if(state.origin)map.setView([state.origin[1],state.origin[0]],16);};
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&panel.classList.contains('navigation-fullscreen'))fullScreen(false);});
  return {leave(){fullScreen(false);}};
}
