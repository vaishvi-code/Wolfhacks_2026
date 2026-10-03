import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Status, Destinations, RouteResponse, Location } from './api'
import type { GeoJsonObject } from 'geojson'

type Props={status:Status,origin:Location,destinations:Destinations|null,route:RouteResponse|null,selected:string,onSelect:(id:string)=>void}
export default function MapView({status,origin,destinations,route,selected,onSelect}:Props) {
  const host=useRef<HTMLDivElement>(null), map=useRef<L.Map|null>(null)
  const fitted=useRef('')
  useEffect(()=>{
    if(!host.current)return
    map.current=L.map(host.current,{zoomControl:false,attributionControl:true,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false}).setView(origin,15)
    L.control.zoom({position:'topright'}).addTo(map.current)
    map.current.attributionControl.addAttribution('Local road geometry · no internet tiles')
    const observer=new ResizeObserver(()=>map.current?.invalidateSize())
    observer.observe(host.current)
    return ()=>{observer.disconnect();map.current?.remove();map.current=null}
  },[])
  useEffect(()=>{
    const m=map.current;if(!m)return
    const layers=L.layerGroup().addTo(m)
    const geo=(value:unknown,options:L.GeoJSONOptions)=>L.geoJSON(value as GeoJsonObject,options).addTo(layers)
    geo(status.roads,{style:{color:'#ffffff',weight:12,opacity:1}})
    const roads=geo(status.roads,{style:{color:'#c4cdc4',weight:7,opacity:1}})
    for(const h of status.hazards){
      const color=h.hazard_type==='heat'?'#b9661a':h.hazard_type==='flood'?'#276b99':'#78549b'
      const layer=geo(h.geometry,{style:{color,weight:2,fillOpacity:.14,dashArray:h.metadata.freshness==='current'?undefined:'5 5'}})
      const label=document.createElement('span');label.textContent=`${h.metadata.event||h.hazard_type} · ${h.metadata.freshness||'unknown'}`
      layer.bindTooltip(label)
    }
    if(route?.comparison.baseline.route)geo(route.comparison.baseline.route.route_geometry,{style:{color:'#8a5448',weight:7,dashArray:'7 9',opacity:.85}})
    if(route?.comparison.safer.route)geo(route.comparison.safer.route.route_geometry,{style:{color:'#167361',weight:6,opacity:1}})
    const user=L.circleMarker(origin,{radius:9,color:'#fff',weight:4,fillColor:'#2369b3',fillOpacity:1}).addTo(layers)
    user.bindTooltip(status.demo?'Demo starting point':'Your chosen location',{permanent:true,direction:'bottom',offset:[0,10]})
    for(const item of destinations?.candidates||[]){const d=item.destination;if(!d)continue
      const marker=L.marker([d.latitude,d.longitude],{icon:L.divIcon({className:'destination-pin',html:`<span class="${d.id===selected?'selected':''}">${d.classification==='official_cooling_center'?'C':'P'}</span>`,iconSize:[34,34],iconAnchor:[17,17]}),title:d.name||d.id,keyboard:true}).addTo(layers)
      const label=document.createElement('span');label.textContent=d.name||d.id;marker.bindTooltip(label)
      marker.on('click',()=>onSelect(d.id))
    }
    const fitKey=`${status.workspace_id}:${origin.join(',')}`
    if(fitted.current!==fitKey){const bounds=roads.getBounds();bounds.extend(origin);m.fitBounds(bounds,{padding:[65,65],maxZoom:16,animate:false});fitted.current=fitKey}
    return ()=>{layers.remove()}
  },[status,origin,destinations,route,selected,onSelect])
  return <div className="map" ref={host} role="region" aria-label="Route map. Route and hazard details are also available in the adjacent panels." />
}
