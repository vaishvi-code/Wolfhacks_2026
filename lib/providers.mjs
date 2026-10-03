import {USER_AGENT} from './config.mjs';
import {normalizeWeather,parseReadings,riverSignal} from './model.mjs';
import {geometryCenter,pointInGeometry,bboxGeometry,segmentIntersectsGeometry} from './geo.mjs';
import {parseRoads} from './routing.mjs';

export async function fetchJSON(url,options={}) {
  const response=await fetch(url,{...options,signal:AbortSignal.timeout(options.timeout||18000),headers:{'User-Agent':USER_AGENT,Accept:'application/geo+json, application/json',...options.headers}});
  if(!response.ok) throw new Error(`Provider returned HTTP ${response.status}`);
  if(!(response.headers.get('content-type')||'').includes('json')) throw new Error('Provider returned a non-JSON response');
  return response.json();
}
export class Providers {
  constructor(store){this.store=store;this.inflight=new Map();}
  async cached(id,name,ttl,loader){
    const old=this.store.get(id),now=Date.now();
    if(old&&now-Date.parse(old.updated)<ttl) return {data:old.data,source:{id,name,status:'cached',lastSuccess:old.updated,detail:'Within the provider refresh interval.'}};
    if(this.inflight.has(id)) return this.inflight.get(id);
    const job=(async()=>{try{
      const data=await loader();this.store.set(id,data);
      return {data,source:{id,name,status:'live',lastSuccess:new Date().toISOString(),detail:'Provider request succeeded.'}};
    }catch(error){return {data:old?.data??null,source:{id,name,status:old?'stale':'unavailable',lastSuccess:old?.updated??null,detail:error.name==='TimeoutError'?'Provider request timed out.':error.message}};}
    finally{this.inflight.delete(id);}})();this.inflight.set(id,job);return job;
  }
  async alerts(region){return this.cached(`nws-${region.id}`,'NOAA · NWS alerts',300000,async()=>{
    const data=await fetchJSON('https://api.weather.gov/alerts/active?area=NC');
    const candidates=normalizeWeather(data);
    const relevant=[];
    for(const i of candidates){
      // County text is only a relevance fallback; it never fabricates a polygon.
      const countyMatch=i.place?.toLowerCase().includes(region.county.replace(' County','').toLowerCase());
      if(!i.geometry && countyMatch){
        const zones=await Promise.allSettled(i.zones.slice(0,12).map(async url=>{
          if(!/^https:\/\/api\.weather\.gov\/zones\/(forecast|county|fire)\/[A-Z0-9]+$/.test(url))return null;
          const cached=await this.cached(`zone:${url}`,'NWS zone boundary',7*86400000,()=>fetchJSON(url));return cached.data?.geometry;
        }));
        const geometries=zones.filter(r=>r.status==='fulfilled'&&r.value).map(r=>r.value);
        if(geometries.length===i.zones.length&&geometries.length){i.geometry={type:'GeometryCollection',geometries};i.center=geometryCenter(i.geometry);i.footprint='Official affected zones (not an inundation estimate)';}
      }
      const box=bboxGeometry(region.bbox),corners=box.coordinates[0];
      const intersects=i.geometry&&(pointInGeometry(region.center,i.geometry)||corners.some((p,j)=>j>0&&segmentIntersectsGeometry(corners[j-1],p,i.geometry))||(i.center&&pointInGeometry(i.center,box)));
      if(countyMatch||intersects)relevant.push(i);
    }
    return relevant;
  });}
  weather(region){return this.cached(`weather-${region.id}`,'NOAA · weather station',300000,async()=>{
    const data=await fetchJSON(`https://api.weather.gov/stations/${region.station}/observations/latest`),p=data.properties;
    const temperature=p.temperature?.value,wind=p.windSpeed?.value;
    return {temperatureF:temperature==null?null:temperature*9/5+32,humidity:p.relativeHumidity?.value??null,windMph:wind==null?null:wind*.621371,time:p.timestamp,station:region.station,description:p.textDescription,url:`https://api.weather.gov/stations/${region.station}/observations/latest`};
  });}
  async sensor(region){
    const result=await this.cached(`usgs-${region.id}`,'USGS · river sensor',60000,async()=>{
      const query=new URLSearchParams({f:'json',monitoring_location_id:`USGS-${region.gauge}`,parameter_code:'00065',limit:'10000'});
      const latest=await fetchJSON(`https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/items?${query}`);
      let readings=parseReadings(latest,region.gauge);
      if(!readings.length)throw new Error('No gage-height readings returned for this station.');
      this.store.saveReadings(region.gauge,readings);
      const historyKey=`history-${region.id}`,last=this.store.get(historyKey);
      if(!last||Date.now()-Date.parse(last.updated)>6*3600000){
        try{
          query.set('datetime',`${new Date(Date.now()-26*3600000).toISOString()}/..`);
          const history=await fetchJSON(`https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items?${query}`,{timeout:15000});
          this.store.saveReadings(region.gauge,parseReadings(history,region.gauge));this.store.set(historyKey,{ok:true});
        }catch{/* Keep the genuine latest reading; no invented historical trend. */}
      }
      const feature=latest.features.find(f=>f.properties.parameter_code==='00065');
      return {coordinates:feature?.geometry?.coordinates?.slice(0,2)||region.gaugeCoordinates};
    });
    return {source:result.source,data:riverSignal({id:region.gauge,name:region.gaugeName,coordinates:result.data?.coordinates||region.gaugeCoordinates},this.store.readings(region.gauge))};
  }
  osm(region){return this.cached(`osm-v2-${region.id}`,'OpenStreetMap · roads & resources',86400000,async()=>{
    const [w,s,e,n]=region.bbox,bbox=`${s},${w},${n},${e}`;
    const query=`[out:json][timeout:25];(way[highway][highway!~"^(footway|path|cycleway|steps|pedestrian|track|construction|proposed|bridleway|corridor)$"](${bbox});nwr[amenity~"^(hospital|clinic|community_centre|library|fire_station|shelter)$"](${bbox}););out body geom;`;
    let data,lastError;
    for(const endpoint of ['https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter']){try{data=await fetchJSON(endpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({data:query}),timeout:32000});break;}catch(e){lastError=e;}}
    if(!data)throw lastError;if(data.remark)throw new Error('Overpass returned incomplete data. Retry later.');
    const facilities=(data.elements||[]).filter(e=>e.tags?.amenity).flatMap(e=>{
      const points=e.geometry||e.members?.flatMap(m=>m.geometry||[])||[];
      const coordinates=e.lon!=null?[e.lon,e.lat]:e.center?[e.center.lon,e.center.lat]:points.length?[points.reduce((s,p)=>s+p.lon,0)/points.length,points.reduce((s,p)=>s+p.lat,0)/points.length]:null;
      if(!coordinates||!pointInGeometry(coordinates,bboxGeometry(region.bbox)))return [];
      return [{id:`osm-${e.type}-${e.id}`,name:e.tags.name||`Unnamed ${e.tags.amenity.replaceAll('_',' ')}`,kind:e.tags.amenity,coordinates,availability:'Unverified',access:e.tags.access||'unknown',openingHours:e.tags.opening_hours||null,phone:e.tags.phone||e.tags['contact:phone']||null,wheelchair:e.tags.wheelchair||'unknown',url:`https://www.openstreetmap.org/${e.type}/${e.id}`,source:'OpenStreetMap',simulation:false}];
    });
    return {facilities,roads:parseRoads(data)};
  });}
  census(region){
    if(!process.env.CENSUS_API_KEY)return Promise.resolve({data:null,source:{id:`census-${region.id}`,name:'US Census · county context',status:'needs-key',lastSuccess:null,detail:'Add CENSUS_API_KEY for ACS county population estimates.'}});
    return this.cached(`census-${region.id}`,'US Census · county context',7*86400000,async()=>{
      const q=new URLSearchParams({get:'NAME,B01003_001E,B01003_001M',for:`county:${region.fips}`,in:'state:37',key:process.env.CENSUS_API_KEY});
      const data=await fetchJSON(`https://api.census.gov/data/2024/acs/acs5?${q}`);
      if(!Array.isArray(data)||!data[1])throw new Error('Census county estimate unavailable.');
      const row=Object.fromEntries(data[0].map((k,i)=>[k,data[1][i]]));
      if(!Number.isFinite(Number(row.B01003_001E))||Number(row.B01003_001E)<0||!Number.isFinite(Number(row.B01003_001M))||Number(row.B01003_001M)<0)throw new Error('Census returned a suppressed or unavailable population estimate.');
      return {name:row.NAME,population:Number(row.B01003_001E),marginOfError:Number(row.B01003_001M),vintage:'2024 ACS 5-year',note:'Entire county estimate, not population exposed to a hazard.',url:'https://api.census.gov/data/2024/acs/acs5.html'};
    });
  }
}
