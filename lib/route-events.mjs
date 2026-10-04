import {segmentDistanceKm} from './geo.mjs';
export function affectingEvents(route,events,now=Date.now()){
  if(!route?.geometry)return [];
  const line=route.geometry.coordinates;
  return events.filter(e=>['flooded_road','blocked_road','fallen_tree'].includes(e.kind)&&Date.parse(e.expires)>now&&e.status!=='resolved'&&line.some((p,i)=>i>0&&segmentDistanceKm(e.coordinates,line[i-1],p)<=.075));
}
